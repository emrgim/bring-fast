"""X (Twitter) API v2 tools for the Domvs MCP.

Uses the host's user-context developer app via environment variables.
Never reads secrets from the signed-in grocery account. Does not call the
live X API unless a tool is invoked with credentials present.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Any
from urllib.parse import quote, urlsplit

import requests

from . import __version__

DEFAULT_USERNAME = "ilTrumpista"
API_BASE = "https://api.x.com/2"
TIMEOUT = 20

USER_FIELDS = (
    "id,name,username,description,created_at,public_metrics,verified,protected,profile_image_url"
)
TWEET_FIELDS = (
    "id,text,created_at,author_id,public_metrics,lang,conversation_id,"
    "in_reply_to_user_id,referenced_tweets"
)

ENV_USER = (
    "X_API_KEY",
    "X_API_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
)
ENV_BEARER = "X_BEARER_TOKEN"
ENV_ALIASES = {
    "X_API_KEY": ("TWITTER_API_KEY", "TWITTER_CONSUMER_KEY", "X_CONSUMER_KEY"),
    "X_API_SECRET": ("TWITTER_API_SECRET", "TWITTER_CONSUMER_SECRET", "X_CONSUMER_SECRET"),
    "X_ACCESS_TOKEN": ("TWITTER_ACCESS_TOKEN",),
    "X_ACCESS_TOKEN_SECRET": ("TWITTER_ACCESS_TOKEN_SECRET",),
    "X_BEARER_TOKEN": ("TWITTER_BEARER_TOKEN",),
}

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,50}$")
TOOL_NAMES = frozenset(
    {"x_me", "x_user_by_username", "x_user_posts", "x_mentions", "x_search", "x_post"}
)

_MISSING = (
    "X API credentials are not set on this Domvs host. "
    "Set X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, and X_ACCESS_TOKEN_SECRET "
    "(user-context OAuth 1.0a for the operator's X developer app). "
    "Optional: X_BEARER_TOKEN for app-only reads. "
    "Do not paste keys into chat; put them on the Domvs process environment."
)
_NEED_USER = (
    "This X tool needs user-context OAuth 1.0a "
    "(X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET). "
    "X_BEARER_TOKEN alone is not enough to post or to call /2/users/me."
)


def percent_encode(value: str) -> str:
    """RFC 3986 encoding used by OAuth 1.0a (unreserved: ALPHA / DIGIT / - . _ ~)."""
    return quote(str(value), safe="~")


def _env(name: str) -> str:
    val = (os.environ.get(name) or "").strip()
    if val:
        return val
    for alt in ENV_ALIASES.get(name, ()):
        val = (os.environ.get(alt) or "").strip()
        if val:
            return val
    return ""


def user_context_creds() -> dict[str, str] | None:
    creds = {
        "api_key": _env("X_API_KEY"),
        "api_secret": _env("X_API_SECRET"),
        "access_token": _env("X_ACCESS_TOKEN"),
        "access_token_secret": _env("X_ACCESS_TOKEN_SECRET"),
    }
    if all(creds.values()):
        return creds
    return None


def bearer_token() -> str:
    return _env(ENV_BEARER)


def missing_credentials(*, need_user_context: bool = False) -> dict[str, Any]:
    if need_user_context and bearer_token() and not user_context_creds():
        return {
            "success": False,
            "error": _NEED_USER,
            "error_code": "x_user_context_required",
            "need": list(ENV_USER),
            "optional": [ENV_BEARER],
        }
    return {
        "success": False,
        "error": _MISSING,
        "error_code": "x_credentials_missing",
        "need": list(ENV_USER),
        "optional": [ENV_BEARER],
    }


def signature_base_string(method: str, url: str, params: dict[str, str]) -> str:
    """OAuth 1.0a signature base string. `url` must have no query string."""
    parts = urlsplit(url)
    base_url = f"{parts.scheme}://{parts.netloc}{parts.path}"
    encoded = "&".join(
        f"{percent_encode(k)}={percent_encode(params[k])}" for k in sorted(params)
    )
    return (
        f"{method.upper()}&{percent_encode(base_url)}&{percent_encode(encoded)}"
    )


def oauth_signature(
    method: str,
    url: str,
    params: dict[str, str],
    api_secret: str,
    token_secret: str,
) -> str:
    raw = signature_base_string(method, url, params)
    key = f"{percent_encode(api_secret)}&{percent_encode(token_secret)}"
    digest = hmac.new(key.encode("utf-8"), raw.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def oauth_authorization_header(
    method: str,
    url: str,
    extra_params: dict[str, str],
    creds: dict[str, str],
    *,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Build an OAuth 1.0a Authorization header. JSON bodies are not signed."""
    oauth = {
        "oauth_consumer_key": creds["api_key"],
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": creds["access_token"],
        "oauth_version": "1.0",
    }
    signed = {**extra_params, **oauth}
    oauth["oauth_signature"] = oauth_signature(
        method, url, signed, creds["api_secret"], creds["access_token_secret"]
    )
    parts = [
        f'{percent_encode(k)}="{percent_encode(oauth[k])}"' for k in sorted(oauth)
    ]
    return "OAuth " + ", ".join(parts)


