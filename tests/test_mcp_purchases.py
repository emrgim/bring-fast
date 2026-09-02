from datetime import date

from bring_fast import app as bf


def _buy(bf, user_id, invoice_no, day, items, retailer="carrefour", store_name=""):
    bf.purchases.upsert_invoice(
        user_id,
        {
            "retailer": retailer,
            "store_name": store_name or retailer,
            "invoice_no": invoice_no,
            "order_no": invoice_no,
            "invoice_date": day,
            "source": "test",
            "items": items,
        },
    )


def _coke():
    return {
        "name": "Coca-Cola Zero Sugar",
        "qty": 2,
        "unit_price": 6.0,
        "line_total": 12.0,
        "barcode": "5000112668209",
    }


def test_spend_report_week_and_month(bf):
    user = bf.db.create_user("spend@example.com", "secret1")
    _buy(bf, user["id"], "A", "2026-08-01", [_coke()], store_name="Carrefour Meaisem")
    _buy(
        bf,
        user["id"],
        "B",
        "2026-08-20",
        [{"name": "Milk", "qty": 1, "unit_price": 8.5, "line_total": 8.5, "barcode": "111"}],
        retailer="grandiose",
        store_name="Grandiose",
    )
    out = bf.purchases.spend_report(user["id"], range_key="1m", today=date(2026, 8, 24))
    assert out["currency"] == "AED"
    assert out["total"] == 20.5
    assert out["last_30_days"] == 20.5
    assert out["avg_per_week"] > 0
    stores = {r["store"]: r["spend"] for r in out["by_store"]}
    assert stores["Carrefour Meaisem"] == 12.0
    tool = json_call(bf, user, "bf_spend", {"range": "1m", "today": "2026-08-24"})
    assert tool["success"] is True
    assert tool["total"] == 20.5


def json_call(bf, user, name, args):
    import json

    return json.loads(bf._call_tool(user, name, args))


def test_most_expensive_uses_typical_unit(bf):
    user = bf.db.create_user("price@example.com", "secret1")
    _buy(bf, user["id"], "A", "2026-07-01", [_coke()])
    _buy(
        bf,
        user["id"],
        "B",
        "2026-07-08",
        [{"name": "Heineken Can 24x50CL", "qty": 1, "unit_price": 131.14, "line_total": 131.14, "barcode": "222"}],
        retailer="mmi",
    )
    _buy(
        bf,
        user["id"],
        "C",
        "2026-08-01",
        [{"name": "Heineken Can 24x50CL", "qty": 1, "unit_price": 131.14, "line_total": 131.14, "barcode": "222"}],
        retailer="mmi",
    )
    ranked = bf.purchases.ranked_products(user["id"], sort="unit_price", today=date(2026, 8, 24))
    assert ranked[0]["name"] == "Heineken Can 24x50CL"
    assert ranked[0]["typical_unit_aed"] == 131.14
    tool = json_call(bf, user, "bf_products", {"sort": "expensive", "limit": 1})
    assert tool["products"][0]["typical_unit_aed"] == 131.14


def test_shopping_list_marks_due_tomorrow(bf):
    user = bf.db.create_user("due@example.com", "secret1")
    item = {"name": "White Bread", "qty": 1, "unit_price": 4.0, "line_total": 4.0, "barcode": "333"}
    for i, day in enumerate(("2026-07-25", "2026-08-01", "2026-08-08", "2026-08-15", "2026-08-22")):
        _buy(bf, user["id"], f"B{i}", day, [item])
    today = date(2026, 8, 28)
    lst = bf.purchases.shopping_list(user["id"], horizon_days=7, today=today)
    bread = next(p for p in lst if p["name"] == "White Bread")
    assert bread["status"] in ("due_today", "due_tomorrow", "overdue")
    assert bread["score"] and bread["score"] > 0
    assert bread["reason"].startswith("regular_")
    stale = {"name": "Old Spice", "qty": 1, "unit_price": 3.0, "line_total": 3.0, "barcode": "444"}
    _buy(bf, user["id"], "C", "2024-01-01", [stale])
    _buy(bf, user["id"], "D", "2024-02-01", [stale])
    names = [p["name"] for p in bf.purchases.shopping_list(user["id"], horizon_days=400, today=today)]
    assert "Old Spice" not in names
    tool = json_call(bf, user, "bf_shopping_list", {"horizon_days": 7})
    assert tool["success"] is True


def test_product_lookup_next_due(bf):
    user = bf.db.create_user("when@example.com", "secret1")
    item = {"name": "Heineken Can 24x50CL", "qty": 1, "unit_price": 131.14, "line_total": 131.14, "barcode": "222"}
    _buy(bf, user["id"], "A", "2026-07-01", [item], retailer="mmi")
    _buy(bf, user["id"], "B", "2026-08-01", [item], retailer="mmi")
    hits = bf.purchases.find_products(user["id"], "heineken", today=date(2026, 8, 24))
    assert hits
    assert hits[0]["last_buy"] == "2026-08-01"
    assert hits[0]["next_due"]
    tool = json_call(bf, user, "bf_product", {"query": "Heineken"})
    assert tool["product"]["name"] == "Heineken Can 24x50CL"
    assert "category" in tool["product"]


