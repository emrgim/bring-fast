"""Carrefour official cart uses curl_cffi Chrome impersonation, not a real window."""

import pytest

from bring_fast.stores.carrefour import android_headers, food_pos, parse_fulfilment_html, parse_items
from bring_fast.stores.http import StoreAPIError, is_akamai_shell

LOC = {
    "pos_info": (
        "food=073_Zone04,QCOMM=879_Zone01,BULK=073_Zone01,"
        "QELEC=073_Zone01,express=064_Zone01,nonfood=099_Zone01,QMKP=F01_Zone01"
    ),
    "pos_info2": "food=073_Zone04,QCOMM=879_Zone01,BULK=073_Zone01,QELEC=073_Zone01,express=064_Zone01,nonfood=099_Zone01,QMKP=F01_Zone01",
    "polygon_id": "DXB_DubProdCty_01",
    "emirate_code": "DUBAI",
    "food_pos": "073",
    "area": "Dubai Production City",
    "delivery_address": "Element Meaisam 731",
    "service_types": "SLOTTED|DEFAULT|MKP_GLOBAL|QMKP|QELEC|DIGITAL",
    "product_type": "ANY",
    "lat": 25.0321285,
    "lng": 55.1912732,
}

SSR_HTML = (
    '<script>self.__next_f.push([1,"{\\"posInfo\\":\\"'
    "food=073_Zone04,QCOMM=879_Zone01,BULK=073_Zone01,QELEC=073_Zone01,"
    "express=064_Zone01,nonfood=099_Zone01,QMKP=F01_Zone01"
    '\\",\\"posInfo2\\":\\"food=073_Zone04\\",\\"polygonId\\":\\"DXB_DubProdCty_01\\",'
    '\\"emirateCode\\":\\"DUBAI\\"}"])</script>'
    '{"posInfo":"food=073_Zone04,QCOMM=879_Zone01,BULK=073_Zone01,QELEC=073_Zone01,'
    'express=064_Zone01,nonfood=099_Zone01,QMKP=F01_Zone01",'
    '"polygonId":"DXB_DubProdCty_01","emirateCode":"DUBAI"}'
)


def _patch_loc(monkeypatch, api, loc=None):
    monkeypatch.setattr(api, "resolve_fulfilment", lambda **k: dict(loc or LOC))


def _in_stock_card(pid, name="", **_k):
    return {"name": name or pid, "image": "https://x", "in_stock": True, "pos": "073"}


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
    assert items[0]["price"] == 6.5


def test_parse_items_reads_product_name_and_nested_price():
    items = parse_items(
        {
            "data": {
                "items": [
                    {
                        "productId": "743861",
                        "productName": "Coca-Cola Zero 330ml Can",
                        "quantity": 1,
                        "price": {"value": 1.99},
                    }
                ]
            }
        }
    )
    assert items[0]["id"] == "743861"
    assert items[0]["name"] == "Coca-Cola Zero 330ml Can"
    assert items[0]["price"] == 1.99


def test_parse_items_lite_response_is_id_and_qty_only():
    items = parse_items({"data": {"items": [{"id": "743861", "quantity": 1}]}})
    assert items[0]["id"] == "743861"
    assert items[0]["qty"] == 1
    assert items[0]["name"] == ""
    assert items[0]["price"] is None


def test_headers_do_not_override_chrome_user_agent():
    h = android_headers(token="abc", user_id="99")
    assert h["appid"] == "Android"
    assert h["x-maf-appId"] == "Android"
    assert h["x-maf-storeId"] == "mafuae"
    assert h["userId"] == "99"
    assert h["Authorization"] == "Bearer abc"
    assert "User-Agent" not in h
    assert "okhttp" not in str(h)


def test_headers_include_pos_info():
    h = android_headers(token="abc", user_id="99", fulfilment=LOC)
    assert h["posInfo"].startswith("food=073_Zone04")
    assert h["emirateCode"] == "DUBAI"
    assert h["serviceTypes"].startswith("SLOTTED")
    assert h["productType"] == "ANY"
    assert "User-Agent" not in h


def test_food_pos_from_pos_info():
    assert food_pos(LOC["pos_info"]) == "073"
    assert food_pos("QCOMM=879_Zone01") == ""


def test_parse_fulfilment_html_dubai_production_city():
    loc = parse_fulfilment_html(SSR_HTML, lat=25.0321285, lng=55.1912732)
    assert loc["food_pos"] == "073"
    assert loc["polygon_id"] == "DXB_DubProdCty_01"
    assert loc["area"] == "Dubai Production City"
    assert loc["emirate_code"] == "DUBAI"
    assert "food=073_Zone04" in loc["pos_info"]


