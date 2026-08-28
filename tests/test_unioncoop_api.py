"""Union Coop Magento REST cart. GraphQL is Varnish-blocked; tests never hit the live host."""

from bring_fast.stores import unioncoop as api

COKE_SKU = "5000112668209"


class _Resp:
    def __init__(self, status, body, text=""):
        self.status_code = status
        self._body = body
        self.text = text or (body if isinstance(body, str) else "")
        self.headers = {"content-type": "application/json"}

    def json(self):
        if isinstance(self._body, (dict, list, str, bool, int, float)):
            return self._body
        raise ValueError("no json")


def _cart(items=None):
    rows = items
    if rows is None:
        rows = [
            {"item_id": 11, "sku": "6291021213119", "name": "Blu Sparkling Water 1L", "qty": 24, "price": 3.5, "quote_id": "7"},
            {"item_id": 22, "sku": COKE_SKU, "name": "Coca-Cola Zero Calories", "qty": 2, "price": 2.75, "quote_id": "7"},
            {"item_id": 33, "sku": "5283003399547", "name": "Master Kettle Cooked Salt Potato Chips", "qty": 1, "price": 6.0, "quote_id": "7"},
        ]
    return {"id": 7, "items": [dict(r) for r in rows], "items_count": len(rows), "grand_total": 20}


def _patch(monkeypatch, cart=None):
    state = {"cart": cart if cart is not None else _cart(), "calls": []}

    class _Fake:
        def get(self, url, **kwargs):
            state["calls"].append(("GET", url, None))
            return _Resp(200, "<html>ok</html>", "<html>ok</html>")

        def request(self, method, url, json=None, headers=None, timeout=20):
            state["calls"].append((method, url, json))
            path = url.split("/rest/V1")[-1] if "/rest/V1" in url else url
            if method == "POST" and path.endswith("/integration/customer/token"):
                return _Resp(200, "tok")
            if method == "GET" and path.endswith("/customers/me"):
                return _Resp(
                    200,
                    {
                        "id": 5,
                        "email": "a@b.c",
                        "addresses": [
                            {
                                "id": 1,
                                "firstname": "E",
                                "lastname": "M",
                                "street": ["Element"],
                                "city": "Dubai",
                                "default_shipping": True,
                            }
                        ],
                    },
                )
            if method == "GET" and path.endswith("/carts/mine"):
                return _Resp(200, state["cart"])
            if method == "POST" and path.endswith("/carts/mine/items"):
                sku = ((json or {}).get("cartItem") or {}).get("sku")
                qty = int(((json or {}).get("cartItem") or {}).get("qty") or 1)
                state["cart"]["items"].append(
                    {"item_id": 99, "sku": sku, "name": sku, "qty": qty, "price": 1, "quote_id": "7"}
                )
                return _Resp(200, {"item_id": 99, "sku": sku})
            if method == "PUT" and "/carts/mine/items/" in path:
                item_id = int(path.rstrip("/").rsplit("/", 1)[-1])
                qty = int(((json or {}).get("cartItem") or {}).get("qty") or 1)
                for it in state["cart"]["items"]:
                    if int(it["item_id"]) == item_id:
                        it["qty"] = qty
                return _Resp(200, {"item_id": item_id})
            if method == "DELETE" and "/carts/mine/items/" in path:
                item_id = path.rstrip("/").rsplit("/", 1)[-1]
                state["cart"]["items"] = [it for it in state["cart"]["items"] if str(it["item_id"]) != str(item_id)]
                # Magento REST DELETE returns JSON `true` (boolean).
                return _Resp(200, True, "true")
            return _Resp(404, {"message": f"unhandled {method} {path}"})

    monkeypatch.setattr(api, "_client", lambda: _Fake())
    monkeypatch.setattr(api, "login", lambda e, p: {"ok": True, "token": "tok", "user_id": "5", "error": None})
    return state


def test_parse_rest_items():
    items = api.parse_items(_cart())
    assert items[1]["id"] == COKE_SKU
    assert items[1]["item_id"] == "22"
    assert items[1]["qty"] == 2


