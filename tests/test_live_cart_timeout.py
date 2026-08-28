"""Live cart/status must not hold the MCP request until Chrome finishes."""

import time

import pytest

from bring_fast import checkout


def test_in_thread_raises_before_a_slow_worker_finishes():
    def slow():
        time.sleep(8)
        return "done"

    started = time.monotonic()
    with pytest.raises(checkout.LiveCartTimeout, match="exceeded 0s"):
        checkout._in_thread(slow, timeout=0.4)
    elapsed = time.monotonic() - started
    assert elapsed < 2.0


def test_snapshot_cdp_cookies_empty_when_chrome_is_down(monkeypatch):
    import urllib.request

    def boom(*_a, **_k):
        raise OSError("down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert checkout.snapshot_cdp_cookies() == []


def test_akamai_list_does_not_open_the_site_driver(monkeypatch):
    from bring_fast.stores import carrefour as api

    monkeypatch.setattr(
        api,
        "official_cart",
        lambda **k: {
            "ok": False,
            "error": api.AKAMAI_UNREAD,
            "error_code": "akamai_blocked",
            "items": [],
            "logged_in": True,
            "session_reused": True,
        },
    )
    monkeypatch.setattr(api, "refresh_sensor_cookies", lambda: False)
    called = []
    monkeypatch.setattr(checkout, "_official_cart_sync", lambda **k: called.append(k) or {"ok": True})
    out = checkout._carrefour_official_cart(
        email="e@mrg.im", password="x", action="list", items=[], session_token="t", session_user="u"
    )
    assert called == []
    assert out["error_code"] == "akamai_blocked"
    assert out["logged_in"] is True


def test_akamai_add_falls_back_to_site_driver(monkeypatch):
    from bring_fast.stores import carrefour as api

    monkeypatch.setattr(
        api,
        "official_cart",
        lambda **k: {
            "ok": False,
            "error": api.AKAMAI_UNREAD,
            "error_code": "akamai_blocked",
            "items": [],
            "logged_in": True,
            "session_reused": True,
        },
    )
    monkeypatch.setattr(api, "refresh_sensor_cookies", lambda: False)
    monkeypatch.setattr(
        checkout,
        "_official_cart_sync",
        lambda **k: {
            "ok": True,
            "official_count": 1,
            "items": [{"id": "2288448", "name": "Coke Zero", "qty": 1}],
            "logged_in": True,
        },
    )
    out = checkout._carrefour_official_cart(
        email="e@mrg.im",
        password="x",
        action="add",
        items=[{"id": "2288448", "qty": 1, "url": "https://www.carrefouruae.com/mafuae/en/p/2288448"}],
        session_token="t",
        session_user="u",
    )
    assert out["ok"] is True
    assert out["akamai_retry"] == "playwright"
    assert out["items"][0]["id"] == "2288448"


def test_sensor_cookies_retry_skips_playwright(monkeypatch):
    from bring_fast.stores import carrefour as api

    n = {"http": 0}

    def _http(**_k):
        n["http"] += 1
        if n["http"] == 1:
            return {
                "ok": False,
                "error_code": "akamai_blocked",
                "error": "Akamai",
                "items": [],
                "logged_in": True,
            }
        return {"ok": True, "items": [{"id": "1"}], "official_count": 1, "logged_in": True}

    monkeypatch.setattr(api, "official_cart", _http)
    monkeypatch.setattr(api, "refresh_sensor_cookies", lambda: True)
    monkeypatch.setattr(
        checkout,
        "_official_cart_sync",
        lambda **k: (_ for _ in ()).throw(AssertionError("must not open Playwright")),
    )
    out = checkout._carrefour_official_cart(email="a", password="b", action="list", items=[])
    assert out["ok"] is True
    assert out["akamai_retry"] == "sensor_cookies"
