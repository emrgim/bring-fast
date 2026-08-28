"""Grandiose official Magento GraphQL cart. No live writes, no Chrome."""

from bring_fast.stores.grandiose import official_cart, parse_items
from bring_fast.stores.http import StoreAPIError

COKE_SKU = "5000112668209"
WATER_SKU = "6291021213119"
CHIPS_SKU = "5283003399547"


def _raw_cart(items=None):
    rows = items
    if rows is None:
        rows = [
            {
                "uid": "MTIxMTgyODQ=",
                "id": "12118284",
                "quantity": 24,
                "product": {"sku": WATER_SKU, "name": "Blu Sparkling Water 1L"},
                "prices": {"price": {"value": 3.5}},
            },
            {
                "uid": "MTIxMTU2OTA=",
                "id": "12115690",
                "quantity": 2,
                "product": {"sku": COKE_SKU, "name": "Coca-Cola Zero Calories"},
                "prices": {"price": {"value": 2.75}},
            },
            {
                "uid": "MTIxMTIzMTI=",
                "id": "12112312",
                "quantity": 1,
                "product": {"sku": CHIPS_SKU, "name": "Master Kettle Cooked Salt Potato Chips"},
                "prices": {"price": {"value": 6.0}},
            },
        ]
    return {"id": "c1", "items": [dict(r) for r in rows], "total_quantity": sum(int(r["quantity"]) for r in rows)}


def _patch_session(monkeypatch, api, cart):
    state = {"cart": cart, "calls": []}

    monkeypatch.setattr(api, "login", lambda e, p: {"ok": True, "token": "t", "user_id": "u", "error": None})
    monkeypatch.setattr(api, "ensure_delivery_area", lambda: {"area_name": "IMPZ", "inventory_source": "DBSCPC"})
    monkeypatch.setattr(api, "out_of_stock_skus", lambda token, cart_id: set())
    monkeypatch.setattr(api, "customer_cart", lambda token: state["cart"])

    def _gql(token, query, variables=None):
        state["calls"].append((query, variables or {}))
        q = " ".join(query.split())
        cart = state["cart"]
        if "removeItemFromCart" in q:
            uid = str((variables or {}).get("itemUid") or "")
            item_id = (variables or {}).get("itemId")
            kept = []
            for it in cart["items"]:
                if uid and str(it.get("uid")) == uid:
                    continue
                if item_id is not None and str(it.get("id")) == str(item_id):
                    continue
                kept.append(it)
            state["cart"] = {"id": "c1", "items": kept, "total_quantity": sum(int(i["quantity"]) for i in kept)}
            return {"data": {"removeItemFromCart": {"cart": state["cart"]}}}
        if "updateCartItems" in q:
            uid = str((variables or {}).get("itemUid") or "")
            qty = (variables or {}).get("qty")
            for it in cart["items"]:
                if str(it.get("uid")) == uid:
                    it["quantity"] = int(qty)
            state["cart"] = {"id": "c1", "items": cart["items"], "total_quantity": sum(int(i["quantity"]) for i in cart["items"])}
            return {"data": {"updateCartItems": {"cart": state["cart"]}}}
        if "addProductsToCart" in q:
            sku = (variables or {}).get("sku")
            qty = int((variables or {}).get("qty") or 1)
            cart["items"].append(
                {
                    "uid": "new-uid",
                    "id": "9",
                    "quantity": qty,
                    "product": {"sku": sku, "name": sku},
                    "prices": {"price": {"value": 1}},
                }
            )
            state["cart"] = cart
            return {"data": {"addProductsToCart": {"cart": cart, "user_errors": []}}}
        if "customerCart" in q or "query { customer" in q.lower():
            return {"data": {"customerCart": cart, "customer": {"id": "u"}}}
        return {"data": {}}

    monkeypatch.setattr(api, "graphql", _gql)
    return state


def test_parse_cart_items():
    items = parse_items(
        {
            "items": [
                {
                    "id": "9",
                    "uid": "OQ==",
                    "quantity": 1,
                    "product": {"sku": "5960000001030", "name": "Barilla Spaghetti No. 5"},
                    "prices": {"price": {"value": 15.75}},
                }
            ]
        }
    )
    assert items[0]["id"] == "5960000001030"
    assert items[0]["qty"] == 1
    assert items[0]["item_id"] == "9"
    assert items[0]["uid"] == "OQ=="


