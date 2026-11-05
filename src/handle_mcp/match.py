"""Matching a shopping-list item to a store product, by words only. No money here.

- "2 x rugbrød" means quantity 2. A size like "1,5 kg" is the amount wanted.
- A product matches well when every word of the item is a whole word in its name.
- A word only after "m.", "m", "med", "i", "u." ... is a flavour, not the product: "Yoghurt m. banan"
  is not a banana. Such products never win, unless the item asks for it: "makrel i tomat".
- A word only in the brand counts half: "Fior di Frutta" jam is not "fior di latte".
- "makrelfilet" and "makrelstykker" are makrel: a cut of the thing named first. Two cuts of one
  thing are the same ("laksestykke" for "laksefilet"), and so is a part ("kyllingeoverlår").
- Canned, preserved or frozen produce ("Hvidløg i krydderolie") loses when the search also found
  it fresh. Canned fish and frozen meat do not lose: "Makrel i tomat" is a tin.
- A blend ("m. 50% grønt") loses to the plain product and is never sure.
- Danish: c ~ k, singular ~ plural, compounds name the thing last ("frilandsæg" is æg).
"""

from __future__ import annotations

import re
from decimal import Decimal
from itertools import pairwise
from typing import Any

STOP_WORDS = {"og", "med", "i", "af", "en", "et", "til", "uden"}

UNITS = {
    "g": ("g", 1), "gr": ("g", 1), "kg": ("g", 1000),
    "ml": ("ml", 1), "cl": ("ml", 10), "dl": ("ml", 100),
    "l": ("ml", 1000), "ltr": ("ml", 1000), "liter": ("ml", 1000),
    "stk": ("stk", 1), "st": ("stk", 1),
    "ruller": ("rl", 1), "rulle": ("rl", 1), "rl": ("rl", 1),  # rolls, not 42 wet wipes
}  # fmt: skip

PIECES = ("stk", "rl")  # counted things: '2 x køkkenrulle' is 2 packs of rolls, like pieces

SIZE_RE = re.compile(
    r"(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*(" + "|".join(UNITS) + r")\b", re.IGNORECASE
)

# "m" without a dot too: nemlig writes "Mini frugtstænger m blåbær".
FLAVOUR_RE = re.compile(r"\s(?:m\.?|med|i|u\.|uden|smag)\s", re.IGNORECASE)

# Words that only say how a thing is made or packed: "frilandsæg" is æg, "splitlinser" is linser.
# Not "baby": "Babymajs" is another vegetable than "majs".
DESCRIBERS = ("frilands", "skrabe", "øko", "split", "mini", "snack")

# A part inside a compound: "kyllingeoverlår" and "kyllingeunderlår" are kyllingelår.
PARTS = ("over", "under")
LIMBS = ("lår",)  # only before these: never "kartoverofler"

# A cut of the thing named first: "makrelfilet" and "makrelstykker" are makrel.
CUTS = ("filet", "fileter", "stykke", "stykker", "strimler", "tern")

# Frozen or canned. Never the default for fresh food: "blåbær" is fresh, "frosne blåbær" is not.
# "Cherrytomater i tomatjuice", Bilka "Kolonial > Konserves". nemlig puts jars in plain "Kolonial":
# "Appetit agurker" is pickled, never a fresh agurk.
CANNED = ("konserv", "dåse", "juice", "kolonial")

PRESERVED = ("olie", "lage", "marinade")  # last part of a name word: "krydderolie", not "Lagerøl"

FROZEN = ("frost", "frosne", "frossen", "frosset")

FRESH = ("frugt", "grønt", "bær", "grøntsag")  # frozen loses only here: frozen meat is fine

BLEND_RE = re.compile(r"\d+(?:[.,]\d+)?\s*%\s*([a-zæøå]+)", re.IGNORECASE)  # "50% grønt"

NUTRIENTS = {"fedt", "protein", "sukker", "kakao", "alkohol", "salt", "frugt", "kulhydrat"}

QTY_RE = re.compile(r"^\s*(\d+)\s*[x×]\s+(?=\D)", re.IGNORECASE)

OEKO_RE = re.compile(r"\b(?:øko|økologiske?|organic)\b", re.IGNORECASE)  # not "Økonomi"


def get(product: Any, key: str) -> Any:
    """Adapters return Product; tolerate a dict too."""
    return product.get(key) if isinstance(product, dict) else getattr(product, key, None)


def parse_size(text: str | None) -> tuple[str, Decimal] | None:
    """'1 l' → ('ml', 1000). '12 x 0,25 l' → ('ml', 3000). None if no size."""
    match = SIZE_RE.search(text or "")
    if not match:
        return None
    count, amount, unit = match.groups()
    base, factor = UNITS[unit.lower()]
    return base, Decimal(amount.replace(",", ".")) * factor * int(count or 1)


def parse_item(item: str) -> tuple[int, list[str], tuple[str, Decimal] | None]:
    """'2 x letmælk 1 l' → (2, ['letmælk'], ('ml', 1000))."""
    quantity = 1
    if match := QTY_RE.match(item):
        quantity, item = int(match.group(1)), item[match.end() :]
    size = parse_size(item)
    words = [w for w in _words(SIZE_RE.sub(" ", item)) if w not in STOP_WORDS]
    return max(quantity, 1), words, size