def test_list_after_login(monkeypatch):
    _patch(monkeypatch)
    out = api.official_cart(email="a@b.c", password="x", action="list", items=[])
    assert out["ok"] is True
    assert out["driver"] == "magento-rest"
    assert out["official_count"] == 3
    assert out["items"][1]["name"] == "Coca-Cola Zero Calories"


def test_remove_by_name_when_sku_differs(monkeypatch):
    _patch(monkeypatch)
    out = api.official_cart(
        email="a@b.c",
        password="x",
        action="remove",
        items=[{"id": "not-the-cart-sku", "name": "Coca-Cola"}],
    )
    assert out["ok"] is True
    skus = [i["id"] for i in out["items"]]
    assert COKE_SKU not in skus
    assert "6291021213119" in skus


def test_remove_missing_sku_is_not_ok_and_item_stays(monkeypatch):
    _patch(monkeypatch)
    out = api.official_cart(email="a@b.c", password="x", action="remove", items=[{"id": "0000000000000"}])
    assert out["ok"] is False
    assert "not in the official union coop cart" in (out.get("error") or "").lower()
    assert COKE_SKU in [i["id"] for i in out["items"]]


def test_remove_coca_cola_hits_zero_calories(monkeypatch):
    state = _patch(monkeypatch)
    out = api.official_cart(email="a@b.c", password="x", action="remove", items=[{"name": "togli la Coca-Cola"}])
    assert out["ok"] is True
    assert COKE_SKU not in [i["id"] for i in out["items"]]
    deletes = [c for c in state["calls"] if c[0] == "DELETE"]
    assert any(c[1].endswith("/carts/mine/items/22") for c in deletes)


def test_remove_by_sku(monkeypatch):
    _patch(monkeypatch)
    out = api.official_cart(email="a@b.c", password="x", action="remove", items=[{"id": COKE_SKU}])
    assert out["ok"] is True
    assert COKE_SKU not in [i["id"] for i in out["items"]]


def test_remove_by_item_id(monkeypatch):
    _patch(monkeypatch)
    out = api.official_cart(email="a@b.c", password="x", action="remove", items=[{"item_id": "22"}])
    assert out["ok"] is True
    assert COKE_SKU not in [i["id"] for i in out["items"]]


def test_remove_empty_200_body_is_success(monkeypatch):
    state = _patch(monkeypatch)
    orig = api._client

    class _EmptyDelete:
        def get(self, url, **kwargs):
            return orig().get(url, **kwargs)

        def request(self, method, url, json=None, headers=None, timeout=20):
            resp = orig().request(method, url, json=json, headers=headers, timeout=timeout)
            if method == "DELETE":
                return _Resp(200, None, "")
            return resp

    monkeypatch.setattr(api, "_client", lambda: _EmptyDelete())
    out = api.official_cart(email="a@b.c", password="x", action="remove", items=[{"id": COKE_SKU}])
    assert out["ok"] is True
    assert COKE_SKU not in [i["id"] for i in out["items"]]
    assert any(c[0] == "DELETE" for c in state["calls"])


def test_remove_is_not_ok_if_line_still_there(monkeypatch):
    _patch(monkeypatch)
    orig = api._client

    class _NoopDelete:
        def get(self, url, **kwargs):
            return orig().get(url, **kwargs)

        def request(self, method, url, json=None, headers=None, timeout=20):
            if method == "DELETE":
                return _Resp(200, True, "true")
            return orig().request(method, url, json=json, headers=headers, timeout=timeout)

    monkeypatch.setattr(api, "_client", lambda: _NoopDelete())
    out = api.official_cart(email="a@b.c", password="x", action="remove", items=[{"id": COKE_SKU}])
    assert out["ok"] is False
    assert "was not removed" in (out.get("error") or "").lower()
    assert COKE_SKU in [i["id"] for i in out["items"]]


def test_set_updates_qty(monkeypatch):
    _patch(monkeypatch)
    out = api.official_cart(email="a@b.c", password="x", action="set", items=[{"name": "Coca-Cola", "qty": 1}])
    assert out["ok"] is True
    coke = next(i for i in out["items"] if i["id"] == COKE_SKU)
    assert coke["qty"] == 1


