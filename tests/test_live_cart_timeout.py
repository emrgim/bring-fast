"""Live cart/status must not hold the MCP request until Chrome finishes."""

import json
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


def test_browser_api_add_posts_additem_then_lists(monkeypatch):
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
    assert "/addItem" in posts[0]["url"]
    assert "/entries" not in posts[0]["url"]
    assert gets, "must re-read official liteCart after add"
    gh = gets[0].get("headers") or {}
    assert gh.get("userId") == "u9"
    assert (gh.get("appId") or gh.get("appid")) != "Android"
    assert gh.get("appId") == "Reactweb"
    assert not any("carrefouruae.com/mafuae/en/p/" in str(f.get("url")) for f in fetches)


def test_browser_api_add_500_is_ok_when_sku_already_in_cart(monkeypatch):
    from bring_fast.stores import carrefour as api

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
            return '{"posInfo":"food=073_Zone04","polygonId":"DXB_DubProdCty_01","emirateCode":"DUBAI"}'

        def evaluate(self, _js, arg):
            if arg["method"] == "POST":
                return {
                    "status": 500,
                    "body": {"error": {"message": "Internal Server Error"}},
                    "akamai": False,
                }
            if "liteCart" in arg["url"]:
                return {
                    "status": 200,
                    "body": {"data": {"items": [{"id": "743861", "quantity": 1}]}},
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
    monkeypatch.setattr(
        api,
        "_cio_browse_ids",
        lambda ids: [{"value": "Coca-Cola Zero 330ml Can", "data": {"id": "743861", "price": 1.99}}]
        if "743861" in ids
        else [],
    )
    monkeypatch.setattr(
        api,
        "_cio_search",
        lambda q: [{"value": "Coca-Cola Zero 330ml Can", "data": {"id": "743861", "price": 1.99}}],
    )
    out = checkout._carrefour_browser_api_cart(
        email="e@mrg.im",
        password="x",
        action="add",
        items=[{"id": "743861", "qty": 1}],
    )
    assert out["ok"] is True
    assert out["items"][0]["id"] == "743861"
    assert "Coca-Cola" in (out["items"][0].get("name") or "")


def _browser_harness(monkeypatch, page_cls):
    from bring_fast.stores import carrefour as api

    class _Ctx:
        pages = []

        def cookies(self):
            return [
                {"name": "token", "value": "tok", "domain": ".carrefouruae.com"},
                {"name": "userId", "value": "u9", "domain": ".carrefouruae.com"},
            ]

        def storage_state(self, **_k):
            return None

    ctx = _Ctx()
    page = page_cls(ctx)
    monkeypatch.setattr(checkout, "_launch_carrefour_cart", lambda: (None, None, ctx, "headless"))
    monkeypatch.setattr(checkout, "_carrefour_origin_page", lambda _ctx: (page, False))
    monkeypatch.setattr(
        checkout,
        "_ensure_logged_in_page",
        lambda *_a, **_k: {"logged_in": True, "reused": True, "error": None, "token": "tok", "user_id": "u9"},
    )
    monkeypatch.setattr(checkout, "_dismiss", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "save_browser_cookies", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "_cio_browse_ids", lambda ids: [])
    monkeypatch.setattr(api, "_cio_search", lambda q: [])
    return page


def test_browser_api_add_1592968_qty_2(monkeypatch):
    fetches = []

    class _Page:
        url = "https://www.carrefouruae.com/mafuae/en"

        def __init__(self, ctx):
            self.context = ctx

        def content(self):
            return '{"posInfo":"food=073_Zone04","polygonId":"DXB_DubProdCty_01","emirateCode":"DUBAI"}'

        def evaluate(self, _js, arg):
            fetches.append(arg)
            if arg["method"] == "POST":
                return {"status": 200, "body": {"data": {"ok": True}}, "akamai": False}
            if "liteCart" in arg["url"]:
                return {
                    "status": 200,
                    "body": {
                        "data": {
                            "items": [
                                {
                                    "id": "1592968",
                                    "name": "Oasis Blu Sparkling Water, 1L Pack of 6",
                                    "quantity": 2,
                                    "price": 26.99,
                                }
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

    _browser_harness(monkeypatch, _Page)
    out = checkout._carrefour_browser_api_cart(
        email="e@mrg.im",
        password="x",
        action="add",
        items=[
            {"id": "1592968", "qty": 2, "name": "Oasis Blu Sparkling Water, 1L Pack of 6"}
        ],
    )
    assert out["ok"] is True
    assert out["items"][0]["id"] == "1592968"
    assert out["items"][0]["qty"] == 2
    posts = [f for f in fetches if f["method"] == "POST"]
    assert posts[0]["payload"]["productId"] == "1592968"
    assert posts[0]["payload"]["quantity"] == 2
    assert "/addItem" in posts[0]["url"]
    assert not any("/entries" in f["url"] and f["method"] == "POST" for f in fetches)


def test_browser_api_add_500_restores_snapshot_and_skips_entries(monkeypatch):
    rows = [
        {"id": "376161", "name": "Coca-Cola Original", "quantity": 1, "price": 14.99},
        {"id": "743861", "name": "Coca-Cola Zero", "quantity": 1, "price": 1.99},
    ]
    fetches = []

    class _Page:
        url = "https://www.carrefouruae.com/mafuae/en"

        def __init__(self, ctx):
            self.context = ctx

        def content(self):
            return '{"posInfo":"food=073_Zone04","polygonId":"DXB_DubProdCty_01","emirateCode":"DUBAI"}'

        def evaluate(self, _js, arg):
            fetches.append(arg)
            if arg["method"] == "POST":
                assert "/entries" not in arg["url"]
                pid = str((arg.get("payload") or {}).get("productId") or "")
                if pid == "1592968":
                    rows.clear()
                    return {
                        "status": 500,
                        "body": {"error": {"message": "Internal Server Error"}},
                        "akamai": False,
                    }
                rows.append({"id": pid, "quantity": int((arg.get("payload") or {}).get("quantity") or 1)})
                return {"status": 200, "body": {"data": {"ok": True}}, "akamai": False}
            if "liteCart" in arg["url"]:
                return {"status": 200, "body": {"data": {"items": list(rows)}}, "akamai": False}
            return {"status": 404, "body": {}, "akamai": False}

        def close(self):
            pass

        def goto(self, *_a, **_k):
            return None

    _browser_harness(monkeypatch, _Page)
    out = checkout._carrefour_browser_api_cart(
        email="e@mrg.im",
        password="x",
        action="add",
        items=[{"id": "1592968", "qty": 2, "name": "Oasis Blu Sparkling Water, 1L Pack of 6"}],
    )
    assert out["ok"] is False
    assert out["maf_error"] == "Internal Server Error"
    ids = {it["id"] for it in out["items"]}
    assert ids == {"376161", "743861"}
    assert "1592968" not in ids
    posts = [f for f in fetches if f["method"] == "POST"]
    assert any(f["payload"]["productId"] == "1592968" for f in posts)
    assert any(f["payload"]["productId"] == "376161" for f in posts)
    assert not any("/entries" in f["url"] and f["method"] == "POST" for f in fetches)


def test_http_unusable_treats_litecart_400_as_browser_fallback():
    assert checkout._http_unusable({"ok": False, "error": "liteCart HTTP 400"}) is True
    assert checkout._http_unusable({"ok": False, "error": "liteCart HTTP 400", "error_code": None}) is True
    assert checkout._http_unusable({"ok": False, "error": "liteCart needs auth token and userId."}) is True
    assert checkout._http_unusable({"ok": False, "error_code": "akamai_blocked", "error": "Akamai"}) is True
    assert checkout._http_unusable({"ok": False, "error_code": "needs_delivery_slot", "error": "slot"}) is False
    assert checkout._http_unusable({"ok": True, "error": "liteCart HTTP 400"}) is False


def test_browser_get_headers_match_maf_website_xhr():
    h = checkout._browser_headers("tok", "u9", {"pos_info": "food=073_Zone04"}, method="GET")
    assert h.get("userId") == "u9"
    assert (h.get("appId") or h.get("appid")) != "Android"
    assert h.get("appId") == "Reactweb"
    assert h.get("channel") == "c4online"
    assert h["Content-Type"].startswith("application/json")
    assert h["Authorization"].startswith("Bearer ")
    assert h["token"].startswith("Bearer ")
    post = checkout._browser_headers("tok", "u9", {"pos_info": "food=073_Zone04"}, method="POST")
    assert post.get("Content-Type", "").startswith("application/json")
    assert post.get("userId") == "u9"
    anon = checkout._browser_headers("tok", "", {}, method="GET")
    assert anon.get("userId") == "anonymous"


def test_http_litecart_400_with_saved_login_falls_back_to_browser(monkeypatch):
    """Saved login + liteCart HTTP 400 must not be a dead end — try the site browser."""
    from bring_fast.stores import carrefour as api

    http_calls = []

    def _http(**k):
        http_calls.append(k["action"])
        return {
            "ok": False,
            "error": "liteCart HTTP 400",
            "error_code": None,
            "items": [],
            "logged_in": True,
            "session_reused": True,
            "driver": "chrome",
        }

    monkeypatch.setattr(api, "official_cart", _http)
    monkeypatch.setattr(
        checkout,
        "_carrefour_browser_api_cart",
        lambda **k: {
            "ok": True,
            "official_count": 1,
            "items": [
                {
                    "id": "743861",
                    "name": "Coca-Cola Zero 330ml Can",
                    "qty": 1,
                    "price": 1.99,
                }
            ],
            "logged_in": True,
            "driver": "cdp",
            "session_reused": True,
            "akamai_retry": "browser_api",
        },
    )
    out = checkout._carrefour_official_cart(
        email="e@mrg.im",
        password="x",
        action="list",
        items=[],
        session_token="t",
        session_user="u",
    )
    assert http_calls == ["list"]
    assert out["ok"] is True
    assert out["items"][0]["id"] == "743861"
    assert "Coca-Cola" in (out["items"][0].get("name") or "")
    assert out["items"][0]["price"] == 1.99
    assert out["driver"] == "cdp"


def test_akamai_blocked_list_with_saved_login_falls_back_to_browser(monkeypatch):
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
            "session_reused": False,
            "driver": "chrome",
        },
    )
    monkeypatch.setattr(
        checkout,
        "_carrefour_browser_api_cart",
        lambda **k: {
            "ok": True,
            "official_count": 1,
            "items": [{"id": "743861", "name": "Coca-Cola Zero 330ml Can", "qty": 1, "price": 1.99}],
            "logged_in": True,
            "driver": "cdp",
            "session_reused": True,
            "akamai_retry": "browser_api",
        },
    )
    out = checkout._carrefour_official_cart(
        email="e@mrg.im", password="x", action="list", items=[], session_token="t", session_user="u"
    )
    assert out["ok"] is True
    assert out["items"][0]["name"]
    assert out["akamai_retry"] == "browser_api"


def test_browser_list_retries_missing_userid_after_harvest():
    """A 400 that says userId is missing is retried only after harvest fills the header."""
    fetches = []
    harvests = {"n": 0}

    class _Ctx:
        def cookies(self):
            return [{"name": "token", "value": "tok"}]

    class _Page:
        url = "https://www.carrefouruae.com/mafuae/en"

        def __init__(self):
            self.context = _Ctx()

        def content(self):
            return ""

        def evaluate(self, _js, arg=None):
            if arg is None:
                harvests["n"] += 1
                if harvests["n"] == 1:
                    return {"localStorage": {}, "sessionStorage": {}, "cookies": {}}
                return {
                    "localStorage": {"maf.auth": '{"userId":"u9"}'},
                    "sessionStorage": {},
                    "cookies": {},
                }
            fetches.append(arg)
            uid = (arg.get("headers") or {}).get("userId")
            if not uid or uid == "anonymous":
                return {
                    "status": 400,
                    "body": {"error": {"message": "header userId mancante"}},
                    "akamai": False,
                }
            return {
                "status": 200,
                "body": {
                    "data": {
                        "items": [
                            {
                                "id": "743861",
                                "name": "Coca-Cola Zero 330ml Can",
                                "quantity": 1,
                                "price": 1.99,
                            }
                        ]
                    }
                },
                "akamai": False,
            }

    listed = checkout._lite_cart_via_page(_Page(), "tok", "", {}, email="", session_user="")
    assert listed["status"] == 200
    gets = [f for f in fetches if f.get("method") == "GET" and "liteCart" in (f.get("url") or "")]
    assert len(gets) == 2
    assert all((g.get("headers") or {}).get("userId") for g in gets)
    assert (gets[0].get("headers") or {}).get("userId") == "anonymous"
    assert (gets[1].get("headers") or {}).get("userId") == "u9"
    assert listed["body"]["data"]["items"][0]["id"] == "743861"


def test_litecart_400_after_retries_is_not_stamped_akamai(monkeypatch):
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
        },
    )
    monkeypatch.setattr(
        checkout,
        "_carrefour_browser_api_cart",
        lambda **k: {
            "ok": False,
            "error": "liteCart HTTP 400",
            "error_code": "litecart_http_error",
            "items": [],
            "logged_in": True,
            "driver": "cdp",
            "session_reused": False,
        },
    )
    out = checkout._carrefour_official_cart(
        email="e@mrg.im", password="x", action="list", items=[], session_token="t", session_user="u"
    )
    assert out["ok"] is False
    assert out["error_code"] == "litecart_http_error"
    assert out["logged_in"] is True
    assert "liteCart HTTP 400" in (out.get("error") or "")


def test_context_auth_userid_cookie_is_case_insensitive():
    class _Ctx:
        def cookies(self):
            return [
                {"name": "token", "value": "tok"},
                {"name": "userID", "value": "u9"},
            ]

    assert checkout._context_auth(_Ctx()) == {"token": "tok", "user_id": "u9"}


def test_user_id_from_jwt_payload():
    import base64
    import json

    payload = base64.urlsafe_b64encode(json.dumps({"userId": "u42"}).encode()).rstrip(b"=").decode()
    token = f"eyJhbGciOiJub25lIn0.{payload}.x"
    assert checkout._user_id_from_token(token) == "u42"


def test_session_from_page_fills_userid_from_storage():
    class _Ctx:
        def cookies(self):
            return [{"name": "token", "value": "tok1234567890"}]

    class _Page:
        def __init__(self):
            self.context = _Ctx()

        def evaluate(self, _js, arg=None):
            return {"token": "", "user_id": "u9"}

    out = checkout._session_from_page(_Page())
    assert out["token"] == "tok1234567890"
    assert out["user_id"] == "u9"


def _jwt_with(payload: dict) -> str:
    import base64
    import json

    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJub25lIn0.{body}.x"


def test_pick_user_id_from_storage_persist_root():
    uid = checkout._pick_user_id(
        cookies={"token": "tok"},
        extra={"localStorage": {"persist:root": json.dumps({"auth": {"userId": "u9"}})}},
        token="tok",
        email="e@mrg.im",
    )
    assert uid == "u9"


def test_pick_user_id_from_customerid_cookie():
    uid = checkout._pick_user_id(
        cookies={"token": "tok", "customerId": "c-42"},
        extra={},
        token="tok",
        email="e@mrg.im",
    )
    assert uid == "c-42"


def test_pick_user_id_from_json_cookie():
    uid = checkout._pick_user_id(
        cookies={"info": json.dumps({"user": {"id": "nested-9"}})},
        extra={},
        email="e@mrg.im",
    )
    assert uid == "nested-9"


def test_pick_user_id_from_nuxt_and_datalayer():
    assert (
        checkout._pick_user_id(
            extra={"nuxt": {"state": {"auth": {"user": {"customerId": "nuxt-1"}}}}, "dataLayer": []},
        )
        == "nuxt-1"
    )
    assert (
        checkout._pick_user_id(extra={"dataLayer": [{"event": "pageview", "userId": "dl-7"}]})
        == "dl-7"
    )


def test_pick_user_id_falls_back_to_login_email():
    assert checkout._pick_user_id(cookies={"token": "tok"}, email="e@mrg.im") == "e@mrg.im"


def test_user_id_from_jwt_email_and_sub():
    assert checkout._user_id_from_token(_jwt_with({"email": "e@mrg.im"})) == "e@mrg.im"
    assert checkout._user_id_from_token(_jwt_with({"sub": "991122"})) == "991122"


def test_session_from_page_storage_only_still_sets_userid_header():
    class _Ctx:
        def cookies(self):
            return [{"name": "token", "value": "tok-only"}]

    class _Page:
        def __init__(self):
            self.context = _Ctx()

        def content(self):
            return ""

        def evaluate(self, _js, arg=None):
            return {
                "localStorage": {"maf.auth": json.dumps({"userId": "u-storage"})},
                "sessionStorage": {},
                "cookies": {},
            }

    out = checkout._session_from_page(_Page(), email="e@mrg.im")
    assert out["user_id"] == "u-storage"
    h = checkout._browser_headers(out["token"], out["user_id"], {}, method="GET")
    assert h["userId"] == "u-storage"


def test_session_from_page_jwt_only_still_sets_userid_header():
    token = _jwt_with({"userId": "u-jwt"})

    class _Ctx:
        def cookies(self):
            return [{"name": "token", "value": token}]

    class _Page:
        def __init__(self):
            self.context = _Ctx()

        def content(self):
            return ""

        def evaluate(self, _js, arg=None):
            return {"localStorage": {}, "sessionStorage": {}, "cookies": {}}

    out = checkout._session_from_page(_Page(), email="e@mrg.im")
    assert out["user_id"] == "u-jwt"
    h = checkout._browser_headers(out["token"], out["user_id"], {}, method="GET")
    assert h["userId"] == "u-jwt"


def test_header_variants_never_omit_userid():
    variants = checkout._header_variants("tok", "", {})
    assert variants
    assert all(h.get("userId") for h in variants)


def test_missing_userid_400_is_not_retried_without_header():
    fetches = []

    class _Ctx:
        def cookies(self):
            return [{"name": "token", "value": "tok"}]

    class _Page:
        def __init__(self):
            self.context = _Ctx()

        def content(self):
            return ""

        def evaluate(self, _js, arg=None):
            if arg is None:
                return {"localStorage": {}, "sessionStorage": {}, "cookies": {}}
            fetches.append(arg)
            return {
                "status": 400,
                "body": {"error": {"message": "header userId mancante"}},
                "akamai": False,
            }

    listed = checkout._lite_cart_via_page(_Page(), "tok", "", {}, email="", session_user="")
    assert listed["status"] == 400
    gets = [f for f in fetches if f.get("method") == "GET"]
    assert len(gets) == 1
    assert (gets[0].get("headers") or {}).get("userId") == "anonymous"


def test_browser_list_sends_storage_userid_on_official_get(monkeypatch):
    from bring_fast.stores import carrefour as api

    fetches = []

    class _Page:
        url = "https://www.carrefouruae.com/mafuae/en"

        def __init__(self, ctx):
            self.context = ctx

        def content(self):
            return '{"posInfo":"food=073_Zone04","polygonId":"DXB_DubProdCty_01","emirateCode":"DUBAI"}'

        def evaluate(self, _js, arg=None):
            if arg is None:
                return {
                    "localStorage": {"persist:root": json.dumps({"user": {"userId": "u-storage"}})},
                    "sessionStorage": {},
                    "cookies": {},
                }
            fetches.append(arg)
            if arg.get("method") == "GET" and "liteCart" in (arg.get("url") or ""):
                assert (arg.get("headers") or {}).get("userId") == "u-storage"
                return {
                    "status": 200,
                    "body": {
                        "data": {
                            "items": [
                                {
                                    "id": "743861",
                                    "name": "Coca-Cola Zero 330ml Can",
                                    "quantity": 1,
                                    "price": 1.99,
                                }
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

    class _Ctx:
        pages = []

        def cookies(self):
            return [{"name": "token", "value": "tok-only"}]

        def storage_state(self, **_k):
            return None

    ctx = _Ctx()
    page = _Page(ctx)
    monkeypatch.setattr(checkout, "_launch_carrefour_cart", lambda: (None, None, ctx, "headless"))
    monkeypatch.setattr(checkout, "_carrefour_origin_page", lambda _ctx: (page, False))
    monkeypatch.setattr(
        checkout,
        "_ensure_logged_in_page",
        lambda *_a, **_k: {
            "logged_in": True,
            "reused": True,
            "error": None,
            "token": "tok-only",
            "user_id": "",
        },
    )
    monkeypatch.setattr(checkout, "_dismiss", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "save_browser_cookies", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "_cio_browse_ids", lambda ids: [])
    monkeypatch.setattr(api, "_cio_search", lambda q: [])
    out = checkout._carrefour_browser_api_cart(
        email="e@mrg.im",
        password="x",
        action="list",
        items=[],
    )
    assert out["ok"] is True
    assert out["items"][0]["id"] == "743861"
    assert "Coca-Cola" in (out["items"][0].get("name") or "")
    gets = [f for f in fetches if f.get("method") == "GET" and "liteCart" in (f.get("url") or "")]
    assert gets
    assert (gets[0].get("headers") or {}).get("userId") == "u-storage"
