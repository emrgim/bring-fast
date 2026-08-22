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
    # Magento catalog search page scrape-lite via search suggest if available
    s = _session()
    r = s.get(
        "https://www.grandiose.ae/catalogsearch/result/",
        params={"q": query},
        timeout=25,
    )
    text = r.text
    items = []
    # crude product card extraction
    import re

    for m in re.finditer(
        r'href="(https://www\.grandiose\.ae/[^"]+)"[^>]*>\s*<[^>]+alt="([^"]+)"',
        text,
    ):
        name = m.group(2)
        if name.lower() in ("add to wish list",):
            continue
        items.append({"id": m.group(1).rstrip("/").split("/")[-1], "name": name, "price": None, "currency": "AED", "url": m.group(1)})
        if len(items) >= limit:
            break
    # prices nearby
    prices = re.findall(r"AED\s*([0-9]+(?:\.[0-9]+)?)", text)
    for i, it in enumerate(items):
        if i < len(prices):
            try:
                it["price"] = float(prices[i])
            except ValueError:
                pass
    return {"retailer": "grandiose", "query": query, "results": items[:limit]}


def search_waitrose(query: str, limit: int = 8) -> dict[str, Any]:
    r = _session().get(
        "https://www.waitrose.ae/en/search/",
        params={"q": query},
        timeout=25,
        headers={"Accept": "text/html"},
    )
    import re

    items = []
    for m in re.finditer(
        r'href="(https://www\.waitrose\.ae/en/products/[^"]+)"[^>]*>[\s\S]{0,200}?>([^<]{3,120})</',
        r.text,
    ):
        items.append(
            {
                "id": m.group(1).rstrip("/").split("_")[-1].strip("/"),
                "name": m.group(2).strip(),
                "price": None,
                "currency": "AED",
                "url": m.group(1),
            }
        )
        if len(items) >= limit:
            break
    prices = re.findall(r"AED\s*([0-9]+(?:\.[0-9]+)?)", r.text)
    for i, it in enumerate(items):
        if i < len(prices):
            try:
                it["price"] = float(prices[i])
            except ValueError:
                pass
    return {"retailer": "waitrose", "query": query, "results": items}


def search_spinneys(query: str, limit: int = 8) -> dict[str, Any]:
    r = _session().get(
        "https://www.spinneys.com/en-ae/search/",
        params={"q": query},
        timeout=25,
        headers={"Accept": "text/html"},
    )
    import re

    items = []
    for m in re.finditer(r'href="(https://www\.spinneys\.com/en-ae/products/[^"]+)"', r.text):
        slug = m.group(1).rstrip("/").split("/")[-1]
        items.append(
            {
                "id": slug,
                "name": slug.replace("-", " "),
                "price": None,
                "currency": "AED",
                "url": m.group(1),
            }
        )
        if len(items) >= limit:
            break
    return {"retailer": "spinneys", "query": query, "results": items}


SEARCHERS = {
    "carrefour": search_carrefour,
    "grandiose": search_grandiose,
    "waitrose": search_waitrose,
    "spinneys": search_spinneys,
}


def search(retailer: str, query: str, limit: int = 8) -> dict[str, Any]:
    fn = SEARCHERS.get(retailer)
    if not fn:
        return {"error": f"unknown retailer {retailer}", "results": []}
    try:
        return fn(query, limit)
    except Exception as e:
        return {"retailer": retailer, "query": query, "results": [], "error": str(e)}
