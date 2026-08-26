"""Hot-path speed and the bugs it used to hide: schema-on-connect, missing logos, undated first-buy."""

from pathlib import Path


def test_every_store_logo_is_served(client):
    from bring_fast import db

    for r in db.RETAILERS:
        page = client.get(r["logo"])
        assert page.status_code == 200, r["logo"]
        assert page.content, r["logo"]


def test_schema_is_not_rebuilt_on_every_query(bf, monkeypatch):
    calls = {"n": 0}
    real = bf.db._init_schema

    def wrapped(con):
        calls["n"] += 1
        return real(con)

    monkeypatch.setattr(bf.db, "_init_schema", wrapped)
    bf.db.connect().close()
    bf.db.connect().close()
    bf.db.get_user_by_email("nobody@example.com")
    assert calls["n"] == 0


def test_sqlite_uses_wal_and_invoice_indexes(bf):
    con = bf.db.connect()
    mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    con.close()
    assert mode.lower() == "wal"
    assert "idx_invoices_user_date" in names
    assert "idx_invoice_items_invoice" in names
    assert "idx_invoice_items_product" in names


def test_list_products_limit_is_the_top_spenders(bf):
    user = bf.db.create_user("limit@example.com", "secret1")
    for i in range(12):
        bf.purchases.upsert_invoice(
            user["id"],
            {
                "retailer": "carrefour",
                "invoice_no": f"n{i}",
                "invoice_date": "2026-08-10",
                "items": [
                    {
                        "name": f"Item {i}",
                        "qty": 1,
                        "unit_price": i + 1,
                        "line_total": i + 1,
                        "barcode": f"9{i:03d}",
                    }
                ],
            },
        )
    top = bf.purchases.list_products(user["id"], sort="spend", direction="desc", limit=8)
    assert len(top) == 8
    assert [p["name"] for p in top] == [f"Item {i}" for i in range(11, 3, -1)]
    assert len(bf.purchases.list_products(user["id"])) == 12


def test_undated_receipt_does_not_blank_first_buy(bf):
    user = bf.db.create_user("dates@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "dated",
            "invoice_date": "2026-08-10",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": "111"}],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "blank",
            "invoice_date": "",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": "111"}],
        },
    )
    milk = next(p for p in bf.purchases.list_products(user["id"]) if p["barcode"] == "111")
    assert milk["first_buy"] == "2026-08-10"
    assert milk["times_bought"] == 2


def test_unchanged_view_does_not_rewrite(bf):
    user = bf.db.create_user("view@example.com", "secret1")
    assert bf.db.set_last_view(user["id"], "/dashboard", "range=1m&grain=monthly") is True
    assert bf.db.set_last_view(user["id"], "/dashboard", "range=1m&grain=monthly") is False
    rec = bf.db.get_last_view(user["id"])
    assert rec["path"] == "/dashboard"
    assert "range=1m" in rec["query"]
    assert bf.db.set_last_view(user["id"], "/dashboard", "range=1y&grain=yearly") is True
    assert "range=1y" in bf.db.get_last_view(user["id"])["query"]


def test_dashboard_does_not_rescan_the_whole_shelf(bf, client):
    user = bf.db.create_user("dash@example.com", "secret1")
    for i in range(15):
        bf.purchases.upsert_invoice(
            user["id"],
            {
                "retailer": "carrefour",
                "invoice_no": f"d{i}",
                "invoice_date": "2026-08-10",
                "items": [
                    {
                        "name": f"Prod {i}",
                        "qty": 1,
                        "unit_price": 10 + i,
                        "line_total": 10 + i,
                        "barcode": f"8{i:03d}",
                    }
                ],
            },
        )
    client.post("/login", data={"email": "dash@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/dashboard?range=all&grain=monthly").text
    assert "Prod 14" in html
    assert "Prod 7" in html
    assert "Prod 0" not in html
    assert "Monthly average this period" in html
    assert Path(bf.db.__file__).resolve().parent.joinpath("static/logos/waitrose.svg").is_file()