# Shop abbreviations: REMA 1000 writes "HK. OKSEKØD" for hakket oksekød.
ABBREVIATIONS = {"hk": "hakket"}
ABBREVIATION_RE = re.compile(r"\b(" + "|".join(ABBREVIATIONS) + r")\b\.?")


def query(item: str) -> str:
    """What we send to the store's search: the words only. '2 x letmælk 1 l' → 'letmælk'."""
    return " ".join(parse_item(item)[1]) or item


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zæøå0-9]+", text.lower())


def asked(item: str) -> set[str]:
    """Words the item itself puts after 'i', 'med' ...: 'makrel i tomat' asks for tomat."""
    parts = FLAVOUR_RE.split(f" {SIZE_RE.sub(' ', item)} ", maxsplit=1)
    return set(_words(parts[1])) - STOP_WORDS if len(parts) == 2 else set()


def score(words: list[str], product: Any, asked: set[str] | frozenset[str] = frozenset()) -> float:
    """1.0 when every word is a whole word in the name, or its Danish plural or singular
    ('bananer' ~ 'banan'). Half for a word inside another word, or only in the brand: 'Fior di
    Frutta' is jam, not 'fior di latte'. A quarter for a word only in the flavour part of the
    name ('Yoghurt m. banan'), unless the item asks for it there (`asked`: 'makrel i tomat')."""
    # c and k are the same sound in Danish shop names: "Clementiner" ~ "klementiner".
    words = [w.replace("c", "k") for w in words]
    asked = {w.replace("c", "k") for w in asked}
    name = (get(product, "name") or "").replace("C", "K").replace("c", "k").lower()
    name = ABBREVIATION_RE.sub(lambda m: ABBREVIATIONS[m.group(1)], name)
    brand = (get(product, "brand") or "").replace("C", "K").replace("c", "k").lower()
    head = FLAVOUR_RE.split(name)[0]
    head_words = _words(head)
    # A compound split in two: "Cherry tomater" is "cherrytomater".
    tokens = set(head_words) | {a + b for a, b in pairwise(head_words)}
    brand_tokens = set(_words(brand))
    tail_tokens = set(_words(name))
    hits = []
    for w in words:
        if w in tokens or any(_plural(w, t) or _described(w, t) for t in tokens):
            hits.append(1.0)
        elif any(_compound(w, t) for t in tokens):
            hits.append(0.75)  # 'frilandsæg' for 'æg': better than a word inside, below whole
        elif w in head or _stem_hit(w, tokens) or w in brand_tokens or _stem_hit(w, brand_tokens):
            hits.append(0.5)
        elif w in tail_tokens or _stem_hit(w, tail_tokens):
            hits.append(1.0 if w in asked else 0.25)
        else:
            hits.append(0.25 if w in name else 0.0)
    return sum(hits) / len(hits) if hits else 0.0


def _plural(word: str, token: str) -> bool:
    """Danish singular and plural are the same product: 'pære' ~ 'pærer', 'banan' ~ 'bananer'."""
    short, long_ = sorted((word, token), key=len)
    return len(short) >= 3 and long_ in (short + "r", short + "er", short + "e")


def _described(word: str, token: str) -> bool:
    """'frilandsæg' is plain æg: the first part only says how it is made. 'makrelfilet' is
    makrel, 'laksestykke' is 'laksefilet' and 'kyllingeoverlår' is 'kyllingelår'."""
    # 'laksefilet' → 'lakse': then 'laksestykke' is the same fish, cut another way.
    bases = [word] + [word[: -len(c)] for c in CUTS if word.endswith(c) and len(word) > len(c) + 2]
    return (
        any(token == d + word for d in DESCRIBERS)
        or any(token in (b + c, b + "e" + c, b + "s" + c) for b in bases for c in CUTS)
        or any(word.endswith(b) and token == word[: -len(b)] + m + b for b in LIMBS for m in PARTS)
    )


def processed_rank(words: list[str], product: Any, fresh: bool = False) -> int:
    """1 for canned, preserved or frozen fruit and vegetables, when the item does not ask for it.
    `fresh` says the search also found the item fresh ('Grønt'), so the item is produce.
    Canned fish or frozen meat is not demoted: "Makrel i tomat" 125 g is a fine tin, and a
    frozen 35 kr offer is a fine 'kyllingelår'."""
    text = f"{get(product, 'name')} {get(product, 'category') or ''}".lower()
    item = " ".join(words)
    produce = fresh or is_fresh(product) or any(w.endswith("bær") for w in words)
    if not produce:
        return 0
    canned = any(m in text for m in CANNED) and not any(m in item for m in CANNED)
    name_words = _words(get(product, "name") or "")
    preserved = any(w.endswith(m) for w in name_words for m in PRESERVED)
    preserved = preserved and not any(m in item for m in PRESERVED)
    frozen = any(m in text for m in FROZEN) and not any(m in item for m in FROZEN)
    return 1 if canned or preserved or frozen else 0