def test_akamai_empty_shell_is_detected():
    assert is_akamai_shell("<!DOCTYPE html>\n<html>\n<body>\n<p></p>\n</body>\n</html>")
    assert not is_akamai_shell('{"data":[]}')


class _FakeResp:
    def __init__(self, status, payload, headers=None, text=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = text if text is not None else __import__("json").dumps(payload)

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs.get("headers")))
        return _FakeResp(200, {"ok": True})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs.get("headers"), kwargs.get("json")))
        if url.endswith("/v2/customers/login"):
            return _FakeResp(
                200,
                {"data": {"token": "tok123", "userId": "u9"}},
                headers={"token": "tok123", "userId": "u9"},
            )
        return _FakeResp(404, {"meta": {"message": "no"}})

    def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs.get("headers"), kwargs.get("json")))
        return _FakeResp(200, {"data": {"ok": True}})


def _no_network(monkeypatch, api, fake=None):
    """Keep official_cart unit tests off the network (warm homepage is skipped on list)."""
    sess = fake or _FakeSession()
    monkeypatch.setattr(api, "chrome_session", lambda existing=None, **_k: existing or sess)
    return sess


def test_akamai_login_does_not_look_like_missing_password(monkeypatch):
    from bring_fast.stores import carrefour as api
    from bring_fast.stores.http import StoreAPIError

    class _Denied:
        def get(self, *a, **k):
            return _FakeResp(200, {"ok": True})

        def post(self, *a, **k):
            raise StoreAPIError("login: Akamai access denied.", status=403)

    monkeypatch.setattr(api, "session", lambda: _Denied())
    out = api.login("a@b.c", "secret")
    assert out["ok"] is False
    assert "Akamai" in out["error"]
    assert "still present" in out["error"]


def test_expired_token_does_not_retry_login_on_akamai_403(monkeypatch):
    from bring_fast.stores import carrefour as api
    from bring_fast.stores.http import StoreAPIError

    _no_network(monkeypatch, api)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(
        api,
        "lite_cart",
        lambda **k: (_ for _ in ()).throw(StoreAPIError("Akamai access denied.", status=403)),
    )
    called = []
    monkeypatch.setattr(
        api,
        "login",
        lambda e, p, **k: called.append(1) or {"ok": False, "token": "", "user_id": "", "error": "no"},
    )
    out = api.official_cart(
        email="a@b.c", password="x", action="list", items=[], session_token="t", session_user="u"
    )
    assert called == []
    assert out["ok"] is False
    assert "Akamai" in (out.get("error") or "")
    assert out.get("error_code") == "akamai_blocked"


def test_akamai_403_html_does_not_count_as_invalid_token(monkeypatch):
    from bring_fast.stores import carrefour as api
    from bring_fast.stores.http import StoreAPIError

    _no_network(monkeypatch, api)
    _patch_loc(monkeypatch, api)
    err = StoreAPIError("liteCart: Akamai access denied.", status=401, body=None)
    monkeypatch.setattr(api, "lite_cart", lambda **k: (_ for _ in ()).throw(err))
    called = []
    monkeypatch.setattr(
        api, "login", lambda e, p, **k: called.append(1) or {"ok": False, "token": "", "user_id": "", "error": "no"}
    )
    out = api.official_cart(
        email="a@b.c", password="x", action="list", items=[], session_token="t", session_user="u"
    )
    assert called == []
    assert out["ok"] is False
    assert out.get("error_code") == "akamai_blocked"


def test_login_uses_one_customers_login_post_on_warmed_chrome(monkeypatch):
    from bring_fast.stores import carrefour as api

    fake = _FakeSession()
    monkeypatch.setattr(api, "session", lambda: fake)
    out = api.login("a@b.c", "secret")
    assert out["ok"] is True
    assert out["token"] == "tok123"
    assert out["user_id"] == "u9"
    gets = [c for c in fake.calls if c[0] == "GET"]
    posts = [c for c in fake.calls if c[0] == "POST"]
    assert gets, "homepage (and login page) must warm the Chrome session"
    assert "/mafuae/en" in gets[0][1]
    assert gets[0][2] is None or "User-Agent" not in (gets[0][2] or {})
    assert len(posts) == 1
    assert posts[0][1].endswith("/v2/customers/login")
    post_headers = posts[0][2] or {}
    assert "User-Agent" not in post_headers
    assert "okhttp" not in str(post_headers)


def test_official_cart_list_after_login(monkeypatch):
    from bring_fast.stores import carrefour as api

    _no_network(monkeypatch, api)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(
        api, "login", lambda e, p, **k: {"ok": True, "token": "t", "user_id": "u", "error": None}
    )
    monkeypatch.setattr(
        api,
        "lite_cart",
        lambda **k: {"data": {"items": [{"id": "1", "name": "Pasta", "quantity": 1, "price": 4}]}},
    )
    out = api.official_cart(email="a@b.c", password="x", action="list", items=[])
    assert out["ok"] is True
    assert out["driver"] == "chrome"
    assert out["client"] == "com.aswat.carrefouruae"
    assert out["items"][0]["name"] == "Pasta"
    assert out["official_count"] == 1


