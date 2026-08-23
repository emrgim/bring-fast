"""Top-level grocery departments: Edible and Drinks."""

from __future__ import annotations

DEPTS = ("Edible", "Drinks")

_DRINK = (
    " cola", "coca", "coke", "sprite", "fanta", "pepsi", "7up",
    "water", "sparkling", "still water", "oasis", "perrier", "san pel",
    "pel.", "masafi", "aquafina", "mai dubai", "juice", "drink",
    "beverage", "soda", "milk", "latte", "nescafe", "coffee", "tea",
    "zero sugar", "soft drink",
)

_NONEDIBLE = (
    "toothbrush", "t/b", "t/brush", "toothpaste", "t/p ", "colgate tb",
    "shampoo", "shmp", "deodorant", "rexona", "dove ro", "comfort", "omo ",
    "detergent", "stain rem", "bleach", "clrx", "film", "fujifilm", "mini film",
    "hmd ", "pulse pro", "elfbar", "airfryer", "swim", "swim short",
    "odor", "scholl", "wax", "trisa", "prestiges", "streax",
    "shopping bag", "plast shopping", "frozen bag", "ppw bag",
    "wash-up", "cling", "just for men", "hair color", "rstr",
    "diamond cling",
)


def normalize_dept(dept: str) -> str:
    d = (dept or "").strip()
    if d in ("Edible", "Food"):
        return "Edible"
    if d == "Drinks":
        return "Drinks"
    return ""


def classify_dept(name: str, extra: str = "") -> str:
    blob = f" {name or ''} {extra or ''} ".lower()
    if any(k in blob for k in _DRINK):
        if any(k in blob for k in ("toothpaste", "t/p ", "toothbrush", "colgate tb")):
            return ""
        return "Drinks"
    if any(k in blob for k in _NONEDIBLE):
        return ""
    return "Edible"