def is_fresh(product: Any) -> bool:
    """Fruit or vegetables, not canned or frozen: nemlig 'Grønt', Bilka 'Frugt & grønt'."""
    text = f"{get(product, 'name')} {get(product, 'category') or ''}".lower()
    processed = any(m in text for m in (*CANNED, *FROZEN))
    return any(m in text for m in FRESH) and not processed


def processed(product: Any) -> str | None:
    """'frost' or 'konserves' when the name or category says so, else None. A fact for the
    caller, not a rank: plain 'Kolonial' (nemlig's dry goods too) is not konserves here."""
    text = f"{get(product, 'name')} {get(product, 'category') or ''}".lower()
    if any(m in text for m in FROZEN):
        return "frost"
    name_words = _words(get(product, "name") or "")
    preserved = any(w.endswith(m) for w in name_words for m in PRESERVED)
    canned = any(m in text for m in CANNED if m != "kolonial")
    return "konserves" if canned or preserved else None


def is_oeko(product: Any) -> bool:
    if get(product, "oeko"):
        return True
    return bool(OEKO_RE.search(f"{get(product, 'name')} {get(product, 'brand') or ''}"))


def _compound(word: str, token: str) -> bool:
    """Danish compound words name the thing last: 'frilandsæg' is æg, 'splitlinser' is linser.
    The token needs 3 more letters, so 'post' is not 'ost'."""
    return len(token) >= len(word) + 3 and token.endswith(word)


def mixed(words: list[str], product: Any) -> int:
    """1 for a blend the item does not ask for: 'Hakket oksekød m. 50% grønt'. A share of
    another food ('50% grønt') means a mix. A share of fat or sugar ('m. 0,5% fedt') does not,
    nor 'Rugbrød m. kerner' or 'Laks m. skind'. REMA 1000 writes a comma: 'HK. OKSEKØD, 35% GRØNT'."""
    name = get(product, "name") or ""
    parts = FLAVOUR_RE.split(name, maxsplit=1)
    if len(parts) != 2:
        parts = name.split(",", maxsplit=1)
    shares = BLEND_RE.findall(parts[1]) if len(parts) == 2 else []
    if not any(w.lower() not in NUTRIENTS for w in shares):
        return 0
    tail = set(_words(parts[1]))
    return 0 if any(w in tail or _stem_hit(w, tail) for w in words) else 1


def flavour_only(words: list[str], product: Any) -> bool:
    """True when the item's words are only in the flavour part: 'Grøn te m. mandarin'."""
    return score(words, product) > 0 and all(score([w], product) < 0.5 for w in words)


def brand_only(words: list[str], product: Any) -> bool:
    """True when only the brand has the item's words: 'Fior di Frutta' jam for 'fior di latte'."""
    bare = {"name": get(product, "name"), "brand": None}
    return score(words, bare) == 0 and score(words, product) > 0


def _stem_hit(word: str, tokens: set[str]) -> bool:
    return any(len(t) >= 4 and (word.startswith(t) or t.startswith(word)) for t in tokens)


def category_rank(words: list[str], product: Any) -> int:
    """0 when an item word fits the store's category ('cherrytomater' ~ 'Tomater'), else 1."""
    tokens = set(_words(get(product, "category") or ""))
    fits = _stem_hit if tokens else (lambda _w, _t: False)
    return 0 if any(fits(w, tokens) or any(_suffix(w, t) for t in tokens) for w in words) else 1


def _suffix(word: str, token: str) -> bool:
    """Danish compound words: 'cherrytomater' ends with 'tomater'. Short tokens like 'ost' are too
    loose ('post'), so both sides need 4+ letters."""
    return len(token) >= 4 and len(word) >= 4 and (word.endswith(token) or token.endswith(word))


def pack_size(product: Any) -> tuple[str, Decimal] | None:
    """The size field, else the name. Rolls in the name win over 'stk' in the size field:
    nemlig sells 'Toiletpapir 8 ruller' with size '8 stk'."""
    size, name = parse_size(get(product, "size")), parse_size(get(product, "name"))
    if name and name[0] == "rl" and (not size or size[0] == "stk"):
        return name
    return size or name


# Ready to eat: cold cuts, smoked, fried, breaded. Never raw meat unless the item asks for it.
PREPARED = ("røget", "røgede", "grillet", "grillede", "stegt", "stegte", "pålæg")
PREPARED += ("indbagt", "indbagte", "paneret", "panerede")  # ready meals: "Indbagt laksefilet"


def prepared(words: list[str], product: Any) -> bool:
    """True for 'Røget kyllingebryst' (Bilka 'Pålæg' category) when the item does not ask for
    it: 'kyllingebryst 450 g' is raw meat, 'røget laks' asks."""
    text = f"{get(product, 'name')} {get(product, 'category') or ''}".lower()
    item = " ".join(words)
    return _marked(text) and not _marked(item)


def _marked(text: str) -> bool:
    """A PREPARED word starts or ends a word: 'Røget', 'Kyllingepålæg'. Not deep inside one."""
    return any(re.search(rf"\b{m}|{m}\b", text) for m in PREPARED)
