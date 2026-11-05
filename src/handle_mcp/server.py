"""The MCP server: every tool, store selection and error handling.

stdout is the JSON-RPC stream. All logging goes to stderr.
Every answer goes through privacy.scrub() in `_out()`.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from handle_mcp import compare, recipe_cart
from handle_mcp import stores as registry
from handle_mcp.privacy import jsonable, scrub

log = logging.getLogger("handle_mcp")

MAX_ITEMS = 50  # per set_cart_quantities or compare_basket call
MAX_QUANTITY = 50  # per product
MAX_RESULTS = 25
MAX_DAYS = 14
MIN_PERSONS, MAX_PERSONS = 2, 12  # nemlig: 2-12

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
)
ADD = ToolAnnotations(  # add_recipe_to_cart: twice adds twice
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)

mcp = MCPServer(
    "handle",
    instructions=(
        "Tools for Danish grocery stores with home delivery. You can search, compare "
        "baskets, find recipes, list delivery times and change cart quantities. You can never order or pay: the human "
        "always does that on the store's website."
    ),
)

_instances: dict[str, registry.Store] = {}


def _store(name: str) -> registry.Store:
    if name not in registry.STORES:
        known = ", ".join(sorted(registry.STORES)) or "ingen endnu"
        raise ToolError(f"Ukendt butik '{name}'. Kendte butikker: {known}.")
    if name not in _instances:
        _instances[name] = registry.STORES[name]()
    return _instances[name]


def _names(requested: list[str] | None) -> list[str]:
    names = requested or sorted(registry.STORES)
    if not names:
        raise ToolError("Ingen butikker er sat op endnu.")
    for name in names:
        _store(name)  # fail early on an unknown name
    return names


def _persons(persons: int | None) -> int | None:
    return None if persons is None else max(MIN_PERSONS, min(persons, MAX_PERSONS))


def _out(value: Any) -> dict[str, Any]:
    result = scrub(jsonable(value))
    return result if isinstance(result, dict) else {"result": result}


def _error_text(store: str, exc: BaseException) -> str:
    if isinstance(exc, NotImplementedError):
        return f"{store} kan ikke det endnu."
    if isinstance(exc, registry.StoreError):
        return f"{store}: {exc}"
    log.exception("%s failed", store, exc_info=exc)
    return f"{store}: uventet fejl ({type(exc).__name__}). Se serverens log."


async def _call(store: str, method: str, *args: Any) -> Any:
    try:
        return await getattr(_store(store), method)(*args)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(_error_text(store, exc)) from exc


async def _each(names: list[str], work: Callable[[Any], Awaitable[Any]]) -> dict[str, Any]:
    """Run `work(store)` for every store at once. One store failing never stops the others."""
    results = await asyncio.gather(*(work(_store(n)) for n in names), return_exceptions=True)
    return {
        name: {"error": _error_text(name, r)} if isinstance(r, BaseException) else r
        for name, r in zip(names, results, strict=True)
    }


@mcp.tool(annotations=READ)
async def search_products(
    query: str,
    stores: list[str] | None = None,
    limit: int = 10,
    sort: Literal["relevance", "price", "unit_price"] = "relevance",
) -> dict:
    """Search products in one or more stores. Returns price, price per kg/l, offers and EAN when
    known. Stores rank by relevance and put cheap products far down: when the human asks for the
    cheapest, use sort="unit_price" (per kg/l/stk) or "price"."""
    limit = max(1, min(limit, MAX_RESULTS))
    return _out(await _each(_names(stores), lambda s: compare.search(s, query, limit, sort)))


@mcp.tool(annotations=READ)
async def search_recipes(query: str, stores: list[str] | None = None, limit: int = 5) -> dict:
    """Find the stores' own recipes, e.g. "kylling karry". Returns name and link.
    Use get_recipe for time, persons and the products to buy.
    nemlig and REMA 1000 (rema); the other stores answer that they cannot yet."""
    limit = max(1, min(limit, MAX_RESULTS))
    return _out(await _each(_names(stores), lambda s: s.recipes(query, limit)))


@mcp.tool(annotations=READ)
async def get_recipe(store: str, recipe: str, persons: int | None = None) -> dict:
    """The products to buy for one of the store's recipes, as on its "Se varer og læg i kurv":
    per ingredient the store's product, packs for `persons` and alternatives. `have_at_home`
    items (salt, oil) are what the store assumes you have: left out of `total`, ask first.
    `recipe` is the recipe's link or id from search_recipes or list_recipes. Put the products
    in the cart with add_recipe_to_cart after the human says yes (nemlig only).
    nemlig and REMA 1000 (rema); the other stores answer that they cannot yet."""
    return _out(await _call(store, "recipe", recipe, _persons(persons)))


@mcp.tool(annotations=READ)
async def list_recipes(
    stores: list[str] | None = None,
    tags: list[str] | None = None,
    category: str | None = None,
    limit: int = 20,
) -> dict:
    """Browse the store's recipes, with link. No tags or category: this week's featured
    recipes (nemlig: "Ugens udvalgte"). With `tags`: recipes that have every tag, e.g.
    ["Familievenlig", "Budgetvenlig"] or ["Under 30 min", "Kylling"]. nemlig's tags include
    Under 20/30 min, Budgetvenlig, Familievenlig, Populær, Mad til flere dage, Vegetarisk,
    Kylling, Fisk, Oksekød, Gris and cuisines; an unknown tag answers with the full list.
    `category` (nemlig: aftensmad by default, frokost, madpakke, morgenmad, ...).
    Use get_recipe for the products.
    nemlig only for now; BilkaToGo answers that it cannot yet."""
    limit = max(1, min(limit, MAX_RESULTS))
    if not tags and not category:
        return _out(await _each(_names(stores), lambda s: s.featured_recipes(limit)))
    return _out(
        await _each(_names(stores), lambda s: s.recipes_by_tag(tags or [], category, limit))
    )


@mcp.tool(annotations=READ)
async def list_offers(stores: list[str] | None = None, limit: int = 25) -> dict:
    """List this week's offers in each store, also items you would not search for: nemlig's
    Skarp pris, Priskup, Dagstilbud, Undgå madspild and gifts; BilkaToGo's and REMA 1000's
    offers (REMA's "Avisvare"); Aarstiderne has no offers list. `limit` is
    per list; a product on two lists is shown once. A list the store fails to send is left out
    (see the server log). Free gifts come from a separate search and are not cut by `limit`.
    They have price 0 and "max_per_order". Use them when they fit the plan."""
    limit = max(1, min(limit, MAX_RESULTS))
    return _out(await _each(_names(stores), lambda s: s.offers(limit)))


@mcp.tool(annotations=READ)
async def compare_basket(items: list[str], stores: list[str] | None = None) -> dict:
    """Price a shopping list in each store, e.g. ["2 x letmælk 1 l", "rugbrød", "æg 10 stk"].
    Write the amount you need ("fior di latte 300 g"), not a pack size. Returns per item a
    suggested product and up to 5 "candidates" (the suggestion first, one øko when found) with
    the cost for the amount and flags: name_match, size_fits, prepared (cold cuts, smoked,
    breaded), processed (frost/konserves), blend, oeko. The suggestion is a guess by word rules:
    pick the candidate that fits the user, and ask when "sure" is false or none fits
    (search_products finds more). Unknown facts and false flags are left out. Also total incl.
    fees and minimum order met. A store with "list_only": true has no cart here: when it is
    cheapest, write the human a shopping list (REMA 1000: to take to the store; Aarstiderne:
    to put in the basket on aarstiderne.com). Aarstiderne prices are the member price, and
    everything there is øko: compare øko with øko.
    Only reads: use set_cart_quantities with the chosen ids after."""
    items = [i.strip() for i in items if i.strip()]
    if not items:
        raise ToolError("Indkøbslisten er tom.")
    if len(items) > MAX_ITEMS:
        raise ToolError(f"Højst {MAX_ITEMS} varer pr. liste.")
    quotes = await _each(_names(stores), lambda s: compare.quote(s, items))
    return _out({"stores": quotes, **compare.verdict(quotes)})


@mcp.tool(annotations=READ)
async def get_cart(store: str | None = None) -> dict:
    """Show the cart: lines, fees, total and whether the minimum order is met. With `store`
    that store's cart; without, every store's cart keyed by store name, a failing store
    (e.g. no login) as {"error": ...} next to the others."""
    if store is not None:
        return _out(await _call(store, "get_cart"))
    return _out(await _each(_names(None), lambda s: s.get_cart()))


@mcp.tool(annotations=WRITE)
async def set_cart_quantities(store: str, items: dict[str, int]) -> dict:
    """Set ABSOLUTE quantities in one store's cart (0 removes the product). Never orders or pays.
    nemlig and BilkaToGo only: REMA 1000 and Aarstiderne have no cart here. Returns a result per product and
    the new cart."""
    if not items:
        raise ToolError("Ingen varer angivet.")
    if len(items) > MAX_ITEMS:
        raise ToolError(f"Højst {MAX_ITEMS} varer pr. kald.")
    bad = [pid for pid, qty in items.items() if not 0 <= qty <= MAX_QUANTITY]
    if bad:
        raise ToolError(f"Antal skal være 0-{MAX_QUANTITY}. Forkert for: {', '.join(bad)}.")
    results = await _call(store, "set_quantities", dict(items))
    return _out({"results": results, "cart": await _call(store, "get_cart")})


@mcp.tool(annotations=ADD)
async def add_recipe_to_cart(
    store: str,
    recipe: str,
    persons: int | None = None,
    skip: list[str] | None = None,
    swap: dict[str, str] | None = None,
    include: list[str] | None = None,
) -> dict:
    """Put a recipe's products in one store's cart, as get_recipe shows them.
    ASK THE HUMAN FIRST: show the list from get_recipe and get a yes. Never orders or pays.
    Adds to what is already in the cart, so calling it twice adds twice.
    `have_at_home` items (salt, oil) are left out unless named in `include`. `skip` leaves out
    ingredients; `swap` maps an ingredient (or product id) to one of its alternatives' product
    ids. Names: ingredient or product id. Returns before/added/after per product, the skipped
    items and the new cart. Check `ok` per product: a store can refuse some lines; tell the
    human which ones failed.
    nemlig only for now; BilkaToGo answers that it cannot yet."""
    details = await _call(store, "recipe", recipe, _persons(persons))
    try:
        lines, skipped = recipe_cart.plan(details, skip, swap, include)
    except recipe_cart.PlanError as exc:
        raise ToolError(str(exc)) from exc
    if not lines:
        raise ToolError("Ingen varer at lægge i kurven.")
    if len(lines) > MAX_ITEMS:
        raise ToolError(f"Højst {MAX_ITEMS} varer pr. kald.")
    before = recipe_cart.in_cart(await _call(store, "get_cart"))
    targets = {pid: before.get(pid, 0) + ln["quantity"] for pid, ln in lines.items()}
    if bad := [pid for pid, qty in targets.items() if qty > MAX_QUANTITY]:
        raise ToolError(f"Højst {MAX_QUANTITY} stk. pr. vare. For mange af: {', '.join(bad)}.")
    results = await _call(store, "set_quantities", targets)
    added = recipe_cart.report(lines, before, results)
    return _out({"added": added, "skipped": skipped, "cart": await _call(store, "get_cart")})


@mcp.tool(annotations=READ)
async def get_delivery_info(store: str) -> dict:
    """Show the chosen delivery time, edit deadline and next free day, plus fees and minimum order."""
    info, terms = await asyncio.gather(_call(store, "delivery_info"), _call(store, "terms"))
    return _out({"delivery": info, "terms": terms})


@mcp.tool(annotations=READ)
async def list_delivery_slots(stores: list[str] | None = None, days: int = 7) -> dict:
    """List free delivery times for the next `days` days with the delivery fee per slot, in time
    order, plus the 3 cheapest per store. nemlig: "Personlig" (you are home) or "Fleksibel"
    (left at the door). BilkaToGo: one flat fee, night delivery. Only reads: it never chooses
    a time. The human chooses the time on the store's website."""
    days = max(1, min(days, MAX_DAYS))
    return _out(await _each(_names(stores), lambda s: _slots(s, days)))


async def _slots(store: registry.Store, days: int) -> dict:
    slots = sorted(
        (s for s in await store.delivery_slots(days) if s.available), key=lambda s: s.start
    )
    cheapest = sorted(slots, key=lambda s: (s.price is None, s.price or 0, s.start))[:3]
    return {"cheapest": cheapest, "slots": slots}


@mcp.tool(annotations=READ)
async def get_orders(store: str, limit: int = 10) -> dict:
    """List recent orders in one store (newest first). Use get_order for the lines."""
    limit = max(1, min(limit, MAX_RESULTS))
    return _out({"orders": await _call(store, "orders", limit)})


@mcp.tool(annotations=READ)
async def get_order(store: str, order_id: str) -> dict:
    """Show one order with what was bought."""
    return _out(await _call(store, "order", order_id))


def configure_logging() -> None:
    """Log to stderr. httpx logs every request URL at INFO, and URLs can hold ids and
    tokens. MCP clients keep stderr in log files, so httpx only speaks on warnings."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    if len(sys.argv) > 1:  # `handle-mcp login <store>` from a terminal, not from an MCP client
        from handle_mcp.cli import run

        sys.exit(run(sys.argv[1:]))
    mcp.run("stdio")


if __name__ == "__main__":
    main()
