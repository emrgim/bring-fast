"""X MCP tools: credentials-missing path and request shaping. Never hits the live X API."""

from __future__ import annotations

import json

import pytest

from bring_fast import x


X_ENV = (
    "X_API_KEY",
    "X_API_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
    "X_BEARER_TOKEN",
    "TWITTER_API_KEY",
    "TWITTER_API_SECRET",
    "TWITTER_CONSUMER_KEY",
    "TWITTER_CONSUMER_SECRET",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_TOKEN_SECRET",
    "TWITTER_BEARER_TOKEN",
    "X_CONSUMER_KEY",
    "X_CONSUMER_SECRET",
)


class DummyResp:
    def __init__(self, status, payload, text=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _clear_env(monkeypatch):
    for key in X_ENV:
        monkeypatch.delenv(key, raising=False)


def _oauth_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("X_API_KEY", "ck")
    monkeypatch.setenv("X_API_SECRET", "cs")
    monkeypatch.setenv("X_ACCESS_TOKEN", "at")
    monkeypatch.setenv("X_ACCESS_TOKEN_SECRET", "ats")


def _bearer_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("X_BEARER_TOKEN", "bearer-token")


def _capture(monkeypatch, payload=None, status=200):
    calls = []

    def fake(method, url, *, headers, params=None, json_body=None, timeout=20):
        calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "json_body": json_body,
                "timeout": timeout,
            }
        )
        body = payload
        if callable(payload):
            body = payload(method, url, headers, params, json_body)
        if body is None:
            body = {"data": {"id": "1", "username": "ilTrumpista", "name": "T"}}
        return DummyResp(status, body)

    monkeypatch.setattr(x, "http_request", fake)
    return calls


def test_percent_encode_rfc3986():
    assert x.percent_encode("Hello Ladies + Gentlemen, a signed OAuth request!") == (
        "Hello%20Ladies%20%2B%20Gentlemen%2C%20a%20signed%20OAuth%20request%21"
    )


def test_oauth_signature_matches_twitter_creating_a_signature_vector():
    """https://developer.x.com/en/docs/authentication/oauth-1-0a/creating-a-signature"""
    params = {
        "status": "Hello Ladies + Gentlemen, a signed OAuth request!",
        "include_entities": "true",
        "oauth_consumer_key": "xvz1evFS4wEEPTGEFPHBog",
        "oauth_nonce": "kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg",
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": "1318622958",
        "oauth_token": "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
        "oauth_version": "1.0",
    }
    sig = x.oauth_signature(
        "POST",
        "https://api.twitter.com/1.1/statuses/update.json",
        params,
        "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
        "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
    )
    assert sig == "hCtSmYh+iHYCEqBWrE7C7hYmtUk="


def test_missing_credentials_lists_env_vars_and_does_not_http(monkeypatch):
    _clear_env(monkeypatch)
    calls = _capture(monkeypatch)
    for name, args in (
        ("x_me", {}),
        ("x_user_by_username", {"username": "ilTrumpista"}),
        ("x_user_posts", {}),
        ("x_mentions", {}),
        ("x_search", {"query": "from:ilTrumpista"}),
        ("x_post", {"text": "hello"}),
    ):
        out = json.loads(x.call_tool(name, args))
        assert out["success"] is False
        assert out["error_code"] == "x_credentials_missing"
        assert out["need"] == [
            "X_API_KEY",
            "X_API_SECRET",
            "X_ACCESS_TOKEN",
            "X_ACCESS_TOKEN_SECRET",
        ]
        assert "X_BEARER_TOKEN" in out["optional"]
        assert "X_API_KEY" in out["error"]
        assert "Domvs" in out["error"]
    assert calls == []


def test_bearer_alone_cannot_post_or_read_me(monkeypatch):
    _bearer_env(monkeypatch)
    calls = _capture(monkeypatch)
    me = json.loads(x.call_tool("x_me", {}))
    assert me["success"] is False
    assert me["error_code"] == "x_user_context_required"
    post = json.loads(x.call_tool("x_post", {"text": "nope"}))
    assert post["success"] is False
    assert post["error_code"] == "x_user_context_required"
    assert calls == []


def test_user_by_username_default_and_strips_at(monkeypatch):
    _oauth_env(monkeypatch)

    def payload(method, url, headers, params, json_body):
        assert method == "GET"
        assert url == "https://api.x.com/2/users/by/username/ilTrumpista"
        assert params["user.fields"].startswith("id,name,username")
        assert headers["Authorization"].startswith("OAuth ")
        assert "oauth_token=" in headers["Authorization"]
        return {
            "data": {
                "id": "42",
                "username": "ilTrumpista",
                "name": "il Trumpista",
                "description": "bio",
                "public_metrics": {"followers_count": 9, "following_count": 1, "tweet_count": 3},
            }
        }

    _capture(monkeypatch, payload)
    out = json.loads(x.call_tool("x_user_by_username", {}))
    assert out["success"] is True
    assert out["username"] == "ilTrumpista"
    assert out["id"] == "42"
    assert out["followers"] == 9

    calls = _capture(monkeypatch, payload)
    json.loads(x.call_tool("x_user_by_username", {"username": "@ilTrumpista"}))
    assert calls[0]["url"].endswith("/users/by/username/ilTrumpista")


