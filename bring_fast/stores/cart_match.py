"""Match a live Magento cart line by sku, item id, or name. No invented SKUs."""

from __future__ import annotations

import re
from typing import Any

# Longer phrases first. Used both to infer action=remove and to match the line.
REMOVE_NAME_PREFIXES = (
    "remove from the cart ",
    "remove from cart ",
    "take out the ",
    "take out ",
    "togli dal carrello ",
    "togli la ",
    "togli il ",
    "togli l'",
    "togli ",
    "remove the ",
    "remove ",
)
_STOP = frozenset({"the", "la", "il", "lo", "a", "un", "una", "le", "gli", "and", "or", "di", "del", "dal"})


def fold_name(value: str) -> str:
    return " ".join(re.split(r"[^a-z0-9]+", (value or "").lower())).strip()


def fold_significant(value: str) -> str:
    return " ".join(t for t in fold_name(value).split() if t not in _STOP and len(t) > 1)


def peel_remove_name(value: str) -> str | None:
    """If this is a take-out phrase, return the product name. Else None."""
    q = " ".join((value or "").split())
    if not q:
        return None
    low = q.lower()
    for prefix in REMOVE_NAME_PREFIXES:
        if low.startswith(prefix):
            rest = q[len(prefix) :].strip()
            return rest or None
    return None


def token_score(query: str, name: str) -> float:
    q = {t for t in fold_significant(query).split() if len(t) > 2}
    n = {t for t in fold_significant(name).split() if len(t) > 2}
    if not q or not n:
        return 0.0
    return len(q & n) / len(q)


def match_cart_line(
    lines: list[dict[str, Any]],
    *,
    item_id: str = "",
    sku: str = "",
    name: str = "",
) -> dict[str, Any] | None:
    """Resolve a remove/set against lines already in the official cart.

    item_id is the numeric Magento quote item id (CartItemInterface.id), not a UID
    and not the EAN. sku/id is the catalog EAN. name matches the live line title.
    """
    rows = [it for it in lines if isinstance(it, dict)]
    want_item = str(item_id or "").strip()
    want_sku = str(sku or "").strip()
    want_name = str(name or "").strip()
    peeled = peel_remove_name(want_name)
    if peeled:
        want_name = peeled
    if want_item:
        for it in rows:
            # Numeric Magento quote item id, or GraphQL uid — never the EAN in `id`.
            if want_item == str(it.get("item_id") or "").strip():
                return it
            if want_item == str(it.get("uid") or "").strip():
                return it
    if want_sku:
        for it in rows:
            if str(it.get("id") or "").strip() == want_sku or str(it.get("sku") or "").strip() == want_sku:
                return it
    if not want_name:
        return None
    folded = fold_significant(want_name)
    if not folded:
        return None
    exact = [it for it in rows if fold_significant(str(it.get("name") or "")) == folded]
    if exact:
        return exact[0]
    contained = []
    for it in rows:
        n = fold_significant(str(it.get("name") or ""))
        if n and (folded in n or n in folded):
            contained.append(it)
    if len(contained) == 1:
        return contained[0]
    if len(contained) > 1:
        contained.sort(
            key=lambda it: (
                -token_score(want_name, str(it.get("name") or "")),
                len(fold_significant(str(it.get("name") or ""))),
            )
        )
        return contained[0]
    ranked = sorted(
        rows,
        key=lambda it: token_score(want_name, str(it.get("name") or "")),
        reverse=True,
    )
    if not ranked:
        return None
    best = token_score(want_name, str(ranked[0].get("name") or ""))
    second = token_score(want_name, str(ranked[1].get("name") or "")) if len(ranked) > 1 else 0.0
    if best >= 0.5 and best > second:
        return ranked[0]
    return None


def missing_line_error(label: str, lines: list[dict[str, Any]], *, store: str) -> str:
    names = [str(it.get("name") or it.get("id") or "").strip() for it in lines if isinstance(it, dict)]
    names = [n for n in names if n]
    what = (label or "That product").strip() or "That product"
    if names:
        return f"{what} is not in the official {store} cart. Cart has: {', '.join(names)}."
    return f"{what} is not in the official {store} cart. The cart is empty."
