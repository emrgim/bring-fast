"""Top-level grocery departments: Edible and Drinks."""

from __future__ import annotations

import re

DEPTS = ("Edible", "Drinks")

# Phrases, matched as whole tokens (not substrings). "tea" must not catch steak,
# "gin" must not catch virgin / original / ciliegine, "rum" must not catch crumbs.
_DRINK = (
    "cola",
    "coca",
    "coke",
    "sprite",
    "fanta",
    "pepsi",
    "7up",
    "water",
    "sparkling",
    "still water",
    "oasis",
    "perrier",
    "san pel",
    "pel.",
    "masafi",
    "aquafina",
    "acqua",
    "mai dubai",
    "juice",
    "drink",
    "beverage",
    "soda",
    "milk",
    "latte",
    "nescafe",
    "coffee",
    "tea",
    "zero sugar",
    "soft drink",
    "beer",
    "wine",
    "whisky",
    "whiskey",
    "vodka",
    "gin",
    "rum",
    "champagne",
    "prosecco",
    "cider",
    "heineken",
)

# Food that contains a drink token as a real word (cider vinegar, milk chocolate).
_EDIBLE_OVERRIDE = (
    "vinegar",
    "olive oil",
    "milk chocolate",
    "chocolate bar",
    "chocolate cake",
    "coffee cake",
    "tea biscuit",
    "tea cookie",
    "wine gum",
    "wine vinegar",
    "coconut milk",
    "baking soda",
    "water cracker",
    "rose water",
)

_NONEDIBLE = (
    "toothbrush",
    "t/b",
    "t/brush",
    "toothpaste",
    "t/p",
    "colgate tb",
    "shampoo",
    "shmp",
    "deodorant",
    "rexona",
    "dove ro",
    "comfort",
    "omo",
    "detergent",
    "stain rem",
    "bleach",
    "clrx",
    "film",
    "fujifilm",
    "mini film",
    "hmd",
    "pulse pro",
    "elfbar",
    "airfryer",
    "swim",
    "swim short",
    "odor",
    "scholl",
    "wax",
    "trisa",
    "prestiges",
    "streax",
    "shopping bag",
    "plast shopping",
    "frozen bag",
    "ppw bag",
    "wash-up",
    "cling",
    "just for men",
    "hair color",
    "rstr",
    "diamond cling",
)

_NOT_DRINK = ("toothpaste", "toothbrush", "colgate tb", "t/p", "t/b", "t/brush")


def _keyword_re(words: tuple[str, ...]) -> re.Pattern[str]:
    parts = []
    for raw in words:
        word = raw.strip()
        if not word:
            continue
        parts.append(re.escape(word).replace(r"\ ", r"[\s/-]+"))
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])", re.I)


_DRINK_RE = _keyword_re(_DRINK)
_EDIBLE_RE = _keyword_re(_EDIBLE_OVERRIDE)
_NONEDIBLE_RE = _keyword_re(_NONEDIBLE)
_NOT_DRINK_RE = _keyword_re(_NOT_DRINK)


def normalize_dept(dept: str) -> str:
    d = (dept or "").strip()
    if d in ("Edible", "Food"):
        return "Edible"
    if d == "Drinks":
        return "Drinks"
    return ""


def classify_dept(name: str, extra: str = "") -> str:
    blob = f"{name or ''} {extra or ''}"
    if _EDIBLE_RE.search(blob):
        return "Edible"
    if _DRINK_RE.search(blob):
        if _NOT_DRINK_RE.search(blob):
            return ""
        return "Drinks"
    if _NONEDIBLE_RE.search(blob):
        return ""
    return "Edible"


def matches_dept(dept: str, name: str, extra: str = "") -> bool:
    want = normalize_dept(dept)
    if not want:
        return True
    return classify_dept(name, extra) == want
