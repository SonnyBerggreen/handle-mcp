# 0003: List-only stores

Builds on [0001](0001-the-human-always-pays.md).

Two stores have prices in handle-mcp but no cart. `StoreTerms.list_only` marks them, and when one is cheapest `compare_basket` says: write the human a shopping list.

## REMA 1000

No home delivery. Its online shopping list and Køb & Hent are left out on purpose: the human takes a written list to the store. The store reads prices, offers and recipes from the open API, with no login.

## Aarstiderne

The basket on aarstiderne.com belongs to one browser session, not to the account (live test 2026-11-04): a basket filled by handle-mcp is invisible in the human's own browser, and so is a delivery day the human chose there. The only way around that is to move session cookies between handle-mcp and the browser, which the human said no to. So handle-mcp reads the public search page only, with no login: it shows both the normal and the member price. The human puts the list in the basket on aarstiderne.com.

The basket also carries a week frequency per line, so a wrong call can start a subscription. One more reason the basket stays out.

## Consequences

- No login is stored for either store. `handle-mcp login rema` and `login aarstiderne` say so.
- `tests/test_safety.py` forbids Aarstiderne's basket, login, delivery day and meal box calls, and REMA 1000's shopping-list calls.
- A cart for either store needs a new ADR that replaces this one.
