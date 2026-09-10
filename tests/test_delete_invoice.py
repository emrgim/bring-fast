import json

from bring_fast import app as bf


def _import(bf, user, invoice_no, retailer="amazon_it", items=None):
    return json.loads(
        bf._call_tool(
            user,
            "bf_import_invoice",
            {
                "retailer": retailer,
                "invoice_no": invoice_no,
                "invoice_date": "2026-08-15",
                "items": items
                or [
                    {
                        "name": "Kindle Paperwhite",
                        "qty": 1,
                        "unit_price": 139.99,
                        "line_total": 139.99,
                    }
                ],
            },
        )
    )


def test_delete_invoice_by_id(bf):
    user = bf.db.create_user("del-id@example.com", "secret1")
    imported = _import(bf, user, "DEL-1001")
    invoice_id = imported["invoice_id"]

    out = json.loads(bf._call_tool(user, "bf_delete_invoice", {"invoice_id": invoice_id}))
    assert out["success"] is True
    assert out["invoice_id"] == invoice_id
    assert out["invoice_no"] == "DEL-1001"
    assert out["retailer"] == "amazon_it"
    assert out["items_removed"] == 1

    orders = json.loads(bf._call_tool(user, "bf_orders", {"range": "all"}))
    assert not any(o["invoice_no"] == "DEL-1001" for o in orders["orders"])


def test_delete_invoice_by_retailer_and_invoice_no(bf):
    user = bf.db.create_user("del-pair@example.com", "secret1")
    _import(bf, user, "DEL-2002", retailer="amazon_ae")

    out = json.loads(
        bf._call_tool(
            user,
            "bf_delete_invoice",
            {"retailer": "amazon_ae", "invoice_no": "DEL-2002"},
        )
    )
    assert out["success"] is True
    assert out["invoice_no"] == "DEL-2002"
    assert out["retailer"] == "amazon_ae"
    assert out["items_removed"] == 1

    spend = json.loads(bf._call_tool(user, "bf_spend", {"range": "all"}))
    assert spend["total"] == 0.0


def test_delete_invoice_cannot_touch_another_user(bf):
    owner = bf.db.create_user("owner@example.com", "secret1")
    other = bf.db.create_user("other@example.com", "secret1")
    imported = _import(bf, owner, "OWN-9001")
    invoice_id = imported["invoice_id"]

    out = json.loads(bf._call_tool(other, "bf_delete_invoice", {"invoice_id": invoice_id}))
    assert out["success"] is False
    assert "not found" in out["error"].lower()

    out2 = json.loads(
        bf._call_tool(
            other,
            "bf_delete_invoice",
            {"retailer": "amazon_it", "invoice_no": "OWN-9001"},
        )
    )
    assert out2["success"] is False

    orders = json.loads(bf._call_tool(owner, "bf_orders", {"range": "all"}))
    assert any(o["invoice_no"] == "OWN-9001" for o in orders["orders"])


def test_delete_invoice_alias(bf):
    user = bf.db.create_user("alias-del@example.com", "secret1")
    imported = _import(bf, user, "ALIAS-1")
    out = json.loads(bf._call_tool(user, "delete_invoice", {"invoice_id": imported["invoice_id"]}))
    assert out["success"] is True


def test_delete_invoice_requires_one_shape(bf):
    user = bf.db.create_user("bad-del@example.com", "secret1")
    out = json.loads(bf._call_tool(user, "bf_delete_invoice", {}))
    assert out["success"] is False
    assert "invoice_id or retailer" in out["error"]


def test_bf_delete_invoice_is_listed(bf):
    names = {t["name"] for t in bf.tools_catalog()}
    assert "bf_delete_invoice" in names