def test_official_cart_list_skips_fulfilment(monkeypatch):
    """Grok times out if list waits on homepage + addresses + posInfo. Those are add-only."""
    from bring_fast.stores import carrefour as api

    _no_network(monkeypatch, api)
    monkeypatch.setattr(
        api,
        "resolve_fulfilment",
        lambda **k: (_ for _ in ()).throw(AssertionError("list must not resolve fulfilment")),
    )
    monkeypatch.setattr(
        api,
        "lite_cart",
        lambda **k: {"data": {"items": [{"id": "1", "name": "Pasta", "quantity": 1, "price": 4}]}},
    )
    out = api.official_cart(
        email="a@b.c", password="x", action="list", items=[], session_token="t", session_user="u"
    )
    assert out["ok"] is True
    assert out["items"][0]["name"] == "Pasta"
    assert out.get("food_pos") in (None, "")
    got = api.official_cart(
        email="a@b.c", password="x", action="get", items=[], session_token="t", session_user="u"
    )
    assert got["ok"] is True
    assert got["official_count"] == 1


class _BasketSession:
    def __init__(self, *, add_status=200, delete_status=200):
        self.calls = []
        self.add_status = add_status
        self.delete_status = delete_status

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs.get("json"), kwargs.get("headers")))
        if url.endswith("/entries") or url.endswith("/addItem"):
            return _FakeResp(self.add_status, {"data": {"ok": True}})
        return _FakeResp(404, {"meta": {"message": "no"}})

    def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs.get("json"), kwargs.get("headers")))
        return _FakeResp(self.delete_status, {"data": {"ok": True}})

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs.get("headers")))
        return _FakeResp(200, {"data": {"items": []}})


def test_add_item_posts_additem_not_entries(monkeypatch):
    from bring_fast.stores import carrefour as api

    fake = _BasketSession()
    monkeypatch.setattr(api, "session", lambda: fake)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(api, "_product_card", _in_stock_card)
    out = api.add_item(token="t", user_id="u", product_id="11530", qty=2, name="Milk 1L")
    assert out["data"]["ok"] is True
    posts = [c for c in fake.calls if c[0] == "POST"]
    assert posts
    assert posts[0][1].endswith("/v1/basket/mafuae/en/addItem")
    assert posts[0][2]["productId"] == "11530"
    assert posts[0][2]["quantity"] == 2
    assert posts[0][2]["shopId"] == "0000"
    assert "food=073_Zone04" in (posts[0][3] or {}).get("posInfo", "")
    assert (posts[0][3] or {}).get("intent") == "SLOTTED"
    assert "User-Agent" not in (posts[0][3] or {})
    assert "okhttp" not in str(posts[0][3] or {})
    assert not any(c[0] == "POST" and str(c[1]).endswith("/entries") for c in fake.calls)
    home = [c for c in fake.calls if c[0] == "GET" and "liteCart" not in c[1]]
    assert home
    assert home[0][2] is None or "User-Agent" not in (home[0][2] or {})


def test_add_item_1592968_qty_2_posts_additem(monkeypatch):
    """Oasis Blu 1L pack of 6 — two packs, append-only, POS 073."""
    from bring_fast.stores import carrefour as api

    fake = _BasketSession()
    monkeypatch.setattr(api, "session", lambda: fake)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(api, "_product_card", _in_stock_card)
    out = api.add_item(
        token="t",
        user_id="u",
        product_id="1592968",
        qty=2,
        name="Oasis Blu Sparkling Water, 1L Pack of 6",
    )
    assert out["data"]["ok"] is True
    posts = [c for c in fake.calls if c[0] == "POST"]
    assert posts[0][1].endswith("/v1/basket/mafuae/en/addItem")
    assert posts[0][2]["productId"] == "1592968"
    assert posts[0][2]["quantity"] == 2
    assert posts[0][2]["productName"] == "Oasis Blu Sparkling Water, 1L Pack of 6"
    assert not any(c[0] == "POST" and str(c[1]).endswith("/entries") for c in fake.calls)


