"""Invoice-backed purchase history (Carrefour + Grandiose emails)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import db
from .depts import classify_dept, normalize_dept


def ean13_check_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(body):
        total += int(ch) if i % 2 == 0 else int(ch) * 3
    return str((10 - total % 10) % 10)


def official_ean(raw: str) -> str:
    digits = re.sub(r"\D+", "", raw or "")
    if len(digits) == 13:
        return digits
    if len(digits) == 12:
        return digits + ean13_check_digit(digits)
    return digits


def canonical_key(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return key
    con = db.connect()
    row = con.execute("SELECT canonical_key FROM product_aliases WHERE alias_key=?", (key,)).fetchone()
    if row:
        con.close()
        return row["canonical_key"]
    ean = key[4:] if key.startswith("ean:") else ""
    full = official_ean(ean) if ean else ""
    if full and full != ean:
        cand = f"ean:{full}"
        hit = con.execute("SELECT product_key FROM product_meta WHERE product_key=? OR official_ean=?", (cand, full)).fetchone()
        alias = con.execute("SELECT canonical_key FROM product_aliases WHERE alias_key=?", (cand,)).fetchone()
        con.close()
        if alias:
            return alias["canonical_key"]
        if hit:
            return hit["product_key"]
        return key
    if full:
        hit = con.execute("SELECT product_key FROM product_meta WHERE official_ean=?", (full,)).fetchone()
        con.close()
        if hit:
            return hit["product_key"]
        return key
    con.close()
    return key


def product_key(barcode: str, name: str) -> str:
    code = (barcode or "").strip()
    if code:
        return canonical_key(f"ean:{code}")
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return canonical_key(f"name:{slug}")


def upsert_product_meta(product_key: str, meta: dict[str, Any]) -> None:
    key = canonical_key(product_key)
    con = db.connect()
    con.execute(
        """INSERT INTO product_meta(product_key, sku, category, official_name, image_url, source, official_ean)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(product_key) DO UPDATE SET
             sku=excluded.sku,
             category=excluded.category,
             official_name=excluded.official_name,
             image_url=excluded.image_url,
             source=excluded.source,
             official_ean=excluded.official_ean""",
        (
            key,
            meta.get("sku") or meta.get("official_ean") or "",
            meta.get("category") or "",
            meta.get("name") or "",
            meta.get("image_url") or "",
            meta.get("source") or "",
            meta.get("official_ean") or meta.get("sku") or "",
        ),
    )
    con.commit()
    con.close()


def merge_product_keys(old_key: str, new_key: str) -> None:
    if not old_key or not new_key or old_key == new_key:
        return
    con = db.connect()
    con.execute("UPDATE invoice_items SET product_key=? WHERE product_key=?", (new_key, old_key))
    con.execute("UPDATE catalog_prices SET product_key=? WHERE product_key=?", (new_key, old_key))
    con.execute(
        "INSERT INTO product_aliases(alias_key, canonical_key) VALUES (?,?) "
        "ON CONFLICT(alias_key) DO UPDATE SET canonical_key=excluded.canonical_key",
        (old_key, new_key),
    )
    con.execute("DELETE FROM product_meta WHERE product_key=?", (old_key,))
    con.commit()
    con.close()


def set_official_identity(
    *,
    official_ean_code: str,
    official_name: str,
    aliases: list[str] | None = None,
    image_url: str = "",
    source: str = "",
) -> str:
    ean = official_ean(official_ean_code)
    key = f"ean:{ean}"
    upsert_product_meta(
        key,
        {
            "name": official_name,
            "sku": ean,
            "official_ean": ean,
            "image_url": image_url,
            "source": source or "official",
        },
    )
    seen = {ean}
    for raw in aliases or []:
        extra = re.sub(r"\D+", "", raw or "")
        if extra and extra not in seen:
            merge_product_keys(f"ean:{extra}", key)
            seen.add(extra)
    return key


def lookup_official_product(code: str) -> dict[str, Any] | None:
    from bring_fast import catalog

    hit = catalog.lookup_carrefour_gtin(code)
    if hit and hit.get("name"):
        ean = official_ean(str(hit.get("sku") or code))
        hit["sku"] = ean or str(hit.get("sku") or "")
        return hit
    variants = catalog.gtin_variants(code) or [re.sub(r"\D+", "", code or "")]
    want = {official_ean(code), *variants}
    want.discard("")
    for sid in ("grandiose", "carrefour"):
        for q in variants:
            try:
                out = catalog.search(sid, q, 3)
            except Exception:
                continue
            for h in out.get("results") or []:
                name = (h.get("name") or "").strip()
                ean = re.sub(r"\D+", "", str(h.get("ean") or h.get("sku") or ""))
                if not name or not ean:
                    continue
                if ean in want or official_ean(ean) in want:
                    return {
                        "name": name,
                        "sku": official_ean(ean) or ean,
                        "image_url": h.get("image_url") or "",
                        "source": sid,
                    }
    return None


def backfill_official_identities(*, user_id: int | None = None, lookup: bool = True, sleep: float = 0.12) -> dict[str, int]:
    import time

    con = db.connect()
    where = "WHERE ifnull(it.barcode,'')!=''"
    args: list[Any] = []
    if user_id:
        where += " AND i.user_id=?"
        args.append(user_id)
    rows = con.execute(
        f"""
        SELECT DISTINCT it.product_key, it.barcode
        FROM invoice_items it
        JOIN invoices i ON i.id = it.invoice_id
        {where}
        """,
        args,
    ).fetchall()
    con.close()
    by_body: dict[str, set[str]] = {}
    codes: set[str] = set()
    for r in rows:
        digits = re.sub(r"\D+", "", r["barcode"] or "")
        if not digits:
            continue
        codes.add(digits)
        if len(digits) >= 12:
            by_body.setdefault(digits[:12], set()).add(digits)
    merged = named = skipped = 0
    for body, group in by_body.items():
        if len(body) != 12:
            continue
        full = official_ean(body)
        if full not in group:
            continue
        for short in group:
            if short != full:
                merge_product_keys(f"ean:{short}", f"ean:{full}")
                merged += 1
    if not lookup:
        return {"merged": merged, "named": named, "skipped": skipped, "codes": len(codes)}
    seen_full: set[str] = set()
    for digits in sorted(codes):
        full = official_ean(digits) if len(digits) in (12, 13) else digits
        if not full or full in seen_full:
            continue
        seen_full.add(full)
        meta = get_product_meta(f"ean:{full}") or get_product_meta(f"ean:{digits}") or {}
        if (meta.get("official_name") or "").strip() and (meta.get("official_ean") or meta.get("sku") or "").strip():
            skipped += 1
            continue
        hit = lookup_official_product(digits)
        if not hit:
            skipped += 1
            time.sleep(sleep)
            continue
        set_official_identity(
            official_ean_code=str(hit.get("sku") or full),
            official_name=hit["name"],
            aliases=[digits, full],
            image_url=hit.get("image_url") or "",
            source=hit.get("source") or "",
        )
        named += 1
        time.sleep(sleep)
    return {"merged": merged, "named": named, "skipped": skipped, "codes": len(codes)}


def get_product_meta(product_key: str) -> dict[str, Any] | None:
    key = canonical_key(product_key)
    con = db.connect()
    row = con.execute("SELECT * FROM product_meta WHERE product_key=?", (key,)).fetchone()
    con.close()
    return dict(row) if row else None


def upsert_invoice(user_id: int, parsed: dict[str, Any], *, gmail_id: str = "") -> int | None:
    if not parsed.get("invoice_no") or not parsed.get("items"):
        return None
    con = db.connect()
    con.execute(
        """INSERT INTO invoices(user_id, retailer, invoice_no, order_no, invoice_date, store_name, gmail_id, source_file)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id, retailer, invoice_no) DO UPDATE SET
             order_no=excluded.order_no,
             invoice_date=excluded.invoice_date,
             store_name=excluded.store_name,
             gmail_id=excluded.gmail_id,
             source_file=excluded.source_file""",
        (
            user_id,
            parsed["retailer"],
            parsed["invoice_no"],
            parsed.get("order_no") or "",
            parsed.get("invoice_date") or "",
            parsed.get("store_name") or parsed["retailer"],
            gmail_id,
            parsed.get("source") or "",
        ),
    )
    row = con.execute(
        "SELECT id FROM invoices WHERE user_id=? AND retailer=? AND invoice_no=?",
        (user_id, parsed["retailer"], parsed["invoice_no"]),
    ).fetchone()
    invoice_id = int(row["id"])
    con.execute("DELETE FROM invoice_items WHERE invoice_id=?", (invoice_id,))
    for it in parsed["items"]:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        barcode = (it.get("barcode") or "").strip()
        con.execute(
            """INSERT INTO invoice_items(invoice_id, barcode, name, qty, unit_price, line_total, image_url, product_key)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                invoice_id,
                barcode,
                name,
                float(it.get("qty") or 0),
                it.get("unit_price"),
                float(it.get("line_total") or 0),
                it.get("image_url") or "",
                product_key(barcode, name),
            ),
        )
    con.commit()
    con.close()
    return invoice_id


