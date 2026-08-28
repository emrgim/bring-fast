"""Regression tests for the connector handshake a client such as Grok performs.

Each test here maps to a failure that made the connector report
"Connection failed" or "this connector is unavailable at the moment".
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse

import pytest
from fastapi.testclient import TestClient

ACCEPT = "application/json, text/event-stream"
TUNNEL = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "bring-fast.example.com"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BRINGFAST_DATA", str(tmp_path))
    import importlib

    from bring_fast import app as app_module
    from bring_fast import db

    importlib.reload(db)
    importlib.reload(app_module)
    monkeypatch.setattr(app_module, "PUBLIC_URL", "", raising=False)
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture()
def token(client):
    """Complete the OAuth authorization-code + PKCE flow and return the access token."""
    redirect_uri = "https://grok.com/oauth/callback"
    reg = client.post(
        "/oauth/register",
        json={"client_name": "Grok", "redirect_uris": [redirect_uri],
              "token_endpoint_auth_method": "none"},
    )
    assert reg.status_code == 201
    client_id = reg.json()["client_id"]

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()

    client.post("/register", data={"email": "probe@example.com", "password": "probe-password"})
    authz = client.post(
        "/oauth/authorize",
        data={"email": "probe@example.com", "password": "probe-password",
              "redirect_uri": redirect_uri, "state": "xyz", "client_id": client_id,
              "code_challenge": challenge, "code_challenge_method": "S256"},
        follow_redirects=False,
    )
    assert authz.status_code == 302
    code = urllib.parse.parse_qs(
        urllib.parse.urlparse(authz.headers["location"]).query
    )["code"][0]

    tok = client.post(
        "/oauth/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
              "client_id": client_id, "code_verifier": verifier},
    )
    assert tok.status_code == 200
    return tok.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}", "Accept": ACCEPT,
            "MCP-Protocol-Version": "2025-06-18"}


def test_get_mcp_with_valid_token_is_not_an_auth_failure(client, token):
    """Grok opens this stream right after the token exchange; a 401 reads as a bad token."""
    r = client.get("/mcp", headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"})
    assert r.status_code == 405
    assert "POST" in r.headers["allow"]


def test_unauthenticated_requests_still_challenge(client):
    for call in (lambda: client.get("/mcp"),
                 lambda: client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})):
        r = call()
        assert r.status_code == 401
        assert "resource_metadata=" in r.headers["www-authenticate"]


def test_notifications_get_no_response_body(client, token):
    r = client.post("/mcp", headers=auth(token),
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202
    assert r.content == b""


@pytest.mark.parametrize("asked,expected",
                         [("2025-06-18", "2025-06-18"),
                          ("2025-03-26", "2025-03-26"),
                          ("2024-11-05", "2024-11-05"),
                          ("1999-01-01", "2025-06-18")])
def test_initialize_negotiates_the_requested_protocol_version(client, token, asked, expected):
    r = client.post("/mcp", headers=auth(token),
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": asked, "capabilities": {},
                                     "clientInfo": {"name": "Grok", "version": "1.0"}}})
    assert r.json()["result"]["protocolVersion"] == expected


def test_failing_tool_stays_json_rpc(client, token):
    """A tool blowing up must not surface as an unparseable HTTP 500."""
    r = client.post("/mcp", headers=auth(token),
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "grandiose_cart",
                                     "arguments": {"action": "add", "product_id": "x", "qty": "two"}}})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["isError"] is True
    assert result["content"][0]["type"] == "text"


def test_batch_and_malformed_bodies_stay_json_rpc(client, token):
    batch = client.post("/mcp", headers=auth(token),
                        json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                              {"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert batch.status_code == 200
    assert batch.json() == [{"jsonrpc": "2.0", "id": 1, "result": {}}]

    only_notifications = client.post("/mcp", headers=auth(token),
                                     json=[{"jsonrpc": "2.0", "method": "notifications/cancelled"}])
    assert only_notifications.status_code == 202

    assert client.post("/mcp", headers=auth(token), json="hello").json()["error"]["code"] == -32600
    assert client.post("/mcp", headers=auth(token), content=b"{").status_code == 400


def test_unknown_method_reports_method_not_found(client, token):
    r = client.post("/mcp", headers=auth(token),
                    json={"jsonrpc": "2.0", "id": 1, "method": "no/such/method"})
    assert r.json()["error"]["code"] == -32601


def test_tools_list_is_reachable_after_the_handshake(client, token):
    tools = client.post("/mcp", headers=auth(token),
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"bf_search", "bf_stores", "bf_compare", "bf_spend", "bf_products", "bf_shopping_list", "bf_product", "bf_orders", "grandiose_cart", "carrefour_search", "carrefour_cart", "carrefour_status"} <= names
    assert "carrefour_checkout" not in names
    assert "spinneys_checkout" not in names
    assert all(t["inputSchema"]["type"] == "object" for t in tools)


def test_discovery_advertises_the_public_host_not_localhost(client):
    """Behind a tunnel with no BRINGFAST_PUBLIC_URL, 127.0.0.1 is unreachable for the client."""
    prm = client.get("/.well-known/oauth-protected-resource/mcp", headers=TUNNEL).json()
    assert prm["resource"] == "https://bring-fast.example.com/mcp"
    assert prm["authorization_servers"] == ["https://bring-fast.example.com"]

    meta = client.get("/.well-known/oauth-authorization-server", headers=TUNNEL).json()
    assert meta["issuer"] == "https://bring-fast.example.com"
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        assert meta[key].startswith("https://bring-fast.example.com/")

    challenge = client.post("/mcp", headers=TUNNEL, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert "https://bring-fast.example.com/" in challenge.headers["www-authenticate"]


def test_public_request_host_wins_over_configured_url(client, monkeypatch):
    """A stale BRINGFAST_PUBLIC_URL must not advertise a different origin than Grok called."""
    from bring_fast import app as app_module

    monkeypatch.setattr(app_module, "PUBLIC_URL", "https://stale.example.com")
    meta = client.get("/.well-known/oauth-authorization-server", headers=TUNNEL).json()
    assert meta["issuer"] == "https://bring-fast.example.com"


def test_loopback_public_url_is_ignored_behind_a_tunnel(client, monkeypatch):
    from bring_fast import app as app_module

    monkeypatch.setattr(app_module, "PUBLIC_URL", "http://127.0.0.1:8877")
    meta = client.get("/.well-known/oauth-authorization-server", headers=TUNNEL).json()
    assert meta["issuer"] == "https://bring-fast.example.com"


def test_resources_and_prompts_ship_the_agent_skill(client, token):
    prompts = client.post("/mcp", headers=auth(token),
                          json={"jsonrpc": "2.0", "id": 1, "method": "prompts/list"}).json()["result"]["prompts"]
    assert prompts[0]["name"] == "bring-fast-agent"
    got = client.post("/mcp", headers=auth(token),
                      json={"jsonrpc": "2.0", "id": 2, "method": "prompts/get",
                            "params": {"name": "bring-fast-agent"}}).json()["result"]
    text = got["messages"][0]["content"]["text"]
    assert "bf_spend" in text
    assert "bf_shopping_list" in text
    resources = client.post("/mcp", headers=auth(token),
                            json={"jsonrpc": "2.0", "id": 3, "method": "resources/list"}).json()["result"]["resources"]
    assert resources[0]["uri"] == "bringfast://skill/agent"
    read = client.post("/mcp", headers=auth(token),
                       json={"jsonrpc": "2.0", "id": 4, "method": "resources/read",
                             "params": {"uri": "bringfast://skill/agent"}}).json()["result"]
    assert "Bring Fast" in read["contents"][0]["text"]


def test_initialize_loads_skill_and_mcp_description(client, token):
    r = client.post("/mcp", headers=auth(token),
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "Grok", "version": "1.0"}}})
    body = r.json()["result"]
    assert "per-user grocery MCP" in body["serverInfo"]["description"]
    assert "bf_whoami" in body["instructions"]
    assert "bf_spend" in body["instructions"]
    assert "not search-only" in body["instructions"].lower()
    assert "bf_cart retailer=carrefour" in body["instructions"]
    assert "carrefour_search" in body["instructions"]
    assert "query=2288448" in body["instructions"]
    assert "carrefour_checkout" in body["instructions"]
    assert body["capabilities"]["tools"] == {"listChanged": True}
    assert body["capabilities"]["prompts"] == {"listChanged": False}


def test_health_advertises_the_public_host(client):
    r = client.get("/health", headers=TUNNEL)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["public_url"] == "https://bring-fast.example.com"
    assert body["mcp_url"] == "https://bring-fast.example.com/mcp"


def test_trailing_slash_mcp_alias(client, token):
    r = client.post("/mcp/", headers=auth(token),
                    json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 200
    assert r.json()["result"] == {}


def test_event_stream_only_clients_get_sse(client, token):
    r = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream",
                 "MCP-Protocol-Version": "2025-06-18"},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: message" in r.text
    assert '"id": 1' in r.text


def test_register_serves_both_the_signup_form_and_client_registration(client):
    """The OAuth alias used to be shadowed by the signup form route and returned 422."""
    dcr = client.post("/register", json={"client_name": "Grok",
                                         "redirect_uris": ["https://grok.com/oauth/callback"]})
    assert dcr.status_code == 201
    assert dcr.json()["client_id"]

    form = client.post("/register", data={"email": "someone@example.com", "password": "secret123"},
                       follow_redirects=False)
    assert form.status_code == 303


def test_pkce_is_enforced(client):
    redirect_uri = "https://grok.com/oauth/callback"
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    client.post("/register", data={"email": "pkce@example.com", "password": "probe-password"})
    authz = client.post(
        "/oauth/authorize",
        data={"email": "pkce@example.com", "password": "probe-password",
              "redirect_uri": redirect_uri, "state": "s", "client_id": "c",
              "code_challenge": challenge, "code_challenge_method": "S256"},
        follow_redirects=False,
    )
    code = urllib.parse.parse_qs(
        urllib.parse.urlparse(authz.headers["location"]).query
    )["code"][0]
    bad = client.post("/oauth/token",
                      data={"grant_type": "authorization_code", "code": code,
                            "redirect_uri": redirect_uri, "code_verifier": "wrong-verifier"})
    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_grant"


def test_session_teardown_is_accepted(client, token):
    assert client.delete("/mcp", headers=auth(token)).status_code == 204