def test_official_cart_refuses_unavailable_add(monkeypatch):
    from bring_fast.stores import grandiose as api

    monkeypatch.setattr(api, "login", lambda e, p: {"ok": True, "token": "t", "user_id": "u", "error": None})
    monkeypatch.setattr(api, "ensure_delivery_area", lambda: {"area_name": "IMPZ", "inventory_source": "DBSCPC"})
    monkeypatch.setattr(
        api,
        "availability",
        lambda sku, qty=1: {"sku": sku, "name": "Barilla", "available": False, "area": "IMPZ"},
    )
    monkeypatch.setattr(api, "customer_cart", lambda token: {"id": "c1", "items": [], "total_quantity": 0})
    out = official_cart(email="a@b.c", password="x", action="add", items=[{"id": "5960000001030", "qty": 1}])
    assert out["ok"] is False
    assert out["items"] == []
    assert "out of stock" in (out.get("error") or "").lower()


def test_official_cart_list_after_login(monkeypatch):
    from bring_fast.stores import grandiose as api

    _patch_session(monkeypatch, api, _raw_cart())
    out = official_cart(email="a@b.c", password="x", action="list", items=[])
    assert out["ok"] is True
    assert out["driver"] == "magento"
    assert out["items"][1]["name"] == "Coca-Cola Zero Calories"
    assert out["items"][1]["id"] == COKE_SKU
    assert out["official_count"] == 3


def test_remove_by_name_when_catalog_sku_differs(monkeypatch):
    from bring_fast.stores import grandiose as api

    state = _patch_session(monkeypatch, api, _raw_cart())
    out = official_cart(
        email="a@b.c",
        password="x",
        action="remove",
        items=[{"id": "catalog-can-sku-not-in-cart", "name": "Coca-Cola", "qty": 0}],
    )
    assert out["ok"] is True
    skus = [i["id"] for i in out["items"]]
    assert COKE_SKU not in skus
    assert WATER_SKU in skus
    assert CHIPS_SKU in skus
    queries = " ".join(c[0] for c in state["calls"])
    assert "cart_item_uid" in queries
    assert any(c[1].get("itemUid") == "MTIxMTU2OTA=" for c in state["calls"])


def test_remove_missing_sku_is_not_ok_and_coke_stays(monkeypatch):
    from bring_fast.stores import grandiose as api

    _patch_session(monkeypatch, api, _raw_cart())
    out = official_cart(
        email="a@b.c",
        password="x",
        action="remove",
        items=[{"id": "0000000000000", "name": "", "qty": 0}],
    )
    assert out["ok"] is False
    assert "not in the official grandiose cart" in (out.get("error") or "").lower()
    skus = [i["id"] for i in out["items"]]
    assert COKE_SKU in skus
    assert WATER_SKU in skus


def test_remove_coca_cola_hits_zero_calories(monkeypatch):
    from bring_fast.stores import grandiose as api

    _patch_session(monkeypatch, api, _raw_cart())
    out = official_cart(email="a@b.c", password="x", action="remove", items=[{"name": "togli la Coca-Cola"}])
    assert out["ok"] is True
    assert COKE_SKU not in [i["id"] for i in out["items"]]
    assert out["official_count"] == 2


def test_remove_by_sku(monkeypatch):
    from bring_fast.stores import grandiose as api

    _patch_session(monkeypatch, api, _raw_cart())
    out = official_cart(email="a@b.c", password="x", action="remove", items=[{"id": COKE_SKU}])
    assert out["ok"] is True
    assert COKE_SKU not in [i["id"] for i in out["items"]]


def test_remove_by_item_id(monkeypatch):
    from bring_fast.stores import grandiose as api

    _patch_session(monkeypatch, api, _raw_cart())
    out = official_cart(email="a@b.c", password="x", action="remove", items=[{"item_id": "12115690"}])
    assert out["ok"] is True
    assert COKE_SKU not in [i["id"] for i in out["items"]]


def test_remove_is_not_ok_if_line_still_there(monkeypatch):
    from bring_fast.stores import grandiose as api

    state = _patch_session(monkeypatch, api, _raw_cart())

    def _noop(token, query, variables=None):
        state["calls"].append((query, variables or {}))
        return {"data": {"removeItemFromCart": {"cart": state["cart"]}}}

    monkeypatch.setattr(api, "graphql", _noop)
    out = official_cart(email="a@b.c", password="x", action="remove", items=[{"id": COKE_SKU}])
    assert out["ok"] is False
    assert "was not removed" in (out.get("error") or "").lower()
    assert COKE_SKU in [i["id"] for i in out["items"]]


def test_set_updates_qty_on_live_line(monkeypatch):
    from bring_fast.stores import grandiose as api

    _patch_session(monkeypatch, api, _raw_cart())
    out = official_cart(email="a@b.c", password="x", action="set", items=[{"name": "Coca-Cola", "qty": 1}])
    assert out["ok"] is True
    coke = next(i for i in out["items"] if i["id"] == COKE_SKU)
    assert coke["qty"] == 1