def test_add_item_500_does_not_post_entries_or_v8(monkeypatch):
    from bring_fast.stores import carrefour as api

    class _Five(_BasketSession):
        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs.get("json"), kwargs.get("headers")))
            if "/v1/basket/" in url and url.endswith("/addItem"):
                return _FakeResp(500, {"error": {"message": "Internal Server Error"}})
            if url.endswith("/entries"):
                return _FakeResp(200, {"data": {"wiped": True}})
            if "/v8/carts/" in url:
                return _FakeResp(200, {"data": {"ok": True}})
            return _FakeResp(404, {"meta": {"message": "no"}})

    fake = _Five()
    monkeypatch.setattr(api, "session", lambda: fake)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(api, "_product_card", _in_stock_card)
    with pytest.raises(StoreAPIError) as raised:
        api.add_item(token="t", user_id="u", product_id="1592968", qty=2, name="Oasis Blu")
    assert raised.value.status == 500
    assert "Internal Server Error" in str(raised.value)
    posts = [c[1] for c in fake.calls if c[0] == "POST"]
    assert any(u.endswith("/v1/basket/mafuae/en/addItem") for u in posts)
    assert not any(u.endswith("/entries") for u in posts)
    assert not any("/v8/carts/" in u for u in posts)


def test_add_item_falls_back_to_v8_when_additem_is_missing(monkeypatch):
    from bring_fast.stores import carrefour as api

    class _Fallback(_BasketSession):
        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs.get("json"), kwargs.get("headers")))
            if url.endswith("/entries"):
                raise AssertionError("add must not POST /entries")
            if "/v1/basket/" in url and url.endswith("/addItem"):
                return _FakeResp(404, {"meta": {"message": "no proxy"}})
            if "/v8/carts/" in url and url.endswith("/addItem"):
                return _FakeResp(200, {"data": {"ok": True}})
            return _FakeResp(404, {"meta": {"message": "no"}})

    fake = _Fallback()
    monkeypatch.setattr(api, "session", lambda: fake)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(api, "_product_card", _in_stock_card)
    out = api.add_item(token="t", user_id="u", product_id="11530", qty=1, name="Milk")
    assert out["data"]["ok"] is True
    posts = [c[1] for c in fake.calls if c[0] == "POST"]
    assert any("/v1/basket/" in u and u.endswith("/addItem") for u in posts)
    assert any("/v8/carts/" in u and u.endswith("/addItem") for u in posts)
    assert not any(u.endswith("/entries") for u in posts)


def test_add_item_maps_slotted_intent_to_needs_delivery_slot(monkeypatch):
    from bring_fast.stores import carrefour as api

    slotted = {
        "error": {
            "message": (
                "Invalid Request, please check and retry. | SLOTTED is not a valid intent "
                "for product, available purchase indicators  are null"
            )
        }
    }

    class _Slotted(_BasketSession):
        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs.get("json"), kwargs.get("headers")))
            return _FakeResp(400, slotted)

    fake = _Slotted()
    monkeypatch.setattr(api, "session", lambda: fake)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(api, "_product_card", _in_stock_card)
    with pytest.raises(StoreAPIError) as raised:
        api.add_item(token="t", user_id="u", product_id="11811", qty=2, name="Galbani Mascarpone, 250g")
    err = raised.value
    assert err.error_code == "needs_delivery_slot"
    assert "purchase indicators" in (err.maf_error or "").lower()
    assert "needs_delivery_slot" in str(err)


def test_add_item_without_posinfo_is_needs_delivery_slot(monkeypatch):
    from bring_fast.stores import carrefour as api

    empty = dict(LOC, pos_info="", food_pos="")
    monkeypatch.setattr(api, "session", lambda: _BasketSession())
    _patch_loc(monkeypatch, api, empty)
    monkeypatch.setattr(api, "_product_card", _in_stock_card)
    with pytest.raises(StoreAPIError) as raised:
        api.add_item(token="t", user_id="u", product_id="11811", qty=1)
    assert raised.value.error_code == "needs_delivery_slot"


def test_product_card_uses_fulfilment_food_pos(monkeypatch):
    from bring_fast.stores import carrefour as api

    payload = {
        "response": {
            "results": [
                {
                    "value": "Jenan Large White Eggs 15 PCS",
                    "data": {
                        "id": "1102885",
                        "image_url": "https://img/eggs",
                        "stock": [
                            {"pos": "072", "isAvailable": False, "stock_status": "OUT_OF_STOCK"},
                            {"pos": "073", "isAvailable": True, "stock_status": "IN_STOCK", "quantity": 100},
                        ],
                    },
                }
            ]
        }
    }

    monkeypatch.setattr(api, "_cio_browse_ids", lambda ids: payload["response"]["results"])
    monkeypatch.setattr(api, "_cio_search", lambda q: payload["response"]["results"])
    at_073 = api._product_card("1102885", "Jenan Large White Eggs 15 PCS", fulfilment=LOC)
    assert at_073["in_stock"] is True
    assert at_073["pos"] == "073"
    at_072 = api._product_card(
        "1102885",
        "Jenan Large White Eggs 15 PCS",
        fulfilment={**LOC, "food_pos": "072", "pos_info": "food=072_Zone01"},
    )
    assert at_072["in_stock"] is False


