"""Carrefour UAE official cart via curl_cffi Chrome impersonation.

TLS, HTTP/1.1, and User-Agent are Chrome's. MAF JSON headers stay on API
calls only — never override User-Agent (Akamai 403 if TLS Chrome + okhttp).
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from bring_fast.stores.http import StoreAPIError, is_akamai_shell, json_or_error, session

# Same Apigee surface the Play Store app uses (RetailSSO + site gateway).
# RetailSSO first: www.carrefouruae.com often hangs or returns Akamai HTML from datacenter IPs.
API_HOSTS = (
    "https://api-prod.retailsso.com",
    "https://www.carrefouruae.com",
)
SITE = "https://www.carrefouruae.com"
MARKET = "mafuae"
LANG = "en"
APP_ID = "Android"
APP_VERSION = "26.8.21"
PACKAGE = "com.aswat.carrefouruae"
LAT = 25.0321285
LNG = 55.1912732
SERVICE_TYPES = "SLOTTED|DEFAULT|MKP_GLOBAL|QMKP|QELEC|DIGITAL"
POLYGON_NAMES = {
    "DXB_DubProdCty_01": "Dubai Production City",
    "DXB_DFC_11": "Dubai Festival City",
}

_FULFILMENT: dict[tuple[float, float], dict[str, Any]] = {}


def _device_id() -> str:
    return "bf-" + uuid.uuid5(uuid.NAMESPACE_DNS, "bring-fast.android.mafuae").hex[:16]


CHROME_IMPERSONATE = ("chrome", "chrome131", "chrome124")


def _new_impersonate(name: str):
    from curl_cffi import requests as cf
    from curl_cffi.const import CurlHttpVersion

    return cf.Session(impersonate=name, http_version=CurlHttpVersion.V1_1)


def chrome_session(existing=None, *, warm: bool = True):
    """One Chrome client. Replay browser cookies; GET homepage only when warm=True.

    Cart *list* and HTTP add must not pay for a homepage round-trip — Grok times
    out. Browser fallback gets Akamai cookies from a real page instead.
    """
    if existing is not None:
        apply_browser_cookies(existing)
        return existing

    def warmed(s) -> bool:
        try:
            resp = s.get(f"{SITE}/{MARKET}/{LANG}", timeout=4)
            text = getattr(resp, "text", "") or ""
        except Exception:
            return False
        return not is_akamai_shell(text)

    s = session()
    apply_browser_cookies(s)
    if not warm:
        return s
    if warmed(s):
        capture_client_cookies(s)
        return s
    for name in CHROME_IMPERSONATE:
        try:
            alt = _new_impersonate(name)
        except Exception:
            continue
        apply_browser_cookies(alt)
        if warmed(alt):
            capture_client_cookies(alt)
            return alt
    return s


def _cookie_file() -> Path:
    root = Path(os.environ.get("BRINGFAST_DATA", Path.home() / ".bring-fast"))
    return root / "carrefour-net" / "cookies.json"


def save_browser_cookies(cookies: list[dict[str, Any]]) -> None:
    path = _cookie_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cookies))
    os.chmod(path, 0o600)


def apply_browser_cookies(s) -> None:
    """Replay the desktop Chrome cookie jar (token, _abck, ak_bmsc) onto curl_cffi."""
    path = _cookie_file()
    if not path.is_file():
        return
    try:
        cookies = json.loads(path.read_text())
    except Exception:
        return
    if not isinstance(cookies, list):
        return
    for c in cookies:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        domain = c.get("domain") or ".carrefouruae.com"
        try:
            s.cookies.set(c["name"], c.get("value") or "", domain=domain)
        except Exception:
            try:
                s.cookies.set(c["name"], c.get("value") or "")
            except Exception:
                pass


def token_from_browser_cookies() -> dict[str, str]:
    path = _cookie_file()
    out = {"token": "", "user_id": ""}
    if not path.is_file():
        return out
    try:
        cookies = json.loads(path.read_text())
    except Exception:
        return out
    if not isinstance(cookies, list):
        return out
    for c in cookies:
        if not isinstance(c, dict):
            continue
        if c.get("name") == "token":
            out["token"] = str(c.get("value") or "")
        if c.get("name") == "userId":
            out["user_id"] = str(c.get("value") or "")
    return out


def capture_client_cookies(s) -> None:
    """Persist _abck / token from a live curl_cffi session for the next call."""
    cookies: list[dict[str, Any]] = []
    try:
        jar = getattr(s, "cookies", None)
        inner = getattr(jar, "jar", None) if jar is not None else None
        iterable = inner if inner is not None else jar
        if iterable is None:
            return
        for c in iterable:
            name = getattr(c, "name", None)
            if not name and isinstance(c, (list, tuple)) and c:
                name, value = c[0], c[1] if len(c) > 1 else ""
                cookies.append({"name": str(name), "value": str(value or ""), "domain": ".carrefouruae.com"})
                continue
            if not name:
                continue
            cookies.append(
                {
                    "name": str(name),
                    "value": str(getattr(c, "value", "") or ""),
                    "domain": str(getattr(c, "domain", "") or ".carrefouruae.com"),
                    "path": str(getattr(c, "path", "") or "/"),
                }
            )
    except Exception:
        return
    if cookies:
        save_browser_cookies(cookies)


def refresh_sensor_cookies() -> bool:
    """Copy Akamai sensor cookies from an already-running desktop Chrome (CDP). No new window."""
    try:
        from bring_fast import checkout

        cookies = checkout.snapshot_cdp_cookies()
    except Exception:
        return False
    if not cookies:
        return False
    save_browser_cookies(cookies)
    names = {str(c.get("name") or "") for c in cookies if isinstance(c, dict)}
    return bool(names & {"_abck", "ak_bmsc", "bm_sz", "token"})


def android_headers(*, token: str = "", user_id: str = "", fulfilment: dict[str, Any] | None = None) -> dict[str, str]:
    """JSON/API headers only. Do not set User-Agent — curl_cffi impersonate owns it."""
    loc = fulfilment or {}
    lat = loc.get("lat") if loc.get("lat") is not None else LAT
    lng = loc.get("lng") if loc.get("lng") is not None else LNG
    h = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "appid": APP_ID,
        "env": "prod",
        "storeid": MARKET,
        "lang": LANG,
        "langCode": LANG,
        "currency": "AED",
        "x-maf-appId": APP_ID,
        "x-maf-storeId": MARKET,
        "x-maf-lang": LANG,
        "x-maf-env": "prod",
        "x-maf-tenant": MARKET,
        "x-maf-deviceId": _device_id(),
        "x-maf-appVersion": APP_VERSION,
        "x-maf-requestId": str(uuid.uuid4()),
        "latitude": str(lat),
        "longitude": str(lng),
        "serviceTypes": str(loc.get("service_types") or SERVICE_TYPES),
        "productType": str(loc.get("product_type") or "ANY"),
    }
    if loc.get("pos_info"):
        h["posInfo"] = str(loc["pos_info"])
    if loc.get("pos_info2"):
        h["posInfo2"] = str(loc["pos_info2"])
    if loc.get("emirate_code"):
        h["emirateCode"] = str(loc["emirate_code"])
    if loc.get("intent"):
        h["intent"] = str(loc["intent"])
    if user_id:
        h["userId"] = str(user_id)
        h["x-maf-account"] = str(user_id)
    if token:
        raw = token[7:].strip() if str(token).lower().startswith("bearer ") else str(token)
        h["Authorization"] = f"Bearer {raw}"
        h["token"] = raw
    return h


def food_pos(pos_info: str) -> str:
    m = re.search(r"(?:^|,)food=(\d+)", pos_info or "", re.I)
    return m.group(1) if m else ""


def _html_str(html: str, key: str) -> str:
    for pat in (rf'\\"{re.escape(key)}\\":\\"([^"\\]+)\\"', rf'"{re.escape(key)}":"([^"]+)"'):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return ""


def parse_fulfilment_html(html: str, *, lat: float, lng: float) -> dict[str, Any]:
    """posInfo / polygon from the Carrefour SSR payload (lat/long cookies)."""
    pos_info = _html_str(html, "posInfo")
    pos_info2 = _html_str(html, "posInfo2") or pos_info
    polygon_id = _html_str(html, "polygonId")
    emirate = _html_str(html, "emirateCode") or "DUBAI"
    area = POLYGON_NAMES.get(polygon_id) or polygon_id.replace("_", " ").strip()
    return {
        "pos_info": pos_info,
        "pos_info2": pos_info2,
        "polygon_id": polygon_id,
        "emirate_code": emirate,
        "food_pos": food_pos(pos_info),
        "area": area,
        "delivery_address": area,
        "service_types": SERVICE_TYPES,
        "product_type": "ANY",
        "lat": lat,
        "lng": lng,
    }


def _address_label(row: dict[str, Any]) -> str:
    bits = [
        row.get("appartment") or row.get("apartment") or row.get("building"),
        row.get("streetName") or row.get("street") or row.get("addressLabel"),
        row.get("town") or row.get("area") or row.get("emirate"),
    ]
    return ", ".join(str(b) for b in bits if b)


def _address_rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if not isinstance(body, dict):
        return []
    data = body.get("data") if isinstance(body.get("data"), (list, dict)) else body
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("addresses", "items", "content"):
            raw = data.get(key)
            if isinstance(raw, list):
                return [r for r in raw if isinstance(r, dict)]
    return []


def customer_addresses(*, token: str, user_id: str, client=None) -> list[dict[str, Any]]:
    if not token or not user_id:
        return []
    s = chrome_session(client)
    headers = android_headers(token=token, user_id=user_id)
    for host in API_HOSTS:
        try:
            resp = s.get(f"{host}/v2/addresses/{MARKET}/{LANG}", headers=headers, timeout=4)
            body = json_or_error(resp, "addresses")
        except StoreAPIError as e:
            if e.status in (401, 403) or _is_akamai(e):
                return []
            continue
        except Exception:
            continue
        if resp.status_code < 400:
            return _address_rows(body)
    return []


def _coords_from_address(row: dict[str, Any]) -> tuple[float, float] | None:
    try:
        lat = float(row.get("latitude") or row.get("lat") or 0)
        lng = float(row.get("longitude") or row.get("lng") or row.get("lon") or 0)
    except (TypeError, ValueError):
        return None
    if not lat or not lng:
        return None
    return lat, lng


def _fulfilment_from_homepage(lat: float, lng: float, *, client=None) -> dict[str, Any]:
    s = chrome_session(client)
    headers = android_headers()
    headers.pop("Content-Type", None)
    headers["Accept"] = "text/html,application/json"
    try:
        s.cookies.set("lat", str(lat), domain="www.carrefouruae.com", path="/")
        s.cookies.set("long", str(lng), domain="www.carrefouruae.com", path="/")
        s.cookies.set("storeInfo", f"{MARKET}|{LANG}|AED", domain="www.carrefouruae.com", path="/")
    except Exception:
        pass
    try:
        resp = s.get(f"{SITE}/{MARKET}/{LANG}", headers=headers, timeout=5)
        html = resp.text or ""
    except Exception as e:
        raise StoreAPIError(f"Carrefour location page: {type(e).__name__}") from e
    loc = parse_fulfilment_html(html, lat=lat, lng=lng)
    if not loc.get("pos_info"):
        raise StoreAPIError("Carrefour location page did not include posInfo.")
    return loc


def resolve_fulfilment(
    *,
    token: str = "",
    user_id: str = "",
    lat: float | None = None,
    lng: float | None = None,
    client=None,
) -> dict[str, Any]:
    """Bind the MAF delivery store (posInfo) for this point. Cached per lat/lng."""
    use_lat = LAT if lat is None else lat
    use_lng = LNG if lng is None else lng
    address_label = ""
    if token and user_id:
        for row in customer_addresses(token=token, user_id=user_id, client=client):
            coords = _coords_from_address(row)
            if not coords:
                continue
            if row.get("defaultAddress") or row.get("default_shipping") or not address_label:
                use_lat, use_lng = coords
                address_label = _address_label(row)
                if row.get("defaultAddress") or row.get("default_shipping"):
                    break
    key = (round(float(use_lat), 5), round(float(use_lng), 5))
    cached = _FULFILMENT.get(key)
    if cached:
        out = dict(cached)
        if address_label:
            out["delivery_address"] = address_label
        return out
    try:
        loc = _fulfilment_from_homepage(use_lat, use_lng, client=client)
    except StoreAPIError:
        loc = {
            "pos_info": "",
            "pos_info2": "",
            "polygon_id": "",
            "emirate_code": "DUBAI",
            "food_pos": "",
            "area": "",
            "delivery_address": address_label,
            "service_types": SERVICE_TYPES,
            "product_type": "ANY",
            "lat": use_lat,
            "lng": use_lng,
        }
    if address_label:
        loc["delivery_address"] = address_label
    if loc.get("pos_info"):
        _FULFILMENT[key] = loc
    return loc


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
    "The saved store login is still present. Official cart unread. "
    "error_code=akamai_blocked."
)


def _is_akamai(err: StoreAPIError) -> bool:
    if getattr(err, "error_code", None) == "akamai_blocked":
        return True
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

    Cart/list after this stay on the HTTP APIs. Chrome is not used to read the cart.
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
        try:
            save_browser_cookies(list(context.cookies()))
        except Exception:
            pass
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


def lite_cart(
    *,
    token: str,
    user_id: str,
    fulfilment: dict[str, Any] | None = None,
    client=None,
    timeout: float = 20,
) -> dict[str, Any]:
    if not token or not user_id:
        raise StoreAPIError("liteCart needs auth token and userId.")
    s = chrome_session(client, warm=False)
    loc = fulfilment if fulfilment is not None else resolve_fulfilment(token=token, user_id=user_id, client=s)
    headers = android_headers(token=token, user_id=user_id, fulfilment=loc)
    last_err: StoreAPIError | None = None
    deadline = time.monotonic() + max(2.0, float(timeout or 8))
    for host in API_HOSTS:
        remaining = deadline - time.monotonic()
        if remaining < 1:
            break
        try:
            resp = s.get(
                f"{host}/v1/basket/{MARKET}/{LANG}/liteCart",
                params={
                    "nsp": "food,nonfood,express,QCOMM,QELEC",
                    "lm": "false",
                    "liteResponse": "true",
                    "latitude": loc.get("lat") if loc.get("lat") is not None else LAT,
                    "longitude": loc.get("lng") if loc.get("lng") is not None else LNG,
                },
                headers=headers,
                timeout=remaining,
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


def _cio_search(query: str) -> list[dict[str, Any]]:
    if not query:
        return []
    try:
        import requests
        from urllib.parse import quote

        r = requests.get(
            f"https://ac.cnstrc.com/search/{quote(str(query).strip(), safe='')}",
            params={
                "key": "key_UzmQuiABmYtLGFME",
                "c": "cio-python-bringfast-1.0",
                "i": "bringfast",
                "s": 1,
                "num_results_per_page": 8,
            },
            timeout=4,
        )
        return list(((r.json().get("response") or {}).get("results") or []))
    except Exception:
        return []


def _stock_at_pos(stock: Any, pos: str) -> dict[str, Any] | None:
    if not pos:
        return None
    if isinstance(stock, dict):
        row = stock.get(pos)
        return row if isinstance(row, dict) else None
    if not isinstance(stock, list):
        return None
    return next((row for row in stock if isinstance(row, dict) and str(row.get("pos") or "") == str(pos)), None)


def _product_card(product_id: str, name: str = "", *, fulfilment: dict[str, Any] | None = None) -> dict[str, Any]:
    """Name, image, and stock for the bound food POS (from posInfo), not a hardcoded store."""
    loc = fulfilment if fulfilment is not None else resolve_fulfilment()
    pos = str(loc.get("food_pos") or food_pos(str(loc.get("pos_info") or "")) or "")
    card: dict[str, Any] = {
        "name": name or str(product_id),
        "image": f"{SITE}/{MARKET}/{LANG}/p/{product_id}",
        "in_stock": True,
        "pos": pos,
    }
    queries = []
    if name and name != str(product_id):
        queries.append(name)
    queries.append(str(product_id))
    seen: set[str] = set()
    for q in queries:
        if q in seen:
            continue
        seen.add(q)
        for it in _cio_search(q):
            d = it.get("data") or {}
            if str(d.get("id") or "") != str(product_id):
                continue
            card["name"] = it.get("value") or d.get("online_name_en") or card["name"]
            card["image"] = d.get("image_url") or card["image"]
            here = _stock_at_pos(d.get("stock"), pos)
            if here is not None:
                status = str(here.get("stock_status") or here.get("stock") or "").upper()
                available = here.get("isAvailable")
                if available is None:
                    available = "OUT" not in status
                card["in_stock"] = bool(available) and "OUT" not in status
            return card
    return card


def _err_message(body: Any, fallback: str) -> str:
    if not isinstance(body, dict):
        return fallback
    err_obj = body.get("error")
    if isinstance(err_obj, dict):
        msg = str(err_obj.get("message") or "")
        if msg:
            return msg
    if isinstance(err_obj, str) and err_obj.strip():
        return err_obj
    meta = body.get("meta")
    if isinstance(meta, dict):
        msg = str(meta.get("message") or "")
        if msg:
            return msg
    return fallback


def _error_blob(err: StoreAPIError) -> str:
    parts = [str(err), str(err.error_code or ""), str(err.maf_error or "")]
    if err.body is not None:
        try:
            parts.append(json.dumps(err.body) if not isinstance(err.body, str) else err.body)
        except Exception:
            parts.append(str(err.body))
    return " ".join(parts).lower()


def _is_purchase_intent_error(err: StoreAPIError) -> bool:
    if err.status == 409:
        return False
    blob = _error_blob(err)
    if "out of stock" in blob:
        return False
    return "purchase indicator" in blob or "purchaseindicators" in blob or (
        "slotted" in blob and "not a valid intent" in blob
    )


def _slot_error(err: StoreAPIError | None = None, *, maf: str = "") -> StoreAPIError:
    raw = maf or (_err_message(err.body, str(err)) if err else "")
    return StoreAPIError(
        "Carrefour needs a bound delivery store before add-to-cart "
        "(error_code=needs_delivery_slot). The area had no SLOTTED purchase indicators. "
        "List the cart to refresh the store location, then retry add.",
        status=err.status if err else 400,
        body=err.body if err else None,
        error_code="needs_delivery_slot",
        maf_error=raw or None,
    )


def _basket(
    method: str,
    path: str,
    *,
    token: str,
    user_id: str,
    payload: dict[str, Any] | None,
    what: str,
    fulfilment: dict[str, Any] | None = None,
    client=None,
) -> dict[str, Any]:
    """Call BasketControllerV5 on either host. Same Chrome session as the homepage warm-up."""
    s = chrome_session(client)
    loc = fulfilment if fulfilment is not None else resolve_fulfilment(token=token, user_id=user_id, client=s)
    headers = android_headers(token=token, user_id=user_id, fulfilment=loc)
    last_err: StoreAPIError | None = None
    for host in API_HOSTS:
        url = f"{host}/v1/basket/{MARKET}/{LANG}/{path}"
        try:
            resp = getattr(s, method)(url, json=payload, headers=headers, timeout=8)
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
        msg = _err_message(body, f"{what} HTTP {resp.status_code}")
        last_err = StoreAPIError(msg, status=resp.status_code, body=body, maf_error=msg)
        if resp.status_code in (401, 403) or _is_akamai(last_err):
            raise last_err
    raise last_err or StoreAPIError(f"{what} failed")


def _v8_add_item(
    *,
    token: str,
    user_id: str,
    payload: dict[str, Any],
    fulfilment: dict[str, Any],
    client=None,
) -> dict[str, Any]:
    """Website STANDARD cart (SLOTTED food) as a last fallback after BasketControllerV5."""
    s = chrome_session(client)
    headers = android_headers(token=token, user_id=user_id, fulfilment={**fulfilment, "intent": "SLOTTED"})
    last_err: StoreAPIError | None = None
    for host in API_HOSTS:
        url = f"{host}/v8/carts/{MARKET}/{LANG}/STANDARD/addItem"
        try:
            resp = s.post(url, json=payload, headers=headers, timeout=8)
            body = json_or_error(resp, "v8add")
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
        msg = _err_message(body, f"v8add HTTP {resp.status_code}")
        last_err = StoreAPIError(msg, status=resp.status_code, body=body, maf_error=msg)
        if resp.status_code in (401, 403) or _is_akamai(last_err):
            raise last_err
    raise last_err or StoreAPIError("v8 add failed")


def add_item(
    *,
    token: str,
    user_id: str,
    product_id: str,
    qty: int = 1,
    name: str = "",
    fulfilment: dict[str, Any] | None = None,
    client=None,
) -> dict[str, Any]:
    """POST BasketControllerV5.addItem with the bound MAF posInfo (SLOTTED food)."""
    s = chrome_session(client)
    loc = fulfilment if fulfilment is not None else resolve_fulfilment(token=token, user_id=user_id, client=s)
    if not loc.get("pos_info"):
        raise _slot_error(maf="posInfo missing")
    card = _product_card(str(product_id), name, fulfilment=loc)
    if card.get("in_stock") is False:
        raise StoreAPIError(
            f"{card['name']} is out of stock for this delivery location.",
            status=409,
        )
    lat = loc.get("lat") if loc.get("lat") is not None else LAT
    lng = loc.get("lng") if loc.get("lng") is not None else LNG
    payload = {
        "productId": str(product_id),
        "productName": card["name"],
        "quantity": int(qty),
        "imageUrl": card["image"],
        "intent": "SLOTTED",
        "offerId": "offer_carrefour_",
        "sellerId": "0000",
        "shopId": "0000",
        "username": user_id,
        "latitude": lat,
        "longitude": lng,
    }
    add_loc = {**loc, "intent": "SLOTTED"}
    last_err: StoreAPIError | None = None
    for path in ("entries", "addItem"):
        try:
            return _basket(
                "post",
                path,
                token=token,
                user_id=user_id,
                payload=payload,
                what="add",
                fulfilment=add_loc,
                client=s,
            )
        except StoreAPIError as e:
            last_err = e
            if e.status in (401, 403, 409) or _is_akamai(e):
                raise
            continue
    try:
        return _v8_add_item(token=token, user_id=user_id, payload=payload, fulfilment=add_loc, client=s)
    except StoreAPIError as e:
        last_err = e
        if e.status in (401, 403, 409) or _is_akamai(e):
            raise
    if last_err and _is_purchase_intent_error(last_err):
        raise _slot_error(last_err)
    raise last_err or StoreAPIError("add item failed")


def remove_items(
    *,
    token: str,
    user_id: str,
    product_ids: list[str],
    fulfilment: dict[str, Any] | None = None,
    client=None,
) -> dict[str, Any]:
    """DELETE BasketControllerV5.deleteProducts with DeleteProductRequestV5."""
    ids = [str(pid) for pid in product_ids if str(pid).strip()]
    if not ids:
        return {}
    s = chrome_session(client)
    loc = fulfilment if fulfilment is not None else resolve_fulfilment(token=token, user_id=user_id, client=s)
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
                fulfilment=loc,
                client=s,
            )
        except StoreAPIError as e:
            last_err = e
            if e.status in (401, 403) or _is_akamai(e):
                raise
            continue
    raise last_err or StoreAPIError("delete items failed")


def _nested_product(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("product", "item", "offer"):
        val = row.get(key)
        if isinstance(val, dict):
            return val
    return {}


def _first_str(*vals: Any) -> str:
    for val in vals:
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return ""


def _first_price(*vals: Any) -> Any:
    for val in vals:
        if val in (None, ""):
            continue
        if isinstance(val, dict):
            inner = _first_price(
                val.get("value"),
                val.get("amount"),
                val.get("price"),
                val.get("sellingPrice"),
                val.get("selling_price"),
            )
            if inner not in (None, ""):
                return inner
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            return val
    return None


def parse_items(body: Any) -> list[dict[str, Any]]:
    """Read liteCart / basket rows. liteResponse often has id+qty only — names come from enrich."""
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
        product = _nested_product(i)
        pid = _first_str(
            i.get("id"),
            i.get("productId"),
            i.get("product_id"),
            i.get("sku"),
            product.get("id"),
            product.get("productId"),
            product.get("sku"),
        )
        name = _first_str(
            i.get("name"),
            i.get("title"),
            i.get("productName"),
            i.get("product_name"),
            i.get("onlineName"),
            i.get("online_name_en"),
            i.get("displayName"),
            product.get("name"),
            product.get("title"),
            product.get("productName"),
            product.get("online_name_en"),
        )
        qty = int(i.get("qty") or i.get("quantity") or product.get("qty") or product.get("quantity") or 1)
        price = _first_price(
            i.get("price"),
            i.get("unitPrice"),
            i.get("unit_price"),
            i.get("sellingPrice"),
            i.get("selling_price"),
            i.get("offerPrice"),
            (i.get("prices") or {}).get("price") if isinstance(i.get("prices"), dict) else None,
            product.get("price"),
            product.get("unitPrice"),
            product.get("sellingPrice"),
        )
        url = f"{SITE}/{MARKET}/{LANG}/p/{pid}" if pid else ""
        out.append({"id": pid, "name": name, "qty": qty, "price": price, "currency": "AED", "url": url})
    return out


def ids_in_cart(requested: list[dict[str, Any]], live: list[dict[str, Any]]) -> bool:
    """True when every requested product_id is already in the official cart rows."""
    wanted = [str(it.get("id") or "").strip() for it in requested if str(it.get("id") or "").strip()]
    if not wanted:
        return False
    have = {str(it.get("id") or "") for it in live}
    return all(pid in have for pid in wanted)


def _catalog_hit(product_id: str) -> dict[str, Any] | None:
    """Name/price from Constructor.io for an id already in the cart. Does not invent SKUs."""
    pid = str(product_id or "").strip()
    if not pid:
        return None
    for it in _cio_search(pid):
        d = it.get("data") or {}
        if not isinstance(d, dict):
            continue
        ids = {
            str(d.get("id") or ""),
            str(d.get("code") or ""),
            str(d.get("ean") or ""),
        }
        if pid not in ids:
            continue
        name = _first_str(it.get("value"), d.get("online_name_en"), d.get("name"))
        price = _first_price(d.get("price"), d.get("selling_price"), d.get("sellingPrice"))
        url = d.get("url") or ""
        if url and not str(url).startswith("http"):
            url = f"{SITE}/{MARKET}/{LANG}{url}"
        return {"id": pid, "name": name, "price": price, "url": url or f"{SITE}/{MARKET}/{LANG}/p/{pid}"}
    return None


def enrich_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill empty name/price from the same catalog search uses. Never add rows."""
    out: list[dict[str, Any]] = []
    for it in items:
        row = dict(it)
        need_name = not str(row.get("name") or "").strip()
        need_price = row.get("price") in (None, "")
        if need_name or need_price:
            try:
                hit = _catalog_hit(str(row.get("id") or ""))
            except Exception:
                hit = None
            if hit:
                if need_name and hit.get("name"):
                    row["name"] = hit["name"]
                if need_price and hit.get("price") not in (None, ""):
                    row["price"] = hit["price"]
                if not row.get("url") and hit.get("url"):
                    row["url"] = hit["url"]
        out.append(row)
    return out


