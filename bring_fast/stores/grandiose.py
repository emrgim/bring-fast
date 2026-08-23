"""Grandiose official Magento APIs. No Chrome, no local cart.

Catalog `salable_qty` / `IN_STOCK` is the default Magento source and is not
store stock. Availability is the product page after the official delivery-area
confirm, then `getOutOfStockItems` on the customer cart.
"""

from __future__ import annotations

import re
from typing import Any

from bring_fast.stores.http import StoreAPIError, json_or_error, session

SITE = "https://www.grandiose.ae"
GRAPHQL = f"{SITE}/graphql"
TOKEN_URL = f"{SITE}/rest/V1/integration/customer/token"
CONFIRM_URL = f"{SITE}/gagstore/deliverymode/confirm/"
CHECKOUT_URL = f"{SITE}/checkout/"
PACKAGE = "net.grandiose.retail"
DELIVERY_NOTE = "Leave with security. Do not ring, call, or leave at the door."
# Element Meaisam / Dubai Production City — same point as Carrefour.
LAT = 25.0321285
LNG = 55.1912732

_CLIENT = None
_AREA: dict[str, Any] | None = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = session()
        _CLIENT.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36",
                "Accept": "application/json,text/html",
            }
        )
    return _CLIENT


def _headers(token: str = "") -> dict[str, str]:
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def login(email: str, password: str) -> dict[str, Any]:
    if not email or not password:
        return {"ok": False, "token": "", "user_id": "", "error": "Missing Grandiose email or password."}
    s = _client()
    try:
        s.get(SITE, timeout=15)
        resp = s.post(TOKEN_URL, json={"username": email, "password": password}, headers=_headers(), timeout=15)
        body = json_or_error(resp, "grandiose login")
    except StoreAPIError as e:
        return {"ok": False, "token": "", "user_id": "", "error": str(e)}
    token = body if isinstance(body, str) else (body.get("token") if isinstance(body, dict) else "")
    token = (token or "").strip().strip('"')
    if resp.status_code >= 400 or not token:
        msg = body.get("message") if isinstance(body, dict) else ""
        return {"ok": False, "token": "", "user_id": "", "error": str(msg or f"Grandiose login HTTP {resp.status_code}")}
    user_id = email
    try:
        data = graphql(token, "query { customer { id email } }")
        cust = ((data.get("data") or {}).get("customer") or {})
        user_id = str(cust.get("id") or cust.get("email") or email)
    except StoreAPIError:
        pass
    try:
        ensure_delivery_area()
    except StoreAPIError:
        pass
    return {"ok": True, "token": token, "user_id": user_id, "error": None}


