"""Match a live Magento cart line by sku, item id, or name. No invented SKUs."""

from __future__ import annotations

import re
from typing import Any


def fold_name(value: str) -> str:
    return " ".join(re.split(r"[^a-z0-9]+", (value or "").lower())).strip()


def token_score(query: str, name: str) -> float:
    q = {t for t in fold_name(query).split() if len(t) > 2}
    n = {t for t in fold_name(name).split() if len(t) > 2}
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
    """Resolve a remove/set against lines already in the official cart."""
    rows = [it for it in lines if isinstance(it, dict)]
    want_item = str(item_id or "").strip()
    want_sku = str(sku or "").strip()
    want_name = str(name or "").strip()
    if want_item:
        for it in rows:
            ids = {
                str(it.get("item_id") or "").strip(),
                str(it.get("uid") or "").strip(),
                str(it.get("id") or "").strip(),
            }
            if want_item in ids:
                return it
    if want_sku:
        for it in rows:
            if str(it.get("id") or "").strip() == want_sku or str(it.get("sku") or "").strip() == want_sku:
                return it
    if not want_name:
        return None
    folded = fold_name(want_name)
    if not folded:
        return None
    exact = [it for it in rows if fold_name(str(it.get("name") or "")) == folded]
    if exact:
        return exact[0]
    contained = []
    for it in rows:
        n = fold_name(str(it.get("name") or ""))
        if n and (folded in n or n in folded):
            contained.append(it)
    if len(contained) == 1:
        return contained[0]
    if len(contained) > 1:
        contained.sort(
            key=lambda it: (
                -token_score(want_name, str(it.get("name") or "")),
                len(fold_name(str(it.get("name") or ""))),
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
