"""Replay the Grok connector handshake against a Bring Fast URL.

    python -m bring_fast.doctor https://your-host/mcp [--token MCP_TOKEN]

Every step Grok performs before it shows "Checking authentication…" is run in
order, so the first FAIL line is the reason the connector never finishes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import socket
import ssl
import sys
import time
from urllib.parse import urlparse, urlsplit

import requests

TIMEOUT = 15
OK, BAD, WARN = "PASS", "FAIL", "WARN"
_failures: list[str] = []
_warnings: list[str] = []


def _say(status: str, title: str, detail: str = "") -> None:
    print(f"[{status}] {title}" + (f"\n       {detail}" if detail else ""))
    if status == BAD:
        _failures.append(title)
    elif status == WARN:
        _warnings.append(title)


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _is_local(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in ("127.0.0.1", "localhost", "0.0.0.0", "::1")


def check_reachable(base: str) -> bool:
    p = urlsplit(base)
    host = p.hostname or ""
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        _say(BAD, f"DNS for {host}", f"{e}. The name does not resolve publicly.")
        return False
    addrs = sorted({i[4][0] for i in infos})
    _say(OK, f"DNS for {host}", ", ".join(addrs))

    for family, _t, _p, _c, sockaddr in infos:
        s = socket.socket(family, socket.SOCK_STREAM)
        s.settimeout(TIMEOUT)
        started = time.time()
        try:
            s.connect(sockaddr)
            _say(OK, f"TCP {sockaddr[0]}:{port}", f"connected in {time.time() - started:.2f}s")
            return True
        except OSError as e:
            _say(WARN, f"TCP {sockaddr[0]}:{port}", str(e))
        finally:
            s.close()
    _say(
        BAD,
        f"TCP connect to {host}:{port}",
        "No address accepted a connection. Grok cannot reach this URL from the internet.\n"
        "       Behind Tailscale: run `tailscale funnel status` and make sure Funnel (not just Serve)\n"
        "       is enabled on 443 for this machine, and that the machine is online.",
    )
    return False


def check_tls(base: str) -> bool:
    """A TLS handshake that hangs after a successful TCP connect is the classic
    signature of a Tailscale Funnel whose backend node is not serving."""
    p = urlsplit(base)
    if p.scheme != "https":
        return True
    host = p.hostname or ""
    port = p.port or 443
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                _say(OK, "TLS handshake", f"{tls.version()} for {host}")
                return True
    except (socket.timeout, TimeoutError):
        _say(
            BAD,
            "TLS handshake times out",
            f"{host}:{port} accepts the TCP connection but never completes TLS.\n"
            "       The proxy in front of Bring Fast is answering while nothing is serving behind it.\n"
            "       With Tailscale, check on the host machine:\n"
            "         tailscale status          # is this machine online?\n"
            "         tailscale funnel status   # is 443 funnelled to the Bring Fast port?\n"
            "         curl -sS http://127.0.0.1:8877/health   # is Bring Fast itself running?\n"
            "       Re-enable with: tailscale funnel --bg 8877  (--bg keeps it alive after the shell exits)",
        )
        return False
    except ssl.SSLError as e:
        _say(BAD, "TLS handshake failed", f"{e}")
        return False
    except OSError as e:
        _say(BAD, "TLS handshake failed", f"{e}")
        return False


def check_health(base: str) -> None:
    try:
        r = requests.get(f"{base}/health", timeout=TIMEOUT)
    except requests.RequestException as e:
        _say(BAD, "GET /health", str(e))
        return
    if r.status_code != 200:
        _say(BAD, "GET /health", f"HTTP {r.status_code}: {r.text[:200]}")
        return
    body = r.json() if "json" in (r.headers.get("content-type") or "") else {}
    _say(OK, "GET /health", json.dumps(body))
    advertised = body.get("public_url")
    if advertised and _origin(advertised) != base:
        _say(
            WARN,
            "Server advertises a different origin",
            f"health says {advertised!r} but you called {base!r}. "
            "Unset BRINGFAST_PUBLIC_URL or set it to the public URL.",
        )


def check_challenge(mcp: str) -> str | None:
    try:
        r = requests.post(
            mcp,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        _say(BAD, "POST /mcp (unauthenticated)", str(e))
        return None
    if r.status_code != 401:
        _say(BAD, "POST /mcp (unauthenticated)", f"expected HTTP 401, got {r.status_code}")
        return None
    header = r.headers.get("WWW-Authenticate") or ""
    if "resource_metadata=" not in header:
        _say(BAD, "WWW-Authenticate header", f"missing resource_metadata: {header!r}")
        return None
    meta = header.split("resource_metadata=", 1)[1].split(",")[0].strip().strip('"')
    _say(OK, "POST /mcp returns the OAuth challenge", meta)
    return meta


def _fetch_json(url: str, label: str) -> dict | None:
    try:
        r = requests.get(url, timeout=TIMEOUT)
    except requests.RequestException as e:
        _say(BAD, label, f"{url}: {e}")
        return None
    if r.status_code != 200:
        _say(BAD, label, f"{url}: HTTP {r.status_code}")
        return None
    try:
        data = r.json()
    except ValueError:
        _say(BAD, label, f"{url}: response is not JSON")
        return None
    _say(OK, label, url)
    return data


def check_metadata(base: str, mcp: str, prm_url: str | None) -> dict | None:
    # When the connector URL is itself local we are testing on the host, so a
    # local endpoint is expected rather than a defect.
    local_target = _is_local(base)
    unreachable = WARN if local_target else BAD

    prm = _fetch_json(prm_url or f"{base}/.well-known/oauth-protected-resource", "Protected resource metadata")
    if prm:
        if prm.get("resource") and _origin(str(prm["resource"])) != base:
            _say(WARN, "PRM resource origin", f"{prm.get('resource')} does not match {mcp}")
        for issuer in prm.get("authorization_servers") or []:
            if _is_local(str(issuer)) and not local_target:
                _say(BAD, "PRM points at localhost", f"authorization_servers = {issuer}. Grok cannot reach it.")

    meta = _fetch_json(f"{base}/.well-known/oauth-authorization-server", "Authorization server metadata")
    if not meta:
        return None
    if _origin(str(meta.get("issuer") or "")) != base:
        _say(
            BAD,
            "Issuer mismatch",
            f"issuer={meta.get('issuer')!r} but the connector URL is {base!r}. "
            "OAuth clients abort when these differ.",
        )
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        value = str(meta.get(key) or "")
        if not value:
            _say(BAD, f"Missing {key}", "required for Grok's dynamic client registration flow")
        elif _is_local(value):
            _say(unreachable, f"{key} is a localhost URL", f"{value} is unreachable from Grok's servers.")
    return meta


def check_registration(meta: dict) -> dict | None:
    endpoint = str(meta.get("registration_endpoint") or "")
    if not endpoint:
        return None
    redirect_uri = "https://grok.com/connectors/oauth/callback"
    try:
        r = requests.post(
            endpoint,
            json={
                "client_name": "Bring Fast doctor",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        _say(BAD, "Dynamic client registration", str(e))
        return None
    if r.status_code not in (200, 201):
        _say(BAD, "Dynamic client registration", f"HTTP {r.status_code}: {r.text[:300]}")
        return None
    data = r.json()
    if not data.get("client_id"):
        _say(BAD, "Dynamic client registration", "response has no client_id")
        return None
    _say(OK, "Dynamic client registration", f"client_id={data['client_id']}")
    return {"client_id": data["client_id"], "redirect_uri": redirect_uri}


def check_authorize(meta: dict, client: dict) -> None:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": client["redirect_uri"],
        "state": "doctor",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": "mcp",
    }
    try:
        r = requests.get(
            str(meta["authorization_endpoint"]), params=params, timeout=TIMEOUT, allow_redirects=False
        )
    except requests.RequestException as e:
        _say(BAD, "GET authorization_endpoint", str(e))
        return
    if r.status_code in (200, 302, 303):
        _say(OK, "Authorization endpoint responds", f"HTTP {r.status_code}")
    else:
        _say(BAD, "GET authorization_endpoint", f"HTTP {r.status_code}: {r.text[:200]}")


def check_authenticated(mcp: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    try:
        r = requests.post(
            mcp,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "doctor", "version": "1"}},
            },
            headers=headers,
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        _say(BAD, "initialize with token", str(e))
        return
    if r.status_code != 200:
        _say(BAD, "initialize with token", f"HTTP {r.status_code}: {r.text[:200]}")
        return
    _say(OK, "initialize with token", r.text[:200])

    try:
        g = requests.get(mcp, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        _say(WARN, "GET /mcp with token", str(e))
    else:
        if g.status_code == 401:
            _say(BAD, "GET /mcp with a valid token returns 401", "this makes clients restart the OAuth flow forever")
        else:
            _say(OK, "GET /mcp with token", f"HTTP {g.status_code}")

    try:
        t = requests.post(
            mcp, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=headers, timeout=TIMEOUT
        )
        tools = (t.json().get("result") or {}).get("tools") or []
        _say(OK, "tools/list", f"{len(tools)} tools")
    except (requests.RequestException, ValueError) as e:
        _say(BAD, "tools/list", str(e))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose a Bring Fast MCP connector URL")
    parser.add_argument("url", help="the connector URL you paste into Grok, e.g. https://host/mcp")
    parser.add_argument("--token", default="", help="an MCP token, to also test the authenticated calls")
    args = parser.parse_args(argv)

    parsed = urlparse(args.url)
    if parsed.scheme not in ("http", "https"):
        print("URL must start with http:// or https://")
        return 2
    base = _origin(args.url)
    mcp = args.url.rstrip("/") if parsed.path.rstrip("/") else f"{base}/mcp"
    print(f"Checking {mcp}\n")
    if parsed.scheme != "https":
        _say(WARN, "Connector URL is not HTTPS", "Grok only accepts https:// connector URLs.")

    if not check_reachable(base):
        return _verdict()
    if not check_tls(base):
        return _verdict()
    check_health(base)
    prm_url = check_challenge(mcp)
    meta = check_metadata(base, mcp, prm_url)
    if meta:
        client = check_registration(meta)
        if client:
            check_authorize(meta, client)
    if args.token:
        check_authenticated(mcp, args.token)
    return _verdict()


def _verdict() -> int:
    print()
    if _failures:
        print(f"{len(_failures)} blocking problem(s): " + "; ".join(_failures))
        return 1
    if _warnings:
        print(f"No blocking problem. {len(_warnings)} warning(s): " + "; ".join(_warnings))
        return 0
    print("All checks passed. Grok should be able to connect to this URL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
