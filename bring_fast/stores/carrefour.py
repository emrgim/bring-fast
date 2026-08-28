"""Carrefour UAE official cart via curl_cffi Chrome impersonation.

TLS, HTTP/1.1, and User-Agent are Chrome's. MAF JSON headers stay on API
calls only — never override User-Agent (Akamai 403 if TLS Chrome + okhttp).
"""

from __future__ import annotations

import uuid
from typing import Any

from bring_fast.stores.http import StoreAPIError, is_akamai_shell, json_or_error, session

# Same Apigee surface the Play Store app uses (RetailSSO + site gateway).
API_HOSTS = (
    "https://www.carrefouruae.com",
    "https://api-prod.retailsso.com",
)
SITE = "https://www.carrefouruae.com"
MARKET = "mafuae"
LANG = "en"
APP_ID = "Android"
APP_VERSION = "26.8.21"
PACKAGE = "com.aswat.carrefouruae"
LAT = 25.0321285
LNG = 55.1912732


def _device_id() -> str:
    return "bf-" + uuid.uuid5(uuid.NAMESPACE_DNS, "bring-fast.android.mafuae").hex[:16]


CHROME_IMPERSONATE = ("chrome", "chrome131", "chrome124")


def _new_impersonate(name: str):
    from curl_cffi import requests as cf
    from curl_cffi.const import CurlHttpVersion

    return cf.Session(impersonate=name, http_version=CurlHttpVersion.V1_1)


def chrome_session(existing=None):
    """One Chrome client. GET the homepage first so Akamai cookies (_abck, bm_sz) stick."""
    if existing is not None:
        return existing

    def warmed(s) -> bool:
        try:
            resp = s.get(f"{SITE}/{MARKET}/{LANG}", timeout=12)
            text = getattr(resp, "text", "") or ""
        except Exception:
            return False
        return not is_akamai_shell(text)

    s = session()
    if warmed(s):
        return s
    for name in CHROME_IMPERSONATE:
        try:
            alt = _new_impersonate(name)
        except Exception:
            continue
        if warmed(alt):
            return alt
    return s


def android_headers(*, token: str = "", user_id: str = "") -> dict[str, str]:
    """JSON/API headers only. Do not set User-Agent — curl_cffi impersonate owns it."""
    h = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "appid": APP_ID,
        "env": "prod",
        "storeid": MARKET,
        "lang": LANG,
        "langCode": LANG,
        "x-maf-appId": APP_ID,
        "x-maf-storeId": MARKET,
        "x-maf-lang": LANG,
        "x-maf-env": "prod",
        "x-maf-tenant": MARKET,
        "x-maf-deviceId": _device_id(),
        "x-maf-appVersion": APP_VERSION,
        "x-maf-requestId": str(uuid.uuid4()),
        "latitude": str(LAT),
        "longitude": str(LNG),
    }
    if user_id:
        h["userId"] = str(user_id)
        h["x-maf-account"] = str(user_id)
    if token:
        raw = token[7:].strip() if str(token).lower().startswith("bearer ") else str(token)
        h["Authorization"] = f"Bearer {raw}"
        h["token"] = raw
    return h


def _extract_session(body: Any, resp) -> dict[str, str]:
    token = (
        resp.headers.get("authorization")
        or resp.headers.get("Authorization")
        or resp.headers.get("token")
        or ""
    )
    user_id = resp.headers.get("userid") or resp.headers.get("userId") or ""
    data = body.get("data") if isinstance(body, dict) else {}
    if not isinstance(data, dict):
        data = body if isinstance(body, dict) else {}
    token = token or data.get("token") or data.get("accessToken") or data.get("access_token") or ""
    user_id = user_id or data.get("userId") or data.get("user_id") or data.get("id") or data.get("customerId") or ""
    cust = data.get("customer") if isinstance(data, dict) and isinstance(data.get("customer"), dict) else {}
    user_id = user_id or (cust.get("id") if cust else "") or (cust.get("userId") if cust else "") or ""
    if isinstance(token, str) and token.lower().startswith("bearer "):
        token = token[7:].strip()
    return {"token": str(token or ""), "user_id": str(user_id or "")}


AKAMAI_UNREAD = (
    "Carrefour blocked the HTTP API from this server (Akamai). "
    "The saved store login is still present. Official cart unread."
)


def _is_akamai(err: StoreAPIError) -> bool:
    return err.status == 403 or "akamai" in str(err).lower() or "access denied" in str(err).lower()


def _is_invalid_auth_token(err: StoreAPIError) -> bool:
    """Password login is allowed only on HTTP 401 JSON (expired token), never Akamai HTML."""
    if err.status != 401 or _is_akamai(err):
        return False
    return isinstance(err.body, dict)


