"""Aarstiderne: prices only, from the public search page. No login, no basket.

The basket on aarstiderne.com belongs to one browser session, not to the account (live
2026-11-04): a basket filled here never reaches the human's own browser. So handle-mcp only
reads prices, and the human puts the list in the basket on aarstiderne.com (docs/adr/0003).
The search page shows the normal price and the member price ("Medlemspris") without a login.
Never add basket, delivery day, meal box, subscription, voucher, checkout or payment calls here.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from urllib.parse import quote

import httpx

from handle_mcp.models import (
    Cart,
    DeliveryInfo,
    DeliverySlot,
    Order,
    Product,
    QuantityResult,
    Recipe,
    RecipeDetails,
    StoreTerms,
)
from handle_mcp.stores import StoreError
from handle_mcp.stores import aarstiderne_html as page
from handle_mcp.stores._http import request

log = logging.getLogger(__name__)

BASE = "https://www.aarstiderne.com"
UNITS = {"kg": "kg", "l": "l", "ltr": "l", "stk": "stk"}
SKIP_PATHS = ("/maaltidskasser/",)  # meal boxes: not groceries
HEADERS = {"User-Agent": "Mozilla/5.0 (handle-mcp)"}
MINIMUM_ORDER = Decimal(89)  # "minimumskøb på 89 kr." in the basket, live 2026-11-05
NO_CART = (
    "Aarstiderne har ingen kurv i handle-mcp: kurven hører til din egen browser. "
    "Brug compare_basket og læg selv listen i kurven på aarstiderne.com."
)
MEMBER_NOTE = (
    "Kun priser: læg selv varerne i kurven på aarstiderne.com. "
    "Medlemspris og gratis levering med Aarstiderne Plus. Alt er økologisk."
)
PLAIN_NOTE = "Kun priser: læg selv varerne i kurven på aarstiderne.com. Normalpris, ikke medlem."


def member() -> bool:
    """Member price unless HANDLE_AARSTIDERNE_MEMBER=0: the search shows both, logged out."""
    return os.environ.get("HANDLE_AARSTIDERNE_MEMBER", "1").strip() not in ("0", "false", "nej")


class AarstiderneStore:
    name = "aarstiderne"
    needs_login = False  # the public search page has both prices

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=20, follow_redirects=True, headers=HEADERS
        )

    async def search(self, query: str, limit: int = 10) -> list[Product]:
        resp = await request(
            self._client, "GET", f"{BASE}/soegeresultater?q={quote(query)}", "aarstiderne.com"
        )
        if resp.status_code != 200:
            raise StoreError(f"aarstiderne.com svarer {resp.status_code} på søgningen.")
        is_member = member()
        products = [
            self._product(r, is_member)
            for r in page.search_rows(resp.text)
            if not (r["url"] or "").startswith(SKIP_PATHS)
        ]
        return products[:limit]

    def _product(self, row: dict, is_member: bool) -> Product:
        normal, member_price = row["normal"], row["member"]
        price = member_price if is_member and member_price else normal or member_price
        unit = row.get("unit")
        return Product(
            store=self.name,
            id=str(row["id"]),
            name=row["name"],
            price=price or Decimal(0),
            brand="Aarstiderne",
            size=row.get("size"),
            unit_price=row.get("unit_price") if unit else None,
            unit=UNITS.get(unit, unit) if unit else None,
            available=not row.get("sold_out"),
            # '/dagligvarer/groent/smagsgivere-krydderurter/hvidlog' → 'groent > smagsgivere-...'
            category=" > ".join((row.get("url") or "").strip("/").split("/")[1:3]) or None,
            oeko=bool(row.get("oeko")),
        )

    async def terms(self) -> StoreTerms:
        is_member = member()
        return StoreTerms(
            store=self.name,
            home_delivery=True,
            delivery_fee=Decimal(0) if is_member else None,
            minimum_order=MINIMUM_ORDER,
            source="config",
            list_only=True,
            note=MEMBER_NOTE if is_member else PLAIN_NOTE,
        )

    async def get_cart(self) -> Cart:
        raise StoreError(NO_CART)

    async def set_quantities(self, items: dict[str, int]) -> list[QuantityResult]:
        raise StoreError(NO_CART)

    async def offers(self, limit: int = 25) -> list[Product]:
        raise NotImplementedError  # no offers list found on aarstiderne.com (2026-11-04)

    async def recipes(self, query: str, limit: int = 5) -> list[Recipe]:
        raise NotImplementedError

    async def recipe(self, recipe_id: str, persons: int | None = None) -> RecipeDetails:
        raise NotImplementedError

    async def featured_recipes(self, limit: int = 20) -> list[Recipe]:
        raise NotImplementedError

    async def recipes_by_tag(
        self, tags: list[str], category: str | None = None, limit: int = 20
    ) -> list[Recipe]:
        raise NotImplementedError

    async def delivery_info(self) -> DeliveryInfo:
        raise NotImplementedError

    async def delivery_slots(self, days: int = 7) -> list[DeliverySlot]:
        raise NotImplementedError

    async def orders(self, limit: int = 10) -> list[Order]:
        raise NotImplementedError

    async def order(self, order_id: str) -> Order:
        raise NotImplementedError
