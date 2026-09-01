"""Macro-category filter on Home and Buys (All chip dropdown)."""

from bring_fast.macro_categories import CHEESE, DAIRY, MEAT, WATER
from bring_fast import purchases


def _buy(bf, user_id, no, day, items, retailer="carrefour"):
    bf.purchases.upsert_invoice(
        user_id,
        {
            "retailer": retailer,
            "invoice_no": no,
            "invoice_date": day,
            "items": items,
        },
    )


def _tag(bf, key, macro):
    bf.purchases.upsert_product_meta(key, {"macro_category": macro, "source": "test"})


def test_all_means_no_macro_filter(bf):
    user = bf.db.create_user("allmacro@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "mix",
        "2026-08-10",
        [
            {"name": "Brie", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "c1"},
            {"name": "Steak", "qty": 1, "unit_price": 20, "line_total": 20, "barcode": "m1"},
        ],
    )
    cheese_key = purchases.product_key("c1", "Brie")
    meat_key = purchases.product_key("m1", "Steak")
    _tag(bf, cheese_key, CHEESE)
    _tag(bf, meat_key, MEAT)
    rows = purchases.list_products(user["id"])
    assert len(rows) == 2
    assert purchases.normalize_categories(None) == []
    assert purchases.normalize_categories("") == []


def test_selecting_cheese_filters_products_and_spend(bf, client):
    user = bf.db.create_user("cheese@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "mix",
        "2026-08-10",
        [
            {"name": "Brie", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "c1"},
            {"name": "Steak", "qty": 1, "unit_price": 20, "line_total": 20, "barcode": "m1"},
        ],
    )
    _tag(bf, purchases.product_key("c1", "Brie"), CHEESE)
    _tag(bf, purchases.product_key("m1", "Steak"), MEAT)

    filtered = purchases.list_products(user["id"], categories=[CHEESE])
    assert len(filtered) == 1
    assert filtered[0]["name"] == "Brie"
    days = purchases.daily_spend(user["id"], categories=[CHEESE])
    assert sum(d["spend"] for d in days) == 10

    client.post("/login", data={"email": "cheese@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/dashboard", params={"range": "all", "grain": "daily", "category": CHEESE}).text
    assert "AED 10.00" in html
    assert "Brie" in html
    assert "Steak" not in html
    assert "All · 1" in html
    assert 'id="category-panel"' in html
    assert 'class="store-panel"' in html
    assert "Cheese" in html
    assert "Clear all" in html
    assert ">Done<" in html


def test_multi_select_categories(bf):
    user = bf.db.create_user("multi@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "mix",
        "2026-08-10",
        [
            {"name": "Brie", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "c1"},
            {"name": "Milk", "qty": 1, "unit_price": 8, "line_total": 8, "barcode": "d1"},
            {"name": "Steak", "qty": 1, "unit_price": 20, "line_total": 20, "barcode": "m1"},
        ],
    )
    _tag(bf, purchases.product_key("c1", "Brie"), CHEESE)
    _tag(bf, purchases.product_key("d1", "Milk"), DAIRY)
    _tag(bf, purchases.product_key("m1", "Steak"), MEAT)

    rows = purchases.list_products(user["id"], categories=[CHEESE, DAIRY])
    assert {r["name"] for r in rows} == {"Brie", "Milk"}
    assert purchases.category_query([CHEESE, DAIRY]) == "cheese,dairy"


def test_edible_and_cheese_intersection(bf, client):
    user = bf.db.create_user("edcheese@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "mix",
        "2026-08-10",
        [
            {"name": "Brie", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "c1"},
            {"name": "Oasis Blu Sparkling Water", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": "w1"},
        ],
    )
    _tag(bf, purchases.product_key("c1", "Brie"), CHEESE)
    _tag(bf, purchases.product_key("w1", "Oasis Blu Sparkling Water"), WATER)

    rows = purchases.list_products(user["id"], dept="Edible", categories=[CHEESE])
    assert len(rows) == 1
    assert rows[0]["name"] == "Brie"

    client.post("/login", data={"email": "edcheese@example.com", "password": "secret1", "intent": "signin"})
    html = client.get(
        "/purchases",
        params={"range": "all", "grain": "daily", "dept": "Edible", "category": CHEESE},
    ).text
    assert "Brie" in html
    assert "Oasis" not in html
    assert "AED 10" in html


def test_category_query_string_round_trip(bf, client):
    user = bf.db.create_user("round@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "a",
        "2026-08-10",
        [{"name": "Brie", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "c1"}],
    )
    _tag(bf, purchases.product_key("c1", "Brie"), CHEESE)
    client.post("/login", data={"email": "round@example.com", "password": "secret1", "intent": "signin"})

    comma = client.get("/purchases", params={"category": "cheese,dairy"})
    assert comma.status_code == 200
    assert "category=cheese,dairy" in comma.text or 'value="cheese,dairy"' in comma.text

    repeat = client.get("/purchases", params=[("category", "cheese"), ("category", "dairy")])
    assert repeat.status_code == 200
    assert "All · 2" in repeat.text

    client.get("/purchases", params={"sort": "name", "dir": "asc", "category": "cheese"})
    r = client.get("/purchases", follow_redirects=False)
    assert r.status_code == 303
    assert "category=cheese" in r.headers["location"]


def test_category_panel_uses_store_panel_classes(bf, client):
    user = bf.db.create_user("panel@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "a",
        "2026-08-10",
        [{"name": "Brie", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "c1"}],
    )
    client.post("/login", data={"email": "panel@example.com", "password": "secret1", "intent": "signin"})

    for path in ("/dashboard?range=all&grain=daily", "/purchases?range=all&grain=daily"):
        page = client.get(path).text
        assert 'id="category-toggle"' in page
        assert 'id="category-panel"' in page
        assert 'class="store-panel"' in page
        assert "data-filter-panel" in page
        assert page.index('id="category-toggle"') < page.index('id="category-panel"')
        panel_css = page[page.index(".store-panel {") : page.index(".store-panel[hidden]")]
        assert "position:absolute" not in panel_css
        assert "position:static" in panel_css
        assert "text-align:left" in panel_css
        assert "store-panel-list" in page
        assert "overflow-y:auto" in page
        assert "text-align:left" in page[page.index(".store-panel-list {") : page.index(".store-panel label.on")]
        assert "justify-content:flex-start" in page
        assert ".app-head.is-filter-open { position:relative; top:auto; }" in page
        assert 'head.classList.toggle("is-filter-open"' in page

    phone = client.get("/purchases").text
    phone_css = phone[phone.index("@media (max-width:720px)") :]
    assert phone_css.index('id="category-panel"') < phone_css.index('id="buy-cards"')
    assert 'class="store-panel-list"' in phone
    assert phone_css.index('id="store-panel"') < phone_css.index('id="buy-cards"')