def test_product_card_ignores_wrong_constructor_id(monkeypatch):
    from bring_fast.stores import carrefour as api

    monkeypatch.setattr(
        api,
        "_cio_browse_ids",
        lambda ids: [{"value": "Wallpaper", "data": {"id": "999", "stock": [{"pos": "073", "isAvailable": True, "stock_status": "IN_STOCK"}]}}],
    )
    monkeypatch.setattr(
        api,
        "_cio_search",
        lambda q: [{"value": "Wallpaper", "data": {"id": "999", "stock": [{"pos": "073", "isAvailable": True, "stock_status": "IN_STOCK"}]}}],
    )
    card = api._product_card("11811", "Galbani Mascarpone, 250g", fulfilment=LOC)
    assert card["name"] == "Galbani Mascarpone, 250g"
    assert card["in_stock"] is True


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

    _no_network(monkeypatch, api)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(
        api, "login", lambda e, p, **k: {"ok": True, "token": "t", "user_id": "u", "error": None}
    )
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
    assert added[0]["fulfilment"]["food_pos"] == "073"
    assert out["items"][0]["qty"] == 2

    out = api.official_cart(email="a@b.c", password="x", action="clear", items=[], session_token="t", session_user="u")
    assert out["ok"] is True
    assert removed == [["11530"]]
    assert out["items"] == []


def test_official_cart_add_ok_when_sku_in_cart_despite_500(monkeypatch):
    from bring_fast.stores import carrefour as api
    from bring_fast.stores.http import StoreAPIError

    _no_network(monkeypatch, api)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(
        api, "login", lambda e, p, **k: {"ok": True, "token": "t", "user_id": "u", "error": None}
    )
    monkeypatch.setattr(
        api,
        "add_item",
        lambda **k: (_ for _ in ()).throw(
            StoreAPIError("Internal Server Error", status=500, maf_error="Internal Server Error")
        ),
    )
    monkeypatch.setattr(
        api,
        "lite_cart",
        lambda **k: {"data": {"items": [{"id": "743861", "quantity": 1}]}},
    )
    catalog = {
        "743861": {
            "value": "Coca-Cola Zero 330ml Can",
            "data": {"id": "743861", "price": 1.99, "ean": "5449000131805"},
        }
    }
    monkeypatch.setattr(api, "_cio_browse_ids", lambda ids: [catalog[i] for i in ids if i in catalog])
    monkeypatch.setattr(
        api,
        "_cio_search",
        lambda q: [catalog[q]] if q in catalog else [],
    )
    out = api.official_cart(
        email="a@b.c", password="x", action="add", items=[{"id": "743861", "qty": 1}]
    )
    assert out["ok"] is True
    assert out["error"] is None
    assert out["maf_error"] is None
    assert out["items"][0]["id"] == "743861"
    assert "Coca-Cola" in out["items"][0]["name"]
    assert out["items"][0]["price"] == 1.99


def test_enrich_items_fills_name_and_price_from_catalog(monkeypatch):
    from bring_fast.stores import carrefour as api

    catalog = {
        "743861": {
            "value": "Coca-Cola Zero, 330ml, Can",
            "data": {"id": "743861", "price": 1.99, "ean": "5449000131805"},
        }
    }
    monkeypatch.setattr(api, "_cio_browse_ids", lambda ids: [catalog[i] for i in ids if i in catalog])
    monkeypatch.setattr(
        api,
        "_cio_search",
        lambda q: [catalog[q]] if q in catalog else [],
    )
    items = api.enrich_items(
        [{"id": "743861", "name": "", "qty": 1, "price": None, "currency": "AED", "url": ""}]
    )
    assert items[0]["id"] == "743861"
    assert "Coca-Cola Zero" in items[0]["name"]
    assert items[0]["price"] == 1.99


def test_enrich_items_skips_catalog_when_payload_has_name_and_price(monkeypatch):
    from bring_fast.stores import carrefour as api

    monkeypatch.setattr(
        api, "_cio_browse_ids", lambda ids: (_ for _ in ()).throw(AssertionError("catalog must not run"))
    )
    monkeypatch.setattr(
        api, "_cio_search", lambda q: (_ for _ in ()).throw(AssertionError("catalog must not run"))
    )
    items = api.enrich_items(
        [{"id": "743861", "name": "Coca-Cola Zero 330ml Can", "qty": 1, "price": 1.99, "currency": "AED"}]
    )
    assert items[0]["name"] == "Coca-Cola Zero 330ml Can"
    assert items[0]["price"] == 1.99


