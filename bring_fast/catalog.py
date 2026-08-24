from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json,text/html"})
    return s


def _ean13_check(digits12: str) -> str:
    total = 0
    for i, ch in enumerate(digits12):
        total += int(ch) * (1 if i % 2 == 0 else 3)
    return str((10 - (total % 10)) % 10)


def gtin_variants(code: str) -> list[str]:
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(digits) < 8:
        return []
    out = [digits]
    if len(digits) == 12:
        out.append(digits + _ean13_check(digits))
    if 8 <= len(digits) < 13:
        out.append(digits.zfill(13))
    return list(dict.fromkeys(out))


def lookup_carrefour_gtin(code: str) -> dict[str, Any] | None:
    """Official Carrefour catalog title for a receipt barcode. Does not change the till name."""
    sess = _session()
    for q in gtin_variants(code):
        try:
            r = sess.get(
                f"https://ac.cnstrc.com/search/{quote(q, safe='')}",
                params={
                    "key": "key_UzmQuiABmYtLGFME",
                    "c": "cio-python-bringfast-1.0",
                    "i": "bringfast",
                    "s": 1,
                    "page": 1,
                    "num_results_per_page": 5,
                },
                timeout=20,
            )
            r.raise_for_status()
        except requests.RequestException:
            continue
        for it in ((r.json().get("response") or {}).get("results") or []):
            d = it.get("data") or {}
            ean = "".join(ch for ch in str(d.get("ean") or "") if ch.isdigit())
            if ean and ean != q and not ean.startswith(q) and not q.startswith(ean[:12] if len(ean) >= 12 else ean):
                continue
            name = (it.get("value") or d.get("name") or "").strip()
            if not name:
                continue
            return {
                "sku": ean or q,
                "name": name,
                "image_url": d.get("image_url") or "",
                "source": "carrefour",
            }
    return None


def search_carrefour(query: str, limit: int = 8) -> dict[str, Any]:
    q = quote(query.strip(), safe="")
    url = f"https://ac.cnstrc.com/search/{q}"
    r = _session().get(
        url,
        params={
            "key": "key_UzmQuiABmYtLGFME",
            "c": "cio-python-bringfast-1.0",
            "i": "bringfast",
            "s": 1,
            "page": 1,
            "num_results_per_page": max(1, min(limit, 20)),
        },
        timeout=25,
    )
    r.raise_for_status()
    items = []
    for it in ((r.json().get("response") or {}).get("results") or []):
        d = it.get("data") or {}
        items.append(
            {
                "id": str(d.get("id") or d.get("code") or ""),
                "name": it.get("value") or d.get("name"),
                "price": d.get("price"),
                "currency": d.get("currency") or "AED",
                "url": "https://www.carrefouruae.com/mafuae/en" + (d.get("url") or f"/p/{d.get('id')}"),
            }
        )
    return {"retailer": "carrefour", "query": query, "results": items}


def search_grandiose(query: str, limit: int = 8) -> dict[str, Any]:
    from bring_fast.stores import grandiose as api

    return api.search(query, limit)


def search_mmi(query: str, limit: int = 8) -> dict[str, Any]:
    from bring_fast.stores import mmi as api

    return api.search(query, limit)


def search_africaneastern(query: str, limit: int = 8) -> dict[str, Any]:
    from bring_fast.stores import africaneastern as api

    return api.search(query, limit)


