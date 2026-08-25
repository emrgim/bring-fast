"""The receipts KPI counts every stored receipt, not just the ones the joins like."""

from datetime import date, timedelta

RECENT = (date.today() - timedelta(days=3)).isoformat()
OLD = (date.today() - timedelta(days=400)).isoformat()


def _seed(bf, email):
    user = bf.db.create_user(email, "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "recent-ok",
            "invoice_date": RECENT,
            "items": [{"name": "Rice", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "1"}],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "old-ok",
            "invoice_date": OLD,
            "items": [{"name": "Milk", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": "2"}],
        },
    )
    # Date parsing failed on this one: stored, but it can never sit in a date window.
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "grandiose",
            "invoice_no": "undated",
            "invoice_date": "",
            "items": [{"name": "Eggs", "qty": 1, "unit_price": 8, "line_total": 8, "barcode": "3"}],
        },
    )
    # Line items failed to parse: the invoice row exists with zero items.
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "grandiose",
            "invoice_no": "no-items",
            "invoice_date": RECENT,
            "items": [{"name": "  ", "qty": 1, "line_total": 0}],
        },
    )
    return user


def test_invoice_count_sees_itemless_and_undated_receipts(bf):
    user = _seed(bf, "count@example.com")

    assert bf.purchases.invoice_count(user["id"], include_undated=True) == 4
    since, until, _ = bf.purchases.resolve_window(user["id"], "all")
    assert bf.purchases.invoice_count(user["id"], since=since, until=until, include_undated=True) == 4
    since, until, _ = bf.purchases.resolve_window(user["id"], "1m")
    # The recent receipts, including the one whose items failed to parse.
    assert bf.purchases.invoice_count(user["id"], since=since, until=until) == 2


def test_snapshot_reports_range_and_total_receipts(bf):
    user = _seed(bf, "snap@example.com")

    since, until, _ = bf.purchases.resolve_window(user["id"], "all")
    snap = bf.purchases.spend_snapshot(user["id"], since=since, until=until, include_undated=True)
    assert snap["receipts"] == 4
    assert snap["receipts_total"] == 4

    since, until, _ = bf.purchases.resolve_window(user["id"], "1m")
    snap = bf.purchases.spend_snapshot(user["id"], since=since, until=until)
    assert snap["receipts"] == 2
    assert snap["receipts_total"] == 4


def test_dashboard_leads_with_the_average_and_skips_the_kpi_row(bf, client):
    """The only headline number on the dashboard is the per-grain average."""
    _seed(bf, "dash@example.com")
    client.post("/login", data={"email": "dash@example.com", "password": "secret1", "intent": "signin"})

    html = client.get("/dashboard?range=1m&grain=monthly").text
    assert "receipts</span>" not in html
    assert "spent</span>" not in html
    assert "Monthly average this period" in html


def test_purchases_page_counts_every_receipt_on_all(bf, client):
    _seed(bf, "purch@example.com")
    client.post("/login", data={"email": "purch@example.com", "password": "secret1", "intent": "signin"})

    html = client.get("/purchases?range=all").text
    assert "<b>4</b> receipts" in html

    ranged = client.get("/purchases?range=1m").text
    assert "<b>2</b> of 4 receipts" in ranged


def test_spend_report_counts_receipts_off_the_invoices_table(bf):
    user = _seed(bf, "mcp@example.com")

    report = bf.purchases.spend_report(user["id"], range_key="all")
    assert report["invoices"] == 4
    assert report["invoices_total"] == 4

    report = bf.purchases.spend_report(user["id"], range_key="1m")
    assert report["invoices"] == 2
    assert report["invoices_total"] == 4