def test_official_cart_list_enriches_bare_ids(monkeypatch):
    from bring_fast.stores import carrefour as api

    _no_network(monkeypatch, api)
    monkeypatch.setattr(
        api,
        "lite_cart",
        lambda **k: {
            "data": {
                "items": [
                    {"id": "376161", "quantity": 1},
                    {"id": "743861", "quantity": 1},
                ]
            }
        },
    )
    catalog = {
        "376161": {
            "value": "Coca-Cola Original 330ml x6",
            "data": {"id": "376161", "price": 14.99},
        },
        "743861": {
            "value": "Coca-Cola Zero 330ml Can",
            "data": {"id": "743861", "price": 1.99},
        },
    }
    monkeypatch.setattr(api, "_cio_browse_ids", lambda ids: [catalog[i] for i in ids if i in catalog])
    monkeypatch.setattr(api, "_cio_search", lambda q: [catalog[q]] if q in catalog else [])
    out = api.official_cart(
        email="a@b.c", password="x", action="list", items=[], session_token="t", session_user="u"
    )
    assert out["ok"] is True
    by_id = {it["id"]: it for it in out["items"]}
    assert "Coca-Cola Original" in by_id["376161"]["name"]
    assert by_id["376161"]["price"] == 14.99
    assert "Coca-Cola Zero" in by_id["743861"]["name"]
    assert by_id["743861"]["price"] == 1.99


def test_official_cart_refuses_out_of_stock_add(monkeypatch):
    from bring_fast.stores import carrefour as api
    from bring_fast.stores.http import StoreAPIError

    _no_network(monkeypatch, api)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(
        api, "login", lambda e, p, **k: {"ok": True, "token": "t", "user_id": "u", "error": None}
    )
    monkeypatch.setattr(api, "lite_cart", lambda **k: {"data": {"items": []}})
    monkeypatch.setattr(
        api,
        "add_item",
        lambda **k: (_ for _ in ()).throw(StoreAPIError("Milk is out of stock for this delivery location.", status=409)),
    )
    out = api.official_cart(email="a@b.c", password="x", action="add", items=[{"id": "11530", "qty": 1}])
    assert out["ok"] is False
    assert "out of stock" in (out.get("error") or "").lower()
    assert out["item_errors"]


def test_official_cart_stops_on_needs_delivery_slot(monkeypatch):
    from bring_fast.stores import carrefour as api
    from bring_fast.stores.http import StoreAPIError

    _no_network(monkeypatch, api)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(
        api, "login", lambda e, p, **k: {"ok": True, "token": "t", "user_id": "u", "error": None}
    )
    monkeypatch.setattr(
        api,
        "add_item",
        lambda **k: (_ for _ in ()).throw(
            StoreAPIError("needs_delivery_slot", status=400, error_code="needs_delivery_slot", maf_error="SLOTTED")
        ),
    )
    out = api.official_cart(
        email="a@b.c",
        password="x",
        action="add",
        items=[{"id": "11811", "qty": 2}, {"id": "1101063", "qty": 1}],
    )
    assert out["ok"] is False
    assert out["error_code"] == "needs_delivery_slot"
    assert out["maf_error"] == "SLOTTED"


def test_invalid_auth_token_retries_login_once_on_same_session(monkeypatch):
    from bring_fast.stores import carrefour as api

    class _ExpiredThenOk(_FakeSession):
        def __init__(self):
            super().__init__()
            self.logins = 0

        def get(self, url, **kwargs):
            self.calls.append(("GET", url, kwargs.get("headers")))
            if "liteCart" in url:
                if self.logins == 0:
                    return _FakeResp(401, {"meta": {"message": "Invalid Auth Token"}})
                return _FakeResp(
                    200, {"data": {"items": [{"id": "1", "name": "Milk", "quantity": 1, "price": 4}]}}
                )
            return _FakeResp(200, {"ok": True})

        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs.get("headers"), kwargs.get("json")))
            if url.endswith("/v2/customers/login"):
                self.logins += 1
                return _FakeResp(
                    200,
                    {"data": {"token": "fresh", "userId": "u9"}},
                    headers={"token": "fresh", "userId": "u9"},
                )
            return _FakeResp(404, {"meta": {"message": "no"}})

    fake = _ExpiredThenOk()
    created = []
    monkeypatch.setattr(api, "session", lambda: created.append(1) or fake)
    out = api.official_cart(
        email="a@b.c", password="x", action="list", items=[], session_token="old", session_user="u"
    )
    assert created == [1], "homepage + liteCart + one login POST share one Chrome session"
    assert fake.logins == 1
    posts = [c for c in fake.calls if c[0] == "POST" and "/v2/customers/login" in c[1]]
    assert len(posts) == 1
    assert out["ok"] is True
    assert out["items"][0]["name"] == "Milk"
    assert out["session_reused"] is False