def _parse_day(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _pretty_unit(n: float, unit: str) -> str:
    if abs(n - round(n)) < 0.08:
        v = max(1, int(round(n)))
        return f"every {v} {unit}" + ("" if v == 1 else "s")
    return f"every {n:.1f} {unit}s"


def _fmt_every(days: float) -> str:
    if days < 12:
        return _pretty_unit(max(1, days), "day")
    if days < 25:
        return _pretty_unit(days / 7, "week")
    if days < 700:
        return _pretty_unit(days / 30.437, "month")
    return _pretty_unit(days / 365.25, "year")


def _frequency(
    dates: list[str],
    times: int,
    *,
    since: date | None = None,
    until: date | None = None,
) -> tuple[str, float]:
    """buys / days from first buy of this product to the end of the view."""
    del since
    parsed = [d for d in (_parse_day(x) for x in dates) if d]
    if times < 1 or not parsed:
        return "—", 10**9
    start = min(parsed)
    end = until or date.today()
    if end < start:
        end = max(parsed)
    span = max(1, (end - start).days)
    interval = span / times
    return _fmt_every(interval), interval


def receipt_dir() -> Path:
    from . import db

    root = db.data_dir() / "receipts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_invoice_id(invoice_no: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "", invoice_no or "")


def receipt_path(retailer: str, invoice_no: str) -> Path | None:
    store = re.sub(r"[^a-z0-9]+", "", (retailer or "").lower())
    raw = invoice_no or ""
    candidates = [_safe_invoice_id(raw)]
    if raw.startswith("order-"):
        candidates.append(_safe_invoice_id(raw[6:]))
    elif raw:
        candidates.append(_safe_invoice_id(f"order-{raw}"))
    for inv in candidates:
        if not inv:
            continue
        folder = receipt_dir() / store
        for ext in (".pdf", ".html"):
            p = folder / f"{inv}{ext}"
            if p.is_file():
                return p
    return None


def receipt_page_pngs(pdf: Path) -> list[Path]:
    import shutil
    import subprocess

    dest = pdf.parent / "_preview"
    dest.mkdir(parents=True, exist_ok=True)
    existing = sorted(dest.glob(f"{pdf.stem}-*.png"))
    if existing:
        return existing
    bin_path = shutil.which("pdftoppm")
    if not bin_path:
        return []
    try:
        subprocess.run(
            [bin_path, "-png", "-r", "120", str(pdf), str(dest / pdf.stem)],
            check=True,
            timeout=45,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(dest.glob(f"{pdf.stem}-*.png"))


def _freq_score(p: dict[str, Any]) -> float:
    """Higher = bought more often. One-off yesterday does not beat 100 shopping bags."""
    times = float(p.get("times_bought") or 0)
    interval = float(p.get("interval_days") or 0)
    if times <= 0:
        return 0.0
    if not interval or interval >= 10**8:
        return 0.0
    span = interval * times
    return times / ((span + 60.0) / 365.25)


SORTS = {
    "name": lambda p: (p["name"] or "").lower(),
    "times": lambda p: p["times_bought"],
    "qty": lambda p: p["qty_total"],
    "frequency": _freq_score,
    "likely": lambda p: int(p.get("likely") or 0),
    "spend": lambda p: p["spend_total"],
}


def _frequency_days(label: str) -> int:
    if label == "same day":
        return 0
    if label.startswith("every "):
        try:
            return int(label.split()[1])
        except (IndexError, ValueError):
            return 10**9
    return 10**9


RANGES = {
    "1w": 7,
    "2w": 14,
    "1m": 30,
    "3m": 90,
    "1y": 365,
    "2y": 730,
    "3y": 1095,
    "all": None,
    "custom": None,
    "this_month": None,
    "last_month": None,
}

RANGE_ALIASES = {
    "lastmonth": "last_month",
    "prev_month": "last_month",
    "previous_month": "last_month",
    "mese_scorso": "last_month",
    "last_calendar_month": "last_month",
    "thismonth": "this_month",
    "current_month": "this_month",
    "mese": "this_month",
}


def normalize_range(range_key: str) -> str:
    raw = (range_key or "").strip().lower().replace(" ", "_").replace("-", "_")
    return RANGE_ALIASES.get(raw, raw if raw in RANGES else "all")


def window(range_key: str, start: str = "", end: str = "") -> tuple[str | None, date, str]:
    until = _parse_day(end) or date.today()
    if start:
        since = _parse_day(start)
        if since:
            return since.isoformat(), until, "custom"
    key = normalize_range(range_key)
    if key == "last_month":
        last_prev = until.replace(day=1) - timedelta(days=1)
        return last_prev.replace(day=1).isoformat(), last_prev, "last_month"
    if key == "this_month":
        return until.replace(day=1).isoformat(), until, "this_month"
    days = RANGES.get(key)
    if days:
        return (until - timedelta(days=days)).isoformat(), until, key
    return None, until, "all"


def first_invoice_date(user_id: int) -> date | None:
    con = db.connect()
    row = con.execute("SELECT MIN(invoice_date) AS d FROM invoices WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    return _parse_day(row["d"] if row else None)


def resolve_window(user_id: int, range_key: str, start: str = "", end: str = "") -> tuple[str | None, date, str]:
    since, until, key = window(range_key, start, end)
    if key == "all" and not since:
        first = first_invoice_date(user_id)
        if first:
            since = first.isoformat()
    return since, until, key


def since_date(range_key: str) -> str | None:
    days = RANGES.get(range_key, None)
    if not days:
        return None
    return (date.today() - timedelta(days=days)).isoformat()


def _unit_price(row: dict[str, Any]) -> float | None:
    try:
        if row.get("unit_price") not in (None, ""):
            return float(row["unit_price"])
        qty = float(row.get("qty") or 0)
        if qty:
            return float(row.get("line_total") or 0) / qty
    except (TypeError, ValueError):
        return None
    return None


def _same_price_unit(first: float, last: float) -> bool:
    """Drop piece-vs-kg mixups (garlic 0.30 then 8.61/kg)."""
    if first <= 0 or last <= 0:
        return False
    ratio = last / first
    return 1 / 3 <= ratio <= 3


def list_products(
    user_id: int,
    sort: str = "spend",
    direction: str = "desc",
    since: str | None = None,
    until: date | None = None,
    dept: str = "",
) -> list[dict[str, Any]]:
    con = db.connect()
    where = "WHERE i.user_id=?"
    args: list[Any] = [user_id]
    if since:
        where += " AND i.invoice_date>=?"
        args.append(since)
    if until:
        where += " AND i.invoice_date<=?"
        args.append(until.isoformat())
    rows = con.execute(
        f"""
        SELECT it.product_key,
               MAX(it.name) AS receipt_name,
               MAX(NULLIF(pm.official_name,'')) AS official_name,
               MAX(it.barcode) AS barcode,
               MAX(COALESCE(NULLIF(it.image_url,''), NULLIF(pm.image_url,''))) AS image_url,
               SUM(it.qty) AS qty_total,
               COUNT(*) AS buy_count,
               COUNT(DISTINCT i.id) AS invoice_count,
               SUM(it.line_total) AS spend_total,
               MIN(i.invoice_date) AS first_buy,
               MAX(i.invoice_date) AS last_buy,
               GROUP_CONCAT(i.invoice_date) AS dates
        FROM invoice_items it
        JOIN invoices i ON i.id = it.invoice_id
        LEFT JOIN product_meta pm ON pm.product_key = it.product_key
        {where}
        GROUP BY it.product_key
        """,
        args,
    ).fetchall()
    con.close()
    out = []
    for r in rows:
        times = int(r["invoice_count"] or 0)
        dates = [p for p in (r["dates"] or "").split(",") if p]
        label, interval = _frequency(
            dates,
            times,
            since=_parse_day(since) if since else None,
            until=until,
        )
        receipt = r["receipt_name"] or ""
        official = r["official_name"] or ""
        display = official or receipt
        dept_label = classify_dept(receipt, official)
        if normalize_dept(dept) and dept_label != normalize_dept(dept):
            continue
        out.append(
            {
                "key": r["product_key"],
                "name": display,
                "receipt_name": receipt,
                "official_name": official,
                "barcode": r["barcode"] or "",
                "image_url": r["image_url"] or "",
                "qty_total": float(r["qty_total"] or 0),
                "times_bought": times,
                "frequency": label,
                "interval_days": interval,
                "per_year": round(365.25 / interval, 2) if interval and interval < 10**8 else 0,
                "spend_total": float(r["spend_total"] or 0),
                "first_buy": r["first_buy"] or "",
                "last_buy": r["last_buy"] or "",
                "dept": dept_label,
            }
        )
    attach_likely(user_id, out, today=until)
    key = sort if sort in SORTS else "spend"
    reverse = (direction or "desc").lower() != "asc"
    out.sort(key=SORTS[key], reverse=reverse)
    return out


def attach_likely(user_id: int, products: list[dict[str, Any]], *, today: date | None = None) -> list[dict[str, Any]]:
    from bring_fast import forecast as fc

    today = today or date.today()
    classified = {}
    for rec in fc.buy_history(user_id, until=today).values():
        classified[rec["key"]] = fc.classify(
            rec,
            today=today,
            excluded=False,
            min_buys=fc.DEFAULTS["min_buys"],
            max_interval_days=fc.DEFAULTS["max_interval_days"],
            max_cv=fc.DEFAULTS["max_cv"],
            ewma_alpha=fc.DEFAULTS["ewma_alpha"],
            lapsed_factor=fc.DEFAULTS["lapsed_factor"],
            max_last_age_days=fc.DEFAULTS["max_last_age_days"],
        )
    for p in products:
        hab = classified.get(p.get("key") or "") or {}
        p["likely"] = int(hab.get("score") or 0)
        p["likely_reason"] = hab.get("reason") or ""
    return products


def product_purchases(user_id: int, key: str, since: str | None = None, until: date | None = None) -> dict[str, Any] | None:
    key = canonical_key(key)
    con = db.connect()
    head = con.execute(
        """
        SELECT MAX(it.name) AS name, MAX(it.barcode) AS barcode, MAX(it.image_url) AS image_url
        FROM invoice_items it
        JOIN invoices i ON i.id = it.invoice_id
        WHERE i.user_id=? AND it.product_key=?
        """,
        (user_id, key),
    ).fetchone()
    if not head or not head["name"]:
        con.close()
        return None
    sku_rows = con.execute(
        """
        SELECT DISTINCT it.barcode
        FROM invoice_items it
        JOIN invoices i ON i.id = it.invoice_id
        WHERE i.user_id=? AND it.product_key=? AND ifnull(it.barcode,'')!=''
        """,
        (user_id, key),
    ).fetchall()
    skus = [r["barcode"] for r in sku_rows if r["barcode"]]
    where = "WHERE i.user_id=? AND it.product_key=?"
    args: list[Any] = [user_id, key]
    if since:
        where += " AND i.invoice_date>=?"
        args.append(since)
    if until:
        where += " AND i.invoice_date<=?"
        args.append(until.isoformat())
    rows = con.execute(
        f"""
        SELECT i.invoice_date, i.retailer, i.store_name, i.invoice_no, i.order_no,
               it.qty, it.unit_price, it.line_total, it.name
        FROM invoice_items it
        JOIN invoices i ON i.id = it.invoice_id
        {where}
        ORDER BY i.invoice_date ASC, i.id ASC
        """,
        args,
    ).fetchall()
    con.close()
    buys = [dict(r) for r in rows]
    for b in buys:
        b["has_receipt"] = True
    series = []
    for b in buys:
        price = _unit_price(b)
        if not b.get("invoice_date") or price is None:
            continue
        series.append({"date": b["invoice_date"][:10], "price": round(price, 2), "store": b.get("store_name") or b.get("retailer")})
    first = series[0]["price"] if series else None
    last = series[-1]["price"] if series else None
    delta = None if first in (None, 0) or last is None else round(last - first, 2)
    pct = None if first in (None, 0) or last is None else round(100.0 * (last - first) / first, 1)
    times = len({(b.get("invoice_no"), b.get("retailer")) for b in buys}) or len(buys)
    dates = [b.get("invoice_date") or "" for b in buys]
    freq, interval = _frequency(dates, times, since=_parse_day(since) if since else None, until=until)
    meta = get_product_meta(key) or {}
    barcodes = skus
    catalog_sku = (meta.get("official_ean") or meta.get("sku") or "").strip()
    sku_list = [catalog_sku] if catalog_sku else list(barcodes)
    receipt = head["name"] or ""
    official = (meta.get("official_name") or "").strip()
    display = official or receipt
    return {
        "key": key,
        "name": display,
        "receipt_name": receipt,
        "barcode": head["barcode"] or "",
        "barcodes": barcodes,
        "skus": sku_list,
        "category": meta.get("category") or "",
        "official_name": official,
        "image_url": head["image_url"] or meta.get("image_url") or "",
        "purchases": list(reversed(buys)),
        "series": series,
        "chart_svg": price_chart_svg(series),
        "first_price": first,
        "last_price": last,
        "delta": delta,
        "delta_pct": pct,
        "frequency": freq,
        "interval_days": interval,
        "per_year": round(365.25 / interval, 2) if interval and interval < 10**8 else 0,
        "times_bought": times,
        "dept": classify_dept(receipt, official),
    }
    attach_likely(user_id, [out], today=until or date.today())
    return out


def price_chart_svg(series: list[dict[str, Any]]) -> str:
    w, h, pad = 640, 220, 36
    if not series:
        return ""
    prices = [p["price"] for p in series]
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        hi = lo + 1
    span = hi - lo
    dates = [datetime.strptime(p["date"], "%Y-%m-%d") for p in series]
    t0, t1 = dates[0], dates[-1]
    tspan = max((t1 - t0).total_seconds(), 1)

    def xy(i: int) -> tuple[float, float]:
        x = pad + (w - 2 * pad) * ((dates[i] - t0).total_seconds() / tspan)
        y = h - pad - (h - 2 * pad) * ((prices[i] - lo) / span)
        return x, y

    pts = [xy(i) for i in range(len(series))]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#67e8f9"/>'
        f'<text x="{x:.1f}" y="{max(14, y-10):.1f}" text-anchor="middle" fill="#8b95b8" font-size="11">{prices[i]:.2f}</text>'
        for i, (x, y) in enumerate(pts)
    )
    color = "#67e8f9" if prices[-1] <= prices[0] else "#fb7185"
    return (
        f'<svg viewBox="0 0 {w} {h}" class="chart" preserveAspectRatio="xMidYMid meet">'
        f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" stroke="rgba(140,170,255,.25)"/>'
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{h-pad}" stroke="rgba(140,170,255,.25)"/>'
        f'<text x="{pad}" y="{h-12}" fill="#8b95b8" font-size="12">{series[0]["date"]}</text>'
        f'<text x="{w-pad}" y="{h-12}" text-anchor="end" fill="#8b95b8" font-size="12">{series[-1]["date"]}</text>'
        f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="{line}"/>'
        f"{dots}</svg>"
    )


def purchase_stats(user_id: int) -> dict[str, Any]:
    con = db.connect()
    inv = con.execute("SELECT COUNT(*) n FROM invoices WHERE user_id=?", (user_id,)).fetchone()
    items = con.execute(
        "SELECT COUNT(*) n FROM invoice_items it JOIN invoices i ON i.id=it.invoice_id WHERE i.user_id=?",
        (user_id,),
    ).fetchone()
    con.close()
    return {"invoices": int(inv["n"] if inv else 0), "lines": int(items["n"] if items else 0)}


def daily_spend(
    user_id: int,
    since: str | None = None,
    until: date | None = None,
    dept: str = "",
) -> list[dict[str, Any]]:
    con = db.connect()
    where = "WHERE i.user_id=?"
    args: list[Any] = [user_id]
    if since:
        where += " AND i.invoice_date>=?"
        args.append(since)
    if until:
        where += " AND i.invoice_date<=?"
        args.append(until.isoformat())
    rows = con.execute(
        f"""
        SELECT i.invoice_date, i.retailer, i.store_name, i.invoice_no, it.name, it.line_total
        FROM invoices i
        JOIN invoice_items it ON it.invoice_id = i.id
        {where}
        ORDER BY i.invoice_date ASC, i.id ASC
        """,
        args,
    ).fetchall()
    con.close()
    days: dict[str, dict[str, Any]] = {}
    seen_inv: set[tuple[str, str, str]] = set()
    for r in rows:
        if normalize_dept(dept) and classify_dept(r["name"] or "") != normalize_dept(dept):
            continue
        day = (r["invoice_date"] or "")[:10]
        if not day:
            continue
        key = (day, r["retailer"] or "", r["invoice_no"] or "")
        bucket = days.setdefault(day, {"date": day, "spend": 0.0, "invoices": []})
        bucket["spend"] += float(r["line_total"] or 0)
        if key not in seen_inv:
            seen_inv.add(key)
            bucket["invoices"].append(
                {
                    "invoice_no": r["invoice_no"],
                    "retailer": r["retailer"],
                    "store": r["store_name"] or r["retailer"],
                    "spend": 0.0,
                    "has_receipt": True,
                }
            )
        bucket["invoices"][-1]["spend"] += float(r["line_total"] or 0)
    out = list(days.values())
    peak = max((d["spend"] for d in out), default=0) or 1
    for d in out:
        d["pct"] = max(8, round(100 * d["spend"] / peak))
        d["count"] = len(d["invoices"])
        d["label"] = d["date"]
    return out


GRAINS = ("daily", "weekly", "monthly", "yearly")


def _grain_key(day: date, grain: str) -> tuple[str, str]:
    if grain == "weekly":
        start = day - timedelta(days=day.weekday())
        return start.isoformat(), f"Week of {start.isoformat()}"
    if grain == "monthly":
        return day.strftime("%Y-%m"), day.strftime("%b %Y")
    if grain == "yearly":
        return str(day.year), str(day.year)
    return day.isoformat(), day.isoformat()


def spend_series(
    user_id: int,
    since: str | None = None,
    until: date | None = None,
    grain: str = "daily",
) -> list[dict[str, Any]]:
    return bucket_series(daily_spend(user_id, since=since, until=until), grain)


def bucket_series(days: list[dict[str, Any]], grain: str = "daily") -> list[dict[str, Any]]:
    grain = grain if grain in GRAINS else "daily"
    if grain == "daily":
        return days
    buckets: dict[str, dict[str, Any]] = {}
    for d in days:
        day = _parse_day(d["date"])
        if not day:
            continue
        key, label = _grain_key(day, grain)
        b = buckets.setdefault(key, {"date": key, "label": label, "spend": 0.0, "invoices": []})
        b["spend"] += d["spend"]
        b["invoices"].extend(d["invoices"])
    out = list(buckets.values())
    peak = max((d["spend"] for d in out), default=0) or 1
    for d in out:
        d["pct"] = max(8, round(100 * d["spend"] / peak))
        d["count"] = len(d["invoices"])
    return out


def price_trend(
    user_id: int,
    since: str | None = None,
    until: date | None = None,
    grain: str = "monthly",
) -> dict[str, Any]:
    """Mean first→last unit-price change, plus a time series of that same index."""
    con = db.connect()
    where = "WHERE i.user_id=?"
    args: list[Any] = [user_id]
    if since:
        where += " AND i.invoice_date>=?"
        args.append(since)
    if until:
        where += " AND i.invoice_date<=?"
        args.append(until.isoformat())
    rows = con.execute(
        f"""
        SELECT it.product_key, i.invoice_date, it.qty, it.unit_price, it.line_total
        FROM invoice_items it
        JOIN invoices i ON i.id = it.invoice_id
        {where}
        ORDER BY i.invoice_date ASC, i.id ASC
        """,
        args,
    ).fetchall()
    con.close()
    by_prod: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        price = _unit_price(dict(r))
        day = (r["invoice_date"] or "")[:10]
        if price is None or price <= 0 or not day:
            continue
        by_prod.setdefault(r["product_key"], []).append((day, price))
    tracked = {}
    for key, pts in by_prod.items():
        first = pts[0][1]
        cleaned = [pts[0]] + [p for p in pts[1:] if _same_price_unit(first, p[1])]
        if len(cleaned) >= 2:
            tracked[key] = cleaned
    changes: list[float] = []
    up = down = flat = 0
    for points in tracked.values():
        first, last = points[0][1], points[-1][1]
        pct = 100.0 * (last - first) / first
        changes.append(pct)
        if pct > 0.5:
            up += 1
        elif pct < -0.5:
            down += 1
        else:
            flat += 1
    n = len(changes)
    avg = sum(changes) / n if n else 0.0
    ordered = sorted(changes)
    if n == 0:
        mid = 0.0
    elif n % 2:
        mid = ordered[n // 2]
    else:
        mid = (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    if n == 0:
        direction = "none"
    elif avg > 0.5:
        direction = "up"
    elif avg < -0.5:
        direction = "down"
    else:
        direction = "flat"
    series = _price_index_series(tracked, grain)
    return {
        "avg_pct": round(avg, 1),
        "median_pct": round(mid, 1),
        "products": n,
        "up": up,
        "down": down,
        "flat": flat,
        "direction": direction,
        "series": series,
        "chart_svg": index_chart_svg(series),
    }


def _price_index_series(
    tracked: dict[str, list[tuple[str, float]]],
    grain: str,
) -> list[dict[str, Any]]:
    grain = grain if grain in GRAINS else "monthly"
    first: dict[str, float] = {k: pts[0][1] for k, pts in tracked.items()}
    last_idx: dict[str, float] = {}
    events: list[tuple[date, str, float]] = []
    for key, pts in tracked.items():
        for day_s, price in pts:
            day = _parse_day(day_s)
            if day:
                events.append((day, key, price))
    events.sort(key=lambda e: e[0])
    buckets: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for day, key, price in events:
        last_idx[key] = 100.0 * price / first[key]
        bkey, label = _grain_key(day, grain)
        if bkey not in buckets:
            order.append(bkey)
            buckets[bkey] = {"date": bkey, "label": label}
        mean = sum(last_idx.values()) / len(last_idx)
        buckets[bkey]["index"] = round(mean, 2)
        buckets[bkey]["pct"] = round(mean - 100.0, 1)
        buckets[bkey]["n"] = len(last_idx)
    return [buckets[k] for k in order]


def index_chart_svg(series: list[dict[str, Any]]) -> str:
    w, h, pad = 640, 200, 36
    if len(series) < 2:
        return ""
    values = [p["index"] for p in series]
    lo, hi = min(values + [100.0]), max(values + [100.0])
    if hi <= lo:
        hi = lo + 1
    span = hi - lo

    def y_of(v: float) -> float:
        return h - pad - (h - 2 * pad) * ((v - lo) / span)

    xs = []
    n = len(series)
    for i in range(n):
        xs.append(pad + (w - 2 * pad) * (i / (n - 1)))
    pts = " ".join(f"{xs[i]:.1f},{y_of(values[i]):.1f}" for i in range(n))
    y100 = y_of(100.0)
    color = "#fb7185" if values[-1] > 100.5 else "#4ade80" if values[-1] < 99.5 else "#67e8f9"
    last = series[-1]
    return (
        f'<svg viewBox="0 0 {w} {h}" class="chart" preserveAspectRatio="xMidYMid meet">'
        f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" stroke="rgba(140,170,255,.25)"/>'
        f'<line x1="{pad}" y1="{y100:.1f}" x2="{w-pad}" y2="{y100:.1f}" stroke="rgba(140,170,255,.28)" stroke-dasharray="4 4"/>'
        f'<text x="{pad}" y="{max(12, y100-6):.1f}" fill="#8b95b8" font-size="11">100</text>'
        f'<text x="{pad}" y="{h-12}" fill="#8b95b8" font-size="12">{series[0]["label"]}</text>'
        f'<text x="{w-pad}" y="{h-12}" text-anchor="end" fill="#8b95b8" font-size="12">{last["label"]}</text>'
        f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="{pts}"/>'
        f'<text x="{w-pad}" y="{max(14, y_of(values[-1])-8):.1f}" text-anchor="end" fill="{color}" font-size="12">'
        f'{last["pct"]:+.1f}%</text>'
        f"</svg>"
    )


def invoice_receipt(user_id: int, retailer: str, invoice_no: str) -> dict[str, Any] | None:
    con = db.connect()
    row = con.execute(
        """
        SELECT * FROM invoices
        WHERE user_id=? AND retailer=? AND (invoice_no=? OR order_no=? OR invoice_no=?)
        """,
        (user_id, retailer, invoice_no, invoice_no, f"order-{invoice_no}"),
    ).fetchone()
    if not row:
        con.close()
        return None
    items = con.execute(
        "SELECT name, qty, unit_price, line_total FROM invoice_items WHERE invoice_id=? ORDER BY id",
        (row["id"],),
    ).fetchall()
    con.close()
    lines = [dict(it) for it in items]
    return {
        "retailer": row["retailer"],
        "store": row["store_name"] or row["retailer"],
        "invoice_no": row["invoice_no"],
        "order_no": row["order_no"] or "",
        "invoice_date": row["invoice_date"] or "",
        "items": lines,
        "total": sum(float(it.get("line_total") or 0) for it in lines),
    }


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    ordered = sorted(vals)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def typical_unit_prices(user_id: int, since: str | None = None, until: date | None = None) -> dict[str, float]:
    """Median unit price per product; drop piece-vs-kg (ratio outside 1/3–3×)."""
    con = db.connect()
    where = "WHERE i.user_id=?"
    args: list[Any] = [user_id]
    if since:
        where += " AND i.invoice_date>=?"
        args.append(since)
    if until:
        where += " AND i.invoice_date<=?"
        args.append(until.isoformat())
    rows = con.execute(
        f"""
        SELECT it.product_key, it.qty, it.unit_price, it.line_total
        FROM invoice_items it
        JOIN invoices i ON i.id = it.invoice_id
        {where}
        """,
        args,
    ).fetchall()
    con.close()
    buckets: dict[str, list[float]] = {}
    for r in rows:
        price = _unit_price(dict(r))
        if price is None or price <= 0:
            continue
        buckets.setdefault(r["product_key"], []).append(price)
    out: dict[str, float] = {}
    for key, prices in buckets.items():
        mid = _median(prices)
        if mid is None or mid <= 0:
            continue
        kept = [p for p in prices if _same_price_unit(mid, p)]
        typical = _median(kept or prices)
        if typical is not None:
            out[key] = round(typical, 2)
    return out


def _public_product(p: dict[str, Any]) -> dict[str, Any]:
    interval = p.get("interval_days")
    if interval is None or float(interval or 0) >= 10**8:
        interval_out = None
    else:
        interval_out = round(float(interval), 1)
    return {
        "key": p.get("key"),
        "name": p.get("name"),
        "dept": p.get("dept"),
        "typical_unit_aed": p.get("typical_unit_aed"),
        "spend_total": round(float(p.get("spend_total") or 0), 2),
        "times_bought": int(p.get("times_bought") or 0),
        "frequency": p.get("frequency"),
        "interval_days": interval_out,
        "weighted_interval_days": p.get("weighted_interval_days"),
        "mean_interval_days": p.get("mean_interval_days"),
        "std_interval_days": p.get("std_interval_days"),
        "cv": p.get("cv"),
        "days_since": p.get("days_since"),
        "likely": int(p.get("likely") or p.get("score") or 0),
        "likely_reason": p.get("likely_reason") or p.get("reason"),
        "score": p.get("score"),
        "reason": p.get("reason"),
        "first_buy": p.get("first_buy") or "",
        "last_buy": p.get("last_buy") or "",
        "next_due": p.get("next_due") or "",
        "due_in_days": p.get("due_in_days"),
        "status": p.get("status") or "unknown",
    }


def forecast_products(
    user_id: int,
    *,
    since: str | None = None,
    until: date | None = None,
    dept: str = "",
    today: date | None = None,
) -> list[dict[str, Any]]:
    today = today or date.today()
    until = until or today
    products = list_products(user_id, sort="frequency", direction="desc", since=since, until=until, dept=dept)
    typical = typical_unit_prices(user_id, since=since, until=until)
    out = []
    for p in products:
        p["typical_unit_aed"] = typical.get(p["key"])
        last = _parse_day(p.get("last_buy"))
        interval = float(p.get("interval_days") or 0)
        times = int(p.get("times_bought") or 0)
        if times < 2 or not last or not interval or interval >= 10**8:
            p["next_due"] = ""
            p["due_in_days"] = None
            p["status"] = "unknown"
        else:
            nxt = last + timedelta(days=max(1, round(interval)))
            p["next_due"] = nxt.isoformat()
            p["due_in_days"] = (nxt - today).days
            age = (today - last).days
            if age > max(45, int(1.5 * interval)):
                p["status"] = "lapsed"
            elif p["due_in_days"] < 0:
                p["status"] = "overdue"
            elif p["due_in_days"] == 0:
                p["status"] = "due_today"
            elif p["due_in_days"] == 1:
                p["status"] = "due_tomorrow"
            else:
                p["status"] = "upcoming"
        out.append(p)
    return out


def shopping_list(
    user_id: int,
    *,
    horizon_days: int = 7,
    limit: int = 20,
    dept: str = "",
    today: date | None = None,
    min_buys: int | None = None,
    exclude: list[str] | None = None,
) -> list[dict[str, Any]]:
    from bring_fast import forecast as fc

    today = today or date.today()
    horizon = max(0, int(horizon_days or 7))
    skip_bits = ("plastic bag", "shopping bag", "plast shopping", "t shirt hndl bag")
    extra = {e.strip().lower() for e in (exclude or []) if e and str(e).strip()}
    blocked = fc.load_exclusions(user_id)
    classified = {
        rec["key"]: fc.classify(
            rec,
            today=today,
            excluded=rec["key"] in blocked or rec["key"].lower() in extra or (rec.get("official_name") or rec.get("receipt_name") or "").lower() in extra,
            min_buys=int(min_buys) if min_buys is not None else fc.DEFAULTS["min_buys"],
            max_interval_days=fc.DEFAULTS["max_interval_days"],
            max_cv=fc.DEFAULTS["max_cv"],
            ewma_alpha=fc.DEFAULTS["ewma_alpha"],
            lapsed_factor=fc.DEFAULTS["lapsed_factor"],
            max_last_age_days=fc.DEFAULTS["max_last_age_days"],
        )
        for rec in fc.buy_history(user_id, until=today).values()
    }
    rows = []
    for p in forecast_products(user_id, dept=dept, today=today):
        if p.get("status") in ("unknown", "lapsed"):
            continue
        name = (p.get("name") or "").lower()
        if any(bit in name for bit in skip_bits):
            continue
        last = _parse_day(p.get("last_buy"))
        if last and (today - last).days > 90:
            continue
        due = p.get("due_in_days")
        if due is None or due > horizon:
            continue
        if p.get("key") in blocked or (p.get("key") or "").lower() in extra or name in extra:
            continue
        hab = classified.get(p["key"]) or {}
        p["likely"] = int(hab.get("score") or 0)
        p["likely_reason"] = hab.get("reason") or "unknown"
        p["score"] = p["likely"]
        p["reason"] = p["likely_reason"]
        p["weighted_interval_days"] = hab.get("weighted_interval_days")
        p["mean_interval_days"] = hab.get("mean_interval_days")
        p["std_interval_days"] = hab.get("std_interval_days")
        p["cv"] = hab.get("cv")
        p["days_since"] = hab.get("days_since")
        rows.append(p)
    rows.sort(key=lambda p: (-int(p.get("likely") or 0), int(p.get("due_in_days") or 0)))
    return [_public_product(p) for p in rows[: max(1, min(int(limit or 20), 50))]]


def ranked_products(
    user_id: int,
    *,
    sort: str = "spend",
    limit: int = 10,
    dept: str = "",
    range_key: str = "all",
    today: date | None = None,
) -> list[dict[str, Any]]:
    today = today or date.today()
    since, until, _ = resolve_window(user_id, range_key, end=today.isoformat())
    rows = forecast_products(user_id, since=since, until=until, dept=dept, today=today)
    if sort == "unit_price":
        rows = [p for p in rows if p.get("typical_unit_aed") is not None]
        rows.sort(key=lambda p: float(p["typical_unit_aed"]), reverse=True)
    elif sort == "frequency":
        rows.sort(key=_freq_score, reverse=True)
    elif sort == "times":
        rows.sort(key=lambda p: int(p.get("times_bought") or 0), reverse=True)
    else:
        rows.sort(key=lambda p: float(p.get("spend_total") or 0), reverse=True)
    return [_public_product(p) for p in rows[: max(1, min(int(limit or 10), 50))]]


def find_products(user_id: int, query: str, *, limit: int = 8, today: date | None = None) -> list[dict[str, Any]]:
    q = re.sub(r"\s+", " ", (query or "").strip().lower())
    if not q:
        return []
    today = today or date.today()
    hits = []
    for p in forecast_products(user_id, today=today):
        blob = " ".join(
            [
                str(p.get("name") or ""),
                str(p.get("receipt_name") or ""),
                str(p.get("official_name") or ""),
                str(p.get("barcode") or ""),
                str(p.get("key") or ""),
            ]
        ).lower()
        if q in blob:
            score = 0 if (p.get("name") or "").lower() == q else 1
            hits.append((score, -int(p.get("times_bought") or 0), p))
    hits.sort(key=lambda t: (t[0], t[1]))
    return [_public_product(t[2]) for t in hits[: max(1, min(int(limit or 8), 20))]]


def spend_report(
    user_id: int,
    *,
    range_key: str = "1m",
    grain: str = "",
    dept: str = "",
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    since, until, key = resolve_window(user_id, range_key, end=today.isoformat())
    if not grain:
        grain = "weekly" if key in ("1w", "2w", "1m") else "monthly"
    days = daily_spend(user_id, since=since, until=until, dept=dept)
    total = round(sum(d["spend"] for d in days), 2)
    invoices = sum(int(d.get("count") or 0) for d in days)
    first = first_invoice_date(user_id)
    span_start = _parse_day(since) or first or until
    span_days = max(1, (until - span_start).days + 1)
    last_week_since, last_week_until, _ = window("1w", end=until.isoformat())
    last_month_since, last_month_until, _ = window("1m", end=until.isoformat())
    last_week = round(sum(d["spend"] for d in daily_spend(user_id, since=last_week_since, until=last_week_until, dept=dept)), 2)
    last_month = round(sum(d["spend"] for d in daily_spend(user_id, since=last_month_since, until=last_month_until, dept=dept)), 2)
    by_store: dict[str, float] = {}
    for d in days:
        for inv in d.get("invoices") or []:
            store = inv.get("store") or inv.get("retailer") or "store"
            by_store[store] = round(by_store.get(store, 0.0) + float(inv.get("spend") or 0), 2)
    series = [
        {"date": b["date"], "label": b.get("label") or b["date"], "spend": round(float(b["spend"]), 2), "invoices": int(b.get("count") or 0)}
        for b in bucket_series(days, grain)
    ]
    return {
        "currency": "AED",
        "range": key,
        "grain": grain if grain in GRAINS else "monthly",
        "since": since,
        "until": until.isoformat(),
        "total": total,
        "invoices": invoices,
        "days": span_days,
        "avg_per_week": round(total / (span_days / 7.0), 2),
        "avg_per_month": round(total / (span_days / 30.437), 2),
        "last_7_days": last_week,
        "last_30_days": last_month,
        "orders": list_orders(user_id, since=since, until=until, include_items=False, limit=80),
        "by_store": [{"store": k, "spend": v} for k, v in sorted(by_store.items(), key=lambda kv: -kv[1])],
        "series": series,
    }


def list_orders(
    user_id: int,
    *,
    since: str | None = None,
    until: date | None = None,
    include_items: bool = True,
    limit: int = 40,
) -> list[dict[str, Any]]:
    con = db.connect()
    where = "WHERE i.user_id=?"
    args: list[Any] = [user_id]
    if since:
        where += " AND i.invoice_date>=?"
        args.append(since)
    if until:
        where += " AND i.invoice_date<=?"
        args.append(until.isoformat())
    rows = con.execute(
        f"""
        SELECT i.id, i.invoice_no, i.order_no, i.invoice_date, i.retailer, i.store_name,
               COALESCE(SUM(it.line_total), 0) AS total,
               COUNT(it.id) AS lines
        FROM invoices i
        LEFT JOIN invoice_items it ON it.invoice_id = i.id
        {where}
        GROUP BY i.id
        ORDER BY i.invoice_date DESC, i.id DESC
        LIMIT ?
        """,
        [*args, max(1, min(int(limit or 40), 100))],
    ).fetchall()
    out = []
    for r in rows:
        rec = {
            "date": r["invoice_date"] or "",
            "store": r["store_name"] or r["retailer"],
            "retailer": r["retailer"],
            "invoice_no": r["invoice_no"],
            "order_no": r["order_no"] or "",
            "total": round(float(r["total"] or 0), 2),
            "lines": int(r["lines"] or 0),
            "currency": "AED",
        }
        if include_items:
            items = con.execute(
                """SELECT name, qty, unit_price, line_total, barcode
                   FROM invoice_items WHERE invoice_id=? ORDER BY id""",
                (r["id"],),
            ).fetchall()
            rec["items"] = [
                {
                    "name": it["name"],
                    "qty": float(it["qty"] or 0),
                    "unit_price": float(it["unit_price"] or 0),
                    "line_total": float(it["line_total"] or 0),
                    "barcode": it["barcode"] or "",
                }
                for it in items
            ]
        out.append(rec)
    con.close()
    return out


def orders_report(
    user_id: int,
    *,
    range_key: str = "last_month",
    include_items: bool = True,
    limit: int = 40,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    since, until, key = resolve_window(user_id, range_key, end=today.isoformat())
    spend = spend_report(user_id, range_key=key, today=today)
    orders = list_orders(user_id, since=since, until=until, include_items=include_items, limit=limit)
    return {
        "currency": "AED",
        "range": key,
        "since": since,
        "until": until.isoformat(),
        "total": spend["total"],
        "order_count": spend["invoices"],
        "returned": len(orders),
        "orders": orders,
    }