def login(email: str, password: str, *, client=None) -> dict[str, Any]:
    """One POST to /v2/customers/login on the warmed Chrome session. No other grant URLs."""
    if not email or not password:
        return {"ok": False, "token": "", "user_id": "", "error": "Missing Carrefour email or password."}
    s = chrome_session(client)
    try:
        s.get(f"{SITE}/{MARKET}/{LANG}/login", timeout=8)
    except Exception:
        pass
    headers = android_headers()
    url = f"{SITE}/v2/customers/login"
    payload = {"email": email, "password": password, "langCode": LANG, "storeId": MARKET}
    try:
        resp = s.post(url, json=payload, headers=headers, timeout=8)
        body = json_or_error(resp, "login")
    except StoreAPIError as e:
        err = AKAMAI_UNREAD if _is_akamai(e) else f"Carrefour login: {e}"
        return {"ok": False, "token": "", "user_id": "", "error": err}
    except Exception as e:
        return {"ok": False, "token": "", "user_id": "", "error": f"Carrefour login: {type(e).__name__}"}
    sess = _extract_session(body, resp)
    if isinstance(body, dict) and body.get("access_token") and not sess["token"]:
        sess["token"] = str(body.get("access_token") or "")
        sess["user_id"] = sess["user_id"] or str(body.get("user_id") or body.get("userId") or "")
    if resp.status_code < 400 and sess["token"]:
        return {"ok": True, "token": sess["token"], "user_id": sess["user_id"], "error": None}
    meta = body.get("meta") if isinstance(body, dict) else {}
    last_err = (meta or {}).get("message") or f"HTTP {resp.status_code}"
    return {"ok": False, "token": "", "user_id": "", "error": f"Carrefour login: {last_err}"}


def harvest_token_from_login_page(email: str, password: str) -> dict[str, Any]:
    """One-shot: official Carrefour login page in the existing desktop session, keep only the token.

    Cart/list after this stay on the Android HTTP APIs. Chrome is not used to read the cart.
    """
    if not email or not password:
        return {"ok": False, "token": "", "user_id": "", "error": "Missing Carrefour email or password."}
    try:
        from bring_fast import checkout
    except Exception as e:
        return {"ok": False, "token": "", "user_id": "", "error": f"login page helper unavailable: {type(e).__name__}"}

    captured = {"token": "", "user_id": ""}
    pw = None
    try:
        pw, _browser, context = checkout._launch()
        page = context.new_page()

        def _on_response(resp) -> None:
            url = resp.url or ""
            if "customers/login" not in url and "oauth/token" not in url and "liteCart" not in url:
                return
            try:
                body = resp.json()
            except Exception:
                body = {}
            sess = _extract_session(body if isinstance(body, dict) else {}, resp)
            if sess.get("token"):
                captured["token"] = sess["token"]
            if sess.get("user_id"):
                captured["user_id"] = sess["user_id"]

        page.on("response", _on_response)
        checkout._login_carrefour(page, email, password)
        try:
            cookies = {c["name"]: c.get("value") or "" for c in context.cookies()}
        except Exception:
            cookies = {}
        captured["token"] = captured["token"] or cookies.get("token") or ""
        captured["user_id"] = captured["user_id"] or cookies.get("userId") or cookies.get("customerId") or ""
        if captured["token"] and captured["user_id"]:
            return {"ok": True, "token": captured["token"], "user_id": captured["user_id"], "error": None}
        text = ""
        try:
            text = page.evaluate("() => (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,180)")
        except Exception:
            pass
        if "incorrect" in text.lower():
            return {"ok": False, "token": "", "user_id": "", "error": "Carrefour rejected the saved password."}
        return {"ok": False, "token": "", "user_id": "", "error": "Login page did not yield an API token."}
    except Exception as e:
        return {"ok": False, "token": "", "user_id": "", "error": f"Login page harvest: {type(e).__name__}"}
    finally:
        try:
            if pw:
                pw.stop()
        except Exception:
            pass


def _auth(email: str, password: str, *, client=None) -> dict[str, Any]:
    """HTTP login only. Never open Chrome on the MCP path — that hangs Grok."""
    return login(email, password, client=client)