def test_user_posts_looks_up_then_lists_tweets(monkeypatch):
    _oauth_env(monkeypatch)
    calls = []

    def fake(method, url, *, headers, params=None, json_body=None, timeout=20):
        calls.append({"method": method, "url": url, "params": params, "json_body": json_body})
        if "/users/by/username/" in url:
            return DummyResp(200, {"data": {"id": "42", "username": "ilTrumpista", "name": "T"}})
        assert url == "https://api.x.com/2/users/42/tweets"
        assert params["max_results"] == "7"
        assert params["exclude"] == "replies"
        assert "tweet.fields" in params
        assert params["expansions"] == "author_id"
        assert json_body is None
        return DummyResp(
            200,
            {
                "data": [
                    {
                        "id": "99",
                        "text": "hello",
                        "author_id": "42",
                        "created_at": "2026-08-30T00:00:00.000Z",
                        "public_metrics": {"like_count": 2, "retweet_count": 1, "reply_count": 0, "quote_count": 0},
                    }
                ],
                "includes": {"users": [{"id": "42", "username": "ilTrumpista", "name": "T"}]},
                "meta": {"result_count": 1},
            },
        )

    monkeypatch.setattr(x, "http_request", fake)
    out = json.loads(x.call_tool("x_user_posts", {"max_results": 7, "exclude": "replies"}))
    assert out["success"] is True
    assert out["user_id"] == "42"
    assert out["posts"][0]["id"] == "99"
    assert out["posts"][0]["username"] == "ilTrumpista"
    assert out["posts"][0]["likes"] == 2
    assert [c["url"] for c in calls] == [
        "https://api.x.com/2/users/by/username/ilTrumpista",
        "https://api.x.com/2/users/42/tweets",
    ]


def test_mentions_and_search_urls(monkeypatch):
    _oauth_env(monkeypatch)
    calls = []

    def fake(method, url, *, headers, params=None, json_body=None, timeout=20):
        calls.append({"url": url, "params": dict(params or {})})
        if "/users/by/username/" in url:
            return DummyResp(200, {"data": {"id": "42", "username": "ilTrumpista", "name": "T"}})
        if url.endswith("/mentions"):
            return DummyResp(200, {"data": [], "meta": {"result_count": 0}})
        if url.endswith("/tweets/search/recent"):
            assert params["query"] == "from:ilTrumpista"
            assert params["max_results"] == "10"
            assert params["next_token"] == "abc"
            assert "pagination_token" not in params
            return DummyResp(200, {"data": [], "meta": {"result_count": 0, "next_token": "def"}})
        raise AssertionError(url)

    monkeypatch.setattr(x, "http_request", fake)
    mentions = json.loads(x.call_tool("x_mentions", {}))
    assert mentions["success"] is True
    assert calls[1]["url"] == "https://api.x.com/2/users/42/mentions"
    search = json.loads(x.call_tool("x_search", {"query": "from:ilTrumpista", "pagination_token": "abc"}))
    assert search["success"] is True
    assert calls[-1]["url"] == "https://api.x.com/2/tweets/search/recent"


def test_search_requires_query_before_http(monkeypatch):
    _oauth_env(monkeypatch)
    calls = _capture(monkeypatch)
    out = json.loads(x.call_tool("x_search", {}))
    assert out["success"] is False
    assert out["error_code"] == "x_query_required"
    assert calls == []


def test_post_json_body_and_does_not_sign_text(monkeypatch):
    _oauth_env(monkeypatch)
    calls = []

    def fake(method, url, *, headers, params=None, json_body=None, timeout=20):
        calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "json_body": json_body,
            }
        )
        return DummyResp(201, {"data": {"id": "55", "text": "ciao"}})

    monkeypatch.setattr(x, "http_request", fake)

    # Freeze nonce/timestamp through api_request by wrapping call_tool's POST.
    orig = x.api_request

    def wrapped(method, path, **kw):
        if method == "POST":
            kw.setdefault("nonce", "deadbeef")
            kw.setdefault("timestamp", "1700000000")
        return orig(method, path, **kw)

    monkeypatch.setattr(x, "api_request", wrapped)
    out = json.loads(x.call_tool("x_post", {"text": "ciao", "reply_to": "123"}))
    assert out["success"] is True
    assert out["write"] is True
    assert out["id"] == "55"
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://api.x.com/2/tweets"
    assert calls[0]["json_body"] == {"text": "ciao", "reply": {"in_reply_to_tweet_id": "123"}}
    assert calls[0]["params"] in (None, {})
    header = calls[0]["headers"]["Authorization"]
    creds = x.user_context_creds()
    expected = x.oauth_authorization_header(
        "POST",
        "https://api.x.com/2/tweets",
        {},
        creds,
        nonce="deadbeef",
        timestamp="1700000000",
    )
    assert header == expected
    signed_with_text = x.oauth_authorization_header(
        "POST",
        "https://api.x.com/2/tweets",
        {"text": "ciao"},
        creds,
        nonce="deadbeef",
        timestamp="1700000000",
    )
    assert header != signed_with_text


