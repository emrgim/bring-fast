"""HTTP client helpers. Browser TLS impersonation, never a real Chrome window."""

from __future__ import annotations

from typing import Any


class StoreAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: Any = None,
        error_code: str | None = None,
        maf_error: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.body = body
        self.error_code = error_code
        self.maf_error = maf_error


def session():
    try:
        from curl_cffi import requests as cf
        from curl_cffi.const import CurlHttpVersion
    except ImportError as e:
        raise StoreAPIError(
            "curl_cffi is required for store APIs. pip install curl_cffi"
        ) from e
    # One client: Chrome TLS + Chrome UA. HTTP/1.1 (HTTP/2 to RetailSSO resets).
    # chrome131/124 may exist but still get Akamai's empty HTML shell; generic
    # "chrome" is the fallback that actually receives _abck / bm_sz.
    last = None
    for name in ("chrome131", "chrome124", "chrome"):
        try:
            return cf.Session(impersonate=name, http_version=CurlHttpVersion.V1_1)
        except Exception as e:
            last = e
            continue
    raise StoreAPIError(f"curl_cffi Chrome impersonate failed: {last}")


def is_akamai_shell(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.startswith("<!doctype html>") and "<p></p>" in t and len(t) < 120


def json_or_error(resp, what: str) -> Any:
    text = resp.text or ""
    if is_akamai_shell(text):
        raise StoreAPIError(
            f"{what}: Akamai blocked the HTTP API (empty HTML).",
            status=resp.status_code,
            error_code="akamai_blocked",
        )
    if "access denied" in text.lower() and resp.status_code in (403, 200):
        raise StoreAPIError(
            f"{what}: Akamai access denied.",
            status=resp.status_code,
            error_code="akamai_blocked",
        )
    low = text.lower()
    if "error 54113" in low or ("varnish" in low and "not allowed" in low):
        raise StoreAPIError(
            f"{what}: Varnish blocked the HTTP API (Fastly 54113).",
            status=resp.status_code,
            error_code="varnish_blocked",
        )
    try:
        return resp.json()
    except Exception as e:
        stripped = text.strip()
        # Magento REST DELETE / PUT often returns HTTP 200 with `true` or an empty body.
        if resp.status_code in (200, 201, 204) and not stripped:
            return True
        if resp.status_code in (200, 201, 204) and stripped.lower() in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[stripped.lower()]
        raise StoreAPIError(f"{what}: non-JSON HTTP {resp.status_code}: {text[:160]}", status=resp.status_code) from e