def lite_cart(*, token: str, user_id: str, client=None) -> dict[str, Any]:
    if not token or not user_id:
        raise StoreAPIError("liteCart needs auth token and userId.")
    s = chrome_session(client)
    headers = android_headers(token=token, user_id=user_id)
    last_err: StoreAPIError | None = None
    for host in API_HOSTS:
        try:
            resp = s.get(
                f"{host}/v1/basket/{MARKET}/{LANG}/liteCart",
                params={
                    "nsp": "food,nonfood,express,QCOMM,QELEC",
                    "lm": "false",
                    "liteResponse": "true",
                    "latitude": LAT,
                    "longitude": LNG,
                },
                headers=headers,
                timeout=20,
            )
            body = json_or_error(resp, "liteCart")
        except StoreAPIError as e:
            last_err = e
            if e.status in (401, 403) or _is_akamai(e):
                raise
            continue
        except Exception as e:
            last_err = StoreAPIError(f"{type(e).__name__}")
            continue
        if resp.status_code < 400:
            return body
        meta = body.get("meta") if isinstance(body, dict) else {}
        last_err = StoreAPIError(
            (meta or {}).get("message") or f"liteCart HTTP {resp.status_code}",
            status=resp.status_code,
            body=body,
        )
        if resp.status_code in (401, 403) or _is_akamai(last_err):
            raise last_err
    raise last_err or StoreAPIError("liteCart failed")


def _product_card(product_id: str, name: str = "") -> dict[str, Any]:
    """Name, image, and stock for the default food POS (072)."""
    card: dict[str, Any] = {
        "name": name or str(product_id),
        "image": f"{SITE}/{MARKET}/{LANG}/p/{product_id}",
        "in_stock": True,
    }
    try:
        import requests

        r = requests.get(
            f"https://ac.cnstrc.com/search/{product_id}",
            params={
                "key": "key_UzmQuiABmYtLGFME",
                "c": "cio-python-bringfast-1.0",
                "i": "bringfast",
                "s": 1,
                "num_results_per_page": 5,
            },
            timeout=8,
        )
        for it in ((r.json().get("response") or {}).get("results") or []):
            d = it.get("data") or {}
            if str(d.get("id") or "") != str(product_id):
                continue
            card["name"] = it.get("value") or d.get("online_name_en") or card["name"]
            card["image"] = d.get("image_url") or card["image"]
            stock = d.get("stock") or []
            here = next((row for row in stock if isinstance(row, dict) and str(row.get("pos")) == "072"), None)
            if here is not None:
                card["in_stock"] = bool(here.get("isAvailable")) and str(here.get("stock_status") or "") == "IN_STOCK"
            break
    except Exception:
        pass
    return card


def _err_message(body: Any, fallback: str) -> str:
    if not isinstance(body, dict):
        return fallback
    err_obj = body.get("error")
    if isinstance(err_obj, dict):
        msg = str(err_obj.get("message") or "")
        if msg:
            return msg
    meta = body.get("meta")
    if isinstance(meta, dict):
        msg = str(meta.get("message") or "")
        if msg:
            return msg
    return fallback


def _basket(
    method: str,
    path: str,
    *,
    token: str,
    user_id: str,
    payload: dict[str, Any] | None,
    what: str,
    client=None,
) -> dict[str, Any]:
    """Call BasketControllerV5 on either host. Same Chrome session as the homepage warm-up."""
    s = chrome_session(client)
    headers = android_headers(token=token, user_id=user_id)
    last_err: StoreAPIError | None = None
    for host in API_HOSTS:
        url = f"{host}/v1/basket/{MARKET}/{LANG}/{path}"
        try:
            resp = getattr(s, method)(url, json=payload, headers=headers, timeout=12)
            body = json_or_error(resp, what)
        except StoreAPIError as e:
            last_err = e
            if e.status in (401, 403) or _is_akamai(e):
                raise
            continue
        except Exception as e:
            last_err = StoreAPIError(f"{type(e).__name__}")
            continue
        if resp.status_code < 400:
            return body if isinstance(body, dict) else {"data": body}
        last_err = StoreAPIError(
            _err_message(body, f"{what} HTTP {resp.status_code}"),
            status=resp.status_code,
            body=body,
        )
        if resp.status_code in (401, 403) or _is_akamai(last_err):
            raise last_err
    raise last_err or StoreAPIError(f"{what} failed")


def add_item(*, token: str, user_id: str, product_id: str, qty: int = 1, name: str = "", client=None) -> dict[str, Any]:
    """POST BasketControllerV5.addItem. Prefer /entries on the site host; RetailSSO addItem is the fallback."""
    card = _product_card(str(product_id), name)
    if card.get("in_stock") is False:
        raise StoreAPIError(
            f"{card['name']} is out of stock for this delivery location.",
            status=409,
        )
    payload = {
        "productId": str(product_id),
        "productName": card["name"],
        "quantity": int(qty),
        "imageUrl": card["image"],
        "intent": "SLOTTED",
        "offerId": "offer_carrefour_",
        "sellerId": "0000",
        "username": user_id,
        "latitude": LAT,
        "longitude": LNG,
    }
    last_err: StoreAPIError | None = None
    for path in ("entries", "addItem"):
        try:
            return _basket(
                "post", path, token=token, user_id=user_id, payload=payload, what="add", client=client
            )
        except StoreAPIError as e:
            last_err = e
            if e.status in (401, 403, 409) or _is_akamai(e):
                raise
            continue
    raise last_err or StoreAPIError("add item failed")


