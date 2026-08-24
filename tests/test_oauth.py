"""The Grok connector flow: discover, register, authorize, exchange, call."""

import base64
import hashlib

from fastapi.testclient import TestClient

VERIFIER = "bring-fast-code-verifier-0123456789abcdef"
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


def register_client(client, redirect_uri="https://grok.com/oauth/callback"):
    r = client.post("/oauth/register", json={"redirect_uris": [redirect_uri], "client_name": "Grok"})
    assert r.status_code == 201
    return r.json()["client_id"]


def authorize_params(client_id, redirect_uri="https://grok.com/oauth/callback", state="st4te"):
    return {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "response_type": "code",
    }


def test_metadata_advertises_the_endpoints_and_refresh(client):
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["registration_endpoint"].endswith("/oauth/register")
    assert "refresh_token" in meta["grant_types_supported"]
    prm = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"].endswith("/mcp")
    plex = client.get("/.well-known/oauth-protected-resource/plex/mcp").json()
    assert plex["resource"].endswith("/plex/mcp")
    assert plex["authorization_servers"]


def test_mcp_without_a_token_points_at_the_authorization_server(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]


def test_dynamic_registration_works_on_both_paths(client):
    for path in ("/oauth/register", "/register"):
        r = client.post(path, json={"redirect_uris": ["https://grok.com/oauth/callback"]})
        assert r.status_code == 201, path
        assert r.json()["client_id"].startswith("fb_")


def test_new_user_completes_the_whole_flow_from_the_authorize_page(bf, client):
    client_id = register_client(client)
    page = client.get("/oauth/authorize", params=authorize_params(client_id))
    assert page.status_code == 200
    assert "Create account and authorize" in page.text

    r = client.post(
        "/oauth/authorize",
        data={
            "email": "friend@example.com",
            "password": "secret1",
            **authorize_params(client_id),
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith("https://grok.com/oauth/callback?")
    assert "state=st4te" in location
    code = location.split("code=")[1].split("&")[0]

    token = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://grok.com/oauth/callback",
            "code_verifier": VERIFIER,
        },
    ).json()
    assert token["token_type"] == "bearer"

    call = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "bf_whoami", "arguments": {}}},
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    assert "friend@example.com" in call.json()["result"]["content"][0]["text"]


def test_already_signed_in_user_is_not_asked_again(client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1"})
    client_id = register_client(client)
    r = client.get("/oauth/authorize", params=authorize_params(client_id), follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://grok.com/oauth/callback?code=")


def test_wrong_password_keeps_the_authorize_context(client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1"})
    client.get("/logout")
    client_id = register_client(client)
    r = client.post(
        "/oauth/authorize",
        data={"email": "friend@example.com", "password": "nope", **authorize_params(client_id)},
        follow_redirects=False,
    )
    assert r.status_code == 401
    assert CHALLENGE in r.text
    assert "st4te" in r.text


def test_code_is_never_sent_to_an_unregistered_redirect(client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1"})
    client_id = register_client(client)
    r = client.get(
        "/oauth/authorize",
        params=authorize_params(client_id, redirect_uri="https://evil.example/callback"),
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "not registered" in r.text


def test_extra_query_parameters_on_a_registered_redirect_are_allowed(client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1"})
    client_id = register_client(client)
    r = client.get(
        "/oauth/authorize",
        params=authorize_params(client_id, redirect_uri="https://grok.com/oauth/callback?tenant=1"),
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://grok.com/oauth/callback?tenant=1&code=")


def test_missing_redirect_uri_explains_itself(client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1"})
    r = client.get("/oauth/authorize", params={"client_id": "fast-bring"}, follow_redirects=False)
    assert r.status_code == 400
    assert "redirect_uri" in r.text


def test_state_is_omitted_when_the_client_sent_none(client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1"})
    client_id = register_client(client)
    params = authorize_params(client_id, state="")
    r = client.get("/oauth/authorize", params=params, follow_redirects=False)
    assert "state=" not in r.headers["location"]


def test_pkce_is_enforced(client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1"})
    client_id = register_client(client)
    r = client.get("/oauth/authorize", params=authorize_params(client_id), follow_redirects=False)
    code = r.headers["location"].split("code=")[1].split("&")[0]
    bad = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://grok.com/oauth/callback",
            "code_verifier": "not-the-verifier",
        },
    )
    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_grant"


def test_refresh_keeps_the_connector_signed_in(client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1"})
    client_id = register_client(client)
    r = client.get("/oauth/authorize", params=authorize_params(client_id), follow_redirects=False)
    code = r.headers["location"].split("code=")[1].split("&")[0]
    token = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://grok.com/oauth/callback",
            "code_verifier": VERIFIER,
        },
    ).json()
    refreshed = client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": token["refresh_token"]},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] == token["access_token"]

    assert client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": "junk"}).status_code == 400


def test_one_token_never_reaches_another_account(bf, client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1"})
    other = TestClient(bf.app)
    other.post("/login", data={"email": "someone@example.com", "password": "secret1"})
    token = bf.db.get_user_by_email("someone@example.com")["mcp_token"]
    call = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "bf_whoami", "arguments": {}}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert "someone@example.com" in call.json()["result"]["content"][0]["text"]
