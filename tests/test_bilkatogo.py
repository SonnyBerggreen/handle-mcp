"""BilkaToGo adapter against a fake backend (httpx.MockTransport). No network."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from handle_mcp import server
from handle_mcp import stores as registry
from handle_mcp.stores import StoreError, _http
from handle_mcp.stores import bilkatogo as bilka_module
from handle_mcp.stores.bilkatogo import NOT_HOME, BilkaStore

FIXTURES = Path(__file__).parent / "fixtures" / "bilkatogo"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeBilka:
    """Answers like Gigya, api.bilkatogo.dk and Algolia. Tests can script answers."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.cart = fixture("cart_home")
        self.scripted: dict[
            str, list
        ] = {}  # path → responses (status int or dict body) to give first
        self.counts: dict[str, int] = {"18381": 1}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.scripted.get(path):
            nxt = self.scripted[path].pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return httpx.Response(nxt) if isinstance(nxt, int) else httpx.Response(200, json=nxt)
        if path == "/accounts.login":
            form = parse_qs(request.content.decode())
            if form["password"] != ["hemmeligt"]:
                return httpx.Response(403, json=fixture("gigya_login_invalid"))
            return httpx.Response(200, json=fixture("gigya_login"))
        if path == "/accounts.getJWT":
            return httpx.Response(200, json=fixture("gigya_jwt"))
        if path == "/api/auth/LoginJWT":
            return httpx.Response(
                200, json=fixture("login_jwt"), headers={"set-cookie": "cart=abc; Path=/"}
            )
        if path == "/api/shop/v6/Cart":
            return httpx.Response(200, json=self.cart)
        if path == "/1/indexes/*/queries":
            params = json.loads(request.content)["requests"][0]["params"]
            name = "algolia_offers" if "isInOffer" in params else "algolia_products"
            return httpx.Response(200, json={"results": [fixture(name)]})
        if path == "/api/shop/v6/ChangeLineCount":
            pid, count = request.url.params["productId"], int(request.url.params["count"])
            if pid == "88672":
                return httpx.Response(200, json=fixture("change_line_count_unavailable"))
            answer = fixture("change_line_count")
            applied = min(count, 5) if pid == "limited" else count
            message = "Max 5 pr. kunde" if pid == "limited" else ""
            return httpx.Response(
                200, json={**answer, "productId": pid, "productAmount": applied, "message": message}
            )
        if path == "/api/shop/v3/AvailableDeliveryDates":
            return httpx.Response(200, json=fixture("available_delivery_dates"))
        if path == "/api/shop/v7/OrderHistory":
            return httpx.Response(200, json=fixture("order_history"))
        if path == "/api/shop/v7/OrderDetails":
            return httpx.Response(200, json=fixture("order_details_plain"))
        return httpx.Response(404)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


async def _no_sleep(_seconds):
    return None


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("HANDLE_BILKATOGO_EMAIL", "kunde@example.com")
    monkeypatch.setenv("HANDLE_BILKATOGO_PASSWORD", "hemmeligt")
    monkeypatch.setattr(_http.asyncio, "sleep", _no_sleep)
    return FakeBilka()


@pytest.fixture
def store(fake):
    return BilkaStore(httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)))


async def test_login_flow_and_jwt_header(store, fake):
    await store.get_cart()
    assert fake.paths()[:4] == [
        "/accounts.login",
        "/accounts.getJWT",
        "/api/auth/LoginJWT",
        "/api/shop/v6/Cart",
    ]
    assert fake.requests[3].headers["jwt_token"] == "eyFake.jwt.token"


async def test_login_never_sends_cart_cookie(store, fake):
    """LoginJWT REPLACES the account's cart with the one behind a `cart` cookie."""
    await store.get_cart()  # Bilka sets cart=abc on LoginJWT
    store._jwt = None  # force a second login
    await store.get_cart()
    logins = [r for r in fake.requests if r.url.path == "/api/auth/LoginJWT"]
    assert len(logins) == 2
    assert "cart=" not in logins[1].headers.get("cookie", "")


