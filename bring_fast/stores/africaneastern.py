"""African + Eastern official Magento GraphQL. No Chrome, no local cart.

Login is License DXB: customerTokenForLicenseDXB on www.africaneasternonline.com.
Search is Magento products(). Cart/checkout stay off.
"""

from __future__ import annotations

from typing import Any

from bring_fast.stores.http import StoreAPIError, json_or_error, session

SITE = "https://www.africaneasternonline.com"
GRAPHQL = f"{SITE}/graphql"


def _client():
    s = session()
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    return s


def graphql(query: str, variables: dict[str, Any] | None = None, *, token: str = "") -> dict[str, Any]:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = _client().post(GRAPHQL, json={"query": query, "variables": variables or {}}, headers=headers, timeout=25)
    body = json_or_error(resp, "ae graphql")
    if not isinstance(body, dict):
        raise StoreAPIError("ae graphql: unexpected body", status=resp.status_code, body=body)
    if body.get("errors") and not body.get("data"):
        msg = body["errors"][0].get("message") if isinstance(body["errors"], list) else str(body["errors"])
        raise StoreAPIError(str(msg), status=resp.status_code, body=body)
    return body


def login(email: str, password: str) -> dict[str, Any]:
    ident = (email or "").strip()
    password = password or ""
    if not ident or not password:
        return {"ok": False, "token": "", "user_id": "", "error": "Missing African + Eastern email or password."}
    try:
        body = graphql(
            """
            mutation getAuthTokenDxb($email: String!, $password: String!) {
              customerTokenForLicenseDXB(email: $email, password: $password) { token }
            }
            """,
            {"email": ident, "password": password},
        )
    except StoreAPIError as e:
        return {"ok": False, "token": "", "user_id": "", "error": str(e)}
    token = ((((body.get("data") or {}).get("customerTokenForLicenseDXB") or {}).get("token")) or "").strip()
    if not token:
        err = body.get("errors") or []
        msg = err[0].get("message") if err and isinstance(err[0], dict) else "African + Eastern login failed."
        return {"ok": False, "token": "", "user_id": "", "error": str(msg)}
    user_id = ident
    try:
        me = graphql("query { customer { email firstname } }", token=token)
        cust = ((me.get("data") or {}).get("customer") or {})
        user_id = str(cust.get("email") or ident)
    except StoreAPIError:
        pass
    return {"ok": True, "token": token, "user_id": user_id, "error": None}


def search(query: str, limit: int = 8) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"retailer": "africaneastern", "query": query, "results": []}
    n = max(1, min(int(limit or 8), 20))
    try:
        body = graphql(
            """
            query ($q: String!, $n: Int!) {
              products(search: $q, pageSize: $n) {
                items {
                  sku name url_key
                  small_image { url }
                  price_range { minimum_price { regular_price { value currency } } }
                }
              }
            }
            """,
            {"q": q, "n": n},
        )
    except StoreAPIError as e:
        return {"retailer": "africaneastern", "query": query, "results": [], "error": str(e)}
    items = []
    for it in (((body.get("data") or {}).get("products") or {}).get("items") or []):
        if not isinstance(it, dict):
            continue
        sku = str(it.get("sku") or "")
        name = str(it.get("name") or "").strip()
        price = ((((it.get("price_range") or {}).get("minimum_price") or {}).get("regular_price") or {}).get("value"))
        img = ((it.get("small_image") or {}).get("url") or "")
        slug = it.get("url_key") or sku
        if not name or price is None:
            continue
        items.append(
            {
                "id": sku,
                "sku": sku,
                "ean": sku,
                "name": name,
                "price": float(price),
                "currency": "AED",
                "url": f"{SITE}/{slug}",
                "image_url": img,
            }
        )
    out: dict[str, Any] = {"retailer": "africaneastern", "query": query, "results": items}
    if not items:
        out["error"] = "African + Eastern search returned no products."
    return out


def product_by_sku(sku: str) -> dict[str, Any] | None:
    sku = (sku or "").strip()
    if not sku:
        return None
    try:
        body = graphql(
            """
            query ($sku: String!) {
              products(filter: {sku: {eq: $sku}}, pageSize: 1) {
                items {
                  sku name url_key
                  small_image { url }
                  price_range { minimum_price { regular_price { value } } }
                }
              }
            }
            """,
            {"sku": sku},
        )
    except StoreAPIError:
        return None
    items = (((body.get("data") or {}).get("products") or {}).get("items") or [])
    if not items:
        return None
    it = items[0]
    return {
        "sku": str(it.get("sku") or sku),
        "name": str(it.get("name") or ""),
        "image_url": ((it.get("small_image") or {}).get("url") or ""),
        "url": f"{SITE}/{it.get('url_key') or sku}",
        "price": ((((it.get("price_range") or {}).get("minimum_price") or {}).get("regular_price") or {}).get("value")),
    }
