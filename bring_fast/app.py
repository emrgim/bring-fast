from __future__ import annotations

import json
import os
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import catalog, db

HOST = os.environ.get("BRINGFAST_HOST", "127.0.0.1")
PORT = int(os.environ.get("BRINGFAST_PORT", "8877"))
PUBLIC_URL = os.environ.get("BRINGFAST_PUBLIC_URL", "").rstrip("/")
SECRET = os.environ.get("BRINGFAST_SECRET", "bring-fast-change-me")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
app = FastAPI(title="Fast Bring")
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


def mcp_url() -> str:
    return f"{PUBLIC_URL}/mcp" if PUBLIC_URL else f"http://127.0.0.1:{PORT}/mcp"


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = current_user(request)
    if not user:
        return templates.TemplateResponse(request, "login.html", {"user": None, "title": "Fast Bring"})
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "retailers": db.list_retailer_accounts(user["id"]),
            "mcp_url": mcp_url(),
            "title": "Fast Bring",
        },
    )


@app.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        user = db.create_user(email, password)
    except ValueError as e:
        return templates.TemplateResponse(
            request, "login.html", {"user": None, "error": str(e), "title": "Fast Bring"}, status_code=400
        )
    request.session["uid"] = user["id"]
    return RedirectResponse("/", status_code=303)


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_email(email)
    if not user or not db.verify_password(user, password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"user": None, "error": "Wrong email or password", "title": "Fast Bring"},
            status_code=401,
        )
    request.session["uid"] = user["id"]
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.post("/retailers/{retailer}")
def save_retailer(request: Request, retailer: str, email: str = Form(...), password: str = Form(...)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/", status_code=303)
    if retailer not in {r["id"] for r in db.RETAILERS}:
        return RedirectResponse("/", status_code=303)
    db.set_retailer_account(user["id"], retailer, email.strip(), password)
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


TOOLS = [
    {
        "name": "bf_whoami",
        "description": "Show the Bring Fast user bound to this MCP token (never another account).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "bf_retailers",
        "description": "List supermarket adapters and whether THIS user linked a login.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "bf_search",
        "description": "Search a supermarket catalog. retailer=carrefour|grandiose|waitrose|spinneys",
        "inputSchema": {
            "type": "object",
            "properties": {
                "retailer": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["retailer", "query"],
        },
    },
    {
        "name": "bf_cart",
        "description": "THIS user's cart only. action=list|add|set|remove|clear. Does not invent official checkout.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "retailer": {"type": "string"},
                "product_id": {"type": "string"},
                "name": {"type": "string"},
                "qty": {"type": "integer"},
                "price": {"type": "number"},
            },
            "required": ["action", "retailer"],
        },
    },
]


def _ok(**kw):
    return json.dumps({"success": True, **kw}, ensure_ascii=False)


def _call_tool(user: dict[str, Any], name: str, args: dict[str, Any]) -> str:
    uid = user["id"]
    if name == "bf_whoami":
        return _ok(email=user["email"], user_id=uid, note="tools only see this account")
    if name == "bf_retailers":
        return _ok(retailers=db.list_retailer_accounts(uid))
    if name == "bf_search":
        return json.dumps(catalog.search(args.get("retailer") or "", args.get("query") or "", int(args.get("limit") or 8)), ensure_ascii=False)
    if name == "bf_cart":
        retailer = args.get("retailer") or ""
        if retailer not in {r["id"] for r in db.RETAILERS}:
            return json.dumps({"success": False, "error": "unknown retailer"})
        action = (args.get("action") or "list").lower()
        cart = db.load_cart(uid, retailer)
        items = cart.setdefault("items", [])
        if action == "list":
            return _ok(owner=user["email"], retailer=retailer, **cart, note="personal Bring Fast cart for this user only")
        if action == "clear":
            db.save_cart(uid, retailer, {"items": [], "currency": "AED"})
            return _ok(cleared=True, owner=user["email"], retailer=retailer, items=[])
        if action == "remove":
            pid = str(args.get("product_id") or "")
            cart["items"] = [i for i in items if str(i.get("id")) != pid]
            db.save_cart(uid, retailer, cart)
            return _ok(owner=user["email"], retailer=retailer, **cart)
        if action in ("add", "set"):
            pid = str(args.get("product_id") or "")
            if not pid:
                return json.dumps({"success": False, "error": "product_id required"})
            qty = int(args.get("qty") or 1)
            found = None
            for i in items:
                if str(i.get("id")) == pid:
                    found = i
                    break
            if action == "set" and qty <= 0:
                cart["items"] = [i for i in items if str(i.get("id")) != pid]
            elif found:
                found["qty"] = (int(found.get("qty") or 0) + qty) if action == "add" else qty
                if args.get("name"):
                    found["name"] = args["name"]
            else:
                items.append(
                    {
                        "id": pid,
                        "name": args.get("name") or pid,
                        "qty": max(1, qty),
                        "price": args.get("price"),
                        "currency": "AED",
                    }
                )
            db.save_cart(uid, retailer, cart)
            return _ok(
                owner=user["email"],
                retailer=retailer,
                **cart,
                official_site="not updated — credentials stored on your dashboard; official checkout stays on the retailer site",
            )
        return json.dumps({"success": False, "error": f"unknown action {action}"})
    return json.dumps({"success": False, "error": f"unknown tool {name}"})


