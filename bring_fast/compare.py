"""Live catalog prices vs paid receipt prices."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from . import catalog, db
from .catalog import gtin_variants

SearchFn = Callable[[str, str, int], dict[str, Any]]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(raw: Any) -> float | None:
    try:
        if raw is None or raw == "":
            return None
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _queries(barcodes: list[str], names: list[str]) -> list[str]:
    out: list[str] = []
    for code in barcodes:
        for v in gtin_variants(code) or ([code] if code else []):
            if v and v not in out:
                out.append(v)
    for name in names:
        n = (name or "").strip()
        if n and n not in out:
            out.append(n)
    return out


def _pick(results: list[dict[str, Any]], barcodes: list[str]) -> dict[str, Any] | None:
    digits = {"".join(ch for ch in c if ch.isdigit()) for c in barcodes if c}
    ranked: list[dict[str, Any]] = []
    for hit in results:
        price = _money(hit.get("price"))
        if price is None or price <= 0:
            continue
        ean = "".join(ch for ch in str(hit.get("ean") or hit.get("sku") or hit.get("id") or "") if ch.isdigit())
        score = 1
        if ean and any(ean == d or ean.startswith(d) or d.startswith(ean[:12] if len(ean) >= 12 else ean) for d in digits if d):
            score = 0
        ranked.append({"price": price, "name": (hit.get("name") or "").strip(), "sku": str(hit.get("sku") or hit.get("ean") or hit.get("id") or ""), "score": score})
    ranked.sort(key=lambda r: r["score"])
    return ranked[0] if ranked else None


def quote_store(
    retailer: str,
    barcodes: list[str],
    names: list[str],
    *,
    search: SearchFn | None = None,
) -> dict[str, Any]:
    fn = search or catalog.search
    last_err = ""
    for q in _queries(barcodes, names):
        found = fn(retailer, q, 5)
        if found.get("error"):
            last_err = str(found["error"])
        hit = _pick(found.get("results") or [], barcodes)
        if hit:
            return {"ok": True, "price": hit["price"], "found_name": hit["name"], "sku": hit["sku"], "error": ""}
    return {"ok": False, "price": None, "found_name": "", "sku": "", "error": last_err or "not found"}


def record_quote(
    user_id: int,
    product_key: str,
    retailer: str,
    quote: dict[str, Any],
    source: str,
) -> None:
    con = db.connect()
    con.execute(
        """INSERT INTO catalog_prices(user_id, product_key, retailer, price, found_name, sku, source, error, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            user_id,
            product_key,
            retailer,
            quote.get("price"),
            quote.get("found_name") or "",
            quote.get("sku") or "",
            source,
            quote.get("error") or "",
            _now(),
        ),
    )
    con.commit()
    con.close()


def refresh_store(
    user_id: int,
    product_key: str,
    retailer: str,
    barcodes: list[str],
    names: list[str],
    *,
    source: str = "manual",
    search: SearchFn | None = None,
) -> dict[str, Any]:
    quote = quote_store(retailer, barcodes, names, search=search)
    record_quote(user_id, product_key, retailer, quote, source)
    return quote


def latest_quotes(user_id: int, product_key: str) -> dict[str, dict[str, Any]]:
    con = db.connect()
    rows = con.execute(
        """
        SELECT retailer, price, found_name, sku, source, error, fetched_at
        FROM catalog_prices
        WHERE user_id=? AND product_key=?
        ORDER BY fetched_at DESC, id DESC
        """,
        (user_id, product_key),
    ).fetchall()
    con.close()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["retailer"] in out:
            continue
        out[r["retailer"]] = dict(r)
    return out


def compare_board(user_id: int, product_key: str, paid: float | None) -> list[dict[str, Any]]:
    latest = latest_quotes(user_id, product_key)
    priced = [r for r in latest.values() if r.get("price")]
    lo = min((float(r["price"]) for r in priced), default=None)
    hi = max((float(r["price"]) for r in priced), default=None)
    board = []
    for store in db.RETAILERS:
        rec = latest.get(store["id"]) or {}
        price = rec.get("price")
        vs = None
        if price and paid and paid > 0:
            vs = round(100.0 * (float(price) - paid) / paid, 1)
        board.append(
            {
                "id": store["id"],
                "name": store["name"],
                "logo": store.get("logo") or "",
                "price": float(price) if price else None,
                "found_name": rec.get("found_name") or "",
                "fetched_at": rec.get("fetched_at") or "",
                "error": rec.get("error") or "",
                "vs_paid": vs,
                "cheapest": bool(price and lo is not None and float(price) == lo),
                "dearest": bool(price and hi is not None and lo != hi and float(price) == hi),
            }
        )
    return board


def product_keys_for_user(user_id: int) -> list[dict[str, Any]]:
    con = db.connect()
    rows = con.execute(
        """
        SELECT it.product_key,
               MAX(it.name) AS receipt_name,
               MAX(it.barcode) AS barcode,
               MAX(NULLIF(pm.official_name,'')) AS official_name
        FROM invoice_items it
        JOIN invoices i ON i.id = it.invoice_id
        LEFT JOIN product_meta pm ON pm.product_key = it.product_key
        WHERE i.user_id=?
        GROUP BY it.product_key
        """,
        (user_id,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def stale_before(user_id: int, product_key: str, retailer: str, iso: str) -> bool:
    rec = latest_quotes(user_id, product_key).get(retailer)
    if not rec:
        return True
    return (rec.get("fetched_at") or "") < iso
