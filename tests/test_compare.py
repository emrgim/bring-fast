from bring_fast import compare


def _search(_retailer, query, _limit):
    if "5000112668209" in query or "Coca" in query:
        return {"results": [{"name": "Coca-Cola Zero 2L", "price": 6.5, "ean": "5000112668209"}]}
    return {"results": []}


def test_compare_board_marks_cheapest(bf):
    user = bf.db.create_user("cmp@example.com", "secret1")
    key = "ean:5000112668209"
    compare.record_quote(user["id"], key, "carrefour", {"price": 6.5, "found_name": "Coke", "sku": "1", "error": ""}, "manual")
    compare.record_quote(user["id"], key, "grandiose", {"price": 5.9, "found_name": "Coke", "sku": "1", "error": ""}, "manual")
    board = compare.compare_board(user["id"], key, paid=5.99)
    by = {r["id"]: r for r in board}
    assert by["grandiose"]["cheapest"] is True
    assert by["carrefour"]["dearest"] is True
    assert by["grandiose"]["vs_paid"] < 0
    assert by["waitrose"]["price"] is None


def test_reload_uses_live_search_stub(bf, client):
    user = bf.db.create_user("cmp2@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "c1",
            "invoice_date": "2026-08-23",
            "items": [{"name": "Coca-Cola Zero Sugar", "qty": 1, "unit_price": 5.99, "line_total": 5.99, "barcode": "5000112668209"}],
        },
    )
    compare.refresh_store(
        user["id"],
        "ean:5000112668209",
        "carrefour",
        ["5000112668209"],
        ["Coca-Cola Zero Sugar"],
        search=_search,
    )
    client.post("/login", data={"email": "cmp2@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/purchases/ean:5000112668209").text
    assert "Reload" in html
    assert "Grandiose" in html
    assert "Carrefour" in html
    assert "6.50" in html or "6.5" in html
