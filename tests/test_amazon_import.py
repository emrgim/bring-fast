import json

from bring_fast import app as bf
from bring_fast import db


def test_retailer_for_domain():
    assert db.retailer_for_domain("amazon.it") == "amazon_it"
    assert db.retailer_for_domain("@amazon.it") == "amazon_it"
    assert db.retailer_for_domain("order-update@amazon.ae") == "amazon_ae"
    assert db.domain_for_retailer("amazon_ae") == "amazon.ae"
    assert db.retailer_for_domain("unknown.shop") is None


def test_bf_import_invoice_lands_in_orders_and_spend(bf):
    user = bf.db.create_user("import@example.com", "secret1")
    out = json.loads(
        bf._call_tool(
            user,
            "bf_import_invoice",
            {
                "retailer": "amazon_it",
                "invoice_no": "AMZ-IT-1001",
                "invoice_date": "2026-08-15",
                "store_name": "Amazon.it",
                "gmail_id": "gmail-abc",
                "items": [
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
    assert out["success"] is True
    assert out["invoice_id"]
    assert out["retailer"] == "amazon_it"

    orders = json.loads(bf._call_tool(user, "bf_orders", {"range": "all"}))
    assert any(o["invoice_no"] == "AMZ-IT-1001" for o in orders["orders"])

    spend = json.loads(bf._call_tool(user, "bf_spend", {"range": "all"}))
    assert spend["total"] == 139.99


def test_bf_import_invoice_rejects_unknown_retailer(bf):
    user = bf.db.create_user("bad@example.com", "secret1")
    out = json.loads(
        bf._call_tool(
            user,
            "bf_import_invoice",
            {
                "retailer": "not_a_store",
                "invoice_no": "X",
                "items": [{"name": "Thing", "qty": 1, "line_total": 1.0}],
            },
        )
    )
    assert out["success"] is False
    assert "unknown retailer" in out["error"]


def test_import_invoice_alias(bf):
    user = bf.db.create_user("alias@example.com", "secret1")
    out = json.loads(
        bf._call_tool(
            user,
            "import_invoice",
            {
                "retailer": "amazon_ae",
                "invoice_no": "AMZ-AE-9",
                "items": [{"name": "USB cable", "qty": 2, "line_total": 30.0}],
            },
        )
    )
    assert out["success"] is True
    assert out["retailer"] == "amazon_ae"
