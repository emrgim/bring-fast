from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import FastAPI, Form, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import __version__, catalog, checkout, compare, db, forecast, macro_categories, mcp_skill, purchases, update, x
from .macro_categories import MACRO_CATEGORIES, normalize_macro as normalize_macro_slug
from .stores.cart_match import peel_remove_name

HOST = os.environ.get("BRINGFAST_HOST", "127.0.0.1")
PORT = int(os.environ.get("BRINGFAST_PORT", "8877"))
PUBLIC_URL = os.environ.get("BRINGFAST_PUBLIC_URL", "").rstrip("/")
SESSION_DAYS = int(os.environ.get("BRINGFAST_SESSION_DAYS", "30"))


def _session_secret() -> str:
    env = os.environ.get("BRINGFAST_SECRET")
    if env:
        return env
    db.DATA.mkdir(parents=True, exist_ok=True)
    path = db.DATA / "session.secret"
    if path.exists():
        value = path.read_text().strip()
        if value:
            return value
    value = secrets.token_urlsafe(48)
    path.write_text(value)
    path.chmod(0o600)
    return value


SECRET = _session_secret()
# New value on every start, so a page can tell "the server answered again"
# from "the updated server answered again" and reload at the right moment.
BOOT_ID = secrets.token_hex(8)

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
app = FastAPI(title="Bring Fast")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["WWW-Authenticate"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET,
    session_cookie="bring_fast_session",
    same_site="lax",
    max_age=SESSION_DAYS * 24 * 3600,
    https_only=PUBLIC_URL.startswith("https://"),
)

# A page of a thousand bars is the same forty characters over and over, and a
# shelf of products is the same again: on a phone connection compressing the
# app's own markup is the difference between a tab that opens and a tab that
# arrives. Fonts, logos, product shots and receipt scans are already compressed
# formats, and the MCP wire says no-transform, so those are handed on untouched.
AS_SENT = ("/static/", "/receipts/", "/product-images/", "/favicon.ico", "/apple-touch-icon.png", "/mcp")


class CompressMarkup:
    def __init__(self, app):
        self.app = app
        self.zipped = GZipMiddleware(app, minimum_size=1024)

    async def __call__(self, scope, receive, send):
        path = scope.get("path") or ""
        if scope.get("type") != "http" or path.startswith(AS_SENT):
            return await self.app(scope, receive, send)
        return await self.zipped(scope, receive, send)


app.add_middleware(CompressMarkup)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
STATIC = Path(__file__).resolve().parent / "static"

# A font subset is fixed for the life of its filename, so it is fetched once
# and never asked about again. A page carries live money figures, so the
# browser revalidates it every time — the service worker, not the HTTP cache,
# is what answers when there is no network. JSON here is always live state:
# health, update, MCP and OAuth are worth nothing stale.
FONT_CACHE = "public, max-age=31536000, immutable"
ASSET_CACHE = "public, max-age=86400"
PAGE_CACHE = "no-cache"
LIVE_CACHE = "no-store"


@app.middleware("http")
async def cache_policy(request: Request, call_next):
    response = await call_next(request)
    if response.headers.get("Cache-Control"):
        return response
    path = request.url.path
    kind = response.headers.get("content-type") or ""
    if path.startswith("/static/fonts/"):
        response.headers["Cache-Control"] = FONT_CACHE
    elif path.startswith("/static/"):
        response.headers["Cache-Control"] = ASSET_CACHE
    elif kind.startswith("text/html"):
        response.headers["Cache-Control"] = PAGE_CACHE
    elif kind.startswith("application/json"):
        response.headers["Cache-Control"] = LIVE_CACHE
    return response


@app.get("/manifest.webmanifest")
def pwa_manifest():
    return FileResponse(
        STATIC / "pwa" / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/sw.js")
def pwa_service_worker():
    return FileResponse(
        STATIC / "pwa" / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/offline", response_class=HTMLResponse)
def pwa_offline():
    """Precached last resort: shown when a page was never cached and the network is gone."""
    return FileResponse(
        STATIC / "pwa" / "offline.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/apple-touch-icon.png")
def pwa_apple_touch_icon():
    return FileResponse(STATIC / "pwa" / "icon-180.png", media_type="image/png")


@app.get("/favicon.ico")
def pwa_favicon():
    return FileResponse(STATIC / "pwa" / "favicon.ico", media_type="image/x-icon")


@app.get("/product-images/{name}")
def product_image(name: str):
    from .product_images import image_dir

    safe = Path(name).name
    if not safe.endswith(".webp"):
        return JSONResponse({"error": "not found"}, status_code=404)
    path = image_dir() / safe
    if not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})



def current_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return None
    user = db.get_user_by_id(uid)
    if not user:
        request.session.clear()
    return user


def _sign_in(request: Request, user: dict[str, Any]) -> None:
    request.session["uid"] = user["id"]
    request.session["email"] = user["email"]