def test_lite_cart_warms_homepage_before_api(monkeypatch):
    from bring_fast.stores import carrefour as api

    fake = _BasketSession()
    created = []
    monkeypatch.setattr(api, "session", lambda: created.append(1) or fake)
    api.lite_cart(token="t", user_id="u")
    assert created == [1]
    urls = [c[1] for c in fake.calls if c[0] == "GET"]
    assert any("/mafuae/en" in u and "liteCart" not in u for u in urls)
    assert any("liteCart" in u for u in urls)
    home_headers = next(c[2] for c in fake.calls if c[0] == "GET" and "liteCart" not in c[1])
    assert home_headers is None or "User-Agent" not in home_headers


def test_chrome_session_skips_akamai_empty_homepage(monkeypatch):
    from bring_fast.stores import carrefour as api

    class _Shell:
        def get(self, *a, **k):
            return _FakeResp(
                200,
                {},
                text="<!DOCTYPE html>\n<html>\n<body>\n<p></p>\n</body>\n</html>",
            )

    class _Real:
        def get(self, *a, **k):
            return _FakeResp(200, {}, text='<!DOCTYPE html><html lang="en"><body>Carrefour</body></html>')

    monkeypatch.setattr(api, "session", lambda: _Shell())
    monkeypatch.setattr(api, "_new_impersonate", lambda name: _Real())
    s = api.chrome_session()
    assert isinstance(s, _Real)


def test_browser_cookie_jar_supplies_token(tmp_path, monkeypatch):
    monkeypatch.setenv("BRINGFAST_DATA", str(tmp_path))
    from bring_fast.stores import carrefour as api

    api.save_browser_cookies(
        [
            {"name": "token", "value": "tok-from-chrome", "domain": ".carrefouruae.com"},
            {"name": "userId", "value": "u42", "domain": ".carrefouruae.com"},
            {"name": "_abck", "value": "akamai", "domain": ".carrefouruae.com"},
        ]
    )
    jar = api.token_from_browser_cookies()
    assert jar["token"] == "tok-from-chrome"
    assert jar["user_id"] == "u42"


def test_browser_cookie_jar_userid_is_case_insensitive_and_accepts_customerid(tmp_path, monkeypatch):
    monkeypatch.setenv("BRINGFAST_DATA", str(tmp_path))
    from bring_fast.stores import carrefour as api

    api.save_browser_cookies(
        [
            {"name": "token", "value": "tok", "domain": ".carrefouruae.com"},
            {"name": "customerId", "value": "c-99", "domain": ".carrefouruae.com"},
        ]
    )
    jar = api.token_from_browser_cookies()
    assert jar["user_id"] == "c-99"


def test_akamai_html_sets_error_code():
    from bring_fast.stores.http import StoreAPIError, json_or_error

    class _Resp:
        status_code = 200
        text = "<!DOCTYPE html>\n<html>\n<body>\n<p></p>\n</body>\n</html>"

    with pytest.raises(StoreAPIError) as raised:
        json_or_error(_Resp(), "liteCart")
    assert raised.value.error_code == "akamai_blocked"


def test_varnish_54113_sets_error_code():
    from bring_fast.stores.http import StoreAPIError, json_or_error

    class _Resp:
        status_code = 405
        text = (
            "<html><head><title>405 Not allowed</title></head>"
            "<body><h1>Error 405 Not allowed</h1><h3>Error 54113</h3>"
            "<p>Varnish cache server</p></body></html>"
        )

        def json(self):
            raise ValueError("html")

    with pytest.raises(StoreAPIError) as raised:
        json_or_error(_Resp(), "unioncoop REST /carts/mine")
    assert raised.value.error_code == "varnish_blocked"


def test_empty_2xx_body_is_success():
    from bring_fast.stores.http import json_or_error

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            raise ValueError("empty")

    assert json_or_error(_Resp(), "unioncoop REST DELETE") is True


def test_json_true_body_is_success():
    from bring_fast.stores.http import json_or_error

    class _Resp:
        status_code = 200
        text = "true"

        def json(self):
            return True

    assert json_or_error(_Resp(), "unioncoop REST DELETE") is True


def test_official_cart_never_warms_homepage_on_add(monkeypatch):
    from bring_fast.stores import carrefour as api

    seen = []

    class _Sess(_FakeSession):
        pass

    def _cs(existing=None, warm=True, **_k):
        seen.append(warm)
        return existing or _Sess()

    monkeypatch.setattr(api, "chrome_session", _cs)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(api, "login", lambda e, p, **k: {"ok": True, "token": "t", "user_id": "u", "error": None})
    monkeypatch.setattr(api, "lite_cart", lambda **k: {"data": {"items": []}})
    monkeypatch.setattr(api, "add_item", lambda **k: {"ok": True})
    out = api.official_cart(email="a@b.c", password="x", action="add", items=[{"id": "743861", "qty": 1}])
    assert out["ok"] is True
    assert seen == [False]