async def test_wrong_password_is_a_clear_error(store, monkeypatch):
    monkeypatch.setenv("HANDLE_BILKATOGO_PASSWORD", "forkert")
    with pytest.raises(StoreError, match="Tjek email og kodeord"):
        await store.get_cart()


async def test_expired_session_body_logs_in_again(store, fake):
    await store.get_cart()
    fake.scripted["/api/shop/v6/Cart"] = [{"code": 1, "msg": "Du er ikke logget ind"}]
    assert (await store.get_cart()).lines
    assert fake.paths().count("/api/auth/LoginJWT") == 2


async def test_other_error_body_is_not_a_logout(store, fake):
    await store.get_cart()
    fake.scripted["/api/shop/v6/Cart"] = [{"code": None, "msg": "Noget gik galt", "eid": 500}]
    with pytest.raises(StoreError, match="Noget gik galt"):
        await store.get_cart()
    assert fake.paths().count("/api/auth/LoginJWT") == 1


async def test_retries_on_503(store, fake):
    fake.scripted["/api/shop/v6/Cart"] = [503, 503]
    assert (await store.get_cart()).lines


async def test_retries_on_network_error(store, fake):
    fake.scripted["/api/shop/v6/Cart"] = [
        httpx.ReadError("reset"),
        httpx.RemoteProtocolError("closed"),
    ]
    assert (await store.get_cart()).lines


async def test_search_uses_cart_store_and_parses(store, fake):
    products = await store.search("skyr", limit=5)
    skyr = next(p for p in products if p.id == "19753")
    assert (skyr.name, skyr.price, skyr.unit_price, skyr.unit, skyr.ean, skyr.size) == (
        "Skyr m. vanilje 0,2% fedt u. tilsat sukker",
        Decimal("30.95"),
        Decimal("30.95"),
        "kg",
        "5711953011399",
        "1 kg",
    )
    assert skyr.available and not skyr.on_offer
    assert (
        skyr.category
        == "Mejeri & køl > Mejeri > Yoghurt & syrnede mælkeprodukter > Skyr > Skyr m. smag"
    )
    algolia = next(r for r in fake.requests if r.url.host.endswith(".algolia.net"))
    params = parse_qs(json.loads(algolia.content)["requests"][0]["params"])
    assert params["filters"] == ["isInAssortmentIn:1661 AND nonsearchable:false"]
    assert params["removeWordsIfNoResults"] == ["allOptional"]
    assert algolia.headers["x-algolia-api-key"]


async def test_search_without_address_says_what_to_do(store, fake):
    fake.cart = {**fake.cart, "deliveryAddress": None}
    with pytest.raises(StoreError, match="Vælg hjemlevering"):
        await store.search("mælk")


async def test_home_delivery_cart(store):
    cart = await store.get_cart()
    assert cart.fees == {"levering": Decimal("39.00"), "pakkeservice": Decimal("39.00")}
    assert cart.total == Decimal("166.00")
    assert (cart.minimum_order, cart.minimum_met) == (Decimal("500.00"), False)
    assert NOT_HOME not in cart.warnings
    assert cart.warnings[0].startswith("373,00 kr. fra minimumskøb")  # HTML tags removed
    names = [ln.name for ln in cart.lines]
    assert "Papirpose" not in names and "Pakkeservice" not in names  # fees, not products


async def test_pickup_cart_warns(store, fake):
    fake.cart = fixture("cart_logged_in")
    cart = await store.get_cart()
    assert NOT_HOME in cart.warnings
    assert not (await store.terms()).home_delivery


async def test_set_quantities_ok_refused_and_limited(store):
    results = await store.set_quantities({"18381": 3, "88672": 1, "limited": 9})
    ok, refused, limited = results
    assert (ok.applied, ok.ok) == (3, True)
    assert not refused.ok and "ikke aktuelt tilgængeligt" in refused.message
    assert (limited.applied, limited.ok, limited.message) == (5, False, "Max 5 pr. kunde")