def _money(raw: Any) -> float | None:
    try:
        if raw is None or raw == "":
            return None
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_waitrose_html(html: str, limit: int = 8) -> list[dict[str, Any]]:
    import re

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in re.split(r'data-product-id="', html)[1:]:
        pid_m = re.match(r"(\d+)", block)
        pid = pid_m.group(1) if pid_m else ""
        href_m = re.search(r'href="(/en/products/[^"]+)"', block)
        name_m = re.search(r'href="/en/products/[^"]+"[^>]*>([^<]{2,160})</a>', block)
        if not name_m:
            name_m = re.search(r"<h5[^>]*>\s*<a[^>]*>([^<]{2,160})</a>", block)
        price_m = re.search(r"AED\s*([0-9]+(?:\.[0-9]+)?)", block)
        if not href_m or not name_m:
            continue
        href = href_m.group(1)
        if href in seen:
            continue
        seen.add(href)
        items.append(
            {
                "id": pid or href.rstrip("/").rsplit("_", 1)[-1].strip("/"),
                "name": re.sub(r"\s+", " ", name_m.group(1)).strip(),
                "price": _money(price_m.group(1) if price_m else None),
                "currency": "AED",
                "url": "https://www.waitrose.ae" + href,
            }
        )
        if len(items) >= limit:
            break
    return items


def search_waitrose(query: str, limit: int = 8) -> dict[str, Any]:
    r = _session().get(
        "https://www.waitrose.ae/en/search/",
        params={"q": query},
        timeout=25,
        headers={"Accept": "text/html"},
    )
    r.raise_for_status()
    items = parse_waitrose_html(r.text, limit)
    out: dict[str, Any] = {"retailer": "waitrose", "query": query, "results": items}
    if not items:
        out["error"] = "Waitrose search returned no products."
    return out


def parse_spinneys_html(html: str, limit: int = 8) -> list[dict[str, Any]]:
    import re

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in re.split(r'data-product-id="', html)[1:]:
        pid_m = re.match(r"(\d+)", block)
        pid = pid_m.group(1) if pid_m else ""
        href_m = re.search(r'href="(/en-ae/catalogue/[^"]+)"', block)
        name_m = re.search(r'class="product-name">\s*<a[^>]*>([^<]{2,200})</a>', block)
        if not name_m:
            name_m = re.search(r'<img[^>]+alt="([^"]{2,200})"', block)
        price_m = re.search(r'class="price">\s*([0-9]+(?:\.[0-9]+)?)', block)
        if not href_m:
            continue
        href = href_m.group(1)
        if href in seen:
            continue
        seen.add(href)
        name = re.sub(r"\s+", " ", (name_m.group(1) if name_m else href.rstrip("/").split("/")[-1])).strip()
        items.append(
            {
                "id": pid or href.rstrip("/").rsplit("_", 1)[-1].strip("/"),
                "name": name,
                "price": _money(price_m.group(1) if price_m else None),
                "currency": "AED",
                "url": "https://www.spinneys.com" + href,
            }
        )
        if len(items) >= limit:
            break
    return items


def search_spinneys(query: str, limit: int = 8) -> dict[str, Any]:
    r = _session().get(
        "https://www.spinneys.com/en-ae/search/",
        params={"q": query},
        timeout=25,
        headers={"Accept": "text/html"},
    )
    r.raise_for_status()
    items = parse_spinneys_html(r.text, limit)
    out: dict[str, Any] = {"retailer": "spinneys", "query": query, "results": items}
    if not items:
        out["error"] = "Spinneys search returned no products."
    return out


def _json_object_after(text: str, marker: str) -> dict[str, Any]:
    import json

    i = text.find(marker)
    if i < 0:
        raise ValueError(f"missing {marker}")
    start = text.find("{", i)
    depth = 0
    for k, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : k + 1])
    raise ValueError("unclosed json")