def remove_items(*, token: str, user_id: str, product_ids: list[str], client=None) -> dict[str, Any]:
    """DELETE BasketControllerV5.deleteProducts with DeleteProductRequestV5."""
    ids = [str(pid) for pid in product_ids if str(pid).strip()]
    if not ids:
        return {}
    last_err: StoreAPIError | None = None
    for payload in ({"productIds": ids}, {"productId": ids[0]} if len(ids) == 1 else None):
        if payload is None:
            continue
        try:
            return _basket(
                "delete",
                "entries",
                token=token,
                user_id=user_id,
                payload=payload,
                what="delete",
                client=client,
            )
        except StoreAPIError as e:
            last_err = e
            if e.status in (401, 403) or _is_akamai(e):
                raise
            continue
    raise last_err or StoreAPIError("delete items failed")


def parse_items(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    data = body.get("data") or body.get("cart") or body.get("basket") or body
    if not isinstance(data, dict):
        return []
    raw = data.get("items") or data.get("products") or data.get("entries") or []
    out = []
    if not isinstance(raw, list):
        return []
    for i in raw:
        if not isinstance(i, dict):
            continue
        pid = str(i.get("id") or i.get("productId") or i.get("sku") or "")
        name = i.get("name") or i.get("title") or ""
        qty = int(i.get("qty") or i.get("quantity") or 1)
        price = i.get("price") or i.get("unitPrice")
        url = f"{SITE}/{MARKET}/{LANG}/p/{pid}" if pid else ""
        out.append({"id": pid, "name": name, "qty": qty, "price": price, "currency": "AED", "url": url})
    return out


def official_cart(
    *,
    email: str,
    password: str,
    action: str,
    items: list[dict[str, Any]],
    session_token: str = "",
    session_user: str = "",
    client=None,
) -> dict[str, Any]:
    """Official account cart. One Chrome session for homepage + liteCart + at most one login POST."""
    s = chrome_session(client)
    token, user_id = session_token, session_user
    reused = bool(token and user_id)
    if not reused:
        auth = _auth(email, password, client=s)
        if not auth["ok"]:
            return {
                "ok": False,
                "official_count": None,
                "items": [],
                "logged_in": False,
                "session_reused": False,
                "driver": "chrome",
                "client": PACKAGE,
                "error": auth.get("error") or "Carrefour login failed.",
                "token": "",
                "user_id": "",
            }
        token, user_id = auth["token"], auth["user_id"]
    try:
        if action in ("clear", "create", "empty", "new"):
            current = parse_items(lite_cart(token=token, user_id=user_id, client=s))
            ids = [str(it.get("id") or "") for it in current if it.get("id")]
            if ids:
                remove_items(token=token, user_id=user_id, product_ids=ids, client=s)
        elif action == "remove":
            ids = [str(it.get("id") or "") for it in items if it.get("id")]
            if ids:
                remove_items(token=token, user_id=user_id, product_ids=ids, client=s)
        elif action in ("add", "set"):
            for it in items:
                add_item(
                    token=token,
                    user_id=user_id,
                    product_id=str(it.get("id") or ""),
                    qty=int(it.get("qty") or 1),
                    name=str(it.get("name") or ""),
                    client=s,
                )
        body = lite_cart(token=token, user_id=user_id, client=s)
        live = parse_items(body)
        return {
            "ok": True,
            "official_count": len(live),
            "items": live,
            "logged_in": True,
            "session_reused": reused,
            "driver": "chrome",
            "client": PACKAGE,
            "error": None,
            "token": token,
            "user_id": user_id,
        }
    except StoreAPIError as e:
        if reused and _is_invalid_auth_token(e) and email and password:
            return official_cart(
                email=email,
                password=password,
                action=action,
                items=items,
                session_token="",
                session_user="",
                client=s,
            )
        err = AKAMAI_UNREAD if _is_akamai(e) else str(e)
        return {
            "ok": False,
            "official_count": None,
            "items": [],
            "logged_in": bool(token and user_id),
            "session_reused": reused,
            "driver": "chrome",
            "client": PACKAGE,
            "error": err,
            "token": token,
            "user_id": user_id,
        }