async def test_terms_and_delivery_info(store):
    terms = await store.terms()
    assert (terms.home_delivery, terms.delivery_fee, terms.minimum_order) == (
        True,
        Decimal("39.00"),
        Decimal("500.00"),
    )
    assert terms.other_fees == {"pakkeservice": Decimal("39.00")}
    info = await store.delivery_info()
    assert info.chosen_start.isoformat() == "2036-11-01T14:00:00+01:00"
    assert info.next_available.isoformat() == "2026-11-03"
    assert info.note is None


async def test_orders_and_order(store, fake):
    [first, *_] = await store.orders(limit=5)
    assert (first.id, first.status, first.total) == ("900000000002", "Modtaget", Decimal("456.00"))
    assert first.delivery_start.isoformat() == "2026-11-03T16:00:00+01:00"
    history = next(r for r in fake.requests if r.url.path.endswith("OrderHistory"))
    assert history.method == "POST" and history.url.params["limit"] == "5"
    detail = await store.order("900000000002")
    assert detail.lines[0].name == "Æbler" and detail.lines[0].line_total == Decimal("18.00")


async def test_missing_order_is_clear(store, fake):
    fake.scripted["/api/shop/v7/OrderDetails"] = [{"order": None}]
    with pytest.raises(StoreError, match="Fandt ikke ordre"):
        await store.order("1")


async def test_never_calls_forbidden_endpoints(store, fake):
    await store.search("mælk")
    await store.set_quantities({"18381": 2})
    await store.terms()
    await store.delivery_info()
    await store.delivery_slots()
    await store.orders()
    await store.order("900000000002")
    forbidden = ("EmptyCart", "SetDelivery", "Checkout", "Payment", "PlaceOrder")
    assert not [p for p in fake.paths() if any(f.lower() in p.lower() for f in forbidden)]


async def test_tools_hide_personal_data_from_bilka(store, monkeypatch):
    monkeypatch.setattr(registry, "STORES", {"bilkatogo": BilkaStore})
    monkeypatch.setattr(server, "_instances", {"bilkatogo": store})
    text = json.dumps(
        [
            await server.get_cart("bilkatogo"),
            await server.get_delivery_info("bilkatogo"),
            await server.get_orders("bilkatogo"),
            await server.get_order("bilkatogo", "900000000002"),
            await server.search_products("skyr", stores=["bilkatogo"]),
        ],
        ensure_ascii=False,
    )
    for secret in (
        "Kunde Eksempel",
        "Testvej",
        "Eksempelby",
        "kunde@example.com",
        "900001",
        "eyFake",
    ):
        assert secret not in text


async def test_old_chosen_slot_is_ignored(store, fake):
    fake.cart = {
        **fake.cart,
        "deliveryDate": {**fake.cart["deliveryDate"], "deliveryDate": "2024-09-04"},
    }
    info = await store.delivery_info()
    assert info.chosen_start is None and info.chosen_end is None
    assert (await store.get_cart()).delivery is None  # the cart text said "Ons. 04/09" too


async def test_current_chosen_slot_shows_in_the_cart(store):
    # the cart_home fixture's slot is 2036-11-01, so it stays in the future
    assert (await store.get_cart()).delivery == "Søn. 01/11. kl. 14:00 - 16:00"


async def test_unreadable_slot_keeps_the_cart_text(store, fake):
    fake.cart = {
        **fake.cart,
        "deliveryDate": {**fake.cart["deliveryDate"], "intervalEnd": None},
    }
    assert (await store.get_cart()).delivery == "Søn. 01/11. kl. 14:00 - 16:00"


async def test_set_quantities_stops_on_store_failure(store, fake):
    await store.get_cart()
    fake.scripted["/api/shop/v6/ChangeLineCount"] = [503, 503, 503]
    with pytest.raises(StoreError, match="503"):
        await store.set_quantities({"18381": 2, "19753": 1})
    assert fake.paths().count("/api/shop/v6/ChangeLineCount") == 3  # second item not sent


def test_money_in_ore_is_robust():
    assert bilka_module._kr(12345) == Decimal("123.45")
    assert bilka_module._kr("1250") == Decimal("12.50")
    assert bilka_module._kr(None) == Decimal("0.00")
    with pytest.raises(StoreError, match="ukendt beløb"):
        bilka_module._kr("abc")