def graphql(token: str, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = _client().post(GRAPHQL, json=payload, headers=_headers(token), timeout=20)
    body = json_or_error(resp, "grandiose graphql")
    if not isinstance(body, dict):
        raise StoreAPIError("grandiose graphql: unexpected body", status=resp.status_code, body=body)
    errs = body.get("errors")
    if errs:
        raise StoreAPIError(
            str(errs[0].get("message") if isinstance(errs[0], dict) else errs[0]),
            status=resp.status_code,
            body=body,
        )
    return body


def ensure_delivery_area() -> dict[str, Any]:
    """Bind home-delivery using only Grandiose GraphQL + confirm. No invented area IDs."""
    global _AREA
    if _AREA:
        return _AREA
    s = _client()
    s.get(SITE, timeout=15)
    nearest = graphql(
        "",
        """
        query($la: String!, $lo: String!) {
          GetNearestLocation(latitude: $la, longitude: $lo) {
            area_id area_name cluster_code emirates_id emirates_name inventory_source_code zone_id
          }
        }
        """,
        {"la": str(LAT), "lo": str(LNG)},
    )
    rows = ((nearest.get("data") or {}).get("GetNearestLocation") or [])
    if not rows:
        raise StoreAPIError("Grandiose GetNearestLocation returned no area for this point.")
    loc = rows[0]
    area_id = str(loc.get("area_id") or "").strip()
    area_name = str(loc.get("area_name") or "").strip()
    emirates_id = str(loc.get("emirates_id") or "").strip()
    emirates_name = str(loc.get("emirates_name") or "").strip()
    source = str(loc.get("inventory_source_code") or "").strip()
    if not area_id or not emirates_id:
        raise StoreAPIError(f"Grandiose GetNearestLocation incomplete: {loc}")
    zone = graphql(
        "",
        """
        query($la: Float!, $lo: Float!) {
          checkLocationInZone(latitude: $la, longitude: $lo) {
            area_id area_name inventory_source_code message
          }
        }
        """,
        {"la": LAT, "lo": LNG},
    )
    zloc = ((zone.get("data") or {}).get("checkLocationInZone") or {})
    if zloc.get("area_id"):
        area_id = str(zloc["area_id"])
    if zloc.get("area_name"):
        area_name = str(zloc["area_name"])
    if zloc.get("inventory_source_code"):
        source = str(zloc["inventory_source_code"])
    resp = s.post(
        CONFIRM_URL,
        data={
            "shipping_mode": "home_delivery",
            "store": "",
            "store_name": "",
            "delivery_emirates": emirates_id,
            "delivery_emirates_name": emirates_name,
            "delivery_area": area_id,
            "delivery_area_name": area_name,
        },
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{SITE}/", "Accept": "application/json"},
        timeout=15,
    )
    try:
        confirm = resp.json()
    except Exception as e:
        raise StoreAPIError(f"Grandiose confirm did not return JSON: {e}") from e
    if str(confirm.get("error")) not in ("false", "False", "0"):
        raise StoreAPIError(f"Grandiose confirm failed: {confirm}")
    cookie_src = ""
    try:
        cookie_src = s.cookies.get("cart_inventory_source") or ""
    except Exception:
        cookie_src = ""
    _AREA = {
        "area_id": area_id,
        "area_name": area_name,
        "emirates_id": emirates_id,
        "emirates_name": emirates_name,
        "inventory_source": cookie_src or source,
    }
    return _AREA


def _product_url(url_key: str, sku: str) -> str:
    if url_key:
        return f"{SITE}/{url_key}.html"
    return f"{SITE}/catalogsearch/result/?q={sku}"


def _pdp_in_stock(entity_id: str | int) -> bool | None:
    """Website product page after delivery-area cookies. None if the page cannot be read."""
    ensure_delivery_area()
    resp = _client().get(f"{SITE}/catalog/product/view/id/{entity_id}", timeout=20)
    html = resp.text or ""
    if resp.status_code >= 400 or len(html) < 500:
        return None
    if re.search(r"out of stock", html, re.I):
        return False
    if re.search(r"product-addtocart-button|id=\"product-addtocart-button\"|>Add to Cart<", html, re.I):
        return True
    return None


def availability(sku: str, qty: int = 1) -> dict[str, Any]:
    ensure_delivery_area()
    data = graphql(
        "",
        """
        query One($sku: String!) {
          products(filter: { sku: { eq: $sku } }) {
            items { id sku name url_key }
          }
        }
        """,
        {"sku": sku},
    )
    items = (((data.get("data") or {}).get("products") or {}).get("items") or [])
    if not items:
        return {"sku": sku, "available": False, "error": "Product not found.", "source": "pdp"}
    it = items[0]
    entity_id = it.get("id")
    in_stock = _pdp_in_stock(entity_id) if entity_id is not None else None
    return {
        "sku": str(it.get("sku") or sku),
        "name": it.get("name") or sku,
        "entity_id": entity_id,
        "available": in_stock is True,
        "pdp_checked": in_stock is not None,
        "area": (_AREA or {}).get("area_name"),
        "inventory_source": (_AREA or {}).get("inventory_source"),
        "url": _product_url(str(it.get("url_key") or ""), sku),
        "source": "pdp",
    }


def search(query: str, limit: int = 8) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"retailer": "grandiose", "query": query, "results": []}
    ensure_delivery_area()
    try:
        data = graphql(
            "",
            """
            query Search($q: String!, $n: Int!) {
              products(search: $q, pageSize: $n) {
                items {
                  id
                  sku
                  name
                  url_key
                  canonical_url
                  price_range { minimum_price { regular_price { value currency } } }
                }
              }
            }
            """,
            {"q": q, "n": max(1, min(int(limit or 8), 20))},
        )
    except StoreAPIError:
        data = graphql(
            "",
            """
            query Search($q: String!, $n: Int!) {
              products(search: $q, pageSize: $n) {
                items {
                  id
                  sku
                  name
                  url_key
                  price_range { minimum_price { regular_price { value currency } } }
                }
              }
            }
            """,
            {"q": q, "n": max(1, min(int(limit or 8), 20))},
        )
    items = (((data.get("data") or {}).get("products") or {}).get("items") or [])
    results = []
    for it in items:
        if not isinstance(it, dict):
            continue
        sku = str(it.get("sku") or "")
        price = ((((it.get("price_range") or {}).get("minimum_price") or {}).get("regular_price") or {}).get("value"))
        currency = ((((it.get("price_range") or {}).get("minimum_price") or {}).get("regular_price") or {}).get("currency")) or "AED"
        entity_id = it.get("id")
        in_stock = _pdp_in_stock(entity_id) if entity_id is not None else None
        canon = it.get("canonical_url") or ""
        results.append(
            {
                "id": sku,
                "sku": sku,
                "name": it.get("name") or sku,
                "price": price,
                "currency": currency,
                "url": canon if str(canon).startswith("http") else _product_url(str(it.get("url_key") or ""), sku),
                "available": in_stock is True,
                "area": (_AREA or {}).get("area_name"),
                "inventory_source": (_AREA or {}).get("inventory_source"),
            }
        )
    return {
        "retailer": "grandiose",
        "query": query,
        "results": results,
        "driver": "magento-pdp",
        "area": (_AREA or {}).get("area_name"),
        "inventory_source": (_AREA or {}).get("inventory_source"),
        "note": "available is the official product page for the selected delivery area. Catalog salable_qty is not used.",
    }


