"""REMA 1000 adapter against a fake backend (httpx.MockTransport). No network."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from handle_mcp import compare
from handle_mcp.stores import StoreError
from handle_mcp.stores import rema as rema_module
from handle_mcp.stores.rema import RemaStore, packs

FIXTURES = Path(__file__).parent / "fixtures" / "rema"
TODAY = date(2026, 11, 4)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(rema_module, "_today", lambda: TODAY)


class FakeRema:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/search/products":
            return httpx.Response(200, json=fixture("search_hakkede_tomater"))
        if path == "/api/v3/products":
            return httpx.Response(200, json=fixture("products_page"))
        if path == "/api/search/recipes":
            return httpx.Response(200, json=fixture("recipes_lasagne"))
        return httpx.Response(404, json={"message": "not found"})


@pytest.fixture
def fake() -> FakeRema:
    return FakeRema()


@pytest.fixture
def store(fake) -> RemaStore:
    return RemaStore(httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)))


async def test_search_parses_price_size_brand_and_ean(store, fake):
    first, oeko = (await store.search("hakkede tomater", 4))[:2]
    assert first.store == "rema" and first.name == "HAKKEDE TOMATER"
    assert first.size == "400 GR." and first.brand == "REMA 1000"
    assert first.price == Decimal("6.37") and first.unit_price == Decimal("15.93")
    assert first.unit == "kg" and first.ean == "5705830017857"
    assert first.category == "Kolonial > Konserves & survarer"
    assert not first.oeko and oeko.oeko  # the Økologi label, not the name
    assert fake.requests[0].url.params["per_page"] == "4"


async def test_offers_use_this_weeks_campaign_and_the_normal_price_as_before(store):
    offers = await store.offers(10)
    assert len(offers) == 4  # the two normal products are left out
    sauce = next(p for p in offers if p.name == "KIRSEBÆRSAUCE")
    assert sauce.price == Decimal("20.00") and sauce.before_price == Decimal("31.95")
    assert sauce.on_offer and sauce.campaign == "Avisvare"
    assert sauce.offer_text == "Avisvare" and sauce.offer_until == date(2026, 11, 7)
    savings = [(p.before_price - p.price) / p.before_price for p in offers]
    assert savings == sorted(savings, reverse=True)


async def test_offers_fetch_the_catalogue_once_an_hour(store, fake):
    await store.offers(5)
    await store.offers(5)
    assert sum(r.url.path == "/api/v3/products" for r in fake.requests) == 1


def test_next_weeks_price_never_counts_today():
    row = fixture("products_page")["data"][0]
    after = rema_module.product(row, today=date(2026, 11, 8))
    assert after.price == Decimal("31.95") and not after.on_offer


def test_a_product_without_a_price_today_is_left_out():
    row = {
        "id": 1,
        "name": "X",
        "prices": [{"price": 5, "starting_at": "2027-01-01T00:00:00+00:00"}],
    }
    assert rema_module.product(row, today=TODAY) is None


async def test_recipe_scales_to_persons_and_counts_packs(store):
    recipe = await store.recipe("https://shop.rema1000.dk/opskrifter/lasagne", persons=8)
    assert recipe.name == "Lasagne" and recipe.persons == 8
    assert recipe.url == "https://shop.rema1000.dk/opskrifter/lasagne"
    beef = next(i for i in recipe.items if i.ingredient == "hakket oksekød")
    assert beef.product.quantity == 2  # 800 g of 500 g packs
    onion = next(i for i in recipe.items if i.ingredient == "løg")
    assert onion.have_at_home  # a basic ingredient: left out of the total
    assert recipe.total == sum(
        i.product.price * i.product.quantity for i in recipe.items if not i.have_at_home
    )
    assert "800 g hakket oksekød" in recipe.ingredients


async def test_unknown_recipe_is_a_clear_error(store):
    with pytest.raises(StoreError, match="Fandt ikke opskriften"):
        await store.recipe("findes-ikke")


async def test_recipes_give_slug_link_and_persons(store):
    (first,) = await store.recipes("lasagne", 1)
    assert first.id == "lasagne" and first.persons == 4
    assert first.url.endswith("/opskrifter/lasagne")


@pytest.mark.parametrize(
    ("amount", "unit", "content", "content_unit", "expected"),
    [
        (400, "g", 500, "g", 1),
        (1200, "g", 500, "g", 3),
        (1, "kg", 500, "g", 2),
        (500, "ml", 1, "l", 1),
        (2, "fed", 1, "pcs", 1),
        (0, "", 500, "g", 1),
    ],
)
def test_packs(amount, unit, content, content_unit, expected):
    assert packs(amount, unit, content, content_unit) == expected


async def test_no_cart_says_shop_in_the_store(store):
    with pytest.raises(StoreError, match="handler selv i butikken"):
        await store.get_cart()
    with pytest.raises(StoreError, match="handler selv i butikken"):
        await store.set_quantities({"1": 1})


async def test_compare_marks_rema_as_list_only(store):
    quote = await compare.quote(store, ["hakkede tomater"])
    assert quote.list_only and quote.fees == {}
    assert any("Kun i butik" in n for n in quote.notes)
    assert not any("hjemlevering" in n for n in quote.notes)


def test_verdict_says_make_a_list_when_rema_is_cheapest():
    line = compare.BasketLine("mælk", 1, object(), Decimal(10), True)
    cheap = compare.BasketQuote("rema", [line], [], Decimal(10), {}, Decimal(10), None, None)
    cheap.list_only = True
    dear = compare.BasketQuote(
        "nemlig", [compare.BasketLine("mælk", 1, object(), Decimal(12), True)], [],
        Decimal(12), {"levering": Decimal(29)}, Decimal(41), None, None,
    )  # fmt: skip
    result = compare.verdict({"rema": cheap, "nemlig": dear})
    assert result["cheapest"] == "rema"
    assert "lav en indkøbsliste" in result["summary"]


@pytest.mark.parametrize(
    ("name", "blend"),
    [
        ("HK. OKSEKØD, 35% GRØNT", 1),  # REMA 1000 writes the blend after a comma
        ("Skummetmælk 0,1% øko.", 0),  # a decimal comma is not a blend (live 2026-11-05)
        ("HK. OKSEKØD 8-12%", 0),
    ],
)
def test_blend_after_a_comma_but_not_a_decimal_comma(name, blend):
    from handle_mcp.match import mixed

    words = ["skummetmælk"] if "mælk" in name else ["hakket", "oksekød"]
    assert mixed(words, {"name": name}) == blend