def test_set_missing_line_is_not_ok(monkeypatch):
    from bring_fast.stores import grandiose as api

    _patch_session(monkeypatch, api, _raw_cart())
    out = official_cart(email="a@b.c", password="x", action="set", items=[{"name": "Diet Sprite", "qty": 1}])
    assert out["ok"] is False
    assert "not in the official grandiose cart" in (out.get("error") or "").lower()
    assert COKE_SKU in [i["id"] for i in out["items"]]


def test_add_appends_sku(monkeypatch):
    from bring_fast.stores import grandiose as api

    state = _patch_session(monkeypatch, api, _raw_cart())
    monkeypatch.setattr(
        api,
        "availability",
        lambda sku, qty=1: {"sku": sku, "name": "Barilla", "available": True, "area": "IMPZ"},
    )
    out = official_cart(email="a@b.c", password="x", action="add", items=[{"id": "5960000001030", "qty": 1}])
    assert out["ok"] is True
    assert "5960000001030" in [i["id"] for i in out["items"]]
    assert any("addProductsToCart" in c[0] for c in state["calls"])


def test_clear_empties_the_cart(monkeypatch):
    from bring_fast.stores import grandiose as api

    _patch_session(monkeypatch, api, _raw_cart())
    out = official_cart(email="a@b.c", password="x", action="clear", items=[])
    assert out["ok"] is True
    assert out["items"] == []
    assert out["official_count"] == 0


def test_search_returns_catalog_hits(monkeypatch):
    from bring_fast.stores import grandiose as api

    monkeypatch.setattr(api, "ensure_delivery_area", lambda: {"area_name": "IMPZ", "inventory_source": "DBSCPC"})
    monkeypatch.setattr(api, "_pdp_in_stock", lambda entity_id: True)

    def _gql(token, query, variables=None):
        return {
            "data": {
                "products": {
                    "items": [
                        {
                            "id": 1,
                            "sku": COKE_SKU,
                            "name": "Coca-Cola Zero Calories",
                            "url_key": "coca-cola-zero",
                            "price_range": {"minimum_price": {"regular_price": {"value": 2.75, "currency": "AED"}}},
                        }
                    ]
                }
            }
        }

    monkeypatch.setattr(api, "graphql", _gql)
    out = api.search("Coca-Cola", 5)
    assert out["results"][0]["id"] == COKE_SKU
    assert out["results"][0]["name"] == "Coca-Cola Zero Calories"


def test_official_checkout_empty_cart(monkeypatch):
    from bring_fast.stores import grandiose as api

    monkeypatch.setattr(api, "login", lambda e, p: {"ok": True, "token": "t", "user_id": "u", "error": None})
    monkeypatch.setattr(api, "ensure_delivery_area", lambda: {"area_name": "IMPZ"})
    monkeypatch.setattr(api, "customer_cart", lambda token: {"id": "c1", "items": []})
    out = api.official_checkout(email="a@b.c", password="x")
    assert out["ok"] is False
    assert "empty" in (out.get("error") or "").lower()


def test_official_checkout_prepare_does_not_place(monkeypatch):
    from bring_fast.stores import grandiose as api

    cart = _raw_cart()
    monkeypatch.setattr(api, "login", lambda e, p: {"ok": True, "token": "t", "user_id": "u", "error": None})
    monkeypatch.setattr(api, "ensure_delivery_area", lambda: {"area_name": "IMPZ"})
    monkeypatch.setattr(api, "customer_cart", lambda token: cart)
    monkeypatch.setattr(
        api,
        "customer_addresses",
        lambda token: [{"id": 1, "firstname": "E", "lastname": "M", "street": ["Element"], "city": "Dubai", "default_shipping": True}],
    )

    def _gql(token, query, variables=None):
        if "cart(cart_id" in query:
            return {
                "data": {
                    "cart": {
                        **cart,
                        "available_payment_methods": [{"code": "checkmo", "title": "Check"}],
                        "shipping_addresses": [{}],
                        "prices": {"grand_total": {"value": 10, "currency": "AED"}},
                    }
                }
            }
        return {"data": {}}

    monkeypatch.setattr(api, "graphql", _gql)
    out = api.official_checkout(email="a@b.c", password="x")
    assert out["ok"] is True
    assert out["placed"] is False
    assert out["payment_completed"] is False
    assert "grandiose.ae" in (out.get("checkout_url") or "")
    assert "no order is placed" in (out.get("what_happens") or "").lower()