def parse_items(cart: dict[str, Any]) -> list[dict[str, Any]]:
    raw = cart.get("items") or []
    out = []
    for i in raw:
        if not isinstance(i, dict):
            continue
        product = i.get("product") or {}
        sku = str(product.get("sku") or i.get("sku") or "")
        price = None
        prices = i.get("prices") or {}
        if isinstance(prices, dict):
            price = (prices.get("price") or {}).get("value") or (prices.get("row_total") or {}).get("value")
        out.append(
            {
                "id": sku,
                "item_id": str(i.get("id") or i.get("item_id") or ""),
                "name": product.get("name") or i.get("name") or sku,
                "qty": int(i.get("quantity") or i.get("qty") or 1),
                "price": price,
                "currency": "AED",
                "url": _product_url(str(product.get("url_key") or ""), sku),
            }
        )
    return out


def _cart_query() -> str:
    return """
    query {
      customerCart {
        id
        total_quantity
        items {
          id
          quantity
          prices { price { value currency } row_total { value currency } }
          product { sku name url_key }
        }
        prices { grand_total { value currency } }
      }
    }
    """


def customer_cart(token: str) -> dict[str, Any]:
    data = graphql(token, _cart_query())
    cart = ((data.get("data") or {}).get("customerCart") or {})
    if not isinstance(cart, dict) or not cart.get("id"):
        raise StoreAPIError("Grandiose customerCart missing.")
    return cart


def out_of_stock_skus(token: str, cart_id: str) -> set[str]:
    data = graphql(
        token,
        """
        query($c: String!) {
          getOutOfStockItems(cart_id: $c) {
            oosItems { product { sku name } }
          }
        }
        """,
        {"c": cart_id},
    )
    raw = (((data.get("data") or {}).get("getOutOfStockItems") or {}).get("oosItems") or [])
    skus = set()
    for it in raw:
        if isinstance(it, dict):
            sku = str(((it.get("product") or {}).get("sku") or ""))
            if sku:
                skus.add(sku)
    return skus


def add_item(*, token: str, sku: str, qty: int = 1) -> dict[str, Any]:
    check = availability(sku, qty)
    if not check.get("available"):
        raise StoreAPIError(
            f"{check.get('name') or sku} is out of stock for delivery to {check.get('area') or 'your area'} "
            f"(source {check.get('inventory_source') or 'unknown'}).",
            status=409,
            body=check,
        )
    cart = customer_cart(token)
    data = graphql(
        token,
        """
        mutation Add($cartId: String!, $sku: String!, $qty: Float!) {
          addProductsToCart(cartId: $cartId, cartItems: [{ sku: $sku, quantity: $qty }]) {
            cart {
              id
              total_quantity
              items {
                id quantity
                prices { price { value currency } row_total { value currency } }
                product { sku name url_key }
              }
              prices { grand_total { value currency } }
            }
            user_errors { code message }
          }
        }
        """,
        {"cartId": cart["id"], "sku": sku, "qty": float(qty)},
    )
    payload = ((data.get("data") or {}).get("addProductsToCart") or {})
    errors = payload.get("user_errors") or []
    if errors:
        raise StoreAPIError(str(errors[0].get("message") or errors[0]), status=400, body=errors)
    added = payload.get("cart") or customer_cart(token)
    oos = out_of_stock_skus(token, str(added.get("id") or cart["id"]))
    if sku in oos:
        remove_item(token=token, sku=sku)
        raise StoreAPIError(
            f"{check.get('name') or sku} is in Magento getOutOfStockItems for this cart.",
            status=409,
            body={"sku": sku, "oos": sorted(oos)},
        )
    return added


