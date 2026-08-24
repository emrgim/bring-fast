"""Grandiose official cart uses Magento REST + GraphQL, not Chrome."""

from bring_fast.stores.grandiose import official_cart, parse_items


def test_parse_cart_items():
    items = parse_items(
        {
            "items": [
                {
                    "id": "9",
                    "quantity": 1,
                    "product": {"sku": "5960000001030", "name": "Barilla Spaghetti No. 5"},
                    "prices": {"price": {"value": 15.75}},
                }
            ]
        }
    )
    assert items[0]["id"] == "5960000001030"
    assert items[0]["qty"] == 1


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

    monkeypatch.setattr(api, "login", lambda e, p: {"ok": True, "token": "t", "user_id": "u", "error": None})
    monkeypatch.setattr(api, "ensure_delivery_area", lambda: {"area_name": "IMPZ", "inventory_source": "DBSCPC"})
    monkeypatch.setattr(api, "out_of_stock_skus", lambda token, cart_id: set())
    monkeypatch.setattr(
        api,
        "customer_cart",
        lambda token: {
            "id": "c1",
            "items": [
                {
                    "id": "1",
                    "quantity": 1,
                    "product": {"sku": "8004690051573", "name": "La Molisana Spaghetti No.15"},
                    "prices": {"price": {"value": 14.65}},
                }
            ],
        },
    )
    out = official_cart(email="a@b.c", password="x", action="list", items=[])
    assert out["ok"] is True
    assert out["driver"] == "magento"
    assert out["items"][0]["name"] == "La Molisana Spaghetti No.15"
    assert out["items"][0]["available"] is True
    assert out["official_count"] == 1


def test_official_checkout_empty_cart(monkeypatch):
    from bring_fast.stores import grandiose as api

    monkeypatch.setattr(api, "login", lambda e, p: {"ok": True, "token": "t", "user_id": "u", "error": None})
    monkeypatch.setattr(api, "ensure_delivery_area", lambda: {"area_name": "IMPZ"})
    monkeypatch.setattr(api, "customer_cart", lambda token: {"id": "c1", "items": []})
    out = api.official_checkout(email="a@b.c", password="x")
    assert out["ok"] is False
    assert "empty" in (out.get("error") or "").lower()
