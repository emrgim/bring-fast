from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastapi import FastAPI, Form, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import catalog, checkout, db

HOST = os.environ.get("BRINGFAST_HOST", "127.0.0.1")
PORT = int(os.environ.get("BRINGFAST_PORT", "8877"))
PUBLIC_URL = os.environ.get("BRINGFAST_PUBLIC_URL", "").rstrip("/")
SECRET = os.environ.get("BRINGFAST_SECRET", "bring-fast-change-me")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
app = FastAPI(title="Bring Fast")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["WWW-Authenticate"],
)
app.add_middleware(SessionMiddleware, secret_key=SECRET, same_site="lax")
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")


def current_user(request: Request):
    uid = request.session.get("uid")
    return db.get_user_by_id(uid) if uid else None


def _issuer(request: Request | None = None) -> str:
    """Public base URL clients should call back on.

    Without this, a server behind a tunnel advertises 127.0.0.1 in its OAuth metadata
    and the client has nothing reachable to authenticate against.
    """
    if PUBLIC_URL:
        return PUBLIC_URL
    if request is not None:
        forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
        host = forwarded_host or (request.headers.get("host") or "").strip()
        if host:
            scheme = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
            return f"{scheme or request.url.scheme}://{host}".rstrip("/")
    return f"http://127.0.0.1:{PORT}"


def mcp_url(request: Request | None = None) -> str:
    return f"{_issuer(request)}/mcp"


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = current_user(request)
    if not user:
        return templates.TemplateResponse(request, "login.html", {"user": None, "title": "Bring Fast"})
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "retailers": db.list_retailer_accounts(user["id"]),
            "mcp_url": mcp_url(request),
            "title": "Bring Fast",
        },
    )


def _register_user(request: Request, email: str, password: str):
    try:
        user = db.create_user(email, password)
    except ValueError as e:
        return templates.TemplateResponse(
            request, "login.html", {"user": None, "error": str(e), "title": "Bring Fast"}, status_code=400
        )
    request.session["uid"] = user["id"]
    return RedirectResponse("/", status_code=303)


@app.api_route("/register", methods=["POST", "OPTIONS"])
async def register(request: Request):
    """Two callers share this path: the dashboard signup form and OAuth client registration."""
    if request.method == "OPTIONS":
        return _cors_preflight()
    if "json" in (request.headers.get("content-type") or "").lower():
        return await _register_oauth_client(request)
    form = await request.form()
    return _register_user(request, str(form.get("email") or ""), str(form.get("password") or ""))


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_email(email)
    if not user or not db.verify_password(user, password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"user": None, "error": "Wrong email or password", "title": "Bring Fast"},
            status_code=401,
        )
    request.session["uid"] = user["id"]
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


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


@app.post("/rotate-token")
def rotate(request: Request):
    user = current_user(request)
    if user:
        db.rotate_token(user["id"])
    return RedirectResponse("/", status_code=303)


@app.get("/health")
def health():
    return {"ok": True, "server": "Bring Fast"}


def _user_from_request(request: Request):
    auth = request.headers.get("authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
    return db.get_user_by_token(token)


def _store_tools() -> list[dict[str, Any]]:
    tools = [
        {
            "name": "bf_whoami",
            "description": "Show the Bring Fast user bound to this MCP token (never another account).",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "bf_stores",
            "description": "List THIS user's stores: login linked?, delivery address, checkout URL, last cart count.",
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
                        f"What is happening at {name}: delivery address, current cart, last checkout snapshots."
                    ),
                    "inputSchema": {"type": "object", "properties": {}},
                },
            ]
        )
    return tools


TOOLS = _store_tools()


def _ok(**kw):
    return json.dumps({"success": True, **kw}, ensure_ascii=False)