def _loc_fields(loc: dict[str, Any]) -> dict[str, Any]:
    pos = str(loc.get("food_pos") or food_pos(str(loc.get("pos_info") or "")) or "")
    return {
        "delivery_address": loc.get("delivery_address") or loc.get("area") or "",
        "food_pos": pos,
        "pos": pos,
        "area": loc.get("area") or "",
        "polygon_id": loc.get("polygon_id") or "",
    }


_CART_READ = frozenset({"list", "get", "read", "show", "view", "items", "contents"})
_CART_CLEAR = frozenset({"clear", "create", "empty", "new"})
_CART_SLOT = frozenset({"add", "set"})


def official_cart(
    *,
    email: str,
    password: str,
    action: str,
    items: list[dict[str, Any]],
    session_token: str = "",
    session_user: str = "",
    client=None,
    timeout: float = 25,
) -> dict[str, Any]:
    """Official account cart. List is token + liteCart only; add/set also bind posInfo.

    Never warm www.carrefouruae.com here — that host hangs from datacenter IPs and
    burns Grok's ~38s budget before the browser fallback can run.
    """
    action = (action or "list").strip().lower()
    if action in _CART_READ:
        action = "list"
    needs_slot = action in _CART_SLOT
    s = chrome_session(client, warm=False)
    token, user_id = session_token, session_user
    if not (token and user_id):
        jar = token_from_browser_cookies()
        token = token or jar.get("token") or ""
        user_id = user_id or jar.get("user_id") or ""
    reused = bool(token and user_id)
    if not reused:
        auth = _auth(email, password, client=s)
        if not auth["ok"]:
            akamai = "akamai" in str(auth.get("error") or "").lower()
            return {
                "ok": False,
                "official_count": None,
                "items": [],
                "logged_in": False,
                "session_reused": False,
                "driver": "chrome",
                "client": PACKAGE,
                "error": auth.get("error") or "Carrefour login failed.",
                "error_code": "akamai_blocked" if akamai else None,
                "token": "",
                "user_id": "",
            }
        token, user_id = auth["token"], auth["user_id"]
    loc: dict[str, Any] = {}
    budget = max(2.0, float(timeout or 12))
    read_timeout = min(budget, 8.0)
    try:
        if needs_slot:
            loc = resolve_fulfilment(token=token, user_id=user_id, client=s)
        item_errors: list[dict[str, Any]] = []
        added = 0
        if action in _CART_CLEAR:
            current = parse_items(
                lite_cart(token=token, user_id=user_id, fulfilment=loc, client=s, timeout=read_timeout)
            )
            ids = [str(it.get("id") or "") for it in current if it.get("id")]
            if ids:
                remove_items(token=token, user_id=user_id, product_ids=ids, fulfilment=loc, client=s)
        elif action == "remove":
            ids = [str(it.get("id") or "") for it in items if it.get("id")]
            if ids:
                remove_items(token=token, user_id=user_id, product_ids=ids, fulfilment=loc, client=s)
        elif needs_slot:
            for it in items:
                try:
                    add_item(
                        token=token,
                        user_id=user_id,
                        product_id=str(it.get("id") or ""),
                        qty=int(it.get("qty") or 1),
                        name=str(it.get("name") or ""),
                        fulfilment=loc,
                        client=s,
                    )
                    added += 1
                except StoreAPIError as e:
                    if e.error_code == "needs_delivery_slot" or e.status in (401, 403) or _is_akamai(e):
                        raise
                    item_errors.append(
                        {
                            "id": str(it.get("id") or ""),
                            "name": str(it.get("name") or ""),
                            "error": str(e),
                            "error_code": e.error_code,
                            "maf_error": e.maf_error,
                        }
                    )
        body = lite_cart(token=token, user_id=user_id, fulfilment=loc, client=s, timeout=read_timeout)
        live = enrich_items(parse_items(body))
        ok = True
        error = None
        error_code = None
        maf_error = None
        landed = needs_slot and ids_in_cart(items, live)
        if item_errors and added == 0 and not landed:
            ok = False
            error = item_errors[0]["error"]
            error_code = item_errors[0].get("error_code")
            maf_error = item_errors[0].get("maf_error")
        elif landed:
            item_errors = []
        return {
            "ok": ok,
            "official_count": len(live),
            "items": live,
            "logged_in": True,
            "session_reused": reused,
            "driver": "chrome",
            "client": PACKAGE,
            "error": error,
            "error_code": error_code,
            "maf_error": maf_error,
            "item_errors": item_errors,
            "token": token,
            "user_id": user_id,
            **_loc_fields(loc),
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
                timeout=timeout,
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
            "error_code": e.error_code or ("akamai_blocked" if _is_akamai(e) else None),
            "maf_error": e.maf_error,
            "token": token,
            "user_id": user_id,
            **_loc_fields(loc),
        }
