# 0001: The human always pays

handle-mcp reads stores and changes cart quantities. It never places an order, pays, reads payment cards, chooses a delivery time or changes the address. Those endpoints do not exist in the code, and `tests/test_safety.py` fails if one appears.

## Why

A cart change is reversible and costs nothing. An order spends money and commits the store to pack it. A bug, a misread API answer or a confused model must never be able to do that.

## Consequences

- The human approves and pays on the store's own website or app.
- Anything beyond the cart needs a new ADR that replaces this one.
- REMA 1000 and Aarstiderne have no cart at all: see [0003](0003-list-only-stores.md).
