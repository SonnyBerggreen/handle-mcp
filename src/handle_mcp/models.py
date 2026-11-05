"""The few shapes every store returns. Money is Decimal DKK, never float or øre."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass
class Product:
    store: str
    id: str  # the store's own product id, used in the cart
    name: str
    price: Decimal
    brand: str | None = None
    size: str | None = None  # as the store writes it, e.g. "1 l" or "400 g"
    unit_price: Decimal | None = None  # price per unit below
    unit: str | None = None  # "kg", "l" or "stk"
    ean: str | None = None
    on_offer: bool = False
    offer_text: str | None = None
    before_price: Decimal | None = None  # the price without the offer, when the store says it
    offer_until: date | None = None  # last day of the offer, when the store says it
    available: bool = True
    category: str | None = None  # the store's category path, e.g. "Frugt & grønt > Bananer"
    campaign: str | None = None  # the store's offer list, e.g. "Priskup", "Skarp pris", "Gratis"
    max_per_order: int | None = None  # offer limit, e.g. 1 for a free gift
    oeko: bool = False  # organic by the store's own label, also when the name does not say it


@dataclass
class Recipe:
    store: str
    id: str
    name: str
    url: str  # the store's recipe page: ingredients and how to cook it
    time: str | None = None  # as the store writes it, e.g. "30 min"
    persons: int | None = None
    group: str | None = None  # the store's theme, e.g. "Mad i fad" in nemlig's "Ugens udvalgte"


@dataclass
class RecipeProduct:
    id: str  # the store's product id, for set_cart_quantities
    name: str
    price: Decimal  # per pack
    quantity: int  # packs to buy for the persons asked for
    size: str | None = None
    available: bool = True
    offer_text: str | None = None


@dataclass
class RecipeItem:
    """One ingredient and the product the store picks for it."""

    ingredient: str
    product: RecipeProduct
    have_at_home: bool = False  # the store assumes you have it (salt, oil): left out of the total
    alternatives: list[RecipeProduct] = field(default_factory=list)


@dataclass
class RecipeDetails:
    store: str
    id: str
    name: str
    url: str
    persons: int
    min_persons: int | None = None
    max_persons: int | None = None
    time: str | None = None
    ingredients: list[str] = field(default_factory=list)  # the recipe's text, e.g. "2 stk. rødløg"
    items: list[RecipeItem] = field(default_factory=list)
    total: Decimal = Decimal(0)  # the items to buy, without have_at_home items


@dataclass
class CartLine:
    product_id: str
    name: str
    quantity: int
    line_total: Decimal


@dataclass
class Cart:
    store: str
    lines: list[CartLine]
    products_total: Decimal
    fees: dict[str, Decimal]  # e.g. {"levering": 39, "pakning": 39}
    total: Decimal
    minimum_order: Decimal | None = None
    minimum_met: bool | None = None
    delivery: str | None = None  # chosen delivery time as text, if any
    warnings: list[str] = field(default_factory=list)


@dataclass
class QuantityResult:
    product_id: str
    requested: int
    applied: int | None  # what the store actually set; None if unknown
    ok: bool
    message: str | None = None


@dataclass
class StoreTerms:
    store: str
    home_delivery: bool
    delivery_fee: Decimal | None = None
    other_fees: dict[str, Decimal] = field(default_factory=dict)
    minimum_order: Decimal | None = None
    source: str = "api"  # "api" or "config"
    list_only: bool = False  # no cart in handle-mcp: the human shops from a list
    note: str | None = None  # the store's own terms in Danish, e.g. what the in-store price means


@dataclass
class DeliveryInfo:
    store: str
    chosen_start: datetime | None = None
    chosen_end: datetime | None = None
    edit_deadline: datetime | None = None
    next_available: date | None = None
    note: str | None = None


@dataclass
class DeliverySlot:
    """A delivery time the store offers. Read only: handle-mcp never chooses one."""

    store: str
    start: datetime
    end: datetime
    price: Decimal | None  # delivery fee for this slot, None when the store does not say
    available: bool = True
    kind: str | None = None  # the store's type of delivery, e.g. "Fleksibel" or "Personlig"
    note: str | None = None


@dataclass
class Order:
    store: str
    id: str
    status: str
    total: Decimal
    placed_at: datetime | None = None
    delivery_start: datetime | None = None
    delivery_end: datetime | None = None
    lines: list[CartLine] = field(default_factory=list)  # empty in order lists