# Shapes seen live 2026-11-01 (docs/research.md). Amounts in øre.
def _hit(offer: str, price: int, now: int, show: int) -> dict:
    data = {
        "price": now,
        "inStock": True,
        "offerDescription": offer,
        "unitsOfMeasurePrice": 6500,
        "unitsOfMeasureShowPrice": show,
        "unitsOfMeasurePriceUnit": "Kg.",
    }
    return {"objectID": "1", "name": "Hakket kyllingekød", "price": price, "storeData": {"s": data}}


def test_offer_has_before_price_and_offer_unit_price(store):
    p = store._product(_hit("Skarp pris", 6500, 3900, 3900), "s")
    assert (p.price, p.unit_price, p.before_price) == (
        Decimal("39.00"),
        Decimal("39.00"),
        Decimal("65.00"),
    )
    assert p.offer_text == "Skarp pris"


def test_se_her_gets_a_real_text(store):
    p = store._product(_hit("Se Her", 7495, 6900, 8625), "s")
    assert p.offer_text == "Tilbud, før 74,95 kr" and p.on_offer


def test_no_offer_has_no_before_price(store):
    p = store._product(_hit("", 10995, 8500, 14167), "s")
    assert (p.on_offer, p.before_price, p.unit_price) == (False, None, Decimal("141.67"))


async def test_offers_filter_on_our_store(store, fake):
    fake.cart = {**fake.cart, "deliveryAddress": {**fake.cart["deliveryAddress"], "sid": "1653"}}
    offers = await store.offers(10)
    [call] = [r for r in fake.requests if r.url.path == "/1/indexes/*/queries"]
    assert "isInOffer%3A1653" in json.loads(call.content)["requests"][0]["params"]
    by_id = {p.id: p for p in offers}
    assert (by_id["40258"].campaign, by_id["40258"].price) == ("Skarp pris", Decimal(25))
    assert (by_id["18381"].campaign, by_id["18381"].offer_text) == ("Mix", "Mix 10 for 25,00")


async def test_delivery_slots_flat_fee_and_night_slots(store, fake):
    slots = await store.delivery_slots(days=7)
    assert [s.start.date().isoformat() for s in slots] == ["2026-11-02", "2026-11-04"]
    mon, wed = slots  # Tuesday is sold out: no intervals at all
    assert not mon.available  # closed: past deadline
    assert (wed.available, wed.price, wed.kind) == (True, Decimal("39.00"), "Nat")
    assert wed.start.isoformat() == "2026-11-04T00:00:00+01:00"
    assert wed.end.isoformat() == "2026-11-04T06:00:00+01:00"
    assert wed.note.startswith("Levering vil ske natten til onsdag.") and "<" not in wed.note
    call = next(r for r in fake.requests if r.url.path.endswith("AvailableDeliveryDates"))
    assert call.method == "GET"


async def test_delivery_slots_keep_exactly_days(store, monkeypatch):
    monkeypatch.setattr(bilka_module, "_today", lambda: date(2026, 11, 2))
    assert [s.start.day for s in await store.delivery_slots(days=1)] == [2]
    assert [s.start.day for s in await store.delivery_slots(days=3)] == [2, 4]


async def test_delivery_slots_say_when_the_fee_is_unknown(store, fake):
    fake.cart = {**fake.cart, "stat": {}}  # an empty cart may send no delivery price
    wed = (await store.delivery_slots())[1]
    assert wed.price is None and wed.note.startswith("Gebyret er ukendt")


async def test_delivery_slots_need_home_delivery(store, fake):
    fake.cart = {**fake.cart, "deliveryAddress": {"sid": "1234"}}
    with pytest.raises(StoreError, match="hjemlevering"):
        await store.delivery_slots()


async def test_offers_without_a_login_say_why(store, monkeypatch, tmp_path):
    monkeypatch.delenv("HANDLE_BILKATOGO_EMAIL")
    monkeypatch.delenv("HANDLE_BILKATOGO_PASSWORD")
    monkeypatch.setattr(registry, "CONFIG_DIR", tmp_path)
    with pytest.raises(StoreError, match="leveringsadresse.*handle-mcp login bilkatogo"):
        await store.offers()