def test_set_missing_line_is_not_ok(monkeypatch):
    _patch(monkeypatch)
    out = api.official_cart(email="a@b.c", password="x", action="set", items=[{"name": "Diet Sprite", "qty": 1}])
    assert out["ok"] is False
    assert "not in the official union coop cart" in (out.get("error") or "").lower()
    assert COKE_SKU in [i["id"] for i in out["items"]]


def test_add_appends_sku(monkeypatch):
    _patch(monkeypatch)
    out = api.official_cart(email="a@b.c", password="x", action="add", items=[{"id": "5960000001030", "qty": 2}])
    assert out["ok"] is True
    assert "5960000001030" in [i["id"] for i in out["items"]]


def test_clear_empties(monkeypatch):
    _patch(monkeypatch)
    out = api.official_cart(email="a@b.c", password="x", action="clear", items=[])
    assert out["ok"] is True
    assert out["items"] == []


def test_search_parses_algolia_hits():
    hits = api.algolia_hits_to_results(
        [
            {
                "sku": COKE_SKU,
                "objectID": "1",
                "name": "Coca-Cola Zero Calories",
                "url": "https://www.unioncoop.ae/coca-cola-zero",
                "price": {"AED": {"default": 2.75}},
            }
        ]
    )
    assert hits[0]["id"] == COKE_SKU
    assert hits[0]["price"] == 2.75


def test_search_uses_algolia_not_graphql(monkeypatch):
    class _Sess:
        def __init__(self):
            self.headers = {}

        def get(self, url, **kwargs):
            html = 'window.algoliaConfig = {"applicationId":"XOC07JLE5W","apiKey":"searchkey","indexName":"ucprod_english"};'
            r = _Resp(200, html, html)
            r.raise_for_status = lambda: None
            return r

        def post(self, url, **kwargs):
            assert "algolia.net" in url
            assert "graphql" not in url
            r = _Resp(200, {"hits": [{"sku": COKE_SKU, "name": "Coca-Cola Zero Calories", "price": {"AED": {"default": 2.75}}}]})
            r.raise_for_status = lambda: None
            return r

    monkeypatch.setattr(api.requests, "Session", _Sess)
    out = api.search("Coca-Cola", 4)
    assert out["driver"] == "algolia"
    assert out["results"][0]["id"] == COKE_SKU


def test_checkout_prepare_empty(monkeypatch):
    _patch(monkeypatch, cart=_cart(items=[]))
    monkeypatch.setattr(api, "login", lambda e, p: {"ok": True, "token": "tok", "user_id": "5", "error": None})
    out = api.official_checkout(email="a@b.c", password="x")
    assert out["ok"] is False
    assert "empty" in (out.get("error") or "").lower()


def test_checkout_prepare_does_not_place(monkeypatch):
    _patch(monkeypatch)
    out = api.official_checkout(email="a@b.c", password="x")
    assert out["ok"] is True
    assert out["placed"] is False
    assert out["payment_completed"] is False
    assert out["checkout_url"] == "https://www.unioncoop.ae/checkout/"
    assert "no order is placed" in (out.get("what_happens") or "").lower()


def test_login_posts_magento_customer_token(monkeypatch):
    seen = []

    class _Fake:
        def get(self, url, **kwargs):
            return _Resp(200, "<html>", "<html>")

        def request(self, method, url, json=None, headers=None, timeout=20):
            seen.append((method, url, json))
            if "customer/token" in url:
                return _Resp(200, "abc-token")
            if url.endswith("/customers/me"):
                return _Resp(200, {"id": 9, "email": "a@b.c"})
            return _Resp(404, {"message": "no"})

    monkeypatch.setattr(api, "_client", lambda: _Fake())
    out = api.login("a@b.c", "secret")
    assert out["ok"] is True
    assert out["token"] == "abc-token"
    assert any("integration/customer/token" in u for _, u, _ in seen)
