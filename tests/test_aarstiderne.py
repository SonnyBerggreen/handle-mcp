"""Aarstiderne adapter against a fake website (httpx.MockTransport). No network, no login."""

from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from handle_mcp import compare
from handle_mcp.stores import StoreError
from handle_mcp.stores import aarstiderne_html as page
from handle_mcp.stores.aarstiderne import AarstiderneStore

FIXTURES = Path(__file__).parent / "fixtures" / "aarstiderne"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeAarstiderne:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/soegeresultater":
            return httpx.Response(200, text=read("search_results.html"))
        return httpx.Response(404, text="not found")


@pytest.fixture
def fake() -> FakeAarstiderne:
    return FakeAarstiderne()


@pytest.fixture
def store(fake) -> AarstiderneStore:
    return AarstiderneStore(httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)))


def test_search_rows_read_names_sizes_prices_and_oeko():
    rows = page.search_rows(read("search_results.html"))
    garlic = rows[0]
    assert garlic["id"] == "261" and garlic["name"] == "Hvidløg" and garlic["size"] == "2 stk."
    assert garlic["normal"] == Decimal("16.00") and garlic["member"] == Decimal("14.40")
    assert garlic["unit_price"] == Decimal("8.00") and garlic["unit"] == "stk"
    assert all(r["oeko"] for r in rows)


async def test_search_uses_the_member_price_without_a_login(store, fake):
    products = await store.search("hvidløg", 3)
    assert products[0].price == Decimal("14.40") and products[0].oeko
    assert products[0].category == "groent > smagsgivere-krydderurter"
    assert fake.requests[0].url.params["q"] == "hvidløg"
    assert "cookie" not in fake.requests[0].headers


async def test_normal_price_when_not_a_member(store, monkeypatch):
    monkeypatch.setenv("HANDLE_AARSTIDERNE_MEMBER", "0")
    products = await store.search("hvidløg", 3)
    assert products[0].price == Decimal("16.00")
    terms = await store.terms()
    assert terms.delivery_fee is None and "ikke medlem" in terms.note


async def test_no_cart_says_put_the_list_in_the_basket_yourself(store, fake):
    with pytest.raises(StoreError, match="læg selv listen i kurven"):
        await store.get_cart()
    with pytest.raises(StoreError, match="læg selv listen i kurven"):
        await store.set_quantities({"261": 1})
    assert fake.requests == []  # nothing is sent to the store


async def test_compare_marks_aarstiderne_as_list_only(store):
    quote = await compare.quote(store, ["hvidløg"])
    assert quote.list_only and quote.fees == {}  # free delivery for members
    assert quote.minimum_order == Decimal(89) and quote.minimum_met is False
    assert quote.lines[0].product.price == Decimal("14.40")
    assert any("Kun priser" in n for n in quote.notes)
