"""REMA 1000: the open API that shop.rema1000.dk uses. No login, no cart, no delivery.

The human shops in the store from a list. This store only reads prices, offers and recipes,
so `compare_basket` can say when REMA 1000 is cheapest. Prices are national (live 2026-11).
Never add shopping-list, Køb & Hent, order or payment calls here (docs/adr/0001).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

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
    RecipeItem,
    RecipeProduct,
    StoreTerms,
)
from handle_mcp.stores import StoreError
from handle_mcp.stores._http import request

log = logging.getLogger(__name__)

API = "https://api.digital.rema1000.dk/api"
SITE = "https://shop.rema1000.dk"
DK = ZoneInfo("Europe/Copenhagen")
PAGE = 1000  # the whole catalogue (~4,000 products) is 4 pages of 1,000
CATALOGUE_TTL = 3600  # seconds: offers change weekly, one fetch an hour is plenty
UNITS = {"kg": "kg", "ltr": "l", "l": "l", "stk": "stk"}
OEKO_LABEL = "Økologi"
ADVERTISED = "Avisvare"
NO_CART = (
    "REMA 1000 har ingen kurv i handle-mcp: I handler selv i butikken. "
    "Brug compare_basket og lav en indkøbsliste."
)
IN_STORE = "Kun i butik: ingen kurv, ingen levering. Priserne er REMA 1000's priser i hele landet."
# Recipe amounts → (base unit, factor). Other units ("fed", "dåse", "pakke") mean one pack.
RECIPE_UNITS = {
    "g": ("g", 1), "kg": ("g", 1000),
    "ml": ("ml", 1), "cl": ("ml", 10), "dl": ("ml", 100), "l": ("ml", 1000),
    "stk": ("stk", 1), "pcs": ("stk", 1),
}  # fmt: skip


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _day(value: str | None) -> date | None:
    """'2026-11-07T00:00:00+00:00' → 2026-11-07. REMA dates are whole Danish days."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _today() -> date:
    return datetime.now(DK).date()


def _price(prices: list[dict], today: date | None = None) -> dict | None:
    """The price that counts today: an active campaign first, else the normal price.
    A product carries next week's price too ('starting_at' in the future): never that one."""
    today = today or _today()
    active = [
        p
        for p in prices or []
        if (_day(p.get("starting_at")) or date.min)
        <= today
        <= (_day(p.get("ending_at")) or date.max)
        and p.get("price") is not None
    ]
    if not active:
        return None
    campaigns = [p for p in active if p.get("is_campaign")]
    return min(campaigns or active, key=lambda p: Decimal(str(p["price"])))


def _before(prices: list[dict], now: Decimal) -> Decimal | None:
    """The normal price around a campaign, when it is higher: 'before' in the offer text."""
    normal = [_money(p.get("price")) for p in prices or [] if not p.get("is_campaign")]
    higher = [p for p in normal if p is not None and p > now]
    return min(higher) if higher else None


def _split(underline: str | None) -> tuple[str | None, str | None]:
    """'400 GR. / REMA 1000' → ('400 GR.', 'REMA 1000')."""
    if not underline:
        return None, None
    size, _, brand = underline.partition("/")
    return size.strip() or None, brand.strip() or None


def _labels(p: dict) -> set[str]:
    return {x.get("name", "") for x in p.get("labels") or []}


def product(p: dict, store: str = "rema", today: date | None = None) -> Product | None:
    """One REMA product as a Product, or None when it has no price today."""
    chosen = _price(p.get("prices") or [], today)
    if not chosen:
        return None
    now = _money(chosen["price"])
    if now is None:
        return None
    size, brand = _split(p.get("underline"))
    campaign = bool(chosen.get("is_campaign"))
    before = _before(p.get("prices") or [], now) if campaign else None
    max_qty = chosen.get("max_quantity") if campaign else None
    text = None
    if campaign:
        text = ADVERTISED if chosen.get("is_advertised") else "Tilbud"
        if max_qty:
            text += f", maks. {max_qty} stk. til tilbudspris"
    unit = (chosen.get("compare_unit") or "").lower()
    category = " > ".join(
        x
        for x in ((p.get("department") or {}).get("name"), (p.get("category") or {}).get("name"))
        if x
    )
    return Product(
        store=store,
        id=str(p["id"]),
        name=p.get("name", ""),
        price=now,
        brand=brand,
        size=size,
        unit_price=_money(chosen.get("compare_unit_price")) if unit else None,
        unit=UNITS.get(unit, unit or None),
        ean=next(iter(p.get("barcodes") or []), None),
        on_offer=campaign,
        offer_text=text,
        before_price=before,
        offer_until=_day(chosen.get("ending_at")) if campaign else None,
        category=category or None,
        campaign=ADVERTISED if campaign and chosen.get("is_advertised") else None,
        max_per_order=int(max_qty) if max_qty else None,
        oeko=OEKO_LABEL in _labels(p),
    )


