from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Form, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import __version__, catalog, checkout, db

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
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")


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
        return "/"
    return target


def _sign_in_or_create(email: str, password: str) -> tuple[dict[str, Any] | None, str, bool]:
    """One entry point for the whole product: sign in, or create the account on first use.

    Returns (user, error, created).
    """
    email = (email or "").strip().lower()
    password = password or ""
    if not email or "@" not in email:
        return None, "Enter the email address you want to use for Bring Fast.", False
    if not password:
        return None, "Enter your password.", False
    user = db.get_user_by_email(email)
    if user:
        if db.verify_password(user, password):
            return user, "", False
        return None, "That password does not match this Bring Fast account.", False
    if len(password) < 6:
        return None, f"No account yet for {email}. Pick a password of at least 6 characters to create it.", False
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
):
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "user": None,
            "title": "Bring Fast",
            "error": error,
            "email": email,
            "next": _safe_next(next_url),
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
def home(request: Request, next: str = "/", welcome: int = 0, notice: str = ""):
    user = current_user(request)
    if not user:
        return _login_page(request, next_url=next)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "retailers": db.list_retailer_accounts(user["id"]),
            "mcp_url": mcp_url(request),
            "title": "Bring Fast",
            "notice": (
                f"Welcome to Bring Fast, {user['email']}. Link a store below and you are done."
                if welcome
                else notice
            ),
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    """A signed-in user never sees a login form again; they land where they were going."""
    if current_user(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return _login_page(request, next_url=next)


def _sign_in_response(request: Request, email: str, password: str, next_url: str):
    user, error, created = _sign_in_or_create(email, password)
    if not user:
        return _login_page(
            request,
            error=error,
            email=(email or "").strip(),
            next_url=next_url,
            status_code=401,
        )
    _sign_in(request, user)
    target = _safe_next(next_url)
    if created and target == "/":
        target = "/?welcome=1"
    return RedirectResponse(target, status_code=303)


@app.post("/login")
def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
):
    return _sign_in_response(request, email, password, next)


@app.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


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
    return RedirectResponse("/", status_code=303)


@app.post("/retailers/{retailer}/clear")
def clear_retailer(request: Request, retailer: str):
    user = current_user(request)
    if user:
        db.clear_retailer_account(user["id"], retailer)
    return RedirectResponse("/", status_code=303)


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
    return RedirectResponse("/?" + urlencode({"notice": note}), status_code=303)


@app.post("/rotate-token")
def rotate(request: Request):
    user = current_user(request)
    if user:
        db.rotate_token(user["id"])
    return RedirectResponse("/", status_code=303)


@app.get("/health")
def health(request: Request):
    base = _issuer(request)
    return {
        "ok": True,
        "server": "Bring Fast",
        "version": __version__,
        "public_url": base,
        "mcp_url": f"{base}/mcp",
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
                "THIS user's snapshot: email, which supermarket logins are saved, "
                "recent official orders (items + the address used on the store), last seen cart. "
                "linked=true means the store login is saved. Do not say logins are missing when linked is true. "
                "Delivery address lives on the supermarket account, not on Bring Fast."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "bf_stores",
            "description": (
                "THIS user's stores. linked=true means the supermarket login is saved. "
                "Includes last official orders and last seen cart. "
                "Address is the one on the supermarket account / last official order — do not ask to set it on Bring Fast."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "bf_search",
            "description": "Search one or all stores. retailer=carrefour|grandiose|waitrose|spinneys or omit to search all.",
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
            "description": "Cart alias. retailer=carrefour|grandiose|waitrose|spinneys. action=list|add|set|remove|clear.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "retailer": {"type": "string"},
                    "action": {"type": "string"},
                    "product_id": {"type": "string"},
                    "name": {"type": "string"},
                    "qty": {"type": "integer"},
                    "price": {"type": "number"},
                },
                "required": ["retailer", "action"],
            },
        },
    ]
    for r in db.RETAILERS:
        sid, name = r["id"], r["name"]
        tools.extend(
            [
                {
                    "name": f"{sid}_search",
                    "description": f"Search products at {name} only.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                        "required": ["query"],
                    },
                },
                {
                    "name": f"{sid}_cart",
                    "description": (
                        f"{name} cart for THIS user only. action=list|add|set|remove|clear. "
                        "Always returns the delivery address for this store."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "product_id": {"type": "string"},
                            "name": {"type": "string"},
                            "qty": {"type": "integer"},
                            "price": {"type": "number"},
                        },
                        "required": ["action"],
                    },
                },
                {
                    "name": f"{sid}_checkout",
                    "description": (
                        f"Run the official {name} checkout inside the Bring Fast server: "
                        "login with this user's store account, add the cart on the live site, open checkout. "
                        "Returns delivery address, live URL, and what happened."
                    ),
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {
                    "name": f"{sid}_status",
                    "description": (
                        f"Saved {name} state for THIS user: login saved?, last official orders, last seen cart. "
                        "Then tries the live store cart. A live-cart failure does not mean the login is missing."
                    ),
                    "inputSchema": {"type": "object", "properties": {}},
                },
            ]
        )
    return tools


