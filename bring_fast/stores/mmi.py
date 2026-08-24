"""MMI Home Delivery official Retter API. No Chrome, no local cart.

Web + app (`com.mmiuae.app`) talk to `api.mmiprod.retter.io` project `o9pki8qf`.
Login is `CALL/User/signin` with `channel-id: mmiDubai`.
Catalog is `CALL/ProductManager/searchProductsV2/default` (GET, base64 body).
"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import urlencode

from bring_fast.stores.http import StoreAPIError, json_or_error, session

SITE = "https://www.mmihomedelivery.ae"
API = "https://api.mmiprod.retter.io/o9pki8qf"
CHANNEL = "mmiDubai"
SIGNIN = f"{API}/CALL/User/loginWithDxbLicense"
SEARCH = f"{API}/CALL/ProductManager/searchProductsV2/default"
IMAGE = "https://cdn.mmielr.com/5p1hp1d2t/CALL/PIMAPI/getImage/jux9er4tf08?filename="


def _client():
    s = session()
    s.headers.update(
        {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "channel-id": CHANNEL,
            "client-type": "web",
        }
    )
    return s


def _sort(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sort(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_sort(v) for v in value]
    return value


def _field(raw: Any) -> str:
    if isinstance(raw, list):
        return _field(raw[0]) if raw else ""
    if isinstance(raw, dict):
        val = raw.get("value")
        if isinstance(val, list):
            return _field(val[0]) if val else ""
        return str(val or "")
    return str(raw or "")


def _money(price: dict[str, Any] | None) -> float | None:
    if not isinstance(price, dict):
        return None
    for key in ("salePrice", "listPrice", "officialSalePrice", "officialListPrice"):
        raw = price.get(key)
        try:
            if raw is None:
                continue
            n = float(raw)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return round(n / 100000.0, 2)
    return None


def _tokens(body: Any) -> dict[str, str]:
    blob = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else body
    if not isinstance(blob, dict):
        return {"token": "", "user_id": ""}
    token = str(blob.get("token") or blob.get("accessToken") or blob.get("access_token") or "").strip()
    user = blob.get("user")
    if not isinstance(user, dict):
        user = {}
    user_id = str(blob.get("userId") or blob.get("uid") or blob.get("user_id") or user.get("customerId") or "").strip()
    return {"token": token, "user_id": user_id}


def login(email: str, password: str) -> dict[str, Any]:
    ident = (email or "").strip()
    password = password or ""
    if not ident or not password:
        return {"ok": False, "token": "", "user_id": "", "error": "Missing MMI email/phone or password."}
    payload = {"password": password}
    if "@" in ident:
        payload["email"] = ident
    else:
        payload["phone"] = ident
    try:
        resp = _client().post(SIGNIN, json=payload, timeout=20)
        body = json_or_error(resp, "mmi login")
    except StoreAPIError as e:
        return {"ok": False, "token": "", "user_id": "", "error": str(e)}
    if isinstance(body, dict) and body.get("message") and resp.status_code >= 400:
        return {"ok": False, "token": "", "user_id": "", "error": str(body.get("message"))}
    creds = _tokens(body)
    if resp.status_code >= 400 or not creds["token"]:
        msg = body.get("message") if isinstance(body, dict) else ""
        return {"ok": False, "token": "", "user_id": "", "error": str(msg or f"MMI login HTTP {resp.status_code}")}
    try:
        exchanged = _client().post(f"{API}/TOKEN/auth", json={"customToken": creds["token"]}, timeout=20)
        tok_body = exchanged.json() if exchanged.status_code < 500 else {}
    except Exception:
        tok_body = {}
    access = ""
    if isinstance(tok_body, dict):
        nested = tok_body.get("data")
        if isinstance(nested, dict) and nested.get("accessToken"):
            access = str(nested.get("accessToken") or "").strip()
        else:
            access = str(tok_body.get("accessToken") or "").strip()
    if not access:
        return {"ok": False, "token": "", "user_id": creds["user_id"], "error": "MMI session token was not issued."}
    return {"ok": True, "token": access, "user_id": creds["user_id"] or ident, "error": None}


def search(query: str, limit: int = 8) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"retailer": "mmi", "query": query, "results": []}
    n = max(1, min(int(limit or 8), 20))
    payload = {"searchTerm": q, "sorting": "-stock", "pageSize": n}
    encoded = base64.b64encode(json.dumps(_sort(payload), separators=(",", ":")).encode()).decode()
    url = SEARCH + "?" + urlencode({"data": encoded, "__isbase64": "true"})
    try:
        resp = _client().get(url, timeout=25)
        body = json_or_error(resp, "mmi search")
    except StoreAPIError as e:
        return {"retailer": "mmi", "query": query, "results": [], "error": str(e)}
    items = []
    for hit in body.get("result") or []:
        if not isinstance(hit, dict):
            continue
        product = hit.get("product")
        if not isinstance(product, dict):
            product = {}
        name = _field(product.get("title")) or _field(product.get("erpName"))
        sku = str(product.get("sku") or hit.get("_id") or "")
        slug = _field(product.get("slug")) or _field(product.get("_slug"))
        url_path = f"{SITE}/product/{slug}" if slug else f"{SITE}/product/{sku}"
        price = _money(hit.get("price") if isinstance(hit.get("price"), dict) else None)
        if not name or price is None:
            continue
        items.append(
            {
                "id": sku,
                "sku": sku,
                "ean": _field(product.get("erpBottleBarcode")),
                "name": name,
                "price": price,
                "currency": "AED",
                "url": url_path,
                "image_url": (IMAGE + _field(product.get("images"))) if _field(product.get("images")) else "",
            }
        )
        if len(items) >= n:
            break
    out: dict[str, Any] = {"retailer": "mmi", "query": query, "results": items}
    if not items:
        out["error"] = "MMI search returned no products."
    return out
