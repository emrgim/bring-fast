"""When a receipt adds a product: search that store, download, compress, attach."""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
from typing import Any

import requests

from . import catalog, db, purchases

FETCH = os.environ.get("BRINGFAST_FETCH_IMAGES", "1").strip().lower() not in {"0", "false", "no"}
MAX_EDGE = 256
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_MCD_CACHE: list[dict[str, str]] | None = None


def enabled() -> bool:
    return os.environ.get("BRINGFAST_FETCH_IMAGES", "1").strip().lower() not in {"0", "false", "no"}


def image_dir() -> Path:
    root = db.data_dir() / "product-images"
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_key(product_key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", product_key or "")[:160] or "unknown"


def local_path(product_key: str) -> Path:
    return image_dir() / f"{safe_key(product_key)}.webp"


def public_url(product_key: str) -> str:
    return f"/product-images/{safe_key(product_key)}.webp"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "image/*,text/html,application/json"})
    return s


def compress_bytes(raw: bytes) -> bytes:
    from PIL import Image

    im = Image.open(io.BytesIO(raw))
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    im.thumbnail((MAX_EDGE, MAX_EDGE))
    out = io.BytesIO()
    im.save(out, format="WEBP", quality=78, method=4)
    return out.getvalue()


def download_compress(url: str, dest: Path) -> bool:
    if not url or dest.exists():
        return dest.exists()
    try:
        r = _session().get(url, timeout=20)
        r.raise_for_status()
    except requests.RequestException:
        return False
    if not r.content or len(r.content) < 80:
        return False
    try:
        data = compress_bytes(r.content)
    except Exception:
        return False
    dest.write_bytes(data)
    return True


def _norm(name: str) -> str:
    s = (name or "").lower()
    s = s.replace("™", " ").replace("®", " ")
    s = re.sub(r"\b(large|medium|small)\s+meal\b", " meal", s)
    s = re.sub(r"\bfor\s+\d+\s*aed\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def mcdonalds_menu() -> list[dict[str, str]]:
    global _MCD_CACHE
    if _MCD_CACHE is not None:
        return _MCD_CACHE
    found: dict[str, dict[str, str]] = {}
    bundled = Path(__file__).resolve().parent / "data" / "mcdonalds_uae_menu.json"
    if bundled.is_file():
        try:
            for it in json.loads(bundled.read_text(encoding="utf-8")):
                if isinstance(it, dict) and it.get("name") and it.get("image_url"):
                    found[_norm(it["name"])] = {
                        "name": it["name"],
                        "image_url": it["image_url"],
                        "id": it.get("id") or "",
                    }
        except Exception:
            found = {}
    if found:
        _MCD_CACHE = list(found.values())
        return _MCD_CACHE
    pages = [
        "https://www.mcdonalds.com/ae/en-ae/full-menu.html",
        "https://www.mcdonalds.com/ae/en-ae/full-menu/extra-value-meal.html",
    ]
    sess = _session()
    rx = re.compile(
        r"is/image/mcdonalds/([^\"?:]+):nutrition-calculator-tile[^>]*>\s*([^<]+)",
        re.I,
    )
    for url in pages:
        try:
            html = sess.get(url, timeout=12).text
        except requests.RequestException:
            continue
        for mid, title in rx.findall(html):
            name = re.sub(r"\s+", " ", title).strip()
            if not name:
                continue
            img = f"https://s7d1.scene7.com/is/image/mcdonalds/{mid}:nutrition-calculator-tile?fmt=webp&wid=256"
            key = _norm(name)
            if key:
                found[key] = {"name": name, "image_url": img, "id": mid}
    _MCD_CACHE = list(found.values())
    return _MCD_CACHE


def search_mcdonalds(query: str, limit: int = 8) -> dict[str, Any]:
    q = _norm(query)
    q_tok = [t for t in q.split() if t not in {"meal", "large", "medium", "small"}]
    ranked: list[tuple[float, dict[str, str]]] = []
    for it in mcdonalds_menu():
        n = _norm(it["name"])
        n_tok = [t for t in n.split() if t not in {"meal", "large", "medium", "small"}]
        if q_tok and not set(q_tok).issubset(n_tok) and not set(n_tok).issubset(q_tok):
            continue
        score = catalog._score_name(q, n)
        if score > 0:
            ranked.append((score, it))
    ranked.sort(key=lambda x: x[0], reverse=True)
    results = [
        {
            "id": it["id"],
            "name": it["name"],
            "image_url": it["image_url"],
            "url": f"https://www.mcdonalds.com/ae/en-ae/product/{it['id']}.html",
        }
        for _, it in ranked[: max(1, min(limit, 20))]
    ]
    return {"retailer": "mcdonalds", "query": query, "results": results}


def find_official_image(retailer: str, name: str, barcode: str = "") -> str:
    if barcode:
        hit = purchases.lookup_official_product(barcode)
        if hit and hit.get("image_url"):
            return str(hit["image_url"])
    q = (barcode or name or "").strip()
    if not q:
        return ""
    if retailer == "mcdonalds":
        out = search_mcdonalds(name or q, 5)
    else:
        out = catalog.search(retailer, q, 5)
    hit = catalog.best_match(name or q, out.get("results") or [])
    if not hit:
        return ""
    return str(hit.get("image_url") or "")


def attach_for_receipt(retailer: str, product_key: str, name: str, barcode: str = "") -> str:
    """Search the store that issued the receipt, store a compressed local copy."""
    if not product_key:
        return ""
    dest = local_path(product_key)
    url = public_url(product_key)
    if dest.exists():
        _remember(product_key, url, name)
        return url
    if not enabled():
        return ""
    remote = find_official_image(retailer, name, barcode)
    if not remote or not download_compress(remote, dest):
        return ""
    _remember(product_key, url, name)
    return url


def _remember(product_key: str, url: str, name: str) -> None:
    meta = purchases.get_product_meta(product_key) or {}
    purchases.upsert_product_meta(
        product_key,
        {
            "sku": meta.get("sku") or "",
            "category": meta.get("category") or "",
            "name": meta.get("official_name") or name,
            "image_url": url,
            "source": meta.get("source") or "receipt-image",
            "official_ean": meta.get("official_ean") or "",
        },
    )
    con = db.connect()
    con.execute(
        "UPDATE invoice_items SET image_url=? WHERE product_key=? AND ifnull(image_url,'')=''",
        (url, product_key),
    )
    con.commit()
    con.close()


def backfill_missing(*, retailer: str | None = None, limit: int = 0) -> dict[str, int]:
    con = db.connect()
    sql = """
        SELECT it.product_key, MAX(it.name) AS name, MAX(it.barcode) AS barcode, i.retailer
        FROM invoice_items it
        JOIN invoices i ON i.id = it.invoice_id
        LEFT JOIN product_meta pm ON pm.product_key = it.product_key
        WHERE ifnull(it.image_url,'')='' AND ifnull(pm.image_url,'')=''
    """
    args: list[Any] = []
    if retailer:
        sql += " AND i.retailer=?"
        args.append(retailer)
    sql += " GROUP BY it.product_key, i.retailer"
    rows = con.execute(sql, args).fetchall()
    con.close()
    ok = miss = 0
    for i, r in enumerate(rows):
        if limit and i >= limit:
            break
        got = attach_for_receipt(r["retailer"], r["product_key"], r["name"] or "", r["barcode"] or "")
        if got:
            ok += 1
        else:
            miss += 1
    return {"ok": ok, "miss": miss, "seen": len(rows)}
