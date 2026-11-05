"""Reading aarstiderne.com's HTML. The site renders search results and the basket on the
server, so there is no JSON to read. Standard library only: html.parser builds a small tree.

Search rows: `.clerk-search-results .product-row__inner` (live 2026-11-04).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
NUMBER_RE = re.compile(r"-?\d+(?:\.\d{3})*(?:,\d+)?")


class Node:
    def __init__(self, tag: str, attrs: dict[str, str], parent: Node | None = None) -> None:
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[Node | str] = []

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def walk(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()

    def find_all(self, cls: str | None = None, **attrs: str) -> list[Node]:
        return [
            n
            for n in self.walk()
            if (cls is None or cls in n.classes)
            and all(n.attrs.get(k.replace("_", "-")) == v for k, v in attrs.items())
        ]

    def find(self, cls: str | None = None, **attrs: str) -> Node | None:
        return next(iter(self.find_all(cls, **attrs)), None)

    def text(self) -> str:
        parts = [c if isinstance(c, str) else c.text() for c in self.children]
        return " ".join(" ".join(parts).split())


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: v or "" for k, v in attrs}, self.current)
        self.current.children.append(node)
        if tag not in VOID:  # an svg icon keeps its class ("oko-mark"); its paths have no text
            self.current = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.current.children.append(Node(tag, {k: v or "" for k, v in attrs}, self.current))

    def handle_endtag(self, tag: str) -> None:
        node = self.current
        while node is not self.root and node.tag != tag:
            node = node.parent  # tolerate unclosed tags
        if node is not self.root:
            self.current = node.parent or self.root

    def handle_data(self, data: str) -> None:
        if data.strip() and self.current.tag not in ("script", "style", "svg", "path"):
            self.current.children.append(data)


def tree(html: str) -> Node:
    builder = _Builder()
    builder.feed(html)
    return builder.root


def kroner(text: str | None) -> Decimal | None:
    """'14,40 kr.' → 14.40, '1.234 kr.' → 1234. None without a number."""
    match = NUMBER_RE.search(text or "")
    if not match:
        return None
    try:
        return Decimal(match.group().replace(".", "").replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _price(node: Node | None) -> Decimal | None:
    if node is None:
        return None
    inner = node.find("price")  # <span class="price">14,40</span>
    return kroner(inner.text() if inner else node.text())


def search_rows(html: str) -> list[dict[str, Any]]:
    """Products in the search results. Prices: `PriceDetails.Price` is the normal price,
    `PriceDetails.DiscountPriceAsFormatted` the member price, logged in or not."""
    results = tree(html).find("clerk-search-results")
    if results is None:
        return []
    rows = []
    for row in results.find_all("product-row__inner"):
        quantity = row.find("js-product-quantity")
        title = row.find("product-row__title")
        link = title.find(None) if title else None
        subtitles = [s.text() for s in row.find_all("product-row__subtitle")]
        unit_node = row.find("basket__price-pr-unit")
        unit_text = unit_node.text() if unit_node else ""
        rows.append(
            {
                "id": quantity.attrs.get("data-productid") if quantity else None,
                "url": link.attrs.get("href") if link else None,
                "name": title.text() if title else "",
                "description": subtitles[0] if len(subtitles) > 1 else None,
                "size": subtitles[-1] if subtitles else None,
                "oeko": any("oko-mark" in n.classes for n in row.walk()),
                "normal": _price(row.find(data_prop="PriceDetails.Price")),
                "member": _price(row.find(data_prop="PriceDetails.DiscountPriceAsFormatted")),
                "unit_price": _price(unit_node),
                "unit": unit_text.rsplit("/", 1)[-1].strip(" .").lower()
                if "/" in unit_text
                else None,
                "sold_out": "Udsolgt" in row.text(),
            }
        )
    return [r for r in rows if r["id"]]