@app.api_route("/mcp", methods=["GET", "HEAD", "POST"])
async def mcp_endpoint(request: Request):
    if request.method in ("GET", "HEAD"):
        return _oauth_challenge()
    user = _user_from_request(request)
    if not user:
        return _oauth_challenge()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32700, "message": "parse error"}}, status_code=400)
    rid = body.get("id")
    method = body.get("method")
    if method == "initialize":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "Fast Bring", "version": "1.0.0"},
                    "instructions": (
                        f"Bring Fast for {user['email']} only. Never use another user's stores. "
                        "Retailers: carrefour, grandiose, waitrose, spinneys. "
                        "Carts are private to this account."
                    ),
                },
            }
        )
    if method == "notifications/initialized":
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": {}})
    if method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    if method == "tools/call":
        params = body.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        text = _call_tool(user, name, args)
        return JSONResponse(
            {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}]}}
        )
    if method == "ping":
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": {}})
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": method}})


OAUTH_CLIENT_ID = os.environ.get("BRINGFAST_OAUTH_CLIENT_ID", "fast-bring")
OAUTH_CLIENT_SECRET = os.environ.get("BRINGFAST_OAUTH_CLIENT_SECRET", "fast-bring-grok-secret")


def _issuer() -> str:
    return PUBLIC_URL or f"http://127.0.0.1:{PORT}"


def _oauth_challenge():
    meta = f"{_issuer()}/.well-known/oauth-protected-resource"
    return JSONResponse(
        {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "OAuth required. Sign in with your Fast Bring account."}},
        status_code=401,
        headers={
            "WWW-Authenticate": f'Bearer realm="Fast Bring", resource_metadata="{meta}"',
            "Access-Control-Expose-Headers": "WWW-Authenticate",
        },
    )


def _as_metadata() -> dict:
    base = _issuer()
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


def _prm_metadata() -> dict:
    base = _issuer()
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp"],
        "resource_name": "Fast Bring",
    }


@app.get("/.well-known/oauth-authorization-server")
@app.get("/.well-known/oauth-authorization-server/mcp")
@app.get("/.well-known/openid-configuration")
def oauth_as_metadata():
    return _as_metadata()


@app.get("/.well-known/oauth-protected-resource")
@app.get("/.well-known/oauth-protected-resource/mcp")
def oauth_prm_metadata():
    return _prm_metadata()


def _safe_redirect(uri: str) -> bool:
    if not uri:
        return False
    return uri.startswith("https://") or uri.startswith("http://127.0.0.1") or uri.startswith("http://localhost")


def _issue_code(user, redirect_uri: str, client_id: str, challenge: str, method: str, state: str):
    code = db.save_oauth_code(user["id"], redirect_uri, client_id, challenge, method)
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}code={code}&state={state}", status_code=302)


@app.api_route("/oauth/register", methods=["POST", "OPTIONS"])
@app.api_route("/register", methods=["POST", "OPTIONS"])
async def oauth_register(request: Request):
    if request.method == "OPTIONS":
        return JSONResponse({}, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*"})
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
        }
    )
    return JSONResponse(rec, status_code=201)


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
            "title": "Authorize Fast Bring",
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
                    "title": "Authorize Fast Bring",
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