def remove_item(*, token: str, item_id: str = "", sku: str = "") -> dict[str, Any]:
    cart = customer_cart(token)
    cid = cart["id"]
    if not item_id and sku:
        for it in parse_items(cart):
            if it["id"] == sku:
                item_id = it.get("item_id") or ""
                break
    if not item_id:
        return cart
    data = graphql(
        token,
        """
        mutation Rm($cartId: String!, $itemId: Int!) {
          removeItemFromCart(input: { cart_id: $cartId, cart_item_id: $itemId }) {
            cart { id total_quantity items { id quantity product { sku name } } }
          }
        }
        """,
        {"cartId": cid, "itemId": int(item_id)},
    )
    return ((data.get("data") or {}).get("removeItemFromCart") or {}).get("cart") or {}


def clear_cart(*, token: str) -> dict[str, Any]:
    cart = customer_cart(token)
    for it in parse_items(cart):
        if it.get("item_id"):
            remove_item(token=token, item_id=it["item_id"])
    return customer_cart(token)


def official_cart(
    *,
    email: str,
    password: str,
    action: str,
    items: list[dict[str, Any]],
    session_token: str = "",
    session_user: str = "",
) -> dict[str, Any]:
    """Official Grandiose Magento cart. No Chrome, no local copy."""
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
                "driver": "magento",
                "client": PACKAGE,
                "error": auth.get("error") or "Grandiose Magento login failed.",
                "token": "",
                "user_id": "",
            }
        token, user_id = auth["token"], auth["user_id"]
    try:
        ensure_delivery_area()
        if action == "clear":
            clear_cart(token=token)
        elif action == "remove":
            for it in items:
                remove_item(token=token, sku=str(it.get("id") or ""), item_id=str(it.get("item_id") or ""))
        elif action in ("add", "set"):
            for it in items:
                add_item(token=token, sku=str(it.get("id") or ""), qty=int(it.get("qty") or 1))
        cart = customer_cart(token)
        live = parse_items(cart)
        oos = out_of_stock_skus(token, str(cart.get("id") or ""))
        for row in live:
            row["available"] = row["id"] not in oos
        return {
            "ok": True,
            "official_count": len(live),
            "items": live,
            "logged_in": True,
            "session_reused": reused,
            "driver": "magento",
            "client": PACKAGE,
            "error": None,
            "token": token,
            "user_id": user_id,
            "area": (_AREA or {}).get("area_name"),
            "inventory_source": (_AREA or {}).get("inventory_source"),
        }
    except StoreAPIError as e:
        if reused and e.status in (401, 403) and email and password:
            return official_cart(
                email=email, password=password, action=action, items=items, session_token="", session_user=""
            )
        return {
            "ok": False,
            "official_count": None,
            "items": [],
            "logged_in": bool(token),
            "session_reused": reused,
            "driver": "magento",
            "client": PACKAGE,
            "error": str(e),
            "token": token,
            "user_id": user_id,
            "area": (_AREA or {}).get("area_name"),
            "inventory_source": (_AREA or {}).get("inventory_source"),
        }


def customer_addresses(token: str) -> list[dict[str, Any]]:
    data = graphql(
        token,
        """
        query {
          customer {
            firstname lastname email
            addresses {
              id firstname lastname street city postcode country_code telephone
              default_shipping default_billing
              region { region region_code }
            }
          }
        }
        """,
    )
    cust = ((data.get("data") or {}).get("customer") or {})
    rows = cust.get("addresses") or []
    return [r for r in rows if isinstance(r, dict)]