def test_post_requires_text(monkeypatch):
    _oauth_env(monkeypatch)
    calls = _capture(monkeypatch)
    out = json.loads(x.call_tool("x_post", {}))
    assert out["success"] is False
    assert out["error_code"] == "x_text_required"
    assert out["write"] is True
    assert calls == []


def test_bearer_read_uses_bearer_header(monkeypatch):
    _bearer_env(monkeypatch)
    calls = _capture(
        monkeypatch,
        {"data": {"id": "42", "username": "ilTrumpista", "name": "T"}},
    )
    out = json.loads(x.call_tool("x_user_by_username", {"username": "ilTrumpista"}))
    assert out["success"] is True
    assert calls[0]["headers"]["Authorization"] == "Bearer bearer-token"
    assert not calls[0]["headers"]["Authorization"].startswith("OAuth")


def test_me_url(monkeypatch):
    _oauth_env(monkeypatch)
    calls = _capture(
        monkeypatch,
        {"data": {"id": "7", "username": "op", "name": "Op"}},
    )
    out = json.loads(x.call_tool("x_me", {}))
    assert out["success"] is True
    assert out["username"] == "op"
    assert calls[0]["url"] == "https://api.x.com/2/users/me"
    assert calls[0]["method"] == "GET"


def test_invalid_username_does_not_http(monkeypatch):
    _oauth_env(monkeypatch)
    calls = _capture(monkeypatch)
    out = json.loads(x.call_tool("x_user_by_username", {"username": "https://x.com/ilTrumpista"}))
    assert out["success"] is False
    assert out["error_code"] == "x_username_invalid"
    assert calls == []


def test_http_request_refuses_live_calls_under_pytest(monkeypatch):
    _oauth_env(monkeypatch)
    with pytest.raises(RuntimeError, match="live X API"):
        x.http_request("GET", "https://api.x.com/2/users/me", headers={})
    _oauth_env(monkeypatch)
    with pytest.raises(RuntimeError, match="live X API"):
        x.http_request("GET", "https://api.x.com/2/users/me", headers={})


def test_mcp_dispatch_missing_creds_and_grocery_untouched(bf, monkeypatch):
    _clear_env(monkeypatch)
    user = bf.db.create_user("x@example.com", "secret1")
    out = json.loads(bf._call_tool(user, "x_me", {}))
    assert out["error_code"] == "x_credentials_missing"
    aliased = json.loads(bf._call_tool(user, "twitter_post", {"text": "hi"}))
    assert aliased["error_code"] == "x_credentials_missing"
    names = {t["name"] for t in bf.tools_catalog()}
    assert "grandiose_cart" in names
    assert "carrefour_search" in names
    assert "x_post" in names
    who = json.loads(bf._call_tool(user, "bf_whoami", {}))
    assert who["success"] is True
    assert who["email"] == "x@example.com"
    assert "linked_stores" in who


def test_mcp_dispatch_shapes_post(bf, monkeypatch):
    _oauth_env(monkeypatch)
    user = bf.db.create_user("y@example.com", "secret1")
    calls = []

    def fake(method, url, *, headers, params=None, json_body=None, timeout=20):
        calls.append({"method": method, "url": url, "json_body": json_body})
        return DummyResp(201, {"data": {"id": "1", "text": "posted"}})

    monkeypatch.setattr(bf.x, "http_request", fake)
    out = json.loads(bf._call_tool(user, "x_post", {"text": "posted"}))
    assert out["success"] is True
    assert out["write"] is True
    assert calls == [{"method": "POST", "url": "https://api.x.com/2/tweets", "json_body": {"text": "posted"}}]


def test_tools_mark_x_post_as_write():
    catalog = {t["name"]: t for t in x.tools()}
    assert set(catalog) == set(x.TOOL_NAMES)
    assert "WRITE" in catalog["x_post"]["description"]
    assert catalog["x_search"]["inputSchema"]["required"] == ["query"]
    assert "ilTrumpista" in catalog["x_user_posts"]["description"]
