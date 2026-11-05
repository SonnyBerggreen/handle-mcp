# Tools

| Tool | Writes? | Butikker | What |
|---|---|---|---|
| `search_products(query, stores?)` | no | nemlig, BilkaToGo, REMA 1000, Aarstiderne | Price, price per kg/l, offers, EAN when known. nemlig and BilkaToGo need a login; REMA 1000 and Aarstiderne need none (Aarstiderne: the member price, `HANDLE_AARSTIDERNE_MEMBER=0` for the normal price) |
| `search_recipes(query, stores?)` | no | nemlig, REMA 1000 | The store's own recipes: name and link. `get_recipe` gives time, persons and products |
| `list_recipes(stores?, tags?, category?, limit?)` | no | nemlig | This week's featured recipes (nemlig "Ugens udvalgte"), or with `tags` all recipes that have every tag, e.g. `["Familievenlig", "Budgetvenlig"]` or `["Under 30 min", "Kylling"]`. `category`: aftensmad (default), frokost, madpakke, ... An unknown tag answers with nemlig's list |
| `get_recipe(store, recipe, persons?)` | no | nemlig, REMA 1000 | The products behind the store's "Se varer og læg i kurv": per ingredient the product, packs for `persons`, price and alternatives. `have_at_home` items (salt, oil) are left out of `total`. Fill the cart with `add_recipe_to_cart` after a yes |
| `list_offers(stores?, limit?)` | no | nemlig, BilkaToGo, REMA 1000 | This week's offers, also items you would not search for: nemlig Skarp pris, Priskup, Dagstilbud, Undgå madspild, gifts; BilkaToGo offers. Free gifts have price 0 and a max per order. nemlig needs no login (with one, the offers fit your delivery time); BilkaToGo does, because the store follows your address |
| `compare_basket(items, stores?)` | no | nemlig, BilkaToGo, REMA 1000, Aarstiderne | Prices a shopping list in each store: up to 5 candidates per item with facts and flags, fees, minimum order, unsure and missing items marked, cheapest store. REMA 1000 and Aarstiderne are `list_only`: no cart here, the human shops from a list |
| `get_cart(store?)` | no | nemlig, BilkaToGo | Cart lines, fees, total, minimum order. Without `store`: every store's cart, keyed by store name |
| `set_cart_quantities(store, items)` | **yes** | nemlig, BilkaToGo | Absolute quantities (0 removes). Only the cart |
| `add_recipe_to_cart(store, recipe, persons?, skip?, swap?, include?)` | **yes** | nemlig | A recipe's products from `get_recipe` into the cart, added to what is there (twice adds twice). Ask first. `have_at_home` items only with `include`; `skip` and `swap` (to an alternative's id) by ingredient or product id. Answers before/added/after per product and what was skipped (sold out, have at home, left out) |
| `get_delivery_info(store)` | no | nemlig, BilkaToGo | Chosen delivery time, next free day, fees |
| `list_delivery_slots(stores?, days?)` | no | nemlig, BilkaToGo | Free delivery times with the fee per slot, the 3 cheapest first |
| `get_orders(store)` / `get_order(store, id)` | no | nemlig, BilkaToGo | Past orders, for "what we usually buy" |

Money is a string in kroner with two decimals (`"12.95"`, `"39.00"`) in every store. Dates are ISO. A fact the store does not give is `null`, never left out.

Every product from `search_products` and `list_offers` has the same fields. Two of them are often `null`:

| Field | nemlig | BilkaToGo |
|---|---|---|
| `before_price` (the price without the offer) | when nemlig shows a crossed-out price; a "Skarp pris" without one has `null` | when the offer has a normal price |
| `offer_until` (last day of the offer, ISO date) | from `search_products`; nemlig's offer lists (`list_offers`) do not say, so `null` | always `null`: BilkaToGo does not say |

`compare_basket` candidates list only the facts that are known, to stay short.

What it can **never** do: place an order, pay, read cards, choose a delivery time or change the address. That code does not exist, and a test fails if it shows up. See [ADR 0001](adr/0001-the-human-always-pays.md).


Fields in each `compare_basket` candidate: [skills.md](skills.md).
