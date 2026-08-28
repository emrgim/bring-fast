"""Carrefour official cart uses the Android app APIs, not Chrome."""

from bring_fast.stores.carrefour import android_headers, parse_items
from bring_fast.stores.http import is_akamai_shell


def test_parse_items_from_lite_cart():
    body = {
        "data": {
            "items": [
                {"id": "11530", "name": "Milk 1L", "quantity": 2, "price": 6.5},
            ]
        }
    }
    items = parse_items(body)
    assert items[0]["id"] == "11530"
    assert items[0]["qty"] == 2
    assert items[0]["name"] == "Milk 1L"


def test_android_headers_look_like_the_play_store_app():
    h = android_headers(token="abc", user_id="99")
    assert h["appid"] == "Android"
    assert h["x-maf-appId"] == "Android"
    assert h["x-maf-storeId"] == "mafuae"
    assert h["userId"] == "99"
    assert h["Authorization"] == "Bearer abc"
    assert "okhttp" in h["User-Agent"]
    assert "com.aswat.carrefouruae" in h["User-Agent"]


def test_akamai_empty_shell_is_detected():
    assert is_akamai_shell("<!DOCTYPE html>\n<html>\n<body>\n<p></p>\n</body>\n</html>")
    assert not is_akamai_shell('{"data":[]}')


class _FakeResp:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = __import__("json").dumps(payload)

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        return _FakeResp(200, {"ok": True})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs.get("json")))
        if url.endswith("/v2/customers/login"):
            return _FakeResp(
                200,
                {"data": {"token": "tok123", "userId": "u9"}},
                headers={"token": "tok123", "userId": "u9"},
            )
        return _FakeResp(404, {"meta": {"message": "no"}})


def test_login_uses_android_customers_login(monkeypatch):
    from bring_fast.stores import carrefour as api

    fake = _FakeSession()
    monkeypatch.setattr(api, "session", lambda: fake)
    out = api.login("a@b.c", "secret")
    assert out["ok"] is True
    assert out["token"] == "tok123"
    assert out["user_id"] == "u9"
    assert any(c[0] == "POST" and "/v2/customers/login" in c[1] for c in fake.calls)


def test_official_cart_list_after_login(monkeypatch):
    from bring_fast.stores import carrefour as api

    monkeypatch.setattr(api, "login", lambda e, p: {"ok": True, "token": "t", "user_id": "u", "error": None})
    monkeypatch.setattr(
        api,
        "lite_cart",
        lambda **k: {"data": {"items": [{"id": "1", "name": "Pasta", "quantity": 1, "price": 4}]}},
    )
    out = api.official_cart(email="a@b.c", password="x", action="list", items=[])
    assert out["ok"] is True
    assert out["driver"] == "android"
    assert out["client"] == "com.aswat.carrefouruae"
    assert out["items"][0]["name"] == "Pasta"
    assert out["official_count"] == 1


class _BasketSession:
    def __init__(self, *, add_status=200, delete_status=200):
        self.calls = []
        self.add_status = add_status
        self.delete_status = delete_status

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs.get("json")))
        if url.endswith("/entries") or url.endswith("/addItem"):
            return _FakeResp(self.add_status, {"data": {"ok": True}})
        return _FakeResp(404, {"meta": {"message": "no"}})

    def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs.get("json")))
        return _FakeResp(self.delete_status, {"data": {"ok": True}})

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        return _FakeResp(200, {"data": {"items": []}})


def test_add_item_posts_basket_entries(monkeypatch):
    from bring_fast.stores import carrefour as api

    fake = _BasketSession()
    monkeypatch.setattr(api, "session", lambda: fake)
    monkeypatch.setattr(api, "_product_card", lambda pid, name="": {"name": name or pid, "image": "https://x", "in_stock": True})
    out = api.add_item(token="t", user_id="u", product_id="11530", qty=2, name="Milk 1L")
    assert out["data"]["ok"] is True
    posts = [c for c in fake.calls if c[0] == "POST"]
    assert posts
    assert posts[0][1].endswith("/v1/basket/mafuae/en/entries")
    assert posts[0][2]["productId"] == "11530"
    assert posts[0][2]["quantity"] == 2


def test_add_item_falls_back_to_additem_when_entries_is_missing(monkeypatch):
    from bring_fast.stores import carrefour as api

    class _Fallback(_BasketSession):
        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs.get("json")))
            if url.endswith("/entries"):
                return _FakeResp(404, {"meta": {"message": "no proxy"}})
            if url.endswith("/addItem"):
                return _FakeResp(200, {"data": {"ok": True}})
            return _FakeResp(404, {"meta": {"message": "no"}})

    fake = _Fallback()
    monkeypatch.setattr(api, "session", lambda: fake)
    monkeypatch.setattr(api, "_product_card", lambda pid, name="": {"name": name or pid, "image": "https://x", "in_stock": True})
    out = api.add_item(token="t", user_id="u", product_id="11530", qty=1, name="Milk")
    assert out["data"]["ok"] is True
    paths = [c[1].rsplit("/", 1)[-1] for c in fake.calls if c[0] == "POST"]
    assert "entries" in paths
    assert "addItem" in paths


def test_remove_items_deletes_entries_with_product_ids(monkeypatch):
    from bring_fast.stores import carrefour as api

    fake = _BasketSession()
    monkeypatch.setattr(api, "session", lambda: fake)
    api.remove_items(token="t", user_id="u", product_ids=["11530", "99"])
    deletes = [c for c in fake.calls if c[0] == "DELETE"]
    assert deletes
    assert deletes[0][1].endswith("/v1/basket/mafuae/en/entries")
    assert deletes[0][2]["productIds"] == ["11530", "99"]


def test_official_cart_add_then_clear(monkeypatch):
    from bring_fast.stores import carrefour as api

    added = []
    removed = []
    cart = {"data": {"items": []}}

    monkeypatch.setattr(api, "login", lambda e, p: {"ok": True, "token": "t", "user_id": "u", "error": None})
    monkeypatch.setattr(api, "lite_cart", lambda **k: cart)

    def _add(**kw):
        added.append(kw)
        cart["data"]["items"] = [{"id": kw["product_id"], "name": kw["name"], "quantity": kw["qty"], "price": 4}]
        return {"ok": True}

    def _rm(**kw):
        removed.append(kw["product_ids"])
        cart["data"]["items"] = []
        return {"ok": True}

    monkeypatch.setattr(api, "add_item", _add)
    monkeypatch.setattr(api, "remove_items", _rm)

    out = api.official_cart(email="a@b.c", password="x", action="add", items=[{"id": "11530", "name": "Milk", "qty": 2}])
    assert out["ok"] is True
    assert added[0]["product_id"] == "11530"
    assert out["items"][0]["qty"] == 2

    out = api.official_cart(email="a@b.c", password="x", action="clear", items=[], session_token="t", session_user="u")
    assert out["ok"] is True
    assert removed == [["11530"]]
    assert out["items"] == []


def test_official_cart_refuses_out_of_stock_add(monkeypatch):
    from bring_fast.stores import carrefour as api
    from bring_fast.stores.http import StoreAPIError

    monkeypatch.setattr(api, "login", lambda e, p: {"ok": True, "token": "t", "user_id": "u", "error": None})
    monkeypatch.setattr(
        api,
        "add_item",
        lambda **k: (_ for _ in ()).throw(StoreAPIError("Milk is out of stock for this delivery location.", status=409)),
    )
    out = api.official_cart(email="a@b.c", password="x", action="add", items=[{"id": "11530", "qty": 1}])
    assert out["ok"] is False
    assert "out of stock" in (out.get("error") or "").lower()