def search_unioncoop(query: str, limit: int = 8) -> dict[str, Any]:
    """Official storefront search is Algolia on unioncoop.ae (GraphQL is Varnish-blocked)."""
    s = _session()
    home = s.get("https://www.unioncoop.ae/", timeout=25)
    home.raise_for_status()
    cfg = _json_object_after(home.text, "algoliaConfig")
    app = cfg["applicationId"]
    key = cfg["apiKey"]
    index = f"{cfg['indexName']}_products"
    filters = ((cfg.get("attributeFilter") or {}).get("filters") or "").strip()
    payload: dict[str, Any] = {"query": query, "hitsPerPage": max(1, min(int(limit or 8), 20))}
    if filters:
        payload["filters"] = filters
    r = s.post(
        f"https://{app}-dsn.algolia.net/1/indexes/{index}/query",
        json=payload,
        timeout=25,
        headers={
            "X-Algolia-Application-Id": app,
            "X-Algolia-API-Key": key,
            "Content-Type": "application/json",
            "Referer": "https://www.unioncoop.ae/",
            "Origin": "https://www.unioncoop.ae",
        },
    )
    r.raise_for_status()
    items = []
    for hit in r.json().get("hits") or []:
        price = hit.get("price") or {}
        if isinstance(price, dict):
            aed = price.get("AED") or {}
            amount = aed.get("default") if isinstance(aed, dict) else None
        else:
            amount = price
        items.append(
            {
                "id": str(hit.get("sku") or hit.get("objectID") or ""),
                "name": hit.get("name"),
                "price": _money(amount if amount is not None else hit.get("regular_price")),
                "currency": "AED",
                "url": hit.get("url"),
            }
        )
    out: dict[str, Any] = {"retailer": "unioncoop", "query": query, "results": items}
    if not items:
        out["error"] = "Union Coop Algolia returned no products."
    return out


def _score_name(query: str, name: str) -> float:
    q = {t for t in (query or "").lower().split() if len(t) > 2}
    n = {t for t in (name or "").lower().split() if len(t) > 2}
    if not q or not n:
        return 0.0
    return len(q & n) / len(q)


def best_match(query: str, results: list[dict[str, Any]]) -> dict[str, Any] | None:
    ranked = []
    for it in results:
        if not isinstance(it, dict):
            continue
        ranked.append((_score_name(query, str(it.get("name") or "")), it))
    ranked.sort(key=lambda x: x[0], reverse=True)
    if not ranked or ranked[0][0] <= 0:
        return results[0] if results else None
    return ranked[0][1]


def compare_items(items: list[dict[str, Any]], targets: list[str] | None = None, limit: int = 3) -> dict[str, Any]:
    """Compare named cart lines against other store catalogs. Search only — no add."""
    ids = [r for r in SEARCHERS if r in (targets or list(SEARCHERS))]
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or it.get("id") or "").strip()
        if not name:
            continue
        line = {
            "name": name,
            "qty": int(it.get("qty") or 1),
            "source_price": it.get("price"),
            "source_id": it.get("id"),
            "matches": [],
        }
        for sid in ids:
            found = search(sid, name, max(1, min(int(limit or 3), 8)))
            hit = best_match(name, found.get("results") or [])
            line["matches"].append(
                {
                    "retailer": sid,
                    "name": (hit or {}).get("name"),
                    "price": (hit or {}).get("price"),
                    "currency": (hit or {}).get("currency") or "AED",
                    "url": (hit or {}).get("url"),
                    "id": (hit or {}).get("id"),
                    "error": found.get("error"),
                }
            )
        rows.append(line)
    return {
        "success": True,
        "note": "Comparison is catalog search only. Orders stay on Magento: Grandiose and Union Coop.",
        "items": rows,
    }


SEARCHERS = {
    "carrefour": search_carrefour,
    "grandiose": search_grandiose,
    "unioncoop": search_unioncoop,
    "waitrose": search_waitrose,
    "spinneys": search_spinneys,
    "mmi": search_mmi,
    "africaneastern": search_africaneastern,
}


def search(retailer: str, query: str, limit: int = 8) -> dict[str, Any]:
    fn = SEARCHERS.get(retailer)
    if not fn:
        return {"error": f"unknown retailer {retailer}", "results": []}
    try:
        return fn(query, limit)
    except Exception as e:
        return {"retailer": retailer, "query": query, "results": [], "error": str(e)}
