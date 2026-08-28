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


def test_akamai_list_falls_back_to_browser_api(monkeypatch):
    from bring_fast.stores import carrefour as api

    http_calls = []

    def _http(**k):
        http_calls.append(k["action"])
        return {
            "ok": False,
            "error": api.AKAMAI_UNREAD,
            "error_code": "akamai_blocked",
            "items": [],
            "logged_in": True,
            "session_reused": True,
        }

    monkeypatch.setattr(api, "official_cart", _http)
    monkeypatch.setattr(
        checkout,
        "_carrefour_browser_api_cart",
        lambda **k: {
            "ok": True,
            "official_count": 0,
            "items": [],
            "logged_in": True,
            "driver": "playwright",
            "akamai_retry": "browser_api",
        },
    )
    monkeypatch.setattr(
        checkout,
        "_official_cart_sync",
        lambda **k: (_ for _ in ()).throw(AssertionError("click path must not run")),
    )
    out = checkout._carrefour_official_cart(
        email="e@mrg.im", password="x", action="list", items=[], session_token="t", session_user="u"
    )
    assert http_calls == ["list"]
    assert out["ok"] is True
    assert out["akamai_retry"] == "browser_api"
    assert out["logged_in"] is True


def test_akamai_add_does_not_run_http_add(monkeypatch):
    """HTTP add warms www.carrefouruae.com and hangs; probe list then browser-write."""
    from bring_fast.stores import carrefour as api

    http_calls = []

    def _http(**k):
        http_calls.append(k)
        return {
            "ok": False,
            "error": api.AKAMAI_UNREAD,
            "error_code": "akamai_blocked",
            "items": [],
            "logged_in": True,
            "session_reused": True,
        }

    monkeypatch.setattr(api, "official_cart", _http)
    monkeypatch.setattr(
        checkout,
        "_carrefour_browser_api_cart",
        lambda **k: {
            "ok": True,
            "official_count": 1,
            "items": [{"id": "743861", "name": "Coca-Cola Zero 330ml Can", "qty": 1}],
            "logged_in": True,
            "driver": "playwright",
            "akamai_retry": "browser_api",
        },
    )
    out = checkout._carrefour_official_cart(
        email="e@mrg.im",
        password="x",
        action="add",
        items=[{"id": "743861", "qty": 1, "url": "https://www.carrefouruae.com/mafuae/en/p/743861"}],
        session_token="t",
        session_user="u",
    )
    assert [c["action"] for c in http_calls] == ["list"]
    assert all(float(c.get("timeout") or 0) <= 4.0 for c in http_calls)
    assert out["ok"] is True
    assert out["akamai_retry"] == "browser_api"
    assert out["items"][0]["id"] == "743861"


def test_http_success_add_uses_http_write(monkeypatch):
    from bring_fast.stores import carrefour as api

    actions = []

    def _http(**k):
        actions.append(k["action"])
        if k["action"] == "list":
            return {"ok": True, "items": [], "official_count": 0, "logged_in": True, "token": "t", "user_id": "u"}
        return {
            "ok": True,
            "items": [{"id": "743861", "name": "Coca-Cola Zero 330ml Can", "qty": 1}],
            "official_count": 1,
            "logged_in": True,
        }

    monkeypatch.setattr(api, "official_cart", _http)
    monkeypatch.setattr(
        checkout,
        "_carrefour_browser_api_cart",
        lambda **k: (_ for _ in ()).throw(AssertionError("HTTP worked; do not open the browser")),
    )
    out = checkout._carrefour_official_cart(
        email="e@mrg.im",
        password="x",
        action="add",
        items=[{"id": "743861", "qty": 1}],
        session_token="t",
        session_user="u",
    )
    assert actions == ["list", "add"]
    assert out["ok"] is True
    assert out["items"][0]["id"] == "743861"


