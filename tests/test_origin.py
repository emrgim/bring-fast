"""Origin advertised to OAuth clients must be the host they actually called."""

from __future__ import annotations

from bring_fast.app import _is_loopback, _issuer


def test_loopback_detection_handles_ports_and_schemes():
    assert _is_loopback("http://127.0.0.1:8877")
    assert _is_loopback("https://localhost")
    assert _is_loopback("127.0.0.1:8877")
    assert not _is_loopback("https://domvs.tail38383a.ts.net")
    assert not _is_loopback("https://bring-fast.example.com")


def test_issuer_without_request_uses_loopback_fallback(monkeypatch):
    import bring_fast.app as app_module

    monkeypatch.setattr(app_module, "PUBLIC_URL", "")
    assert _issuer(None).startswith("http://127.0.0.1:")
