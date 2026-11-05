"""Read-only smoke tests against the real stores.

Run: HANDLE_LIVE=1 uv run pytest -m live -s
Needs logins in env vars or ~/.config/handle-mcp/<store>.json.
These tests NEVER change the cart.
"""

import os

import pytest

from handle_mcp import compare, recipe_cart
from handle_mcp.stores import STORES

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("HANDLE_LIVE") != "1", reason="set HANDLE_LIVE=1"),
]


@pytest.fixture(params=sorted(STORES))
def store(request):
    return STORES[request.param]()


async def test_search(store):
    products = await store.search("minimælk", limit=3)
    assert products and all(p.price > 0 for p in products)


async def test_cheapest_first(store):
    """Stores rank by relevance; sorted search must still find the cheap packs far down."""
    hits = await compare.search(store, "kyllingebryst", 5, "unit_price")
    for p in hits:
        print(f"\n[{store.name}] {p.unit_price} kr/{p.unit} | {p.name} {p.size} | {p.price} kr")
    prices = [p.unit_price for p in hits if p.unit_price is not None]
    assert hits and prices == sorted(prices)


async def test_cart_terms_delivery(store):
    terms = await store.terms()
    if terms.list_only:
        pytest.skip(f"{store.name} has no cart: the human shops from a list")
    cart = await store.get_cart()
    assert cart.total >= 0
    info = await store.delivery_info()
    # Printed so a human can check the facts (no personal data in these fields).
    print(f"\n[{store.name}] hjemlevering={terms.home_delivery} gebyrer={cart.fees}")
    print(
        f"[{store.name}] minimum={cart.minimum_order} nået={cart.minimum_met} advarsler={cart.warnings}"
    )
    print(
        f"[{store.name}] valgt tid={info.chosen_start}-{info.chosen_end} næste ledige={info.next_available}"
    )


async def test_delivery_slots(store):
    """GET only: lists slots, never chooses one. Printed for a human check of the prices."""
    try:
        slots = [s for s in await store.delivery_slots(days=3) if s.available]
    except NotImplementedError:
        pytest.skip(f"{store.name} lists no delivery slots")
    assert slots and all(s.end > s.start for s in slots)
    for s in sorted(slots, key=lambda s: s.start)[:12]:
        print(f"\n[{store.name}] {s.start:%a %d/%m %H}-{s.end:%H} {s.kind or ''} {s.price} kr")


async def test_offers(store):
    """Read only. A list can be empty on a given day (offers() leaves it out), so nemlig only
    needs one of its big weekly lists."""
    try:
        offers = await store.offers(10)
    except NotImplementedError:
        pytest.skip(f"{store.name} has no offers list")
    lists = {p.campaign for p in offers}
    print(f"\n[{store.name}] {len(offers)} tilbud, lister: {sorted(map(str, lists))}")
    assert offers and all(p.on_offer and p.id and p.name for p in offers)
    assert all(0 < p.price < 2000 for p in offers if p.campaign != "Gratis")
    assert all(p.before_price is None or p.before_price > p.price for p in offers)
    if store.name == "nemlig":
        assert {"Skarp pris", "Priskup"} & lists


async def test_nemlig_add_recipe_dry_run(monkeypatch):
    """add_recipe_to_cart's plan on live data: recipe and cart are read, nothing is written."""
    nemlig = STORES["nemlig"]()

    async def no_writes(*_args, **_kwargs):
        raise AssertionError("the dry run must never write to the cart")

    monkeypatch.setattr(nemlig, "set_quantities", no_writes)
    (first, *_) = await nemlig.featured_recipes(limit=1)
    recipe = await nemlig.recipe(first.url, persons=4)
    lines, skipped = recipe_cart.plan(recipe)
    before = recipe_cart.in_cart(await nemlig.get_cart())
    targets = {pid: before.get(pid, 0) + ln["quantity"] for pid, ln in lines.items()}
    print(f"\n[nemlig] {recipe.name}: {len(lines)} varer, {len(skipped)} sprunget over")
    assert lines and all(1 <= qty <= 50 for qty in targets.values())
    assert all(s["reason"] for s in skipped)


async def test_nemlig_recipes():
    recipes = await STORES["nemlig"]().recipes("kylling karry", limit=3)
    for r in recipes:
        print(f"\n[nemlig] {r.name} | {r.url}")
    assert recipes and all(r.url.startswith("https://www.nemlig.com/") for r in recipes)


async def test_nemlig_featured_recipe_has_products():
    nemlig = STORES["nemlig"]()
    (first, *_) = await nemlig.featured_recipes(limit=1)
    recipe = await nemlig.recipe(first.url, persons=6)
    print(f"\n[nemlig] {first.group}: {recipe.name} | {recipe.persons} pers. | {recipe.total} kr")
    for item in recipe.items:
        p = item.product
        print(
            f"  {'(har du)' if item.have_at_home else ''} {item.ingredient}: {p.name} x{p.quantity}"
        )
    assert recipe.persons == 6 and recipe.total > 0
    assert any(not i.have_at_home for i in recipe.items)


async def test_nemlig_recipes_by_tag():
    recipes = await STORES["nemlig"]().recipes_by_tag(["Familievenlig", "Under 30 min"], limit=5)
    for r in recipes:
        print(f"\n[nemlig] {r.group}: {r.name} | {r.url}")
    assert recipes and all(r.url.startswith("https://www.nemlig.com/maaltider/") for r in recipes)


async def test_orders(store):
    try:
        orders = await store.orders(limit=2)
    except NotImplementedError:
        pytest.skip(f"{store.name} has no order history")
    if orders:
        await store.order(orders[0].id)


async def test_compare_week_list(monkeypatch):
    """Prices the 15-item week list in every store. Prints a table for a human check."""
    from handle_mcp import server
    from handle_mcp import stores as registry
    from tests.test_compare import WEEK_LIST

    monkeypatch.setattr(registry, "STORES", STORES)  # conftest swaps in fakes
    monkeypatch.setattr(server, "_instances", {})
    result = await server.compare_basket(WEEK_LIST)
    for name, quote in result["stores"].items():
        print(f"\n== {name} ==")
        if "error" in quote:
            print(quote["error"])
            continue
        for ln in quote["lines"]:
            prod = ln["product"]
            mark = "  " if ln["sure"] else "? "
            print(
                f"{mark}{ln['item']:<22} → {prod['name'][:38]:<38} {prod['size'] or '':<10}"
                f" {ln['line_total']:>7} kr. {ln['note'] or ''}"
            )
        for item in quote["missing"]:
            print(f"✗ {item:<22} → ikke fundet")
        print(
            f"varer={quote['products_total']} gebyrer={quote['fees']} total={quote['total']}"
            f" minimum nået={quote['minimum_met']} noter={quote['notes']}"
        )
    print(f"\n{result['summary']}")
    assert result["cheapest"]