TOOLS = _store_tools()


def _ok(**kw):
    return json.dumps({"success": True, **kw}, ensure_ascii=False)


def _last_order_address(orders: list[dict[str, Any]]) -> str | None:
    for order in orders:
        addr = (order.get("delivery_address") or "").strip()
        if addr:
            return addr
    return None


def _store_snapshot(user: dict[str, Any], retailer: str) -> dict[str, Any]:
    """Saved Bring Fast state. Does not invent or write a dashboard delivery address."""
    stores = {s["id"]: s for s in db.list_retailer_accounts(user["id"])}
    s = stores[retailer]
    orders = db.list_orders(user["id"], retailer, 5)
    last_cart = db.load_cart(user["id"], retailer)
    last_items = last_cart.get("items") or []
    last_addr = _last_order_address(orders)
    return {
        "store": s["name"],
        "store_id": retailer,
        "store_url": s["url"],
        "owner": user["email"],
        "linked": bool(s.get("linked")),
        "login_saved": bool(s.get("linked")),
        "login_email": s.get("login_email"),
        "last_delivery_address": last_addr,
        "address_source": "supermarket_order" if last_addr else "supermarket_account",
        "address_note": (
            "Delivery address is the one already saved on the supermarket account. "
            "Do not change it on Bring Fast."
        ),
        "delivery_instruction": "Leave with security. Do not ring, call, or leave at the door.",
        "cart_url": s.get("cart_url"),
        "checkout_url": s.get("checkout_url"),
        "last_seen_cart": last_items,
        "last_seen_count": sum(int(i.get("qty") or 1) for i in last_items),
        "recent_orders": orders,
        "tools": [f"{retailer}_search", f"{retailer}_cart", f"{retailer}_checkout", f"{retailer}_status"],
    }


