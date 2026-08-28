"""Union Coop official Magento REST APIs.

The storefront is Magento 2 Luma (theme Ktpl/unioncoop) with Algolia search.
GraphQL on www.unioncoop.ae is Varnish-blocked (405), so cart/login/checkout
use Magento REST `/rest/V1`, not Grandiose's GraphQL (no GetNearestLocation,
no gagstore confirm, no cart_item_uid). Checkout prepares only; Magento REST
place (shipping-information + payment-information) is not wired, so there is
no action=place.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from bring_fast.stores.cart_match import match_cart_line, missing_line_error
from bring_fast.stores.http import StoreAPIError, json_or_error, session

SITE = "https://www.unioncoop.ae"
REST = f"{SITE}/rest/V1"
TOKEN_URL = f"{REST}/integration/customer/token"
CHECKOUT_URL = f"{SITE}/checkout/"
PACKAGE = "magento-rest"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = session()
        _CLIENT.headers.update({"User-Agent": UA, "Accept": "application/json,text/html"})
    return _CLIENT


def _headers(token: str = "") -> dict[str, str]:
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _request(method: str, url: str, *, token: str = "", json_body: Any = None, timeout: int = 20):
    resp = _client().request(method, url, json=json_body, headers=_headers(token), timeout=timeout)
    return resp


def _rest(method: str, path: str, *, token: str = "", json_body: Any = None) -> Any:
    resp = _request(method, REST + path, token=token, json_body=json_body)
    body = json_or_error(resp, f"unioncoop REST {path}")
    if resp.status_code == 404:
        msg = body.get("message") if isinstance(body, dict) else body
        raise StoreAPIError(str(msg or f"unioncoop REST {path}: HTTP 404"), status=404, body=body)
    if resp.status_code >= 400:
        msg = body.get("message") if isinstance(body, dict) else body
        raise StoreAPIError(str(msg or f"unioncoop REST HTTP {resp.status_code}"), status=resp.status_code, body=body)
    return body


def login(email: str, password: str) -> dict[str, Any]:
    if not email or not password:
        return {"ok": False, "token": "", "user_id": "", "error": "Missing Union Coop email or password."}
    try:
        _client().get(SITE, timeout=15)
        resp = _request("POST", TOKEN_URL, json_body={"username": email, "password": password})
        body = json_or_error(resp, "unioncoop login")
    except StoreAPIError as e:
        return {"ok": False, "token": "", "user_id": "", "error": str(e), "error_code": e.error_code}
    token = body if isinstance(body, str) else (body.get("token") if isinstance(body, dict) else "")
    token = (token or "").strip().strip('"')
    if resp.status_code >= 400 or not token:
        msg = body.get("message") if isinstance(body, dict) else ""
        return {"ok": False, "token": "", "user_id": "", "error": str(msg or f"Union Coop login HTTP {resp.status_code}")}
    user_id = email
    try:
        me = _rest("GET", "/customers/me", token=token)
        if isinstance(me, dict):
            user_id = str(me.get("id") or me.get("email") or email)
    except StoreAPIError:
        pass
    return {"ok": True, "token": token, "user_id": user_id, "error": None}


def parse_items(cart: dict[str, Any]) -> list[dict[str, Any]]:
    raw = cart.get("items") or []
    out = []
    for i in raw:
        if not isinstance(i, dict):
            continue
        sku = str(i.get("sku") or i.get("product_sku") or "")
        item_id = str(i.get("item_id") or i.get("itemId") or "")
        name = i.get("name") or sku
        qty = i.get("qty") if i.get("qty") is not None else i.get("quantity")
        try:
            qty_n = int(float(qty or 1))
        except (TypeError, ValueError):
            qty_n = 1
        price = i.get("price")
        if isinstance(price, dict):
            price = price.get("value")
        url = ""
        if sku:
            url = f"{SITE}/catalogsearch/result/?q={sku}"
        out.append(
            {
                "id": sku,
                "sku": sku,
                "item_id": item_id,
                "uid": "",
                "name": name,
                "qty": qty_n,
                "price": price,
                "currency": "AED",
                "url": url,
            }
        )
    return out


def customer_cart(token: str) -> dict[str, Any]:
    resp = _request("GET", REST + "/carts/mine", token=token)
    if resp.status_code == 404:
        _rest("POST", "/carts/mine", token=token)
        resp = _request("GET", REST + "/carts/mine", token=token)
    body = json_or_error(resp, "unioncoop carts/mine")
    if resp.status_code >= 400:
        msg = body.get("message") if isinstance(body, dict) else body
        raise StoreAPIError(str(msg or f"unioncoop carts/mine HTTP {resp.status_code}"), status=resp.status_code, body=body)
    if not isinstance(body, dict) or body.get("id") in (None, ""):
        raise StoreAPIError("Union Coop Magento cart missing.")
    return body


def add_item(*, token: str, sku: str, qty: int = 1) -> dict[str, Any]:
    if not sku:
        raise StoreAPIError("Union Coop add needs a Magento sku. Do not invent one.", status=400)
    cart = customer_cart(token)
    payload = {"cartItem": {"sku": sku, "qty": int(qty), "quote_id": str(cart["id"])}}
    _rest("POST", "/carts/mine/items", token=token, json_body=payload)
    return customer_cart(token)


def _still_in_cart(lines: list[dict[str, Any]], line: dict[str, Any]) -> bool:
    key = str(line.get("item_id") or "")
    sku = str(line.get("id") or "")
    for it in lines:
        if key and str(it.get("item_id") or "") == key:
            return True
        if sku and str(it.get("id") or "") == sku:
            return True
    return False


def remove_item(*, token: str, item_id: str = "", sku: str = "", name: str = "") -> dict[str, Any]:
    cart = customer_cart(token)
    lines = parse_items(cart)
    line = match_cart_line(lines, item_id=item_id, sku=sku, name=name)
    if not line:
        raise StoreAPIError(
            missing_line_error(name or sku or item_id, lines, store="Union Coop"),
            status=404,
        )
    magento_id = str(line.get("item_id") or "").strip()
    if not magento_id:
        raise StoreAPIError("Union Coop cart line has no Magento item_id.", status=400)
    _rest("DELETE", f"/carts/mine/items/{magento_id}", token=token)
    leftover = parse_items(customer_cart(token))
    if _still_in_cart(leftover, line):
        raise StoreAPIError(
            f"{line.get('name') or sku or item_id} was not removed from the official Union Coop cart.",
            status=502,
        )
    return customer_cart(token)


def update_item(*, token: str, line: dict[str, Any], qty: int) -> dict[str, Any]:
    cart = customer_cart(token)
    magento_id = str(line.get("item_id") or "").strip()
    if not magento_id:
        raise StoreAPIError("Union Coop cart line has no Magento item_id to update.", status=400)
    payload = {"cartItem": {"qty": int(qty), "quote_id": str(cart["id"])}}
    _rest("PUT", f"/carts/mine/items/{magento_id}", token=token, json_body=payload)
    return customer_cart(token)


def set_item(*, token: str, sku: str = "", qty: int = 1, name: str = "", item_id: str = "") -> dict[str, Any]:
    cart = customer_cart(token)
    line = match_cart_line(parse_items(cart), item_id=item_id, sku=sku, name=name)
    if line:
        if int(qty) <= 0:
            return remove_item(token=token, item_id=str(line.get("item_id") or ""), sku=str(line.get("id") or ""))
        return update_item(token=token, line=line, qty=int(qty))
    if not sku:
        raise StoreAPIError(
            missing_line_error(name or item_id, parse_items(cart), store="Union Coop"),
            status=404,
        )
    return add_item(token=token, sku=sku, qty=int(qty))


def clear_cart(*, token: str) -> dict[str, Any]:
    cart = customer_cart(token)
    for it in parse_items(cart):
        remove_item(
            token=token,
            item_id=str(it.get("item_id") or ""),
            sku=str(it.get("id") or ""),
            name=str(it.get("name") or ""),
        )
    emptied = customer_cart(token)
    leftover = parse_items(emptied)
    if leftover:
        raise StoreAPIError(
            "Union Coop cart was not emptied. Still has: "
            + ", ".join(str(it.get("name") or it.get("id")) for it in leftover)
            + ".",
            status=502,
        )
    return emptied


def _cart_payload(
    *,
    token: str,
    user_id: str,
    reused: bool,
    cart: dict[str, Any],
    error: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    live = parse_items(cart) if cart else []
    return {
        "ok": error is None,
        "official_count": len(live),
        "items": live,
        "logged_in": bool(token),
        "session_reused": reused,
        "driver": "magento-rest",
        "client": PACKAGE,
        "error": error,
        "error_code": error_code,
        "token": token,
        "user_id": user_id,
    }


def official_cart(
    *,
    email: str,
    password: str,
    action: str,
    items: list[dict[str, Any]],
    session_token: str = "",
    session_user: str = "",
) -> dict[str, Any]:
    """Official Union Coop Magento cart via REST. No Chrome, no local copy."""
    token, user_id = session_token, session_user
    reused = bool(token)
    if not reused:
        auth = login(email, password)
        if not auth["ok"]:
            return {
                "ok": False,
                "official_count": None,
                "items": [],
                "logged_in": False,
                "session_reused": False,
                "driver": "magento-rest",
                "client": PACKAGE,
                "error": auth.get("error") or "Union Coop Magento login failed.",
                "error_code": auth.get("error_code"),
                "token": "",
                "user_id": "",
            }
        token, user_id = auth["token"], auth["user_id"]
    try:
        if action == "clear":
            clear_cart(token=token)
        elif action == "remove":
            if not items:
                raise StoreAPIError("product_id, name, or item_id required to remove.", status=400)
            for it in items:
                remove_item(
                    token=token,
                    sku=str(it.get("id") or it.get("sku") or ""),
                    item_id=str(it.get("item_id") or ""),
                    name=str(it.get("name") or ""),
                )
        elif action == "set":
            for it in items:
                set_item(
                    token=token,
                    sku=str(it.get("id") or it.get("sku") or ""),
                    qty=int(it.get("qty") or 1),
                    name=str(it.get("name") or ""),
                    item_id=str(it.get("item_id") or ""),
                )
        elif action == "add":
            for it in items:
                add_item(token=token, sku=str(it.get("id") or it.get("sku") or ""), qty=int(it.get("qty") or 1))
        cart = customer_cart(token)
        return _cart_payload(token=token, user_id=user_id, reused=reused, cart=cart)
    except StoreAPIError as e:
        if reused and e.status in (401, 403) and email and password:
            return official_cart(
                email=email, password=password, action=action, items=items, session_token="", session_user=""
            )
        try:
            cart = customer_cart(token) if token else {}
        except StoreAPIError:
            cart = {}
        out = _cart_payload(
            token=token,
            user_id=user_id,
            reused=reused,
            cart=cart,
            error=str(e),
            error_code=getattr(e, "error_code", None),
        )
        out["ok"] = False
        return out


def customer_addresses(token: str) -> list[dict[str, Any]]:
    try:
        me = _rest("GET", "/customers/me", token=token)
    except StoreAPIError:
        return []
    rows = (me or {}).get("addresses") or []
    return [r for r in rows if isinstance(r, dict)]


def prepare_checkout(*, token: str) -> dict[str, Any]:
    """Read the official cart and point at Magento checkout. Does not place the order."""
    cart = customer_cart(token)
    items = parse_items(cart)
    if not items:
        raise StoreAPIError("Official Union Coop cart is empty.", status=409)
    addrs = customer_addresses(token)
    chosen = next((a for a in addrs if a.get("default_shipping")), addrs[0] if addrs else None)
    street = ""
    if chosen:
        raw = chosen.get("street") or []
        street = " ".join(raw) if isinstance(raw, list) else str(raw)
    delivery = " ".join(
        str(x)
        for x in (
            (chosen or {}).get("firstname"),
            (chosen or {}).get("lastname"),
            street,
            (chosen or {}).get("city"),
        )
        if x
    )
    total = cart.get("grand_total") or cart.get("base_grand_total")
    return {
        "ok": True,
        "stage": "checkout",
        "driver": "magento-rest",
        "checkout_url": CHECKOUT_URL,
        "final_url": CHECKOUT_URL,
        "payment_completed": False,
        "placed": False,
        "items": items,
        "delivery_address": delivery,
        "grand_total": total,
        "currency": "AED",
        "what_happens": (
            "Official Union Coop cart is ready. Payment stays on unioncoop.ae — "
            "no order is placed until you say so."
        ),
    }


def official_checkout(
    *,
    email: str,
    password: str,
    session_token: str = "",
    action: str = "prepare",
    payment_method: str = "",
) -> dict[str, Any]:
    a = (action or "prepare").strip().lower()
    if a in ("place", "order", "placeorder", "place_order"):
        return {
            "ok": False,
            "stage": "checkout",
            "driver": "magento-rest",
            "error": (
                "unioncoop_checkout prepares Magento REST checkout only. "
                "Placing the order is not wired (shipping and payment REST are not bound). "
                "Payment stays on unioncoop.ae."
            ),
            "checkout_url": CHECKOUT_URL,
            "payment_completed": False,
            "placed": False,
        }
    token = session_token
    if not token:
        auth = login(email, password)
        if not auth["ok"]:
            return {
                "ok": False,
                "stage": "login",
                "driver": "magento-rest",
                "error": auth.get("error") or "Union Coop login failed.",
                "checkout_url": CHECKOUT_URL,
                "payment_completed": False,
                "placed": False,
            }
        token = auth["token"]
    try:
        return prepare_checkout(token=token)
    except StoreAPIError as e:
        return {
            "ok": False,
            "stage": "checkout",
            "driver": "magento-rest",
            "error": str(e),
            "checkout_url": CHECKOUT_URL,
            "payment_completed": False,
            "placed": False,
        }


def _json_object_after(text: str, marker: str) -> dict[str, Any]:
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


def _money(raw: Any) -> float | None:
    try:
        if raw is None or raw == "":
            return None
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def algolia_hits_to_results(hits: list[Any]) -> list[dict[str, Any]]:
    items = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        price = hit.get("price") or {}
        if isinstance(price, dict):
            aed = price.get("AED") or {}
            amount = aed.get("default") if isinstance(aed, dict) else None
        else:
            amount = price
        sku = str(hit.get("sku") or hit.get("objectID") or "")
        if not sku:
            continue
        items.append(
            {
                "id": sku,
                "name": hit.get("name"),
                "price": _money(amount if amount is not None else hit.get("regular_price")),
                "currency": "AED",
                "url": hit.get("url"),
            }
        )
    return items


def search(query: str, limit: int = 8) -> dict[str, Any]:
    """Official storefront search is Algolia (Magento GraphQL is Varnish-blocked)."""
    q = (query or "").strip()
    if not q:
        return {"retailer": "unioncoop", "query": query, "results": []}
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json,text/html"})
    home = s.get(SITE + "/", timeout=25)
    home.raise_for_status()
    cfg = _json_object_after(home.text, "algoliaConfig")
    app = cfg["applicationId"]
    key = cfg["apiKey"]
    index = f"{cfg['indexName']}_products"
    filters = ((cfg.get("attributeFilter") or {}).get("filters") or "").strip()
    payload: dict[str, Any] = {"query": q, "hitsPerPage": max(1, min(int(limit or 8), 20))}
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
            "Referer": f"{SITE}/",
            "Origin": SITE,
        },
    )
    r.raise_for_status()
    items = algolia_hits_to_results(r.json().get("hits") or [])
    out: dict[str, Any] = {"retailer": "unioncoop", "query": query, "results": items, "driver": "algolia"}
    if not items:
        out["error"] = "Union Coop Algolia returned no products."
    return out