def test_bf_product_recategorize(bf):
    from bring_fast.macro_categories import CHEESE, DAIRY

    user = bf.db.create_user("recat@example.com", "secret1")
    item = {
        "name": "Fresh Milk 2L",
        "qty": 1,
        "unit_price": 8.5,
        "line_total": 8.5,
        "barcode": "111222333",
    }
    _buy(bf, user["id"], "M1", "2026-08-01", [item])
    tool = json_call(bf, user, "bf_product", {"query": "milk", "category": CHEESE})
    assert tool["success"] is True
    assert tool["product"]["category"] == CHEESE
    key = bf.purchases.product_key("111222333", "Fresh Milk 2L")
    assert bf.purchases.get_product_meta(key)["macro_category"] == CHEESE

    bad = json_call(bf, user, "bf_product", {"query": "milk", "category": "fromage"})
    assert bad["success"] is False
    assert "Valid slugs" in bad["error"]

    # Overwrite cheese back to dairy.
    tool2 = json_call(bf, user, "bf_product", {"query": "milk", "category": DAIRY})
    assert tool2["success"] is True
    assert tool2["product"]["category"] == DAIRY


def test_bf_products_category_filter(bf):
    from bring_fast.macro_categories import CHEESE, SOFT_DRINKS

    user = bf.db.create_user("catfilter@example.com", "secret1")
    _buy(bf, user["id"], "A", "2026-08-01", [_coke()])
    _buy(
        bf,
        user["id"],
        "B",
        "2026-08-02",
        [{"name": "PRESIDENT BRI 200G", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "3228020232026"}],
    )
    tool = json_call(bf, user, "bf_products", {"category": CHEESE, "limit": 20})
    assert tool["success"] is True
    names = {p["name"] for p in tool["products"]}
    assert any("BRI" in n or "President" in n for n in names)
    assert not any("Coca" in n for n in names)
    for p in tool["products"]:
        assert p["category"] == CHEESE

    all_tool = json_call(bf, user, "bf_products", {"limit": 20})
    cats = {p["category"] for p in all_tool["products"]}
    assert CHEESE in cats
    assert SOFT_DRINKS in cats


def test_public_product_includes_category(bf):
    from datetime import date

    user = bf.db.create_user("pubcat@example.com", "secret1")
    _buy(bf, user["id"], "A", "2026-08-01", [_coke()])
    hits = bf.purchases.find_products(user["id"], "coca", today=date(2026, 8, 24))
    assert hits[0]["category"] == "soft_drinks"


def test_bf_product_ambiguous_query(bf):
    user = bf.db.create_user("ambig@example.com", "secret1")
    for i, name in enumerate(("Green Apple", "Red Apple")):
        _buy(
            bf,
            user["id"],
            f"A{i}",
            "2026-08-01",
            [{"name": name, "qty": 1, "unit_price": 5, "line_total": 5, "barcode": f"apple{i}"}],
        )
    tool = json_call(bf, user, "bf_product", {"query": "apple", "category": "fruit"})
    assert tool["success"] is False
    assert "candidates" in tool
    assert len(tool["candidates"]) >= 2


def test_purchase_tools_are_listed(bf):
    names = {t["name"] for t in bf.tools_catalog()}
    assert {"bf_spend", "bf_products", "bf_shopping_list", "bf_product", "bf_orders"} <= names
    assert {"x_me", "x_user_by_username", "x_user_posts", "x_mentions", "x_search", "x_post"} <= names
    who = next(t for t in bf.tools_catalog() if t["name"] == "bf_whoami")
    assert "recent official orders" not in who["description"]
    assert "bf_orders" in who["description"]


def test_last_month_is_previous_calendar_month(bf):
    user = bf.db.create_user("july@example.com", "secret1")
    _buy(bf, user["id"], "JUN", "2026-06-30", [_coke()])
    _buy(bf, user["id"], "JUL", "2026-07-15", [_coke()])
    _buy(bf, user["id"], "AUG", "2026-08-02", [_coke()])
    since, until, key = bf.purchases.window("last_month", end="2026-08-24")
    assert key == "last_month"
    assert since == "2026-07-01"
    assert until.isoformat() == "2026-07-31"
    out = bf.purchases.spend_report(user["id"], range_key="mese_scorso", today=date(2026, 8, 24))
    assert out["range"] == "last_month"
    assert out["total"] == 12.0
    assert len(out["orders"]) == 1
    assert out["orders"][0]["invoice_no"] == "JUL"
    orders = json_call(bf, user, "bf_orders", {"range": "last_month"})
    assert orders["success"] is True
    assert orders["total"] == 12.0
    assert orders["orders"][0]["items"][0]["name"] == "Coca-Cola Zero Sugar"
    spend = json_call(bf, user, "bf_spend", {"range": "last_month"})
    assert spend["total"] == 12.0