def _account_snapshot(user: dict[str, Any]) -> dict[str, Any]:
    stores = [_store_snapshot(user, s["id"]) for s in db.RETAILERS]
    linked = [s["store_id"] for s in stores if s["login_saved"]]
    return {
        "email": user["email"],
        "user_id": user["id"],
        "linked_stores": linked,
        "unlinked_stores": [s["store_id"] for s in stores if not s["login_saved"]],
        "note": (
            "linked=true / login_saved=true means the supermarket login is saved. "
            "Do not say a store has no login when it is in linked_stores. "
            "Delivery address lives on the supermarket account, not on Bring Fast."
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
        "delivery_address": snap["last_delivery_address"],
        "last_delivery_address": snap["last_delivery_address"],
        "delivery_note": snap["address_note"],
        "delivery_instruction": snap["delivery_instruction"],
        "cart_url": snap["cart_url"],
        "checkout_url": snap["checkout_url"],
        "items": items,
        "item_count": sum(int(i.get("qty") or 1) for i in items),
        "currency": "AED",
        "estimated_total": round(total, 2) if total else None,
        "cart_source": "store",
        "recent_orders": snap["recent_orders"],
    }


def _mutate_cart(user: dict[str, Any], retailer: str, args: dict[str, Any]) -> str:
    action = (args.get("action") or "list").lower()
    if action not in ("list", "add", "set", "remove", "clear"):
        return json.dumps({"success": False, "error": f"unknown action {action}", "store_id": retailer})
    if action in ("add", "set") and not args.get("product_id"):
        return json.dumps({"success": False, "error": "product_id required", "store_id": retailer})
    creds = db.get_retailer_secret(user["id"], retailer) or {}
    payload = []
    if action in ("add", "set"):
        payload = [
            {
                "id": str(args.get("product_id")),
                "name": args.get("name") or args.get("product_id"),
                "qty": int(args.get("qty") or 1),
                "price": args.get("price"),
                "url": args.get("url") or "",
            }
        ]
    elif action == "remove":
        payload = [{"id": str(args.get("product_id") or ""), "qty": 0}]
    live = checkout.official_cart(
        store=retailer,
        email=creds.get("email") or "",
        password=creds.get("password") or "",
        action=action,
        items=payload,
    )
    items = live.get("items") or []
    if live.get("ok") and items:
        db.save_cart(user["id"], retailer, {"items": items, "currency": "AED"})
    ctx = _store_ctx(user, retailer, items=items)
    ctx["action"] = action
    ctx["official_count"] = live.get("official_count")
    ctx["official_ok"] = bool(live.get("ok"))
    ctx["store_login_ok"] = bool(live.get("logged_in"))
    ctx["store_session_reused"] = bool(live.get("session_reused"))
    if not live.get("ok"):
        return json.dumps(
            {
                "success": False,
                **ctx,
                "items": items,
                "item_count": sum(int(i.get("qty") or 1) for i in items),
                "live_cart_ok": False,
                "what_happens": live.get("error") or "Could not read or update the live store cart.",
                "note": (
                    "A live cart failure does not mean the supermarket login is missing. "
                    f"login_saved={ctx['login_saved']}."
                ),
            },
            ensure_ascii=False,
        )
    ctx["what_happens"] = f"{ctx['store']} cart: {ctx['item_count']} item(s)."
    return _ok(**ctx)


def _checkout_store(user: dict[str, Any], sid: str) -> str:
    listed = json.loads(_mutate_cart(user, sid, {"action": "list"}))
    if not listed.get("success"):
        return json.dumps(listed, ensure_ascii=False)
    if not listed.get("items"):
        listed["ready"] = False
        listed["what_happens"] = "Store cart is empty."
        return json.dumps(listed, ensure_ascii=False)
    creds = db.get_retailer_secret(user["id"], sid) or {}
    live = checkout.run_checkout(
        store=sid,
        email=creds.get("email") or "",
        password=creds.get("password") or "",
        address=listed.get("delivery_address") or creds.get("address") or "",
        items=listed.get("items") or [],
    )
    order = db.create_order(
        user["id"],
        sid,
        listed.get("items") or [],
        listed.get("delivery_address") or "",
        live.get("final_url") or listed.get("checkout_url") or "",
    )
    listed.update(
        {
            "ready": bool(live.get("ok")),
            "order_id": order["order_id"],
            "status": live.get("stage") or order["status"],
            "payment_completed": bool(live.get("payment_completed")),
            "live_checkout": live,
            "checkout_url": live.get("final_url") or listed.get("checkout_url"),
            "what_happens": live.get("what_happens") or live.get("error"),
        }
    )
    return json.dumps(listed, ensure_ascii=False)


def _normalize_tool(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw = (name or "").strip()
    n = raw.lower().replace("-", "_").replace(" ", "_")
    for prefix in ("bring_fast_", "bringfast_", "fast_bring_", "fastbring_"):
        if n.startswith(prefix):
            n = n[len(prefix) :]
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
        "checkout": "bf_checkout",
        "status": "bf_status",
        "bf_retailers": "bf_stores",
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
        if sid in n and "cart" in n:
            return f"{sid}_cart", args
    retailer = (args.get("retailer") or args.get("store") or "").lower()
    if n == "bf_checkout" and retailer in ids:
        return f"{retailer}_checkout", args
    if n == "bf_status" and retailer in ids:
        return f"{retailer}_status", args
    return n, args


def _search_stores(user: dict[str, Any], query: str, retailer: str, limit: int) -> str:
    ids = [retailer] if retailer in {r["id"] for r in db.RETAILERS} else [r["id"] for r in db.RETAILERS]
    out = []
    for sid in ids:
        block = catalog.search(sid, query, limit)
        block["delivery_address"] = _store_ctx(user, sid)["delivery_address"]
        out.append(block)
    return json.dumps({"success": True, "query": query, "stores": out}, ensure_ascii=False)


def _call_tool(user: dict[str, Any], name: str, args: dict[str, Any]) -> str:
    uid = user["id"]
    name, args = _normalize_tool(name, args or {})
    if name == "bf_whoami":
        return _ok(**_account_snapshot(user))
    if name == "bf_search":
        return _search_stores(
            user,
            args.get("query") or args.get("q") or "",
            (args.get("retailer") or args.get("store") or "").lower(),
            int(args.get("limit") or 6),
        )
    if name == "bf_cart":
        retailer = (args.get("retailer") or args.get("store") or "").lower()
        if retailer not in {r["id"] for r in db.RETAILERS}:
            return json.dumps({"success": False, "error": "retailer required: carrefour|grandiose|waitrose|spinneys"})
        return _mutate_cart(user, retailer, args)
    if name == "bf_stores":
        snap = _account_snapshot(user)
        return _ok(
            linked_stores=snap["linked_stores"],
            unlinked_stores=snap["unlinked_stores"],
            note=snap["note"],
            stores=snap["stores"],
        )
    for r in db.RETAILERS:
        sid = r["id"]
        if name == f"{sid}_search":
            result = catalog.search(sid, args.get("query") or "", int(args.get("limit") or 8))
            result["delivery_address"] = _store_ctx(user, sid)["delivery_address"]
            result["store"] = r["name"]
            return json.dumps(result, ensure_ascii=False)
        if name == f"{sid}_cart":
            return _mutate_cart(user, sid, args)
        if name == f"{sid}_checkout":
            return _checkout_store(user, sid)
        if name == f"{sid}_status":
            snap = _store_snapshot(user, sid)
            listed = json.loads(_mutate_cart(user, sid, {"action": "list"}))
            listed.update(
                {
                    "linked": snap["login_saved"],
                    "login_saved": snap["login_saved"],
                    "login_email": snap["login_email"],
                    "last_delivery_address": snap["last_delivery_address"],
                    "address_note": snap["address_note"],
                    "recent_orders": snap["recent_orders"],
                    "last_seen_cart": snap["last_seen_cart"],
                }
            )
            if not listed.get("success"):
                listed["success"] = True
                listed["live_cart_ok"] = False
                listed["items"] = listed.get("items") or snap["last_seen_cart"]
                listed["item_count"] = listed.get("item_count") or snap["last_seen_count"]
                listed["what_happens"] = (
                    f"{snap['store']}: login_saved={snap['login_saved']}. "
                    f"{listed.get('what_happens') or 'Live cart not read.'}"
                )
            return json.dumps(listed, ensure_ascii=False)
    return json.dumps(
        {
            "success": False,
            "error": f"unknown tool {name}",
            "use": "bf_search with query=... or carrefour_search / grandiose_search / waitrose_search / spinneys_search",
            "available": [t["name"] for t in TOOLS],
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
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "Bring Fast", "version": __version__},
                "instructions": (
                    f"Bring Fast for {user['email']} only. "
                    "Call bf_whoami first: it already lists linked supermarket logins, last official orders, "
                    "and last seen carts. linked=true / login_saved=true means that store login is saved — "
                    "never say the login is missing for a store in linked_stores. "
                    "The delivery address is the one already on the supermarket account; do not change it on Bring Fast. "
                    "Tools are split per store: carrefour_*, grandiose_*, waitrose_*, spinneys_* "
                    "(search, cart, checkout, status). "
                    "A live cart/status failure is not proof the login is missing. "
                    "Checkout opens the official store; payment stays on the supermarket site."
                ),
            },
        )
    if method == "tools/list":
        return _rpc_result(rid, {"tools": TOOLS})
    if method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        return _rpc_result(rid, {key: []})
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
):
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
        user, error, _created = _sign_in_or_create(email, password)
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