def test_official_cart_list_respects_short_timeout(monkeypatch):
    from bring_fast.stores import carrefour as api

    seen = []
    _no_network(monkeypatch, api)
    monkeypatch.setattr(
        api,
        "lite_cart",
        lambda **k: seen.append(k.get("timeout")) or {"data": {"items": []}},
    )
    api.official_cart(
        email="a@b.c", password="x", action="list", items=[], session_token="t", session_user="u", timeout=4
    )
    assert seen == [4]


def test_official_cart_add_1592968_qty_2_lands(monkeypatch):
    from bring_fast.stores import carrefour as api

    cart = {"data": {"items": []}}
    _no_network(monkeypatch, api)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(
        api, "login", lambda e, p, **k: {"ok": True, "token": "t", "user_id": "u", "error": None}
    )
    monkeypatch.setattr(api, "lite_cart", lambda **k: cart)
    catalog = {
        "1592968": {
            "value": "Oasis Blu Sparkling Water, 1L Pack of 6",
            "data": {"id": "1592968", "price": 26.99},
        }
    }
    monkeypatch.setattr(api, "_cio_browse_ids", lambda ids: [catalog[i] for i in ids if i in catalog])
    monkeypatch.setattr(api, "_cio_search", lambda q: [catalog[q]] if q in catalog else [])

    def _add(**kw):
        assert kw["product_id"] == "1592968"
        assert kw["qty"] == 2
        cart["data"]["items"] = [
            {
                "id": "1592968",
                "name": "Oasis Blu Sparkling Water, 1L Pack of 6",
                "quantity": 2,
                "price": 26.99,
            }
        ]
        return {"ok": True}

    monkeypatch.setattr(api, "add_item", _add)
    out = api.official_cart(
        email="a@b.c",
        password="x",
        action="add",
        items=[{"id": "1592968", "name": "Oasis Blu Sparkling Water, 1L Pack of 6", "qty": 2}],
    )
    assert out["ok"] is True
    assert out["error"] is None
    assert out["items"][0]["id"] == "1592968"
    assert out["items"][0]["qty"] == 2
    assert out["items"][0]["price"] == 26.99
    assert "Oasis Blu" in out["items"][0]["name"]


def test_official_cart_restores_previous_lines_when_add_500_empties(monkeypatch):
    """POST /entries-style wipe: 4→0 after a failed Blu add must put the old lines back."""
    from bring_fast.stores import carrefour as api
    from bring_fast.stores.http import StoreAPIError

    previous = [
        {"id": "376161", "name": "Coca-Cola Original 330ml x6", "quantity": 1, "price": 14.99},
        {"id": "743861", "name": "Coca-Cola Zero 330ml Can", "quantity": 1, "price": 1.99},
    ]
    rows = list(previous)
    _no_network(monkeypatch, api)
    _patch_loc(monkeypatch, api)
    monkeypatch.setattr(
        api, "login", lambda e, p, **k: {"ok": True, "token": "t", "user_id": "u", "error": None}
    )
    monkeypatch.setattr(api, "lite_cart", lambda **k: {"data": {"items": list(rows)}})
    monkeypatch.setattr(api, "_cio_browse_ids", lambda ids: [])
    monkeypatch.setattr(api, "_cio_search", lambda q: [])

    def _add(**kw):
        pid = str(kw["product_id"])
        if pid == "1592968":
            rows.clear()
            raise StoreAPIError("Internal Server Error", status=500, maf_error="Internal Server Error")
        rows.append(
            {
                "id": pid,
                "name": kw.get("name") or pid,
                "quantity": int(kw.get("qty") or 1),
                "price": 1.99 if pid == "743861" else 14.99,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(api, "add_item", _add)
    out = api.official_cart(
        email="a@b.c",
        password="x",
        action="add",
        items=[{"id": "1592968", "name": "Oasis Blu Sparkling Water, 1L Pack of 6", "qty": 2}],
    )
    assert out["ok"] is False
    assert out["maf_error"] == "Internal Server Error"
    assert out.get("error_code") == "cart_restored"
    ids = {it["id"] for it in out["items"]}
    assert ids == {"376161", "743861"}
    assert "1592968" not in ids
    assert out["official_count"] == 2


def test_cart_was_emptied():
    from bring_fast.stores.carrefour import cart_was_emptied

    before = [{"id": "376161"}, {"id": "743861"}]
    assert cart_was_emptied(before, []) is True
    assert cart_was_emptied(before, [{"id": "1592968"}]) is False
    assert cart_was_emptied([], []) is False