def _store_ctx(user: dict[str, Any], retailer: str, items: list | None = None, total: float | None = None) -> dict[str, Any]:
    stores = {s["id"]: s for s in db.list_retailer_accounts(user["id"])}
    s = stores[retailer]
    items = items or []
    if total is None:
        total = 0.0
        for i in items:
            try:
                total += float(i.get("price") or 0) * int(i.get("qty") or 0)
            except (TypeError, ValueError):
                pass
    addr = (s.get("delivery_address") or "").strip()
    return {
        "store": s["name"],
        "store_id": retailer,
        "store_url": s["url"],
        "owner": user["email"],
        "login_linked": bool(s.get("linked")),
        "login_email": s.get("login_email"),
        "delivery_address": addr or None,
        "delivery_note": (
            "Leave with security. Do not ring, call, or leave at the door."
            if addr
            else "No delivery address saved for this store. Add it on the Bring Fast dashboard store card."
        ),
        "cart_url": s.get("cart_url"),
        "checkout_url": s.get("checkout_url"),
        "items": items,
        "item_count": sum(int(i.get("qty") or 1) for i in items),
        "currency": "AED",
        "estimated_total": round(total, 2) if total else None,
        "cart_source": "store",
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
    ctx = _store_ctx(user, retailer, items=items)
    ctx["action"] = action
    ctx["official_count"] = live.get("official_count")
    ctx["official_ok"] = bool(live.get("ok"))
    if not live.get("ok"):
        return json.dumps(
            {
                "success": False,
                **ctx,
                "items": [],
                "item_count": 0,
                "estimated_total": None,
                "what_happens": live.get("error") or "Could not read or update the store cart.",
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
        return _ok(email=user["email"], user_id=uid, note="tools only see this Bring Fast account")
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
        stores = []
        for s in db.list_retailer_accounts(uid):
            stores.append(
                {
                    "store_id": s["id"],
                    "store": s["name"],
                    "url": s["url"],
                    "checkout_url": s.get("checkout_url"),
                    "linked": s["linked"],
                    "login_email": s.get("login_email"),
                    "delivery_address": s.get("delivery_address") or None,
                    "tools": [f"{s['id']}_search", f"{s['id']}_cart", f"{s['id']}_checkout", f"{s['id']}_status"],
                }
            )
        return _ok(stores=stores)
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
            listed = json.loads(_mutate_cart(user, sid, {"action": "list"}))
            listed["recent_orders"] = db.list_orders(uid, sid, 5)
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
                "serverInfo": {"name": "Bring Fast", "version": "1.0.0"},
                "instructions": (
                    f"Bring Fast for {user['email']} only. "
                    "Tools are split per store: carrefour_*, grandiose_*, waitrose_*, spinneys_* "
                    "(search, cart, checkout, status). Always report delivery_address. "
                    "Checkout prepares the official store URL; payment stays on the supermarket site."
                ),
            },
        )
    if method == "tools/list":
        return _rpc_result(rid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        text, failed = await asyncio.to_thread(_call_tool_safe, user, name, args)
        return _rpc_result(rid, {"content": [{"type": "text", "text": text}], "isError": failed})
    if method == "ping":
        return _rpc_result(rid, {})
    return _rpc_error(rid, -32601, f"method not found: {method}")


@app.api_route("/mcp", methods=["GET", "HEAD", "POST", "DELETE", "OPTIONS"])
async def mcp_endpoint(request: Request):
    if request.method == "OPTIONS":
        return _cors_preflight()
    user = _user_from_request(request)
    if not user:
        return _oauth_challenge(request)
    if request.method in ("GET", "HEAD"):
        # No server-initiated SSE stream here. Answering an authenticated GET with 401
        # tells the client its brand new token is bad, so it reports a failed connection.
        return JSONResponse(
            _rpc_error(None, -32601, "this endpoint has no SSE stream; POST JSON-RPC instead"),
            status_code=405,
            headers={"Allow": "POST, DELETE"},
        )
    if request.method == "DELETE":
        return Response(status_code=204)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_rpc_error(None, -32700, "parse error"), status_code=400)
    try:
        if isinstance(body, list):
            if not body:
                return JSONResponse(_rpc_error(None, -32600, "invalid request: empty batch"), status_code=400)
            replies = [r for r in [await _dispatch(user, m) for m in body] if r is not None]
            return JSONResponse(replies) if replies else Response(status_code=202)
        reply = await _dispatch(user, body)
        return JSONResponse(reply) if reply is not None else Response(status_code=202)
    except Exception as e:
        rid = body.get("id") if isinstance(body, dict) else None
        return JSONResponse(_rpc_error(rid, -32603, f"internal error: {e}"))


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
        "grant_types_supported": ["authorization_code"],
        "token_endpoint_auth_methods_supported": ["none"],
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


def _issue_code(user, redirect_uri: str, client_id: str, challenge: str, method: str, state: str):
    code = db.save_oauth_code(user["id"], redirect_uri, client_id, challenge, method)
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}code={code}&state={state}", status_code=302)


async def _register_oauth_client(request: Request):
    try:
        body = await request.json()
    except Exception:
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
            "response_types": ["code"],
            "client_secret_expires_at": 0,
        }
    )
    return JSONResponse(rec, status_code=201)


@app.api_route("/oauth/register", methods=["POST", "OPTIONS"])
async def oauth_register(request: Request):
    if request.method == "OPTIONS":
        return _cors_preflight()
    return await _register_oauth_client(request)


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
):
    user = current_user(request)
    if user and redirect_uri and _safe_redirect(redirect_uri):
        return _issue_code(user, redirect_uri, client_id, code_challenge, code_challenge_method, state)
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
            "email": user["email"] if user else "",
        },
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
    user = current_user(request)
    if not user:
        user = db.get_user_by_email(email)
        if not user or not db.verify_password(user, password):
            return templates.TemplateResponse(
                request,
                "oauth_authorize.html",
                {
                    "user": None,
                    "title": "Authorize Bring Fast",
                    "redirect_uri": redirect_uri,
                    "state": state,
                    "client_id": client_id,
                    "code_challenge": code_challenge,
                    "code_challenge_method": code_challenge_method,
                    "email": email,
                    "error": "Wrong email or password",
                },
                status_code=401,
            )
        request.session["uid"] = user["id"]
    if not redirect_uri or not _safe_redirect(redirect_uri):
        return RedirectResponse("/", status_code=303)
    return _issue_code(user, redirect_uri, client_id, code_challenge, code_challenge_method, state)


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
    return {
        "access_token": user["mcp_token"],
        "token_type": "bearer",
        "expires_in": 86400 * 30,
        "scope": "mcp",
    }


def main() -> None:
    import uvicorn

    db.connect()
    uvicorn.run("bring_fast.app:app", host=HOST, port=PORT, factory=False)


if __name__ == "__main__":
    main()