def _safe_next(target: str) -> str:
    """Only allow redirects back into this app, never to another host."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return "/dashboard"
    return target


_REMEMBER = ("/dashboard", "/purchases", "/stores")
_DASHBOARD_KEYS = (
    "range",
    "grain",
    "start",
    "end",
    "day",
    "dept",
    "category",
    "today",
    "prev_range",
    "prev_grain",
    "prev_start",
    "prev_end",
    "prev_day",
    "prev_dept",
    "prev_category",
)
_PURCHASES_KEYS = ("sort", "dir", "range", "grain", "start", "end", "day", "dept", "store", "category")
_WINDOW_KEYS = ("range", "grain", "start", "end", "day", "dept")


def _remember(request: Request, user: dict[str, Any] | None) -> None:
    if not user:
        return
    path = request.url.path
    if not any(path == p or path.startswith(p + "/") for p in _REMEMBER):
        return
    db.set_last_view(user["id"], path, str(request.url.query or ""))


def _encode_tab(keys: tuple[str, ...], values: dict[str, str]) -> str:
    return urlencode([(key, values[key]) for key in keys if values.get(key)])


def _tab_query_from_request(
    user_id: int,
    path: str,
    request: Request,
    resolved: dict[str, str],
    keys: tuple[str, ...],
) -> str:
    """Keep this tab's last filters when the request omitted them.

    Chip hrefs send the keys they mean to change. A dock tap restores the
    saved query. Neither may copy the other tab's window.
    """
    saved = {
        key: val
        for key, val in parse_qsl(db.get_tab_query(user_id, path), keep_blank_values=True)
        if key in keys
    }
    params = request.query_params
    window_nav = any(key in params for key in _WINDOW_KEYS)
    out = dict(saved)
    for key in keys:
        if key in params:
            val = (resolved.get(key) or "").strip()
            if val:
                out[key] = val
            else:
                out.pop(key, None)
        elif key not in out:
            val = (resolved.get(key) or "").strip()
            if val:
                out[key] = val
    if window_nav and "day" not in params:
        out.pop("day", None)
    if window_nav and "dept" not in params:
        out.pop("dept", None)
    if "range" in params and params.get("range") != "custom":
        out.pop("start", None)
        out.pop("end", None)
    if out.get("range") != "custom":
        out.pop("start", None)
        out.pop("end", None)
    # Range/grain (and the store form) carry store= when stores are selected.
    # An omitted store on those URLs is a clear, not a keep.
    if "store" not in params and ("range" in params or "grain" in params):
        out.pop("store", None)
    if "category" not in params and ("range" in params or "grain" in params):
        out.pop("category", None)
    return _encode_tab(keys, out)


def _remember_dashboard(
    user: dict[str, Any],
    request: Request,
    range_key: str,
    grain: str,
    start: str,
    end: str,
    day: str,
    dept: str = "",
    category_q: str = "",
    *,
    today_mode: bool = False,
    prev: dict[str, str] | None = None,
) -> None:
    resolved = {
        "range": range_key,
        "grain": grain,
        "start": start if range_key == "custom" else "",
        "end": end if range_key == "custom" else "",
        "day": day,
        "dept": dept,
        "category": category_q,
        "today": "1" if today_mode else "",
    }
    if today_mode and prev:
        for key, val in prev.items():
            if val:
                resolved[key] = val
    q = _tab_query_from_request(
        user["id"],
        "/dashboard",
        request,
        resolved,
        _DASHBOARD_KEYS,
    )
    db.set_last_view(user["id"], "/dashboard", q)


def _dashboard_prev_snapshot(
    request: Request,
    range_key: str,
    grain: str,
    start: str,
    end: str,
    day: str,
    dept: str,
    category_q: str,
) -> dict[str, str]:
    params = request.query_params
    if params.get("today") == "1" and params.get("prev_range"):
        return {
            "prev_range": params.get("prev_range") or "",
            "prev_grain": params.get("prev_grain") or "",
            "prev_start": params.get("prev_start") or "",
            "prev_end": params.get("prev_end") or "",
            "prev_day": params.get("prev_day") or "",
            "prev_dept": params.get("prev_dept") or "",
            "prev_category": params.get("prev_category") or "",
        }
    return {
        "prev_range": range_key,
        "prev_grain": grain,
        "prev_start": start if range_key == "custom" else "",
        "prev_end": end if range_key == "custom" else "",
        "prev_day": day,
        "prev_dept": dept,
        "prev_category": category_q,
    }


def _today_query_tail(today_mode: bool, prev: dict[str, str]) -> str:
    if not today_mode:
        return ""
    from urllib.parse import urlencode

    q: dict[str, str] = {"today": "1"}
    for key in ("prev_range", "prev_grain", "prev_start", "prev_end", "prev_day", "prev_dept", "prev_category"):
        if prev.get(key):
            q[key] = prev[key]
    return "&" + urlencode(q)


def _today_chip_href(
    *,
    today_mode: bool,
    prev: dict[str, str],
    range_key: str,
    grain: str,
    start: str,
    end: str,
    day: str,
    dept: str,
    category_q: str,
    category_tail: str,
) -> str:
    from urllib.parse import urlencode

    if today_mode:
        q = {
            "range": prev.get("prev_range") or range_key,
            "grain": prev.get("prev_grain") or grain,
        }
        if prev.get("prev_dept"):
            q["dept"] = prev["prev_dept"]
        if prev.get("prev_category"):
            q["category"] = prev["prev_category"]
        if prev.get("prev_day"):
            q["day"] = prev["prev_day"]
        if (prev.get("prev_range") or range_key) == "custom":
            if prev.get("prev_start"):
                q["start"] = prev["prev_start"]
            if prev.get("prev_end"):
                q["end"] = prev["prev_end"]
        return "/dashboard?" + urlencode(q)
    q = {
        "today": "1",
        "range": range_key,
        "grain": grain,
        "prev_range": prev["prev_range"],
        "prev_grain": prev["prev_grain"],
    }
    if dept:
        q["dept"] = dept
        q["prev_dept"] = dept
    if category_q:
        q["category"] = category_q
        q["prev_category"] = category_q
    if day:
        q["day"] = day
        q["prev_day"] = day
    if range_key == "custom":
        if start:
            q["start"] = start
            q["prev_start"] = start
        if end:
            q["end"] = end
            q["prev_end"] = end
    return "/dashboard?" + urlencode(q)


def _remember_purchases(
    user: dict[str, Any],
    request: Request,
    range_key: str,
    grain: str,
    start: str,
    end: str,
    day: str,
    sort: str,
    direction: str,
    dept: str,
    store_q: str,
    category_q: str = "",
) -> None:
    q = _tab_query_from_request(
        user["id"],
        "/purchases",
        request,
        {
            "sort": sort,
            "dir": direction,
            "range": range_key,
            "grain": grain,
            "start": start if range_key == "custom" else "",
            "end": end if range_key == "custom" else "",
            "day": day,
            "dept": dept,
            "store": store_q,
            "category": category_q,
        },
        _PURCHASES_KEYS,
    )
    db.set_last_view(user["id"], "/purchases", q)


def _last_url(user: dict[str, Any]) -> str:
    rec = db.get_last_view(user["id"])
    if not rec or not rec.get("path"):
        return "/dashboard"
    path = rec["path"]
    if not any(path == p or path.startswith(p + "/") for p in _REMEMBER):
        return "/dashboard"
    q = rec.get("query") or ""
    return path + (("?" + q) if q else "")


def _authenticate(email: str, password: str, intent: str) -> tuple[dict[str, Any] | None, str, bool]:
    """intent=signup creates only; intent=signin signs in only. Returns (user, error, created)."""
    email = (email or "").strip().lower()
    password = password or ""
    intent = (intent or "signup").strip().lower()
    if intent not in ("signup", "signin"):
        intent = "signup"
    if not email or "@" not in email:
        return None, "Enter the email you want to use for Bring Fast.", False
    if not password:
        return None, "Enter your password.", False
    user = db.get_user_by_email(email)
    if intent == "signin":
        if not user:
            return None, "No Bring Fast account for this email. Create one.", False
        if db.verify_password(user, password):
            return user, "", False
        return None, "That password does not match this Bring Fast account.", False
    if user:
        return None, "This email already has a Bring Fast account. Sign in.", False
    if len(password) < 6:
        return None, "Password must be at least 6 characters.", False
    try:
        return db.create_user(email, password), "", True
    except ValueError as e:
        return None, str(e), False


def _login_page(
    request: Request,
    *,
    error: str = "",
    email: str = "",
    next_url: str = "/",
    status_code: int = 200,
    intent: str = "signup",
    notice: str = "",
):
    intent = "signin" if (intent or "").lower() == "signin" else "signup"
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "user": None,
            "title": "Sign in · Bring Fast" if intent == "signin" else "Create account · Bring Fast",
            "error": error,
            "email": email,
            "next": _safe_next(next_url),
            "intent": intent,
            "notice": notice,
        },
        status_code=status_code,
    )


def _is_loopback(url_or_host: str) -> bool:
    raw = (url_or_host or "").strip()
    if not raw:
        return True
    if "://" not in raw:
        raw = f"http://{raw}"
    host = (urlsplit(raw).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "0.0.0.0", "::1")


def _request_origin(request: Request | None) -> str:
    if request is None:
        return ""
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host = forwarded_host or (request.headers.get("host") or "").strip()
    if not host:
        return ""
    scheme = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    return f"{scheme or request.url.scheme}://{host}".rstrip("/")


def _issuer(request: Request | None = None) -> str:
    """Public base URL clients should call back on.

    A public host on the incoming request wins so a stale BRINGFAST_PUBLIC_URL
    cannot advertise a different origin than the one Grok actually called.
    Loopback PUBLIC_URL values are ignored for the same reason.
    """
    origin = _request_origin(request)
    if origin and not _is_loopback(origin):
        return origin
    if PUBLIC_URL and not _is_loopback(PUBLIC_URL):
        return PUBLIC_URL
    if origin:
        return origin
    return PUBLIC_URL or f"http://127.0.0.1:{PORT}"


def mcp_url(request: Request | None = None) -> str:
    return f"{_issuer(request)}/mcp"


@app.get("/", response_class=HTMLResponse)
def home(request: Request, next: str = "/", welcome: int = 0, notice: str = "", mode: str = ""):
    user = current_user(request)
    if not user:
        return _login_page(request, next_url=next, intent="signin" if mode == "signin" else "signup")
    if welcome:
        return RedirectResponse("/stores?welcome=1", status_code=303)
    return RedirectResponse(_last_url(user), status_code=303)


def _live(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
    """Update state is never worth caching — an offline client wants the truth or nothing."""
    return JSONResponse(payload, status_code=status_code, headers={"Cache-Control": "no-store"})


@app.get("/update/status")
def update_status(request: Request, fetch: int = 0):
    user = current_user(request)
    if not user:
        return _live({"ok": False, "error": "login required"}, status_code=401)
    saved = update.load_saved()
    if fetch or not saved:
        return _live({**update.status(fetch=True), "boot": BOOT_ID})
    return _live({**saved, "boot": BOOT_ID})


@app.post("/update/apply")
def update_apply(request: Request):
    user = current_user(request)
    if not user:
        return _live({"ok": False, "error": "login required"}, status_code=401)
    return _live({**update.apply(), "boot": BOOT_ID})


@app.get("/dashboard", response_class=HTMLResponse)
def spend_home(
    request: Request,
    range: str = "1m",
    start: str = "",
    end: str = "",
    grain: str = "monthly",
    day: str = "",
    dept: str = "",
    category: str = "",
    today: str = "",
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?mode=signin&next=/dashboard", status_code=303)
    if not request.query_params:
        q = db.get_tab_query(user["id"], "/dashboard")
        if q:
            return RedirectResponse("/dashboard?" + q, status_code=303)
    grain = grain if grain in purchases.GRAINS else "monthly"
    dept = purchases.normalize_dept(dept)
    categories = _request_categories(request, category)
    category_q = purchases.category_query(categories)
    _, category_tail = _filter_tails(category_q=category_q)
    today_mode = (today or "").strip() == "1"
    dubai_day = purchases.dubai_today(end=end)
    prev = _dashboard_prev_snapshot(request, range, grain, start, end, day, dept, category_q)
    today_href = _today_chip_href(
        today_mode=today_mode,
        prev=prev,
        range_key=range,
        grain=grain,
        start=start,
        end=end,
        day=day,
        dept=dept,
        category_q=category_q,
        category_tail=category_tail,
    )
    since, until, range_key = purchases.resolve_window(user["id"], range, start, end)
    chart_since, chart_until, _ = (
        purchases.resolve_window(user["id"], "all", start, end)
        if today_mode
        else (since, until, range_key)
    )
    raw_days = purchases.daily_spend(
        user["id"], since=chart_since, until=chart_until, dept=dept, categories=categories
    )
    days = purchases.bucket_series(raw_days, grain)
    if grain == "daily":
        days = purchases.fill_daily_calendar(days, chart_since, chart_until)
    days = purchases.mark_day_windows(days, grain, chart_since, chart_until, day)
    focus_since, focus_until = purchases.focus_products_window(day, grain, since, until)
    focus_end = (focus_until or until).isoformat()
    if today_mode:
        today_total = purchases.day_spend(
            user["id"], dubai_day, dept=dept, categories=categories
        )
        total_spend = today_total
        head = {
            "period_avg": today_total,
            "period_word": "Today",
            "period_unit": "",
            "periods_text": "",
        }
        top = purchases.list_today_products(
            user["id"],
            dubai_day,
            dept=dept,
            categories=categories,
            limit=8,
        )
        trend = purchases.price_trend(
            user["id"],
            since=dubai_day.isoformat(),
            until=dubai_day,
            grain="daily",
            dept=dept,
            categories=categories,
        )
        range_start = dubai_day.isoformat()
        range_end = dubai_day.isoformat()
    else:
        card_days = [
            d
            for d in raw_days
            if (not focus_since or (d.get("date") or "") >= focus_since)
            and (d.get("date") or "") <= focus_end
        ]
        total_spend = sum(d["spend"] for d in card_days)
        head = purchases.period_headline(total_spend, focus_since, focus_until, grain)
        top = purchases.list_products(
            user["id"],
            sort="spend",
            direction="desc",
            since=focus_since,
            until=focus_until,
            dept=dept,
            categories=categories,
            limit=8,
        )
        trend = purchases.price_trend(
            user["id"],
            since=focus_since,
            until=focus_until,
            grain=grain,
            dept=dept,
            categories=categories,
        )
        range_start = focus_since or ""
        range_end = focus_end
    _remember_dashboard(
        user,
        request,
        range_key,
        grain,
        start,
        end,
        day,
        dept,
        category_q,
        today_mode=today_mode,
        prev=prev if today_mode else None,
    )
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": user,
            "title": "Dashboard · Bring Fast",
            "tab": "dashboard",
            "days": days,
            "dash_spend": total_spend,
            "period_avg": head["period_avg"],
            "period_word": head["period_word"],
            "period_unit": head["period_unit"],
            "periods_text": head["periods_text"],
            "range_start": range_start,
            "range_end": range_end,
            "products": top,
            "trend": trend,
            "grain": grain,
            "range": range_key,
            "start": start or (since or ""),
            "end": end or until.isoformat(),
            "day": day,
            "dept": dept,
            "categories": categories,
            "category_q": category_q,
            "category_tail": category_tail,
            "macro_options": macro_categories.MACRO_CATEGORIES,
            "macro_labels": macro_categories.MACRO_LABELS,
            "today_mode": today_mode,
            "today_href": today_href,
            "today_tail": _today_query_tail(today_mode, prev),
            "dubai_day": dubai_day.isoformat(),
        },
    )


@app.get("/stores", response_class=HTMLResponse)
def stores_page(request: Request, welcome: int = 0, notice: str = ""):
    """The list reads out what each store is and can do, and nothing more.

    A store is changed inside its own page, so opening this tab never puts a
    login form on screen for the browser's password manager to jump on, and a
    stray tap cannot turn a store off.
    """
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?mode=signin&next=/stores", status_code=303)
    _remember(request, user)
    probes = db.store_probes_for_user(user["id"])
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "retailers": [
                {**r, "caps": db.store_capabilities(r["id"], probes=probes)}
                for r in db.list_retailer_accounts(user["id"])
            ],
            "mcp_url": mcp_url(request),
            "title": "Stores · Bring Fast",
            "notice": (
                f"Welcome to Bring Fast, {user['email']}. Open Grandiose below to link it."
                if welcome
                else notice
            ),
            "tab": "stores",
        },
    )


@app.get("/stores/{retailer}", response_class=HTMLResponse)
def store_page(request: Request, retailer: str, notice: str = "", edit: int = 0):
    """One store, and everywhere it can be changed.

    A saved login is printed as plain text until `edit` asks for the fields, so
    a password manager is only offered a form when the person wants one.
    """
    user = current_user(request)
    if not user:
        return RedirectResponse(f"/login?mode=signin&next=/stores/{retailer}", status_code=303)
    store = next((r for r in db.list_retailer_accounts(user["id"]) if r["id"] == retailer), None)
    if not store:
        return RedirectResponse("/stores", status_code=303)
    _remember(request, user)
    return templates.TemplateResponse(
        request,
        "store.html",
        {
            "user": user,
            "store": store,
            "caps": db.store_capabilities(retailer, user_id=user["id"]),
            "edit": bool(edit) or not store["login_email"],
            "title": f"{store['name']} · Bring Fast",
            "notice": notice,
            "tab": "stores",
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", mode: str = ""):
    """A signed-in user never sees a login form again; they land where they were going."""
    if current_user(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return _login_page(request, next_url=next, intent="signin" if mode == "signin" else "signup")


def _sign_in_response(request: Request, email: str, password: str, next_url: str, intent: str = "signup"):
    user, error, created = _authenticate(email, password, intent)
    if not user:
        return _login_page(
            request,
            error=error,
            email=(email or "").strip(),
            next_url=next_url,
            status_code=401,
            intent=intent,
        )
    _sign_in(request, user)
    target = _safe_next(next_url)
    if created and target in ("/", "/dashboard"):
        target = "/stores?welcome=1"
    return RedirectResponse(target, status_code=303)


@app.post("/login")
def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
    intent: str = Form("signup"),
):
    return _sign_in_response(request, email, password, next, intent)


@app.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


def _auth_page(request: Request, template: str, *, title: str, error: str = "", notice: str = "", token: str = "", status_code: int = 200):
    return templates.TemplateResponse(
        request,
        template,
        {"user": None, "title": title, "error": error, "notice": notice, "token": token},
        status_code=status_code,
    )


@app.get("/forgot", response_class=HTMLResponse)
def forgot_page(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return _auth_page(request, "forgot.html", title="Forgot password · Bring Fast")


@app.post("/forgot")
def forgot_submit(request: Request, email: str = Form("")):
    token = db.create_reset_token(email)
    if token:
        link = f"{_issuer(request)}/reset?token={token}"
        try:
            _send_reset_email((email or "").strip().lower(), link)
        except Exception:
            pass
    return _auth_page(
        request,
        "forgot.html",
        title="Forgot password · Bring Fast",
        notice="If that email has a Bring Fast account, we sent a reset link. It expires in one hour.",
    )


@app.get("/reset", response_class=HTMLResponse)
def reset_page(request: Request, token: str = ""):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    if not token:
        return _auth_page(request, "forgot.html", title="Forgot password · Bring Fast", error="Missing reset token.")
    return _auth_page(request, "reset.html", title="Reset password · Bring Fast", token=token)


@app.post("/reset")
def reset_submit(request: Request, token: str = Form(""), password: str = Form("")):
    user = db.consume_reset_token(token)
    if not user:
        return _auth_page(
            request,
            "forgot.html",
            title="Forgot password · Bring Fast",
            error="This reset link is invalid or expired. Request a new one.",
            status_code=400,
        )
    try:
        db.set_password(user["id"], password)
    except ValueError as e:
        return _auth_page(
            request, "reset.html", title="Reset password · Bring Fast", token=token, error=str(e), status_code=400
        )
    return _login_page(request, notice="Password updated. Sign in.", intent="signin")


def _send_reset_email(to_email: str, link: str) -> None:
    helper = (
        Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
        / "skills/productivity/google-workspace/scripts/google_api.py"
    )
    if not helper.exists():
        return
    import subprocess

    subprocess.run(
        [
            sys.executable,
            str(helper),
            "gmail",
            "send",
            "--to",
            to_email,
            "--subject",
            "Reset your Bring Fast password",
            "--body",
            f"Reset your Bring Fast password:\n\n{link}\n\nThis link expires in one hour.",
        ],
        check=False,
        timeout=30,
    )


@app.api_route("/register", methods=["POST", "OPTIONS"])
async def register(request: Request):
    """Humans post a form here; OAuth clients post dynamic-registration JSON here.

    Both used to be mounted on this path, so whichever route FastAPI matched first
    rejected the other caller.
    """
    if request.method == "OPTIONS":
        return _cors_preflight()
    if "json" in (request.headers.get("content-type") or "").lower():
        return await oauth_register(request)
    try:
        form = await request.form()
    except Exception:
        form = {}
    return _sign_in_response(
        request,
        str(form.get("email") or ""),
        str(form.get("password") or ""),
        str(form.get("next") or "/"),
        str(form.get("intent") or "signup"),
    )


@app.post("/retailers/{retailer}")
def save_retailer(
    request: Request,
    retailer: str,
    email: str = Form(...),
    password: str = Form(""),
    address: str = Form(""),
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    if retailer not in {r["id"] for r in db.RETAILERS}:
        return RedirectResponse("/", status_code=303)
    db.set_retailer_account(user["id"], retailer, email.strip(), password, address)
    return RedirectResponse(f"/stores/{retailer}", status_code=303)


@app.post("/retailers/{retailer}/clear")
def clear_retailer(request: Request, retailer: str):
    user = current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    db.clear_retailer_account(user["id"], retailer)
    store = db.store_meta(retailer)
    if not store:
        return RedirectResponse("/stores", status_code=303)
    note = f"{store['name']}: login removed"
    return RedirectResponse(f"/stores/{retailer}?" + urlencode({"notice": note}), status_code=303)


@app.post("/retailers/{retailer}/check")
def check_retailer(request: Request, retailer: str):
    """Try the saved store login now, instead of finding out during a checkout."""
    user = current_user(request)
    store = db.store_meta(retailer)
    if not user or not store:
        return RedirectResponse("/", status_code=303)
    creds = db.get_retailer_secret(user["id"], retailer) or {}
    result = checkout.verify_login(
        store=retailer,
        email=creds.get("email") or "",
        password=creds.get("password") or "",
    )
    if result.get("ok"):
        note = f"{store['name']}: login works" + (" (session reused)" if result.get("reused") else "")
    else:
        note = f"{store['name']}: {result.get('error') or 'login failed'}"
    return RedirectResponse(f"/stores/{retailer}?" + urlencode({"notice": note}), status_code=303)


@app.post("/retailers/{retailer}/toggle")
def toggle_retailer(request: Request, retailer: str):
    user = current_user(request)
    store = db.store_meta(retailer)
    if not user or not store:
        return RedirectResponse("/", status_code=303)
    db.set_store_enabled(retailer, not db.is_store_enabled(retailer))
    on = db.is_store_enabled(retailer)
    note = f"{store['name']}: {'enabled' if on else 'disabled'}"
    return RedirectResponse(f"/stores/{retailer}?" + urlencode({"notice": note}), status_code=303)


@app.post("/rotate-token")
def rotate(request: Request):
    user = current_user(request)
    if user:
        db.rotate_token(user["id"])
    return RedirectResponse("/stores", status_code=303)


def _shelf_url(**params: Any) -> str:
    """Where the tab asks for the next batch of its shelf.

    Every filter the page resolved travels with it, so a batch is the same
    shelf continued and not a second one under a different window.
    """
    return "/purchases/rows?" + urlencode({k: (v if v is not None else "") for k, v in params.items()})


def _request_stores(request: Request, store: str = "") -> list[str]:
    raw = list(request.query_params.getlist("store"))
    if store and store not in raw:
        raw.append(store)
    return purchases.normalize_stores(raw)


def _request_categories(request: Request, category: str = "") -> list[str]:
    raw = list(request.query_params.getlist("category"))
    if category and category not in raw:
        raw.append(category)
    return purchases.normalize_categories(raw)


def _filter_tails(store_q: str = "", category_q: str = "") -> tuple[str, str]:
    store_tail = f"&store={store_q}" if store_q else ""
    category_tail = f"&category={category_q}" if category_q else ""
    return store_tail, category_tail


@app.get("/purchases", response_class=HTMLResponse)
def purchases_page(
    request: Request,
    sort: str = "spend",
    dir: str = "desc",
    range: str = "all",
    start: str = "",
    end: str = "",
    grain: str = "daily",
    dept: str = "",
    day: str = "",
    store: str = "",
    category: str = "",
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?mode=signin&next=/purchases", status_code=303)
    if not request.query_params:
        q = db.get_tab_query(user["id"], "/purchases")
        if q:
            return RedirectResponse("/purchases?" + q, status_code=303)
    sort = sort if sort in purchases.SORTS else "spend"
    direction = "asc" if dir == "asc" else "desc"
    grain = grain if grain in purchases.GRAINS else "daily"
    dept = purchases.normalize_dept(dept)
    stores = _request_stores(request, store)
    store_q = purchases.store_query(stores)
    categories = _request_categories(request, category)
    category_q = purchases.category_query(categories)
    store_tail, category_tail = _filter_tails(store_q, category_q)
    since, until, range_key = purchases.resolve_window(user["id"], range, start, end)
    raw_days = purchases.daily_spend(
        user["id"], since=since, until=until, dept=dept, stores=stores, categories=categories
    )
    days = purchases.bucket_series(raw_days, grain)
    if grain == "daily":
        days = purchases.fill_daily_calendar(days, since, until)
    days = purchases.mark_day_windows(days, grain, since, until, day)
    focus_since, focus_until = purchases.focus_products_window(day, grain, since, until)
    focus_end = (focus_until or until).isoformat()
    card_days = [
        d
        for d in raw_days
        if (not focus_since or (d.get("date") or "") >= focus_since)
        and (d.get("date") or "") <= focus_end
    ]
    total_spend = sum(d["spend"] for d in card_days)
    head = purchases.period_headline(total_spend, focus_since, focus_until, grain)
    shelf = purchases.product_shelf(
        user["id"],
        sort=sort,
        direction=direction,
        since=focus_since,
        until=focus_until,
        dept=dept,
        stores=stores,
        categories=categories,
    )
    first = purchases.shelf_batch(shelf, 0, purchases.SHELF_BATCH)
    _remember_purchases(
        user,
        request,
        range_key,
        grain,
        start,
        end,
        day,
        sort,
        direction,
        dept,
        store_q,
        category_q,
    )
    return templates.TemplateResponse(
        request,
        "purchases.html",
        {
            "user": user,
            "title": "Purchases · Bring Fast",
            "tab": "purchases",
            "products": first["rows"],
            "low": False,
            "shelf_total": first["total"],
            "shelf_next": first["next"],
            "shelf_batch": purchases.SHELF_BATCH,
            "shelf_url": _shelf_url(
                sort=sort,
                dir=direction,
                range=range_key,
                grain=grain,
                dept=dept,
                day=day,
                start=start,
                end=end,
                store=store_q,
                category=category_q,
            ),
            "days": days,
            # Only what a tap on a bar reads: over years of daily bars the rest
            # would weigh more than the products.
            "days_json": json.dumps(purchases.day_marks(days)),
            "dash_spend": total_spend,
            "period_avg": head["period_avg"],
            "period_word": head["period_word"],
            "period_unit": head["period_unit"],
            "periods_text": head["periods_text"],
            "range_start": focus_since or "",
            "range_end": focus_end,
            "dash_receipts": (
                sum(d["count"] for d in card_days)
                if dept
                else purchases.invoice_count(
                    user["id"],
                    since=focus_since,
                    until=focus_until,
                    include_undated=(range_key == "all" and not day),
                    stores=stores,
                )
            ),
            "dash_receipts_total": purchases.invoice_count(user["id"], include_undated=True),
            "sort": sort,
            "dir": direction,
            "range": range_key,
            "grain": grain,
            "dept": dept,
            "stores": stores,
            "store_q": store_q,
            "store_tail": store_tail,
            "store_options": purchases.user_stores(user["id"]),
            "categories": categories,
            "category_q": category_q,
            "category_tail": category_tail,
            "macro_options": macro_categories.MACRO_CATEGORIES,
            "macro_labels": macro_categories.MACRO_LABELS,
            "start": start or (since or ""),
            "end": end or until.isoformat(),
            "day": day,
        },
    )


@app.get("/purchases/rows", response_class=HTMLResponse)
def purchases_rows(
    request: Request,
    sort: str = "spend",
    dir: str = "desc",
    range: str = "all",
    start: str = "",
    end: str = "",
    grain: str = "daily",
    dept: str = "",
    day: str = "",
    store: str = "",
    category: str = "",
    offset: int = 0,
    limit: int = purchases.SHELF_BATCH,
):
    """One batch of the purchases shelf, for the tab that is already on screen.

    The board is drawn from the page itself; the products arrive here, a batch
    at a time, so opening the tab never waits on the whole shelf.
    """
    user = current_user(request)
    if not user:
        return HTMLResponse("", status_code=401)
    sort = sort if sort in purchases.SORTS else "spend"
    direction = "asc" if dir == "asc" else "desc"
    grain = grain if grain in purchases.GRAINS else "daily"
    dept = purchases.normalize_dept(dept)
    stores = _request_stores(request, store)
    categories = _request_categories(request, category)
    since, until, _range_key = purchases.resolve_window(user["id"], range, start, end)
    focus_since, focus_until = purchases.focus_products_window(day, grain, since, until)
    shelf = purchases.product_shelf(
        user["id"],
        sort=sort,
        direction=direction,
        since=focus_since,
        until=focus_until,
        dept=dept,
        stores=stores,
        categories=categories,
    )
    batch = purchases.shelf_batch(shelf, offset, limit)
    return templates.TemplateResponse(
        request,
        "_shelf_batch.html",
        {
            "user": user,
            "products": batch["rows"],
            "offset": batch["offset"],
            "next": batch["next"],
            "total": batch["total"],
            # A batch that arrived after the page never outranks what the
            # reader asked for next.
            "low": True,
            "range": _range_key,
            "start": start,
            "end": end,
        },
    )


@app.get("/purchases/{key:path}", response_class=HTMLResponse)
def purchase_detail(request: Request, key: str, range: str = "all", start: str = "", end: str = ""):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?mode=signin&next=/purchases", status_code=303)
    since, until, range_key = purchases.resolve_window(user["id"], range, start, end)
    key = purchases.canonical_key(key)
    product = purchases.product_purchases(user["id"], key, since=since, until=until)
    if not product:
        return RedirectResponse("/purchases", status_code=303)
    _remember(request, user)
    return templates.TemplateResponse(
        request,
        "purchase_detail.html",
        {
            "user": user,
            "title": f"{product['name']} · Bring Fast",
            "tab": "purchases",
            "product": product,
            "compare": compare.compare_board(user["id"], key, product.get("last_price")),
            "range": range_key,
            "start": start or (since or ""),
            "end": end or until.isoformat(),
        },
    )


@app.post("/purchases/{key:path}/compare/{retailer}")
def refresh_compare(request: Request, key: str, retailer: str, range: str = "all", start: str = "", end: str = ""):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?mode=signin&next=/purchases", status_code=303)
    if not db.store_meta(retailer):
        return RedirectResponse(f"/purchases/{key}", status_code=303)
    product = purchases.product_purchases(user["id"], key)
    if not product:
        return RedirectResponse("/purchases", status_code=303)
    names = [product.get("official_name") or "", product.get("receipt_name") or "", product.get("name") or ""]
    compare.refresh_store(
        user["id"],
        key,
        retailer,
        product.get("barcodes") or ([product.get("barcode")] if product.get("barcode") else []),
        names,
        source="manual",
    )
    tail = f"?range={range}"
    if range == "custom":
        tail += f"&start={start}&end={end}"
    return RedirectResponse(f"/purchases/{key}{tail}", status_code=303)


def _local_next(raw: str, fallback: str) -> str:
    path = (raw or "").strip()
    dest = ""
    if path.startswith("/") and not path.startswith("//"):
        dest = path
    else:
        parts = urlsplit(path)
        if parts.scheme and parts.netloc and parts.path.startswith("/"):
            dest = parts.path
            if parts.query:
                dest += "?" + parts.query
    if not dest:
        return fallback
    if dest.startswith("/purchases/rows"):
        dest = "/purchases" + dest[len("/purchases/rows"):]
        split = dest.split("?", 1)
        if len(split) == 2:
            kept = []
            for bit in split[1].split("&"):
                key = bit.split("=", 1)[0]
                if key not in ("offset", "limit"):
                    kept.append(bit)
            dest = split[0] + (("?" + "&".join(kept)) if kept else "")
    return dest


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    xhr = (request.headers.get("x-requested-with") or "").lower()
    if xhr in ("xmlhttprequest", "fetch"):
        return True
    return "application/json" in accept and "text/html" not in accept.split(",")[0]


@app.post("/purchases/{key:path}/vote")
def vote_likely(request: Request, key: str, vote: str = Form(""), next: str = Form("")):
    user = current_user(request)
    wants = _wants_json(request)
    if not user:
        if wants:
            return JSONResponse({"error": "auth"}, status_code=401)
        return RedirectResponse("/login?mode=signin&next=/purchases", status_code=303)
    key = purchases.canonical_key(key)
    dest = _local_next(next or request.headers.get("referer") or "", f"/purchases/{key}")
    product = purchases.product_purchases(user["id"], key)
    if not product:
        if wants:
            return JSONResponse({"error": "not found"}, status_code=404)
        return RedirectResponse("/purchases", status_code=303)
    wanted = (vote or "").strip().lower()
    if wanted not in ("up", "down"):
        if wants:
            return JSONResponse({"error": "vote"}, status_code=400)
        return RedirectResponse(dest, status_code=303)
    push = forecast.set_vote(user["id"], key, wanted)
    purchases.forget_shelf()
    product = purchases.product_purchases(user["id"], key) or product
    payload = {
        "key": key,
        "likely": int(product.get("likely") or 0),
        "push": int(push),
        "vote": forecast.vote_label(push),
    }
    if wants:
        return JSONResponse(payload)
    return RedirectResponse(dest, status_code=303)


@app.get("/receipts/{retailer}/{invoice_no}", response_class=HTMLResponse)
def receipt_view(request: Request, retailer: str, invoice_no: str):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?mode=signin", status_code=303)
    path = purchases.receipt_path(retailer, invoice_no)
    pages: list[int] = []
    built = None
    if path and path.suffix.lower() == ".pdf":
        pages = list(range(1, len(purchases.receipt_page_pngs(path)) + 1))
    elif path and path.suffix.lower() == ".html":
        return FileResponse(path, media_type="text/html")
    else:
        built = purchases.invoice_receipt(user["id"], retailer, invoice_no)
        if not built:
            return Response("Receipt not found", status_code=404)
    return templates.TemplateResponse(
        request,
        "receipt.html",
        {
            "title": f"Receipt {invoice_no}",
            "user": user,
            "retailer": retailer,
            "invoice_no": invoice_no,
            "pages": pages,
            "built": built,
        },
    )


@app.get("/receipts/{retailer}/{invoice_no}/file.pdf")
def receipt_pdf(request: Request, retailer: str, invoice_no: str):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?mode=signin", status_code=303)
    path = purchases.receipt_path(retailer, invoice_no)
    if not path:
        return Response("Receipt not found", status_code=404)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{invoice_no}.pdf",
        content_disposition_type="inline",
    )


@app.get("/receipts/{retailer}/{invoice_no}/p/{n}")
def receipt_page(request: Request, retailer: str, invoice_no: str, n: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?mode=signin", status_code=303)
    path = purchases.receipt_path(retailer, invoice_no)
    if not path:
        return Response("Receipt not found", status_code=404)
    pngs = purchases.receipt_page_pngs(path)
    if n < 1 or n > len(pngs):
        return Response("Page not found", status_code=404)
    return FileResponse(pngs[n - 1], media_type="image/png")


@app.get("/health")
def health(request: Request):
    base = _issuer(request)
    return {
        "ok": True,
        "server": "Bring Fast",
        "version": __version__,
        "boot": BOOT_ID,
        "revision": (update.load_saved().get("local") or ""),
        "public_url": base,
        "mcp_url": f"{base}/mcp",
        "description": mcp_skill.DESCRIPTION,
        "public_url_env": PUBLIC_URL or None,
    }


def _user_from_request(request: Request):
    auth = request.headers.get("authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
    return db.get_user_by_token(token)


def _store_tools() -> list[dict[str, Any]]:
    tools = [
        {
            "name": "bf_whoami",
            "description": (
                "THIS user's snapshot only: email, which supermarket logins are saved, and `version` "
                "(this Bring Fast build). "
                "Does NOT return order history or spend. For last month / invoices use bf_spend or bf_orders. "
                "linked=true means the store login is saved. Do not say logins are missing when linked is true. "
                "Delivery address lives on the supermarket account, not on Bring Fast. "
                "Use version to answer whether a given 1.10.x is live."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "bf_stores",
            "description": (
                "THIS user's stores and saved logins. Includes `version` (this Bring Fast build). "
                "linked=true means the supermarket login is saved. "
                "Does NOT include order history, spend totals, or a last-seen cart. "
                "For invoices / last month use bf_spend or bf_orders. "
                "Address is the one on the supermarket account — do not ask to set it on Bring Fast."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "bf_search",
            "description": (
                "Search ALL supermarkets for price comparison. "
                "Official cart on Grandiose, Union Coop, and Carrefour when enabled. "
                "Checkout: grandiose_checkout action=prepare (default) or "
                "action=place payment_method=ccod|cashondelivery (Magento placeOrder; no card number). "
                "Union Coop prepares only. Payment stays on the store site."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "retailer": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "bf_cart",
            "description": (
                "Official supermarket account cart (not a local Bring Fast cart). "
                "retailer=grandiose|unioncoop|carrefour. Waitrose and Spinneys are search-only. "
                "action=list|add|set|remove|clear. "
                "list aliases: get, read, show, view. add needs product_id or name, plus qty. "
                "Magento remove (Grandiose GraphQL, Union Coop REST) matches a live cart line "
                "by sku, item_id, or name — never success if the line is still there. "
                "Use this when carrefour_cart / grandiose_cart / unioncoop_cart is missing from the client tool list: "
                "bf_cart retailer=carrefour action=list reads the official Carrefour UAE cart. "
                "Carrefour add binds the MAF delivery store from the account location; "
                "error_code=needs_delivery_slot means list the cart and retry. "
                "clear (also create/empty/new) empties the official cart. "
                "Checkout: grandiose_checkout action=prepare (default) or "
                "action=place payment_method=ccod|cashondelivery (Magento placeOrder; no card number). "
                "Union Coop prepares only. MCP does not charge a card."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "retailer": {"type": "string"},
                    "action": {"type": "string"},
                    "product_id": {"type": "string"},
                    "name": {"type": "string"},
                    "item_id": {"type": "string"},
                    "qty": {"type": "integer"},
                    "price": {"type": "number"},
                },
                "required": ["retailer", "action"],
            },
        },
        {
            "name": "bf_compare",
            "description": (
                "Compare a cart or named items across stores. "
                "source=grandiose or carrefour reads the official cart, then searches the other catalogs. "
                "Search only — does not add anything. Checkout stays on Grandiose/Union Coop."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "against": {"type": "string", "description": "Comma-separated store ids, or omit for all"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "bf_spend",
            "description": (
                "How much THIS user spent from invoices. "
                "THIS is the tool for 'quanto ho speso il mese scorso'. "
                "range=last_month is the previous calendar month; this_month is the current month; "
                "1m is the last 30 days. Also 1w|2w|3m|1y|all. grain=weekly|monthly. dept=Edible|Drinks. "
                "Returns total AED, orders (date/store/total), by_store, series."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "range": {"type": "string"},
                    "grain": {"type": "string"},
                    "dept": {"type": "string"},
                },
            },
        },
        {
            "name": "bf_products",
            "description": (
                "Rank THIS user's bought products. "
                "sort=unit_price for most expensive typical unit price; sort=spend for most money spent; "
                "sort=frequency for bought most often. dept=Edible|Drinks. range=1w|1m|3m|1y|all. "
                "category=<macro slug> filters by grocery macro-category (e.g. cheese, dairy, meat). "
                "Each product includes category (macro slug)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sort": {"type": "string"},
                    "limit": {"type": "integer"},
                    "dept": {"type": "string"},
                    "range": {"type": "string"},
                    "category": {"type": "string"},
                },
            },
        },
        {
            "name": "bf_shopping_list",
            "description": (
                "Weekly-style shopping list from invoice history (mean cadence, as before). "
                "Each item has likely 0-100: how much more probable it is a real buy-again "
                "(EWMA + regularity, then this user's stacked thumbs on Likely). "
                "Each thumbs-up raises likely and can put a weak staple on the list; each "
                "thumbs-down lowers it. Repeated taps accumulate. "
                "500ml single water stays on the list with likely=0 unless thumbed. "
                "horizon_days=0 today, 1 tomorrow, 7 week. exclude=comma keys or names."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "horizon_days": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "dept": {"type": "string"},
                    "min_buys": {"type": "integer"},
                    "exclude": {"type": "string"},
                },
            },
        },
        {
            "name": "bf_product",
            "description": (
                "One product from THIS user's invoices: last buy, typical unit AED, frequency, next due date, "
                "and category (macro slug). "
                "Use for: when should I buy Heineken / milk again. "
                "Pass category=cheese (or another slug from MACRO_CATEGORIES) with query= to recategorize "
                "that product; omit category to only look up."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                    "category": {"type": "string"},
                    "macro": {"type": "string"},
                    "macro_category": {"type": "string"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "bf_orders",
            "description": (
                "THIS user's invoice/order history with date, store, total, and line items. "
                "Use for: order history, last month receipts, what I bought. "
                "Does NOT read grandiose_cart / bf_whoami. "
                "range=last_month|this_month|1w|1m|3m|1y|all. include_items=true by default."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "range": {"type": "string"},
                    "include_items": {"type": "boolean"},
                    "limit": {"type": "integer"},
                    "dept": {"type": "string"},
                },
            },
        },
    ]
    for r in db.RETAILERS:
        sid, name = r["id"], r["name"]
        # A receipts-only store has no catalog, so it is never offered a search
        # tool: a listed tool that can only answer with an error is worse than
        # no tool at all.
        if r.get("search"):
            extra = " Search only — not used for orders."
            props: dict[str, Any] = {"query": {"type": "string"}, "limit": {"type": "integer"}}
            required: list[str] = ["query"]
            if r.get("shop"):
                id_ex = "2288448" if sid == "carrefour" else "PRODUCT_ID"
                extra = (
                    f" NOT search-only. This same tool adds to the official {name} account cart: "
                    f"action=add product_id= qty= — or, if your schema only lists query+limit, "
                    f"query=<numeric product_id> (e.g. query={id_ex}) adds qty 1. "
                    f"Also {sid}_cart / bf_cart retailer={sid}. "
                    + (
                        "Checkout stays on the website."
                        if not db.store_can_checkout(sid)
                        else (
                            f"Prepare or place Magento checkout with {sid}_checkout "
                            "(action=place payment_method=ccod|cashondelivery). MCP does not charge a card."
                            if sid == "grandiose"
                            else f"Prepare Magento checkout with {sid}_checkout; payment stays on the store site."
                        )
                    )
                )
                props.update(
                    {
                        "action": {"type": "string"},
                        "product_id": {"type": "string"},
                        "name": {"type": "string"},
                        "item_id": {"type": "string"},
                        "qty": {"type": "integer"},
                    }
                )
                required = []
            tools.append(
                {
                    "name": f"{sid}_search",
                    "description": f"Search products at {name}.{extra}",
                    "inputSchema": {
                        "type": "object",
                        "properties": props,
                        **({"required": required} if required else {}),
                    },
                }
            )
        # Search stays listed even when the dashboard toggle is off. Cart/status
        # are listed whenever the store is a shop so MCP clients that cache
        # tools/list still see carrefour_cart — call-time checks enabled/shop.
        # Checkout stays hidden until the store is enabled (Magento only).
        if not r.get("shop"):
            continue
        if db.store_can_checkout(sid):
            driver = (
                "Magento GraphQL"
                if sid == "grandiose"
                else "Magento REST (GraphQL is Varnish-blocked on unioncoop.ae)"
            )
            cart_desc = (
                f"{name} official {driver} cart. action=list|add|set|remove|clear "
                "(list aliases: get, read, show, view). "
                "Remove matches a live cart line by sku, item_id, or name — never success if the line is still there. "
                + (
                    f"Checkout: {sid}_checkout action=prepare (default) or "
                    "action=place payment_method=ccod|cashondelivery. MCP does not charge a card."
                    if sid == "grandiose"
                    else f"Prepare checkout with {sid}_checkout when the store is enabled; payment stays on the store site. MCP does not charge a card."
                )
            )
        else:
            cart_desc = (
                f"{name} official account cart — the supermarket shopping list, not a local copy. "
                f"MCP name: {sid}_cart (also bf_cart retailer={sid} if this tool is missing from the client). "
                "action=list|add|set|remove|clear. list aliases: get, read, show, view. "
                "add needs product_id or name, plus qty. "
                "Add binds the delivery store from the Carrefour account location (posInfo / SLOTTED). "
                "If add returns error_code=needs_delivery_slot, list the cart to refresh the area, then retry. "
                "clear (also create/empty/new) empties the cart into a fresh list. "
                f"Checkout stays on the {name} website."
            )
        tools.append(
            {
                "name": f"{sid}_cart",
                "description": cart_desc,
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "product_id": {"type": "string"},
                        "name": {"type": "string"},
                        "item_id": {"type": "string"},
                        "qty": {"type": "integer"},
                        "price": {"type": "number"},
                    },
                    "required": ["action"],
                },
            }
        )
        if db.store_can_checkout(sid) and db.is_store_enabled(sid):
            if sid == "grandiose":
                checkout_desc = (
                    "Official Grandiose Magento checkout. "
                    "action=prepare (default) binds address + Home Delivery and lists methods; does not place. "
                    "action=place payment_method=ccod|cashondelivery sets the Magento method and calls placeOrder. "
                    "ccod is card-on-delivery — Bring Fast never takes a card number. "
                    "Does not charge a card."
                )
                checkout_schema: dict[str, Any] = {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "prepare (default) or place",
                        },
                        "payment_method": {
                            "type": "string",
                            "description": "ccod or cashondelivery; required for action=place",
                        },
                    },
                }
            else:
                checkout_desc = (
                    f"Prepare official {name} Magento REST checkout. "
                    "Does not place the order or charge a card. Payment stays on the store site. "
                    "There is no action=place on this tool."
                )
                checkout_schema = {"type": "object", "properties": {}}
            tools.append(
                {
                    "name": f"{sid}_checkout",
                    "description": checkout_desc,
                    "inputSchema": checkout_schema,
                }
            )
        tools.append(
            {
                "name": f"{sid}_status",
                "description": (
                    f"Saved {name} login and live official cart for THIS user. "
                    f"MCP name: {sid}_status (also {sid}_cart action=list)."
                ),
                "inputSchema": {"type": "object", "properties": {}},
            }
        )
    return tools


def tools_catalog() -> list[dict[str, Any]]:
    return _store_tools() + x.tools()


def _ok(**kw):
    return json.dumps({"success": True, **kw}, ensure_ascii=False)


def _no_catalog(retailer: str) -> str:
    """Said the same way everywhere a receipts-only store is asked for prices."""
    name = (db.store_meta(retailer) or {}).get("name") or retailer
    return (
        f"{name} is receipts-only: its emailed invoices are read into purchases, "
        "but it has no catalog to search or compare."
    )


def _store_snapshot(user: dict[str, Any], retailer: str) -> dict[str, Any]:
    """Saved Bring Fast state. Does not invent or write a dashboard delivery address."""
    stores = {s["id"]: s for s in db.list_retailer_accounts(user["id"])}
    s = stores[retailer]
    return {
        "store": s["name"],
        "store_id": retailer,
        "store_url": s["url"],
        "owner": user["email"],
        "linked": bool(s.get("linked")),
        "login_saved": bool(s.get("linked")),
        "login_email": s.get("login_email"),
        "delivery_address": s.get("delivery_address") or "",
        "address_note": (
            "The only cart is the official supermarket account cart. "
            "Bring Fast does not keep a local or virtual cart."
        ),
        "delivery_instruction": "Leave with security. Do not ring, call, or leave at the door.",
        "cart_url": s.get("cart_url"),
        "checkout_url": s.get("checkout_url"),
        "enabled": bool(s.get("enabled")),
        "shop": bool(s.get("shop")),
        "searchable": bool(s.get("search")),
        "receipts_only": bool(s.get("receipts") and not s.get("search")),
        "capabilities": (
            (["search"] if s.get("search") else [])
            + (["cart"] if s.get("shop") else [])
            + (["checkout"] if db.store_can_checkout(retailer) else [])
            + (["receipts"] if s.get("receipts") else [])
        ),
        "tools": (
            ([f"{retailer}_search"] if s.get("search") else [])
            + (
                [f"{retailer}_cart", f"{retailer}_status"]
                + (
                    [f"{retailer}_checkout"]
                    if db.store_can_checkout(retailer) and s.get("enabled")
                    else []
                )
                if s.get("shop")
                else []
            )
        ),
    }


def _account_snapshot(user: dict[str, Any]) -> dict[str, Any]:
    stores = [_store_snapshot(user, s["id"]) for s in db.RETAILERS]
    linked = [s["store_id"] for s in stores if s["login_saved"]]
    return {
        "email": user["email"],
        "user_id": user["id"],
        "version": __version__,
        "boot": BOOT_ID,
        "linked_stores": linked,
        "unlinked_stores": [s["store_id"] for s in stores if not s["login_saved"]],
        "note": (
            "linked=true / login_saved=true means the supermarket login is saved. "
            "Do not say a store has no login when it is in linked_stores. "
            "The only cart is the official store account cart. "
            "Official cart (add/list/clear) on Grandiose, Union Coop, and Carrefour — not search-only. "
            "Checkout only on Magento: Grandiose and Union Coop. "
            "Waitrose and Spinneys are search-only — not tested for orders. "
            "Careem is receipts-only: its emailed invoices are read into purchases, "
            "and it has no catalog to search and nothing to compare. "
            "Never invent or report a Bring Fast local cart or awaiting_official_payment order."
        ),
        "stores": stores,
    }


def _store_ctx(user: dict[str, Any], retailer: str, items: list | None = None, total: float | None = None) -> dict[str, Any]:
    snap = _store_snapshot(user, retailer)
    items = items or []
    if total is None:
        total = 0.0
        for i in items:
            try:
                total += float(i.get("price") or 0) * int(i.get("qty") or 0)
            except (TypeError, ValueError):
                pass
    return {
        "store": snap["store"],
        "store_id": retailer,
        "store_url": snap["store_url"],
        "owner": user["email"],
        "login_linked": snap["login_saved"],
        "login_saved": snap["login_saved"],
        "login_email": snap["login_email"],
        "delivery_note": snap["address_note"],
        "delivery_address": snap.get("delivery_address") or "",
        "delivery_instruction": snap["delivery_instruction"],
        "cart_url": snap["cart_url"],
        "checkout_url": snap["checkout_url"],
        "items": items,
        "item_count": sum(int(i.get("qty") or 1) for i in items),
        "currency": "AED",
        "estimated_total": round(total, 2) if total else None,
        "cart_source": "official_account",
    }


def _record_cart_probe(user: dict[str, Any], retailer: str, *, ok: bool, error_code: str | None = None) -> None:
    """Stores pills read this. MCP cart tools stay on either way."""
    db.record_store_probe(int(user["id"]), retailer, "cart", ok=ok, error_code=error_code)


def _resolve_cart_line(retailer: str, args: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]], str | None, str]:
    """product_id or a catalog name. Returns id, name, other matches, error, url."""
    pid = str(args.get("product_id") or args.get("id") or args.get("sku") or "").strip()
    name = str(args.get("name") or args.get("query") or args.get("q") or "").strip()
    url = str(args.get("url") or "").strip()
    if pid:
        if not url and retailer == "carrefour":
            url = f"https://www.carrefouruae.com/mafuae/en/p/{pid}"
        return pid, name or pid, [], None, url
    if not name:
        return "", "", [], "product_id or name required", ""
    found = catalog.search(retailer, name, 5)
    results = [r for r in (found.get("results") or []) if isinstance(r, dict) and r.get("id")]
    if not results:
        return "", name, [], f"No {retailer} product matched {name!r}.", ""
    hit = catalog.best_match(name, results) or results[0]
    others = [r for r in results if str(r.get("id")) != str(hit.get("id"))][:4]
    return (
        str(hit.get("id") or ""),
        str(hit.get("name") or name),
        others,
        None,
        url or str(hit.get("url") or ""),
    )


_CART_ACTION_ALIASES = {
    "create": "clear",
    "empty": "clear",
    "new": "clear",
    "get": "list",
    "read": "list",
    "show": "list",
    "view": "list",
    "items": "list",
    "contents": "list",
}

# Stale MCP clients (Grok) often cache only {store}_search with {query, limit}.
# Shop-store search therefore also mutates the official account cart.
_ADD_QUERY_PREFIXES = (
    "add to the cart ",
    "add to cart ",
    "aggiungi al carrello ",
    "metti nel carrello ",
    "add ",
    "aggiungi ",
    "metti ",
)
_ADD_QUERY_SUFFIXES = (
    " al carrello",
    " nel carrello",
    " to the cart",
    " to cart",
)
_LIST_CART_QUERIES = frozenset(
    {
        "cart",
        "my cart",
        "the cart",
        "show cart",
        "list cart",
        "view cart",
        "get cart",
        "read cart",
        "carrello",
        "il carrello",
        "mio carrello",
        "mostra carrello",
        "vedi carrello",
        "lista carrello",
    }
)
_CLEAR_CART_QUERIES = frozenset(
    {
        "clear cart",
        "empty cart",
        "clear the cart",
        "svuota carrello",
        "svuota il carrello",
    }
)
# Carrefour / Magento product ids are 5–7 digits. 8+ is usually a barcode search.
_NUMERIC_PRODUCT_ID = re.compile(
    r"^(?P<id>\d{5,7})(?:\s*(?:x|×|\*|qty\s+|qta\s+)(?P<qty>\d{1,3}))?$",
    re.IGNORECASE,
)


def _qty_from_search_args(args: dict[str, Any], *, use_limit: bool) -> int:
    raw = args.get("qty")
    if raw not in (None, ""):
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    if use_limit and args.get("limit") not in (None, ""):
        try:
            n = int(args["limit"])
            if 1 <= n <= 20:
                return n
        except (TypeError, ValueError):
            pass
    return 1


def _strip_remove_query(query: str) -> str | None:
    return peel_remove_name(query)


def _strip_add_query(query: str) -> str | None:
    q = query.strip()
    low = q.lower()
    rest: str | None = None
    for prefix in _ADD_QUERY_PREFIXES:
        if low.startswith(prefix):
            rest = q[len(prefix) :].strip()
            low = rest.lower()
            break
    if rest is None:
        return None
    for suffix in _ADD_QUERY_SUFFIXES:
        if low.endswith(suffix):
            rest = rest[: -len(suffix)].strip()
            break
    return rest or None


def _search_args_as_cart(args: dict[str, Any]) -> dict[str, Any] | None:
    """If this shop-store search call is really a cart op, return _mutate_cart args."""
    action = str(args.get("action") or "").strip().lower()
    if action in _CART_ACTION_ALIASES:
        action = _CART_ACTION_ALIASES[action]
    query = str(args.get("query") or args.get("q") or args.get("name") or "").strip()
    # Take-out phrasing wins even when a stale client sent action=list.
    rest = _strip_remove_query(query)
    if rest:
        return {"action": "remove", "name": rest, "item_id": str(args.get("item_id") or "")}
    if action in ("list", "add", "set", "remove", "clear"):
        out = dict(args)
        out["action"] = action
        if action in ("add", "set") and out.get("qty") in (None, ""):
            out["qty"] = _qty_from_search_args(args, use_limit=True)
        return out

    product_id = str(args.get("product_id") or args.get("id") or args.get("sku") or "").strip()
    query = str(args.get("query") or args.get("q") or "").strip()
    if product_id and (not query or query == product_id or query.isdigit()):
        return {
            "action": "add",
            "product_id": product_id,
            "name": str(args.get("name") or ""),
            "qty": _qty_from_search_args(args, use_limit=True),
        }

    qn = " ".join(query.lower().split())
    if qn in _LIST_CART_QUERIES:
        return {"action": "list"}
    if qn in _CLEAR_CART_QUERIES:
        return {"action": "clear"}

    rest = _strip_remove_query(query)
    if rest:
        return {"action": "remove", "name": rest}

    rest = _strip_add_query(query)
    if rest:
        matched = _NUMERIC_PRODUCT_ID.match(rest)
        if matched:
            return {
                "action": "add",
                "product_id": matched.group("id"),
                "qty": int(matched.group("qty") or _qty_from_search_args(args, use_limit=False)),
            }
        return {
            "action": "add",
            "name": rest,
            "qty": _qty_from_search_args(args, use_limit=False),
        }

    matched = _NUMERIC_PRODUCT_ID.match(query)
    if matched:
        return {
            "action": "add",
            "product_id": matched.group("id"),
            "qty": int(matched.group("qty") or 1),
        }
    return None


def _decorate_shop_search(sid: str, block: dict[str, Any]) -> dict[str, Any]:
    """Every catalog hit on a shop store must tell stale clients how to add with this tool."""
    results = block.get("results") if isinstance(block.get("results"), list) else []
    example_id = ""
    for item in results:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip()
        if not pid:
            continue
        item["add_with_this_tool"] = {"query": pid}
        if not example_id:
            example_id = pid
    example_id = example_id or "PRODUCT_ID"
    block["not_search_only"] = True
    block["official_cart"] = True
    block["add_to_official_cart"] = {
        "never_say_search_only": True,
        "same_tool": f"{sid}_search",
        "how": (
            f"{sid} is not search-only. Call {sid}_search with query set to the numeric "
            f"product_id (example query=\"{example_id}\") to add qty 1 to the official "
            f"account cart. Or action=add product_id={example_id} qty=1. "
            f"Also {sid}_cart / bf_cart retailer={sid}. Checkout stays on the website."
        ),
        "example": {"query": example_id},
    }
    return block


def _promote_takeout_to_remove(args: dict[str, Any]) -> dict[str, Any]:
    """Food keeper often lists the cart (action=list) while the chat says take it out."""
    out = dict(args)
    blob = str(out.get("query") or out.get("q") or out.get("name") or "").strip()
    rest = peel_remove_name(blob)
    if rest:
        out["action"] = "remove"
        out["name"] = rest
    return out


def _mutate_cart(user: dict[str, Any], retailer: str, args: dict[str, Any]) -> str:
    args = _promote_takeout_to_remove(args)
    action = (args.get("action") or "list").lower()
    action = _CART_ACTION_ALIASES.get(action, action)
    if action not in ("list", "add", "set", "remove", "clear"):
        return json.dumps(
            {
                "success": False,
                "error": (
                    f"unknown action {action}. "
                    "Use list|add|set|remove|clear (list aliases: get, read, show, view)."
                ),
                "store_id": retailer,
            }
        )
    picked = None
    also_matched: list[dict[str, Any]] = []
    payload: list[dict[str, Any]] = []
    if action in ("add", "set"):
        pid, pname, also_matched, err, page_url = _resolve_cart_line(retailer, args)
        if err:
            return json.dumps({"success": False, "error": err, "store_id": retailer})
        picked = {"id": pid, "name": pname}
        payload = [
            {
                "id": pid,
                "name": pname,
                "qty": int(args.get("qty") or 1),
                "price": args.get("price"),
                "url": args.get("url") or page_url or "",
            }
        ]
    elif action == "remove":
        pid = str(args.get("product_id") or args.get("id") or args.get("sku") or "").strip()
        name = str(args.get("name") or args.get("query") or args.get("q") or "").strip()
        item_id = str(args.get("item_id") or "").strip()
        page_url = str(args.get("url") or "").strip()
        if retailer in ("grandiose", "unioncoop"):
            if not pid and not name and not item_id:
                return json.dumps(
                    {
                        "success": False,
                        "error": "product_id, name, or item_id required",
                        "store_id": retailer,
                    }
                )
            picked = {"id": pid or item_id, "name": name or pid or item_id}
            payload = [
                {
                    "id": pid,
                    "name": name,
                    "item_id": item_id,
                    "qty": 0,
                    "url": page_url,
                }
            ]
        else:
            pid, pname, also_matched, err, page_url = _resolve_cart_line(retailer, args)
            if err:
                return json.dumps({"success": False, "error": err, "store_id": retailer})
            picked = {"id": pid, "name": pname}
            payload = [
                {
                    "id": pid,
                    "name": pname,
                    "qty": 0,
                    "price": args.get("price"),
                    "url": args.get("url") or page_url or "",
                }
            ]
    creds = db.get_retailer_secret(user["id"], retailer) or {}
    try:
        if retailer == "carrefour":
            # HTTP probe is ~4s; remaining budget is the official-site browser API.
            timeout = 28 if action == "list" else 32
        else:
            timeout = 25 if action == "list" else 40
        live = checkout.official_cart(
            store=retailer,
            email=creds.get("email") or "",
            password=creds.get("password") or "",
            action=action,
            items=payload,
            timeout=timeout,
            session_token=creds.get("auth_token") or "",
            session_user=creds.get("store_user_id") or "",
        )
    except checkout.LiveCartTimeout as e:
        _record_cart_probe(user, retailer, ok=False, error_code="cart_timeout")
        ctx = _store_ctx(user, retailer, items=[])
        ctx["action"] = action
        ctx["official_count"] = None
        ctx["official_ok"] = False
        ctx["live_cart_ok"] = False
        ctx["store_login_ok"] = bool(ctx.get("login_saved"))
        ctx["error_code"] = "cart_timeout"
        return json.dumps(
            {
                "success": False,
                **ctx,
                "items": [],
                "item_count": 0,
                "what_happens": str(e),
                "note": (
                    "Official cart was not read in time (error_code=cart_timeout). "
                    "This is not a missing login. "
                    f"login_saved={ctx['login_saved']}."
                ),
            },
            ensure_ascii=False,
        )
    items = live.get("items") or []
    if retailer == "carrefour":
        from bring_fast.stores import carrefour as carrefour_api

        try:
            items = carrefour_api.enrich_items(items)
            live["items"] = items
        except Exception:
            pass
        if action in ("add", "set") and not live.get("ok") and carrefour_api.ids_in_cart(payload, items):
            live["ok"] = True
            live["error"] = None
            live["error_code"] = None
            live["maf_error"] = None
            live["item_errors"] = []
    _record_cart_probe(
        user,
        retailer,
        ok=bool(live.get("ok")),
        error_code=None if live.get("ok") else (live.get("error_code") or "unread"),
    )
    if live.get("token") and live.get("user_id"):
        db.save_store_session(user["id"], retailer, token=live["token"], store_user_id=str(live["user_id"]))
    ctx = _store_ctx(user, retailer, items=items)
    ctx["action"] = action
    ctx["official_count"] = live.get("official_count")
    ctx["official_ok"] = bool(live.get("ok"))
    ctx["store_login_ok"] = bool(live.get("logged_in"))
    if not live.get("ok") and ctx.get("login_saved") and "akamai" in str(live.get("error") or "").lower():
        ctx["store_login_ok"] = True
    ctx["store_session_reused"] = bool(live.get("session_reused"))
    ctx["driver"] = live.get("driver") or "http"
    if live.get("delivery_address"):
        ctx["delivery_address"] = live["delivery_address"]
    for key in ("error_code", "maf_error", "item_errors", "food_pos", "area", "polygon_id", "pos", "akamai_retry"):
        val = live.get(key)
        if val not in (None, "", []):
            ctx[key] = val
    if picked:
        ctx["picked"] = picked
        if also_matched:
            ctx["also_matched"] = [{"id": r.get("id"), "name": r.get("name")} for r in also_matched]
    if not live.get("ok"):
        note = (
            "A live cart failure does not mean the supermarket login is missing. "
            f"login_saved={ctx['login_saved']}."
        )
        if ctx.get("error_code") == "akamai_blocked" or "akamai" in str(live.get("error") or "").lower():
            note = (
                "Carrefour cart is enabled. HTTP from this server was blocked by Akamai "
                "(error_code=akamai_blocked). Login is still saved. "
                f"login_saved={ctx['login_saved']}."
            )
        elif ctx.get("error_code") == "litecart_http_error" or "litecart http" in str(live.get("error") or "").lower():
            note = (
                "Carrefour cart is enabled. The official-site liteCart call returned HTTP 400 "
                "(error_code=litecart_http_error). Login is still saved. "
                "Retry bf_cart retailer=carrefour action=list. "
                f"login_saved={ctx['login_saved']}."
            )
        elif ctx.get("error_code") == "varnish_blocked" or "varnish" in str(live.get("error") or "").lower():
            note = (
                "Official Magento HTTP from this network was blocked by Varnish/Fastly "
                "(error_code=varnish_blocked). Login is still saved. "
                f"login_saved={ctx['login_saved']}."
            )
        return json.dumps(
            {
                "success": False,
                **ctx,
                "items": items,
                "item_count": sum(int(i.get("qty") or 1) for i in items),
                "live_cart_ok": False,
                "what_happens": live.get("error") or "Could not read or update the live store cart.",
                "note": note,
            },
            ensure_ascii=False,
        )
    ctx["what_happens"] = f"{ctx['store']} cart: {ctx['item_count']} item(s)."
    return _ok(**ctx)


def _checkout_store(user: dict[str, Any], sid: str, args: dict[str, Any] | None = None) -> str:
    args = args or {}
    action = str(args.get("action") or "prepare")
    payment_method = str(args.get("payment_method") or args.get("method") or "")
    listed = json.loads(_mutate_cart(user, sid, {"action": "list"}))
    if not listed.get("success"):
        listed["placed"] = False
        listed["payment_completed"] = False
        return json.dumps(listed, ensure_ascii=False)
    if not listed.get("items"):
        listed["success"] = False
        listed["ready"] = False
        listed["placed"] = False
        listed["payment_completed"] = False
        listed["error"] = listed.get("error") or "Official cart is empty."
        listed["what_happens"] = "Store cart is empty."
        return json.dumps(listed, ensure_ascii=False)
    creds = db.get_retailer_secret(user["id"], sid) or {}
    live = checkout.run_checkout(
        store=sid,
        email=creds.get("email") or "",
        password=creds.get("password") or "",
        address=creds.get("address") or "",
        items=listed.get("items") or [],
        action=action,
        payment_method=payment_method,
    )
    ok = bool(live.get("ok"))
    placed = bool(live.get("placed"))
    listed.update(
        {
            "success": ok,
            "ready": ok,
            "placed": placed,
            "order_id": live.get("order_id"),
            "payment_completed": bool(live.get("payment_completed")),
            "stage": live.get("stage"),
            "payment_methods": live.get("payment_methods"),
            "payment_method": live.get("payment_method"),
            "grand_total": live.get("grand_total"),
            "currency": live.get("currency") or listed.get("currency"),
            "shipping_method": live.get("shipping_method"),
            "live_checkout": live,
            "checkout_url": live.get("final_url") or listed.get("checkout_url"),
            "what_happens": live.get("what_happens") or live.get("error"),
            "error": None if ok else (live.get("error") or listed.get("error")),
            "error_code": live.get("error_code"),
        }
    )
    if live.get("delivery_address"):
        listed["delivery_address"] = live["delivery_address"]
    if live.get("items"):
        listed["items"] = live["items"]
        listed["item_count"] = sum(int(i.get("qty") or 1) for i in live["items"] if isinstance(i, dict))
    return json.dumps(listed, ensure_ascii=False)


def _normalize_tool(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw = (name or "").strip()
    n = raw.lower().replace("-", "_").replace(" ", "_")
    n = re.sub(r"_+", "_", n).strip("_")
    for prefix in ("bring_fast_", "bringfast_", "fast_bring_", "fastbring_"):
        if n.startswith(prefix):
            n = n[len(prefix) :].strip("_")
            break
    aliases = {
        "search": "bf_search",
        "product_search": "bf_search",
        "catalog_search": "bf_search",
        "search_products": "bf_search",
        "retailers": "bf_stores",
        "stores": "bf_stores",
        "whoami": "bf_whoami",
        "cart": "bf_cart",
        "add_to_cart": "bf_cart",
        "compare": "bf_compare",
        "compare_cart": "bf_compare",
        "compare_prices": "bf_compare",
        "spend": "bf_spend",
        "expenses": "bf_spend",
        "spending": "bf_spend",
        "orders": "bf_orders",
        "order_history": "bf_orders",
        "invoices": "bf_orders",
        "history": "bf_orders",
        "products": "bf_products",
        "top_products": "bf_products",
        "shopping_list": "bf_shopping_list",
        "due": "bf_shopping_list",
        "product": "bf_product",
        "when_to_buy": "bf_product",
        "checkout": "bf_checkout",
        "status": "bf_status",
        "bf_retailers": "bf_stores",
        "tweet": "x_post",
        "twitter_post": "x_post",
        "create_tweet": "x_post",
        "post_tweet": "x_post",
        "twitter_me": "x_me",
        "twitter_user": "x_user_by_username",
        "twitter_profile": "x_user_by_username",
        "twitter_posts": "x_user_posts",
        "twitter_timeline": "x_user_posts",
        "twitter_mentions": "x_mentions",
        "twitter_search": "x_search",
    }
    n = aliases.get(n, n)
    ids = [r["id"] for r in db.RETAILERS]
    for sid in ids:
        if sid in n and "search" in n:
            return f"{sid}_search", args
        if sid in n and "checkout" in n:
            return f"{sid}_checkout", args
        if sid in n and "status" in n:
            return f"{sid}_status", args
        if sid in n and (
            "cart" in n
            or n == f"{sid}_list"
            or n.endswith(f"{sid}_shopping_list")
            or n.endswith(f"{sid}_lista")
        ):
            return f"{sid}_cart", args
    retailer = (args.get("retailer") or args.get("store") or "").lower()
    if n == "bf_checkout" and retailer in ids:
        return f"{retailer}_checkout", args
    if n == "bf_status" and retailer in ids:
        return f"{retailer}_status", args
    return n, args


def _search_stores(user: dict[str, Any], query: str, retailer: str, limit: int) -> str:
    all_ids = [r["id"] for r in db.RETAILERS]
    if retailer and retailer not in all_ids:
        return json.dumps({"success": False, "error": f"unknown retailer {retailer}", "stores": []}, ensure_ascii=False)
    if retailer and not db.store_can_search(retailer):
        return json.dumps(
            {"success": False, "error": _no_catalog(retailer), "stores": []},
            ensure_ascii=False,
        )
    ids = [retailer] if retailer else [r["id"] for r in db.searchable_retailers()]
    out = []
    for sid in ids:
        block = catalog.search(sid, query, limit)
        block["delivery_address"] = _store_ctx(user, sid)["delivery_address"]
        block["shop"] = db.store_can_shop(sid)
        if block["shop"]:
            _decorate_shop_search(sid, block)
        out.append(block)
    return json.dumps({"success": True, "query": query, "stores": out}, ensure_ascii=False)


def _compare(user: dict[str, Any], args: dict[str, Any]) -> str:
    source = (args.get("source") or args.get("retailer") or "").lower()
    against_raw = args.get("against") or args.get("stores") or ""
    targets = [p.strip() for p in str(against_raw).split(",") if p.strip()] if against_raw else None
    items = args.get("items") if isinstance(args.get("items"), list) else []
    q = (args.get("query") or args.get("q") or "").strip()
    if source:
        listed = json.loads(_mutate_cart(user, source, {"action": "list"}))
        if not listed.get("success") and not listed.get("items"):
            return json.dumps(
                {"success": False, "error": listed.get("error") or f"Could not read {source} cart.", "items": []},
                ensure_ascii=False,
            )
        items = listed.get("items") or []
        source_total = listed.get("estimated_total")
    elif q:
        items = [{"name": q, "qty": 1}]
        source_total = None
    else:
        return json.dumps({"success": False, "error": "Pass source=<store> (official cart) or query=...", "items": []})
    if source and targets is None:
        targets = [r["id"] for r in db.searchable_retailers() if r["id"] != source]
    out = catalog.compare_items(items, targets=targets, limit=int(args.get("limit") or 3))
    out["source"] = source or None
    out["source_total"] = source_total
    return json.dumps(out, ensure_ascii=False)


def _call_tool(user: dict[str, Any], name: str, args: dict[str, Any]) -> str:
    uid = user["id"]
    name, args = _normalize_tool(name, args or {})
    if name in x.TOOL_NAMES:
        return x.call_tool(name, args)
    if name == "bf_whoami":
        return _ok(**_account_snapshot(user))
    if name == "bf_search":
        retailer = (args.get("retailer") or args.get("store") or "").lower()
        if retailer and db.store_can_shop(retailer):
            cart_args = _search_args_as_cart(args)
            if cart_args:
                return _mutate_cart(user, retailer, cart_args)
        return _search_stores(
            user,
            args.get("query") or args.get("q") or "",
            retailer,
            int(args.get("limit") or 6),
        )
    if name == "bf_compare":
        return _compare(user, args)
    if name == "bf_spend":
        raw_range = str(args.get("range") or args.get("window") or "last_month")
        today_raw = str(args.get("today") or args.get("end") or "").strip()
        today = purchases._parse_day(today_raw) if today_raw else None
        return _ok(
            **purchases.spend_report(
                uid,
                range_key=raw_range,
                grain=str(args.get("grain") or ""),
                dept=str(args.get("dept") or ""),
                categories=purchases.normalize_categories(
                    args.get("category") or args.get("categories") or ""
                ),
                today=today,
            )
        )
    if name == "bf_orders":
        include = args.get("include_items")
        if include is None:
            include = True
        return _ok(
            **purchases.orders_report(
                uid,
                range_key=str(args.get("range") or args.get("window") or "last_month"),
                include_items=bool(include),
                limit=int(args.get("limit") or 40),
            )
        )
    if name == "bf_products":
        sort = str(args.get("sort") or "spend")
        if sort in ("price", "expensive", "unit", "cost"):
            sort = "unit_price"
        category_raw = (
            args.get("category") or args.get("categories") or args.get("macro") or args.get("macro_category") or ""
        )
        return _ok(
            sort=sort,
            products=purchases.ranked_products(
                uid,
                sort=sort,
                limit=int(args.get("limit") or 10),
                dept=str(args.get("dept") or ""),
                categories=purchases.normalize_categories(category_raw),
                range_key=str(args.get("range") or "all"),
            ),
        )
    if name == "bf_shopping_list":
        raw_ex = str(args.get("exclude") or "")
        exclude = [p.strip() for p in raw_ex.split(",") if p.strip()]
        min_buys = args.get("min_buys")
        return _ok(
            items=purchases.shopping_list(
                uid,
                horizon_days=int(args.get("horizon_days") or args.get("days") or 7),
                limit=int(args.get("limit") or 20),
                dept=str(args.get("dept") or ""),
                min_buys=int(min_buys) if min_buys not in (None, "") else None,
                exclude=exclude or None,
            )
        )
    if name == "bf_product":
        q = str(args.get("query") or args.get("q") or args.get("name") or "").strip()
        if not q:
            return json.dumps({"success": False, "error": "Pass query=product name or barcode."})
        category = str(
            args.get("category") or args.get("macro") or args.get("macro_category") or ""
        ).strip()
        if category:
            slug = normalize_macro_slug(category)
            if not slug:
                valid = ", ".join(MACRO_CATEGORIES)
                return json.dumps(
                    {"success": False, "error": f"Invalid category slug {category!r}. Valid slugs: {valid}"}
                )
            product, candidates, err = purchases.find_product_for_recategorize(uid, q)
            if err:
                payload: dict[str, Any] = {"success": False, "error": err, "query": q}
                if candidates:
                    payload["candidates"] = candidates
                return json.dumps(payload, ensure_ascii=False)
            assert product is not None
            purchases.set_macro_category(product["key"], slug)
            hits = purchases.find_products(uid, q, limit=int(args.get("limit") or 8))
            updated = hits[0] if hits else None
            return _ok(query=q, category=slug, products=hits, product=updated)
        hits = purchases.find_products(uid, q, limit=int(args.get("limit") or 8))
        return _ok(query=q, products=hits, product=hits[0] if hits else None)
    if name == "bf_cart":
        retailer = (args.get("retailer") or args.get("store") or "").lower()
        if retailer not in {r["id"] for r in db.enabled_retailers()} or not db.store_can_shop(retailer):
            return json.dumps(
                {
                    "success": False,
                    "error": "Cart on grandiose, unioncoop, or carrefour when the store is enabled. Others are search-only.",
                }
            )
        return _mutate_cart(user, retailer, args)
    if name == "bf_stores":
        snap = _account_snapshot(user)
        return _ok(
            version=snap["version"],
            boot=snap["boot"],
            linked_stores=snap["linked_stores"],
            unlinked_stores=snap["unlinked_stores"],
            note=snap["note"],
            stores=snap["stores"],
        )
    for r in db.RETAILERS:
        sid = r["id"]
        if name == f"{sid}_search":
            if not r.get("search"):
                return json.dumps({"success": False, "error": _no_catalog(sid), "results": []}, ensure_ascii=False)
            if r.get("shop"):
                cart_args = _search_args_as_cart(args)
                if cart_args:
                    return _mutate_cart(user, sid, cart_args)
            result = catalog.search(sid, args.get("query") or "", int(args.get("limit") or 8))
            result["store"] = r["name"]
            result["shop"] = bool(r.get("shop"))
            if r.get("shop"):
                _decorate_shop_search(sid, result)
            return json.dumps(result, ensure_ascii=False)
        if name == f"{sid}_cart":
            if not r.get("shop"):
                return json.dumps(
                    {
                        "success": False,
                        "error": f"{name} is search-only. Official cart on Grandiose, Union Coop, and Carrefour.",
                    }
                )
            return _mutate_cart(user, sid, args)
        if name == f"{sid}_checkout":
            if not db.store_can_checkout(sid):
                return json.dumps(
                    {
                        "success": False,
                        "error": (
                            f"{name} is not wired. Fill the official cart with {sid}_cart; "
                            "checkout stays on the supermarket website."
                        ),
                    }
                )
            return _checkout_store(user, sid, args)
        if name == f"{sid}_status":
            if not r.get("shop"):
                return json.dumps(
                    {
                        "success": False,
                        "error": f"{name} is search-only. Official cart on Grandiose, Union Coop, and Carrefour.",
                    }
                )
            snap = _store_snapshot(user, sid)
            listed = json.loads(_mutate_cart(user, sid, {"action": "list"}))
            listed.update(
                {
                    "linked": snap["login_saved"],
                    "login_saved": snap["login_saved"],
                    "login_email": snap["login_email"],
                    "address_note": snap["address_note"],
                }
            )
            if not listed.get("success"):
                listed["success"] = True
                listed["live_cart_ok"] = False
                listed["items"] = []
                listed["item_count"] = 0
                listed["what_happens"] = (
                    f"{snap['store']}: login_saved={snap['login_saved']}. "
                    "Official cart was not read. No local copy exists."
                )
            return json.dumps(listed, ensure_ascii=False)
    return json.dumps(
        {
            "success": False,
            "error": f"unknown tool {name}",
            "use": "bf_search with query=... or grandiose_search / grandiose_cart / carrefour_cart",
            "available": [t["name"] for t in tools_catalog()],
        }
    )


def _call_tool_safe(user: dict[str, Any], name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """A failing tool must stay a tool failure; an escaping exception becomes an HTTP 500
    that the client cannot parse as JSON-RPC."""
    try:
        text = _call_tool(user, name, args)
    except Exception as e:
        return json.dumps({"success": False, "tool": name, "error": f"{type(e).__name__}: {e}"}), True
    try:
        return text, not json.loads(text).get("success", True)
    except (TypeError, ValueError):
        return text, False


SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]


def _rpc_result(rid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _rpc_error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _is_notification(message: dict[str, Any]) -> bool:
    """JSON-RPC notifications carry no id and must never be answered with a response."""
    return "id" not in message or str(message.get("method") or "").startswith("notifications/")


async def _dispatch(user: dict[str, Any], message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return _rpc_error(None, -32600, "invalid request: expected a JSON-RPC object")
    if _is_notification(message):
        return None
    rid = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _rpc_result(
            rid,
            {
                "protocolVersion": asked if asked in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": True},
                    "prompts": {"listChanged": False},
                    "resources": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "Bring Fast",
                    "version": __version__,
                    "description": mcp_skill.DESCRIPTION,
                },
                "instructions": mcp_skill.instructions(user.get("email") or ""),
            },
        )
    if method == "tools/list":
        return _rpc_result(rid, {"tools": tools_catalog()})
    if method == "prompts/list":
        return _rpc_result(rid, {"prompts": mcp_skill.prompts()})
    if method == "prompts/get":
        got = mcp_skill.prompt_get(str(params.get("name") or ""))
        if not got:
            return _rpc_error(rid, -32602, "unknown prompt")
        return _rpc_result(rid, got)
    if method == "resources/list":
        return _rpc_result(rid, {"resources": mcp_skill.resources()})
    if method == "resources/read":
        got = mcp_skill.resource_read(str(params.get("uri") or ""))
        if not got:
            return _rpc_error(rid, -32602, "unknown resource")
        return _rpc_result(rid, got)
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        text, failed = await asyncio.to_thread(_call_tool_safe, user, name, args)
        return _rpc_result(rid, {"content": [{"type": "text", "text": text}], "isError": failed})
    if method == "ping":
        return _rpc_result(rid, {})
    return _rpc_error(rid, -32601, f"method not found: {method}")


def _mcp_reply(request: Request, payload: dict[str, Any] | list | None, status: int = 200, extra_headers: dict[str, str] | None = None):
    """JSON by default; one-shot SSE when the client refuses application/json."""
    headers = extra_headers or {}
    if payload is None:
        return Response(status_code=202, headers=headers)
    accept = (request.headers.get("accept") or "").lower()
    if "text/event-stream" in accept and "application/json" not in accept:
        data = json.dumps(payload, ensure_ascii=False)
        sse_headers = {
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            **headers,
        }
        return Response(
            f"event: message\ndata: {data}\n\n",
            status_code=status,
            media_type="text/event-stream",
            headers=sse_headers,
        )
    return JSONResponse(payload, status_code=status, headers=headers)


@app.api_route("/mcp", methods=["GET", "HEAD", "POST", "DELETE", "OPTIONS"])
@app.api_route("/mcp/", methods=["GET", "HEAD", "POST", "DELETE", "OPTIONS"], include_in_schema=False)
async def mcp_endpoint(request: Request):
    if request.method == "OPTIONS":
        return _cors_preflight()
    user = _user_from_request(request)
    if not user:
        return _oauth_challenge(request)
    if request.method in ("GET", "HEAD"):
        # No server-initiated SSE stream here. Answering an authenticated GET with 401
        # tells the client its brand new token is bad, so it reports a failed connection.
        return _mcp_reply(
            request,
            _rpc_error(None, -32601, "this endpoint has no SSE stream; POST JSON-RPC instead"),
            status=405,
            extra_headers={"Allow": "POST, DELETE"},
        )
    if request.method == "DELETE":
        return Response(status_code=204)
    try:
        body = await request.json()
    except Exception:
        return _mcp_reply(request, _rpc_error(None, -32700, "parse error"), status=400)
    try:
        if isinstance(body, list):
            if not body:
                return _mcp_reply(request, _rpc_error(None, -32600, "invalid request: empty batch"), status=400)
            replies = [r for r in [await _dispatch(user, m) for m in body] if r is not None]
            return _mcp_reply(request, replies if replies else None)
        reply = await _dispatch(user, body)
        return _mcp_reply(request, reply)
    except Exception as e:
        rid = body.get("id") if isinstance(body, dict) else None
        return _mcp_reply(request, _rpc_error(rid, -32603, f"internal error: {e}"))


OAUTH_CLIENT_ID = os.environ.get("BRINGFAST_OAUTH_CLIENT_ID", "fast-bring")
OAUTH_CLIENT_SECRET = os.environ.get("BRINGFAST_OAUTH_CLIENT_SECRET", "fast-bring-grok-secret")


def _cors_preflight():
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        },
    )


def _oauth_challenge(request: Request | None = None):
    meta = f"{_issuer(request)}/.well-known/oauth-protected-resource/mcp"
    return JSONResponse(
        {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "OAuth required. Sign in with your Bring Fast account."}},
        status_code=401,
        headers={
            "WWW-Authenticate": f'Bearer realm="Bring Fast", resource_metadata="{meta}"',
            "Access-Control-Expose-Headers": "WWW-Authenticate",
        },
    )


def _as_metadata(request: Request | None = None) -> dict:
    base = _issuer(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "scopes_supported": ["mcp"],
        "service_documentation": base,
    }


def _prm_metadata(request: Request | None = None) -> dict:
    base = _issuer(request)
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp"],
        "resource_name": "Bring Fast",
    }


@app.get("/.well-known/oauth-authorization-server")
@app.get("/.well-known/oauth-authorization-server/mcp")
@app.get("/.well-known/openid-configuration")
def oauth_as_metadata(request: Request):
    return _as_metadata(request)


@app.get("/.well-known/oauth-protected-resource")
@app.get("/.well-known/oauth-protected-resource/mcp")
def oauth_prm_metadata(request: Request):
    return _prm_metadata(request)


@app.get("/.well-known/oauth-protected-resource/plex/mcp")
def oauth_prm_plex(request: Request):
    base = _issuer(request)
    return {
        "resource": f"{base}/plex/mcp",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp"],
        "resource_name": "Plex",
    }


@app.get("/.well-known/oauth-authorization-server/plex/mcp")
def oauth_as_plex(request: Request):
    return _as_metadata(request)


def _safe_redirect(uri: str) -> bool:
    if not uri:
        return False
    return uri.startswith("https://") or uri.startswith("http://127.0.0.1") or uri.startswith("http://localhost")


def _client_redirect_uris(client: dict[str, Any] | None) -> list[str]:
    if not client:
        return []
    uris = client.get("redirect_uris")
    if isinstance(uris, str):
        try:
            uris = json.loads(uris)
        except Exception:
            uris = [uris]
    return [str(u) for u in uris] if isinstance(uris, list) else []


def _registered_uri(redirect_uri: str, registered: list[str]) -> bool:
    given = redirect_uri.rstrip("/")
    for uri in registered:
        known = uri.rstrip("/")
        if given == known or given.startswith(known + "?") or given.startswith(known + "/"):
            return True
    return False


def _check_redirect(client_id: str, redirect_uri: str) -> str:
    """Empty string means the redirect is usable, otherwise the reason it is not."""
    if not redirect_uri:
        return "This app did not send a redirect_uri, so there is nowhere to send you back to."
    if not _safe_redirect(redirect_uri):
        return "The redirect_uri must be https, or http on localhost."
    registered = _client_redirect_uris(db.get_oauth_client(client_id))
    if registered and not _registered_uri(redirect_uri, registered):
        return "That redirect_uri is not registered for this client."
    return ""


def _issue_code(user, redirect_uri: str, client_id: str, challenge: str, method: str, state: str):
    code = db.save_oauth_code(user["id"], redirect_uri, client_id, challenge, method)
    params = {"code": code}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)


def _authorize_error(request: Request, reason: str):
    return templates.TemplateResponse(
        request,
        "oauth_error.html",
        {"user": current_user(request), "title": "Bring Fast", "error": reason},
        status_code=400,
    )


@app.api_route("/oauth/register", methods=["POST", "OPTIONS"])
async def oauth_register(request: Request):
    if request.method == "OPTIONS":
        return _cors_preflight()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    uris = body.get("redirect_uris") or []
    if isinstance(uris, str):
        uris = [uris]
    uris = [u for u in uris if _safe_redirect(str(u))]
    if not uris:
        uris = ["https://grok.com/", "https://grok.x.ai/"]
    rec = db.register_oauth_client(uris, body.get("token_endpoint_auth_method") or "none")
    rec.update(
        {
            "client_name": body.get("client_name") or "Grok",
            "grant_types": ["authorization_code"],
            "client_secret_expires_at": 0,
        }
    )
    return JSONResponse(rec, status_code=201)


def _authorize_page(
    request: Request,
    *,
    user,
    redirect_uri: str,
    state: str,
    client_id: str,
    code_challenge: str,
    code_challenge_method: str,
    email: str = "",
    error: str = "",
    status_code: int = 200,
    intent: str = "signup",
):
    intent = "signin" if (intent or "").lower() == "signin" else "signup"
    return templates.TemplateResponse(
        request,
        "oauth_authorize.html",
        {
            "user": user,
            "title": "Authorize Bring Fast",
            "redirect_uri": redirect_uri,
            "state": state,
            "client_id": client_id,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "email": email or (user["email"] if user else ""),
            "error": error,
            "intent": intent,
        },
        status_code=status_code,
    )


@app.get("/oauth/authorize")
@app.get("/authorize")
def oauth_authorize_get(
    request: Request,
    redirect_uri: str = "",
    state: str = "",
    client_id: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    response_type: str = "code",
    scope: str = "",
    resource: str = "",
):
    problem = _check_redirect(client_id, redirect_uri)
    if problem:
        return _authorize_error(request, problem)
    user = current_user(request)
    if user:
        return _issue_code(user, redirect_uri, client_id, code_challenge, code_challenge_method, state)
    return _authorize_page(
        request,
        user=None,
        redirect_uri=redirect_uri,
        state=state,
        client_id=client_id,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )


@app.post("/oauth/authorize")
@app.post("/authorize")
def oauth_authorize_post(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    intent: str = Form("signup"),
    redirect_uri: str = Form(""),
    state: str = Form(""),
    client_id: str = Form(""),
    code_challenge: str = Form(""),
    code_challenge_method: str = Form("S256"),
):
    problem = _check_redirect(client_id, redirect_uri)
    if problem:
        return _authorize_error(request, problem)
    user = current_user(request)
    if not user:
        user, error, _created = _authenticate(email, password, intent)
        if not user:
            return _authorize_page(
                request,
                user=None,
                redirect_uri=redirect_uri,
                state=state,
                client_id=client_id,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                email=(email or "").strip(),
                error=error,
                status_code=401,
                intent=intent,
            )
        _sign_in(request, user)
    return _issue_code(user, redirect_uri, client_id, code_challenge, code_challenge_method, state)


def _token_response(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "access_token": user["mcp_token"],
        "refresh_token": user["mcp_token"],
        "token_type": "bearer",
        "expires_in": 86400 * 30,
        "scope": "mcp",
    }


@app.post("/oauth/token")
@app.post("/token")
async def oauth_token(request: Request):
    form = {}
    ctype = request.headers.get("content-type") or ""
    if "json" in ctype:
        try:
            form = await request.json()
        except Exception:
            form = {}
    else:
        try:
            form = dict(await request.form())
        except Exception:
            form = {}
    grant = form.get("grant_type") or ""
    code = form.get("code") or ""
    redirect_uri = form.get("redirect_uri") or ""
    verifier = form.get("code_verifier") or ""
    if grant == "refresh_token":
        # The MCP token is long lived, so a refresh just re-issues the same one
        # instead of dropping the connector back to a login screen.
        refreshed = db.get_user_by_token(str(form.get("refresh_token") or ""))
        if not refreshed:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return _token_response(refreshed)
    if grant and grant != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    user = db.consume_oauth_code(code, redirect_uri or None)
    if not user:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    challenge = user.get("_oauth_code_challenge") or ""
    method = (user.get("_oauth_code_challenge_method") or "S256").upper()
    if challenge:
        import hashlib
        import base64

        if not verifier:
            return JSONResponse({"error": "invalid_grant", "error_description": "code_verifier required"}, status_code=400)
        if method == "S256":
            digest = hashlib.sha256(verifier.encode()).digest()
            calc = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if calc != challenge:
                return JSONResponse({"error": "invalid_grant", "error_description": "pkce failed"}, status_code=400)
        elif verifier != challenge:
            return JSONResponse({"error": "invalid_grant", "error_description": "pkce failed"}, status_code=400)
    return _token_response(user)


def main() -> None:
    import uvicorn

    db.connect()
    if not PUBLIC_URL:
        print(
            "WARNING: BRINGFAST_PUBLIC_URL is unset. "
            "Set it to the public https origin (or run behind a proxy that sends "
            "X-Forwarded-Proto and X-Forwarded-Host) so Grok can authenticate.",
            file=sys.stderr,
        )
    uvicorn.run(
        "bring_fast.app:app",
        host=HOST,
        port=PORT,
        factory=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