def test_http_timeout_on_list_falls_back_to_browser(monkeypatch):
    from bring_fast.stores import carrefour as api

    monkeypatch.setattr(
        api,
        "official_cart",
        lambda **k: {
            "ok": False,
            "error": "Live carrefour cart exceeded 38s. error_code=cart_timeout.",
            "error_code": "cart_timeout",
            "items": [],
            "logged_in": True,
        },
    )
    monkeypatch.setattr(
        checkout,
        "_carrefour_browser_api_cart",
        lambda **k: {
            "ok": True,
            "items": [{"id": "743861", "qty": 1}],
            "official_count": 1,
            "logged_in": True,
            "akamai_retry": "browser_api",
        },
    )
    out = checkout._carrefour_official_cart(
        email="e@mrg.im", password="x", action="add", items=[{"id": "743861", "qty": 1}]
    )
    assert out["ok"] is True
    assert out["items"][0]["id"] == "743861"


def test_carrefour_http_probe_leaves_time_for_browser(monkeypatch):
    captured = {}

    def fake_in_thread(fn, timeout=240, **kwargs):
        captured["timeout"] = timeout
        captured["http_timeout"] = kwargs.get("http_timeout")
        captured["action"] = kwargs.get("action")
        return {"ok": True}

    monkeypatch.setattr(checkout, "_in_thread", fake_in_thread)
    checkout.official_cart(
        store="carrefour",
        email="e@mrg.im",
        password="x",
        action="add",
        items=[{"id": "743861", "qty": 1}],
        timeout=38,
    )
    assert captured["timeout"] == 38
    assert captured["http_timeout"] <= 4.0
    assert captured["action"] == "add"


def test_browser_api_add_posts_entries_then_lists(monkeypatch):
    """In-page fetch must write 743861 then read liteCart — no product-page click."""
    from bring_fast.stores import carrefour as api

    fetches = []

    class _Ctx:
        pages = []

        def cookies(self):
            return [
                {"name": "token", "value": "tok", "domain": ".carrefouruae.com"},
                {"name": "userId", "value": "u9", "domain": ".carrefouruae.com"},
            ]

        def storage_state(self, **_k):
            return None

    class _Page:
        url = "https://www.carrefouruae.com/mafuae/en"

        def __init__(self, ctx):
            self.context = ctx

        def content(self):
            return (
                '{"posInfo":"food=073_Zone04,QCOMM=879_Zone01","polygonId":"DXB_DubProdCty_01",'
                '"emirateCode":"DUBAI"}'
            )

        def evaluate(self, _js, arg):
            fetches.append(arg)
            url = arg["url"]
            if arg["method"] == "POST":
                return {"status": 200, "body": {"data": {"ok": True}}, "akamai": False}
            if "liteCart" in url:
                return {
                    "status": 200,
                    "body": {
                        "data": {
                            "items": [
                                {"id": "743861", "name": "Coca-Cola Zero 330ml Can", "quantity": 1, "price": 1.99}
                            ]
                        }
                    },
                    "akamai": False,
                }
            return {"status": 404, "body": {}, "akamai": False}

        def close(self):
            pass

        def goto(self, *_a, **_k):
            return None

    ctx = _Ctx()
    page = _Page(ctx)
    monkeypatch.setattr(checkout, "_launch_carrefour_cart", lambda: (None, None, ctx, "headless"))
    monkeypatch.setattr(checkout, "_carrefour_origin_page", lambda _ctx: (page, False))
    monkeypatch.setattr(
        checkout,
        "_ensure_logged_in_page",
        lambda *_a, **_k: {"logged_in": True, "reused": True, "error": None, "token": "tok", "user_id": "u9"},
    )
    monkeypatch.setattr(checkout, "_dismiss", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "save_browser_cookies", lambda *_a, **_k: None)
    out = checkout._carrefour_browser_api_cart(
        email="e@mrg.im",
        password="x",
        action="add",
        items=[{"id": "743861", "qty": 1, "name": "Coca-Cola Zero 330ml Can"}],
    )
    assert out["ok"] is True
    assert out["akamai_retry"] == "browser_api"
    assert out["items"][0]["id"] == "743861"
    posts = [f for f in fetches if f["method"] == "POST"]
    gets = [f for f in fetches if f["method"] == "GET" and "liteCart" in f["url"]]
    assert posts and posts[0]["payload"]["productId"] == "743861"
    assert posts[0]["payload"]["quantity"] == 1
    assert gets, "must re-read official liteCart after add"
    assert not any("carrefouruae.com/mafuae/en/p/" in str(f.get("url")) for f in fetches)