def http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: int = TIMEOUT,
):
    """Overridable transport. Tests monkeypatch this; production uses requests."""
    if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("X_ALLOW_LIVE") != "1":
        raise RuntimeError(
            "Refusing to call the live X API during pytest (set X_ALLOW_LIVE=1 to override)."
        )
    return requests.request(
        method,
        url,
        headers=headers,
        params=params or None,
        json=json_body,
        timeout=timeout,
    )


def _query(params: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (params or {}).items():
        if value is None or value is False or value == "":
            continue
        out[str(key)] = str(value)
    return out


def _max_results(raw: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(raw if raw not in (None, "") else default)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _username(raw: Any, default: str = DEFAULT_USERNAME) -> str:
    s = str(raw or "").strip().lstrip("@")
    return s or default


def api_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    need_user_context: bool = False,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """GET/POST https://api.x.com/2/... with OAuth 1.0a or bearer. Never retries writes."""
    creds = user_context_creds()
    bearer = bearer_token()
    if need_user_context and not creds:
        return missing_credentials(need_user_context=True)
    if not creds and not bearer:
        return missing_credentials()

    path = "/" + str(path or "").lstrip("/")
    url = f"{API_BASE}{path}"
    query = _query(params)
    headers = {
        "Accept": "application/json",
        "User-Agent": f"BringFast-MCP-X/{__version__}",
    }
    if creds:
        headers["Authorization"] = oauth_authorization_header(
            method, url, query, creds, nonce=nonce, timestamp=timestamp
        )
    else:
        headers["Authorization"] = f"Bearer {bearer}"
    if json_body is not None:
        headers["Content-Type"] = "application/json"

    try:
        resp = http_request(
            method.upper(),
            url,
            headers=headers,
            params=query or None,
            json_body=json_body,
        )
    except requests.RequestException as e:
        return {
            "success": False,
            "error": f"X API request failed: {type(e).__name__}: {e}",
            "error_code": "x_request_failed",
        }

    payload: Any
    try:
        payload = resp.json()
    except (ValueError, TypeError):
        payload = {"error": (resp.text or "")[:800]}

    if resp.status_code in (200, 201) and not (
        isinstance(payload, dict) and payload.get("errors")
    ):
        if not isinstance(payload, dict):
            return {"success": True, "data": payload}
        return {"success": True, **payload}

    detail = _api_error_text(payload, resp.status_code)
    code = "x_rate_limited" if resp.status_code == 429 else "x_api_error"
    return {
        "success": False,
        "error": detail,
        "error_code": code,
        "status": resp.status_code,
    }


def _api_error_text(payload: Any, status: int) -> str:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            bits = []
            for err in errors:
                if isinstance(err, dict):
                    bits.append(str(err.get("message") or err.get("detail") or err.get("title") or err))
                else:
                    bits.append(str(err))
            if bits:
                return "; ".join(bits)
        for key in ("detail", "title", "error", "error_description"):
            if payload.get(key):
                return str(payload[key])
    return f"X API HTTP {status}"


def _users_from_includes(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    users = {}
    includes = payload.get("includes") if isinstance(payload, dict) else None
    if isinstance(includes, dict):
        for u in includes.get("users") or []:
            if isinstance(u, dict) and u.get("id"):
                users[str(u["id"])] = u
    return users


def _shape_user(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    metrics = data.get("public_metrics") if isinstance(data.get("public_metrics"), dict) else {}
    return {
        "id": data.get("id"),
        "username": data.get("username"),
        "name": data.get("name"),
        "description": data.get("description") or "",
        "verified": bool(data.get("verified")),
        "protected": bool(data.get("protected")),
        "created_at": data.get("created_at"),
        "profile_image_url": data.get("profile_image_url") or "",
        "followers": metrics.get("followers_count"),
        "following": metrics.get("following_count"),
        "tweet_count": metrics.get("tweet_count"),
    }


def _shape_tweet(row: dict[str, Any], users: dict[str, dict[str, Any]]) -> dict[str, Any]:
    author = users.get(str(row.get("author_id") or "")) or {}
    metrics = row.get("public_metrics") if isinstance(row.get("public_metrics"), dict) else {}
    return {
        "id": row.get("id"),
        "text": row.get("text"),
        "created_at": row.get("created_at"),
        "author_id": row.get("author_id"),
        "username": author.get("username"),
        "name": author.get("name"),
        "lang": row.get("lang"),
        "conversation_id": row.get("conversation_id"),
        "in_reply_to_user_id": row.get("in_reply_to_user_id"),
        "referenced_tweets": row.get("referenced_tweets") or [],
        "likes": metrics.get("like_count"),
        "reposts": metrics.get("retweet_count"),
        "replies": metrics.get("reply_count"),
        "quotes": metrics.get("quote_count"),
    }


def _shape_tweets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    users = _users_from_includes(payload)
    raw = payload.get("data")
    rows = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    return [_shape_tweet(r, users) for r in rows if isinstance(r, dict)]


def _ok(tool: str, **kw: Any) -> str:
    return json.dumps({"success": True, "tool": tool, **kw}, ensure_ascii=False)


def _fail(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _user_params() -> dict[str, str]:
    return {"user.fields": USER_FIELDS}


def _tweet_list_params(max_results: int, pagination_token: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "max_results": max_results,
        "tweet.fields": TWEET_FIELDS,
        "expansions": "author_id",
        "user.fields": "id,name,username,profile_image_url",
    }
    if pagination_token:
        params["pagination_token"] = pagination_token
    return params


def resolve_user(username: str) -> dict[str, Any]:
    """Look up a user. `me` / `self` is the authenticated developer-app user."""
    who = _username(username)
    if who.lower() in ("me", "self"):
        got = api_request("GET", "/users/me", params=_user_params(), need_user_context=True)
    else:
        if not _USERNAME_RE.fullmatch(who):
            return {
                "success": False,
                "error": f"Invalid X username {who!r}. Use a handle like ilTrumpista (no URL).",
                "error_code": "x_username_invalid",
            }
        got = api_request(
            "GET",
            f"/users/by/username/{who}",
            params=_user_params(),
        )
    if not got.get("success"):
        return got
    shaped = _shape_user(got.get("data") if isinstance(got.get("data"), dict) else None)
    if not shaped.get("id"):
        return {"success": False, "error": f"X user {who!r} had no id.", "error_code": "x_user_missing"}
    return {"success": True, **shaped, "data": got.get("data")}


def _timeline(tool: str, path: str, username: str, args: dict[str, Any], lo: int) -> str:
    user = resolve_user(username)
    if not user.get("success"):
        return _fail(user)
    n = _max_results(args.get("max_results") or args.get("limit"), 10, lo, 100)
    token = str(args.get("pagination_token") or args.get("next_token") or "").strip() or None
    params = _tweet_list_params(n, token)
    exclude = str(args.get("exclude") or "").strip()
    if exclude:
        params["exclude"] = exclude
    got = api_request("GET", path.format(id=user["id"]), params=params)
    if not got.get("success"):
        return _fail(got)
    posts = _shape_tweets(got)
    return _ok(
        tool,
        username=user.get("username"),
        user_id=user.get("id"),
        name=user.get("name"),
        posts=posts,
        meta=got.get("meta") or {},
        data=got.get("data") if isinstance(got.get("data"), list) else posts,
    )


def call_tool(name: str, args: dict[str, Any] | None) -> str:
    args = args or {}
    if name == "x_me":
        got = api_request("GET", "/users/me", params=_user_params(), need_user_context=True)
        if not got.get("success"):
            return _fail(got)
        shaped = _shape_user(got.get("data") if isinstance(got.get("data"), dict) else None)
        return _ok("x_me", **shaped, data=got.get("data"))
    if name == "x_user_by_username":
        got = resolve_user(args.get("username") or args.get("user") or args.get("handle"))
        if not got.get("success"):
            return _fail(got)
        data = got.pop("data", None)
        return _ok("x_user_by_username", **got, data=data)
    if name == "x_user_posts":
        who = _username(args.get("username") or args.get("user") or args.get("handle"))
        return _timeline("x_user_posts", "/users/{id}/tweets", who, args, lo=5)
    if name == "x_mentions":
        who = _username(args.get("username") or args.get("user") or args.get("handle"))
        return _timeline("x_mentions", "/users/{id}/mentions", who, args, lo=5)
    if name == "x_search":
        query = str(args.get("query") or args.get("q") or "").strip()
        if not query:
            return _fail(
                {
                    "success": False,
                    "error": "x_search needs query= (recent search, last 7 days). Example: from:ilTrumpista",
                    "error_code": "x_query_required",
                }
            )
        n = _max_results(args.get("max_results") or args.get("limit"), 10, 10, 100)
        token = str(args.get("pagination_token") or args.get("next_token") or "").strip() or None
        params = _tweet_list_params(n, token)
        params["query"] = query
        # search/recent uses next_token, not pagination_token
        if token:
            params.pop("pagination_token", None)
            params["next_token"] = token
        got = api_request("GET", "/tweets/search/recent", params=params)
        if not got.get("success"):
            return _fail(got)
        posts = _shape_tweets(got)
        return _ok(
            "x_search",
            query=query,
            posts=posts,
            meta=got.get("meta") or {},
            data=got.get("data") if isinstance(got.get("data"), list) else posts,
        )
    if name == "x_post":
        text = str(args.get("text") or args.get("status") or args.get("tweet") or "").strip()
        if not text:
            return _fail(
                {
                    "success": False,
                    "error": (
                        "x_post is a WRITE: it creates a live tweet as the authenticated X user. "
                        "Pass text=. Optional reply_to=<tweet id>."
                    ),
                    "error_code": "x_text_required",
                    "write": True,
                }
            )
        body: dict[str, Any] = {"text": text}
        reply_to = str(args.get("reply_to") or args.get("in_reply_to_tweet_id") or "").strip()
        if reply_to:
            body["reply"] = {"in_reply_to_tweet_id": reply_to}
        got = api_request("POST", "/tweets", json_body=body, need_user_context=True)
        if not got.get("success"):
            got.setdefault("write", True)
            return _fail(got)
        data = got.get("data") if isinstance(got.get("data"), dict) else {}
        return _ok(
            "x_post",
            write=True,
            id=data.get("id"),
            text=data.get("text") or text,
            data=data,
        )
    return _fail({"success": False, "error": f"unknown tool {name}", "available": sorted(TOOL_NAMES)})


def tools() -> list[dict[str, Any]]:
    """MCP tools/list entries. Grocery catalog is unchanged; these are appended."""
    return [
        {
            "name": "x_me",
            "description": (
                "Read the authenticated X (Twitter) user for THIS Domvs host's developer app "
                "(OAuth 1.0a user context). Not the signed-in grocery account, and not Cursor's X plugin. "
                "Returns id, username, name, description, metrics."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "x_user_by_username",
            "description": (
                "Read an X profile by username. Default username=ilTrumpista. "
                "Pass username=me for the authenticated developer-app user. Strip or keep the @."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"username": {"type": "string"}},
            },
        },
        {
            "name": "x_user_posts",
            "description": (
                "Read recent posts (timeline) for an X user. Default username=ilTrumpista. "
                "max_results 5-100. Optional pagination_token, exclude=retweets,replies. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "pagination_token": {"type": "string"},
                    "exclude": {"type": "string"},
                },
            },
        },
        {
            "name": "x_mentions",
            "description": (
                "Read recent posts that mention an X user. Default username=ilTrumpista. "
                "max_results 5-100. Optional pagination_token. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "pagination_token": {"type": "string"},
                },
            },
        },
        {
            "name": "x_search",
            "description": (
                "Search recent X posts (last 7 days). query is required "
                "(example: from:ilTrumpista or @ilTrumpista). max_results 10-100. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "pagination_token": {"type": "string"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "x_post",
            "description": (
                "WRITE: create a tweet as the authenticated X user on THIS Domvs host's developer app. "
                "Only call when the user explicitly asked to post/tweet. "
                "text is required. Optional reply_to is the tweet id to reply to. "
                "Does not use Cursor's X plugin."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Tweet text (required). This publishes a live post."},
                    "reply_to": {"type": "string", "description": "Tweet id to reply to"},
                },
                "required": ["text"],
            },
        },
    ]