def packs(amount: Any, unit: str | None, content: Any, content_unit: str | None) -> int:
    """Packs to buy for a recipe amount: '400 g' of a '500 g' pack is 1, '1,2 kg' is 3.
    Units that do not convert ('2 fed' hvidløg of '1 pcs') are 1 pack."""
    want = RECIPE_UNITS.get((unit or "").strip().lower())
    have = RECIPE_UNITS.get((content_unit or "").strip().lower())
    try:
        need, size = Decimal(str(amount or 0)), Decimal(str(content or 0))
    except InvalidOperation:
        return 1
    if not want or not have or want[0] != have[0] or need <= 0 or size <= 0:
        return 1
    return max(1, math.ceil(need * want[1] / (size * have[1])))


class RemaStore:
    name = "rema"
    needs_login = False  # open API: `handle-mcp login rema` has nothing to save

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
        self._catalogue: tuple[float, list[dict]] | None = None
        self._lock = asyncio.Lock()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await request(self._client, "GET", f"{API}{path}", "REMA 1000", params=params)
        if resp.status_code != 200:
            raise StoreError(f"REMA 1000 svarer {resp.status_code} på {path.split('?')[0]}.")
        try:
            return resp.json()
        except ValueError as exc:
            raise StoreError("REMA 1000 sendte ikke JSON. API'et har måske ændret sig.") from exc

    # --- products -----------------------------------------------------------

    async def search(self, query: str, limit: int = 10) -> list[Product]:
        data = await self._get(
            "/search/products", {"query": query, "per_page": max(1, min(limit, 100))}
        )
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise StoreError("REMA 1000-søgningen har ændret format.")
        return [p for p in (product(r, self.name) for r in rows) if p][:limit]

    async def _all_products(self) -> list[dict]:
        """The whole catalogue, at most once an hour: REMA has no offers-only call."""
        async with self._lock:
            if self._catalogue and time.monotonic() - self._catalogue[0] < CATALOGUE_TTL:
                return self._catalogue[1]
            rows: list[dict] = []
            page, last = 1, 1
            while page <= last and page <= 10:  # 10 pages = 10,000 products: a safe stop
                data = await self._get("/v3/products", {"per_page": PAGE, "page": page})
                rows += data.get("data") or []
                last = int(((data.get("meta") or {}).get("pagination") or {}).get("last_page") or 1)
                page += 1
            self._catalogue = (time.monotonic(), rows)
            return rows

    async def offers(self, limit: int = 25) -> list[Product]:
        """This week's campaign prices, the biggest discount first."""
        hits = [p for p in (product(r, self.name) for r in await self._all_products()) if p]
        on_offer = [p for p in hits if p.on_offer]

        def saving(p: Product) -> Decimal:
            return (p.before_price - p.price) / p.before_price if p.before_price else Decimal(0)

        return sorted(on_offer, key=saving, reverse=True)[:limit]

    # --- recipes ------------------------------------------------------------

    def _recipe(self, hit: dict) -> Recipe:
        fields = hit.get("fields") or {}
        form = fields.get("customForm") or {}
        minutes = form.get("totalTime") or 0
        return Recipe(
            store=self.name,
            id=fields.get("slug") or hit.get("id", ""),
            name=fields.get("title", ""),
            url=f"{SITE}/opskrifter/{fields.get('slug', '')}",
            time=f"{minutes} min" if minutes else None,
            persons=(form.get("servings") or {}).get("amount"),
        )

    async def _recipe_hits(self, query: str, limit: int) -> list[dict]:
        data = await self._get(
            "/search/recipes", {"query": query, "per_page": max(1, min(limit, 100)), "page": 1}
        )
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise StoreError("REMA 1000-opskrifter har ændret format.")
        return rows

    async def recipes(self, query: str, limit: int = 5) -> list[Recipe]:
        return [self._recipe(h) for h in (await self._recipe_hits(query, limit))[:limit]]

    async def recipe(self, recipe_id: str, persons: int | None = None) -> RecipeDetails:
        """A recipe by its slug or link, with the product REMA picks per ingredient.
        Basic ingredients (salt, oil, flour) are have_at_home and left out of the total."""
        slug = recipe_id.rstrip("/").rsplit("/", 1)[-1]
        hits = await self._recipe_hits(slug.replace("-", " "), 20)
        hit = next(
            (h for h in hits if slug in ((h.get("fields") or {}).get("slug"), h.get("id"))), None
        )
        if not hit:
            raise StoreError(f"Fandt ikke opskriften '{recipe_id}' hos REMA 1000.")
        info = self._recipe(hit)
        form = (hit.get("fields") or {}).get("customForm") or {}
        servings = int((form.get("servings") or {}).get("amount") or 4)
        persons = persons or servings
        items, texts = [], []
        for group in form.get("ingredientGroups") or []:
            for ing in group.get("ingredients") or []:
                if ing.get("isExcludedFromShoppingList"):
                    continue
                amount = ing.get("amountOfContent") or 0
                unit = (ing.get("unitOfContent") or "").strip()
                scaled = Decimal(str(amount)) * persons / servings if amount else Decimal(0)
                shown = f"{scaled.normalize():f}".replace(".", ",") if amount else ""
                texts.append(" ".join(x for x in (shown, unit, ing.get("ingredient", "")) if x))
                if item := self._item(ing, scaled, unit):
                    items.append(item)
        return RecipeDetails(
            store=self.name,
            id=info.id,
            name=info.name,
            url=info.url,
            persons=persons,
            time=info.time,
            ingredients=texts,
            items=items,
            total=sum(
                (i.product.price * i.product.quantity for i in items if not i.have_at_home),
                Decimal(0),
            ),
        )

    def _item(self, ing: dict, amount: Decimal, unit: str) -> RecipeItem | None:
        digital = ing.get("digitalProduct") or {}
        if not digital:
            return None
        active = digital.get("active_product") or {}
        now = product(active, self.name) if active else None
        return RecipeItem(
            ingredient=(ing.get("ingredient") or "").strip(),
            product=RecipeProduct(
                id=now.id if now else str(digital.get("id", "")),
                name=now.name if now else digital.get("title", ""),
                price=now.price if now else (_money(digital.get("price")) or Decimal(0)),
                quantity=packs(
                    amount, unit, digital.get("amount_of_content"), digital.get("unit_of_content")
                ),
                size=now.size if now else None,
                available=now is not None,
                offer_text=now.offer_text if now else None,
            ),
            have_at_home=bool(digital.get("is_basic_ingredient")),
        )

    async def featured_recipes(self, limit: int = 20) -> list[Recipe]:
        raise NotImplementedError

    async def recipes_by_tag(
        self, tags: list[str], category: str | None = None, limit: int = 20
    ) -> list[Recipe]:
        raise NotImplementedError

    # --- no cart, no delivery: the human shops in the store -------------------

    async def terms(self) -> StoreTerms:
        return StoreTerms(store=self.name, home_delivery=False, list_only=True, note=IN_STORE)

    async def get_cart(self) -> Cart:
        raise StoreError(NO_CART)

    async def set_quantities(self, items: dict[str, int]) -> list[QuantityResult]:
        raise StoreError(NO_CART)

    async def delivery_info(self) -> DeliveryInfo:
        raise NotImplementedError

    async def delivery_slots(self, days: int = 7) -> list[DeliverySlot]:
        raise NotImplementedError

    async def orders(self, limit: int = 10) -> list[Order]:
        raise NotImplementedError

    async def order(self, order_id: str) -> Order:
        raise NotImplementedError