def prepare_checkout(*, token: str, address_id: int | None = None) -> dict[str, Any]:
    """Bind official customer address + Home Delivery. Does not place the order."""
    ensure_delivery_area()
    cart = customer_cart(token)
    cid = str(cart["id"])
    items = parse_items(cart)
    if not items:
        raise StoreAPIError("Official Grandiose cart is empty.", status=409)
    addrs = customer_addresses(token)
    chosen = None
    if address_id is not None:
        chosen = next((a for a in addrs if int(a.get("id") or 0) == int(address_id)), None)
    if chosen is None:
        chosen = next((a for a in addrs if a.get("default_shipping")), addrs[0] if addrs else None)
    if not chosen:
        raise StoreAPIError("No saved Grandiose address on the customer account.", status=409)
    aid = int(chosen["id"])
    graphql(
        token,
        """
        mutation($c: String!, $a: Int!) {
          setShippingAddressesOnCart(input: { cart_id: $c, shipping_addresses: [{ customer_address_id: $a }] }) {
            cart { id }
          }
        }
        """,
        {"c": cid, "a": aid},
    )
    graphql(
        token,
        """
        mutation($c: String!, $a: Int!) {
          setBillingAddressOnCart(input: { cart_id: $c, billing_address: { customer_address_id: $a } }) {
            cart { id }
          }
        }
        """,
        {"c": cid, "a": aid},
    )
    graphql(
        token,
        """
        mutation($c: String!) {
          setShippingMethodsOnCart(
            input: { cart_id: $c, shipping_methods: [{ carrier_code: "tablerate", method_code: "bestway" }] }
          ) { cart { id } }
        }
        """,
        {"c": cid},
    )
    try:
        graphql(
            token,
            """
            mutation($n: String!) {
              setDeliveryInstructions(input: { instructions: $n })
            }
            """,
            {"n": DELIVERY_NOTE},
        )
    except StoreAPIError:
        pass
    ready = graphql(
        token,
        """
        query($c: String!) {
          cart(cart_id: $c) {
            id
            email
            total_quantity
            available_payment_methods { code title }
            selected_payment_method { code title }
            prices { grand_total { value currency } }
            shipping_addresses {
              firstname lastname street city
              selected_shipping_method { carrier_code method_code method_title amount { value currency } }
              available_shipping_methods { carrier_code method_code method_title amount { value currency } }
            }
            billing_address { firstname lastname street city }
            items {
              id quantity
              prices { price { value currency } row_total { value currency } }
              product { sku name url_key }
            }
          }
        }
        """,
        {"c": cid},
    )
    live = ((ready.get("data") or {}).get("cart") or {})
    payments = live.get("available_payment_methods") or []
    ship = (live.get("shipping_addresses") or [{}])[0]
    total = ((live.get("prices") or {}).get("grand_total") or {})
    pay_txt = ", ".join(f"{p.get('title')} ({p.get('code')})" for p in payments)
    return {
        "ok": True,
        "stage": "payment",
        "driver": "magento",
        "checkout_url": CHECKOUT_URL,
        "final_url": CHECKOUT_URL,
        "payment_completed": False,
        "placed": False,
        "items": parse_items(live),
        "delivery_address": " ".join(
            str(x)
            for x in (
                chosen.get("firstname"),
                chosen.get("lastname"),
                " ".join(chosen.get("street") or []),
                chosen.get("city"),
            )
            if x
        ),
        "delivery_instruction": DELIVERY_NOTE,
        "shipping_method": (ship.get("selected_shipping_method") or {}),
        "shipping_methods": ship.get("available_shipping_methods") or [],
        "payment_methods": payments,
        "grand_total": total.get("value"),
        "currency": total.get("currency") or "AED",
        "area": (_AREA or {}).get("area_name"),
        "what_happens": (
            "Official Grandiose cart is ready to pay. Methods on the account: "
            + pay_txt
            + ". Payment stays on grandiose.ae — no order is placed until you say so."
        ),
    }


def official_checkout(*, email: str, password: str, session_token: str = "") -> dict[str, Any]:
    token = session_token
    if not token:
        auth = login(email, password)
        if not auth["ok"]:
            return {
                "ok": False,
                "stage": "login",
                "driver": "magento",
                "error": auth.get("error") or "Grandiose login failed.",
                "checkout_url": CHECKOUT_URL,
            }
        token = auth["token"]
    try:
        return prepare_checkout(token=token)
    except StoreAPIError as e:
        return {
            "ok": False,
            "stage": "checkout",
            "driver": "magento",
            "error": str(e),
            "checkout_url": CHECKOUT_URL,
        }
