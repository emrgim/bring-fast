"""Macro-category filter on Home and Buys (All dropdown)."""

from datetime import date

from bring_fast.macro_categories import CHEESE, DAIRY, SOFT_DRINKS
from bring_fast import purchases


def _macro_user(bf, email="macrofilt@example.com"):
    user = bf.db.create_user(email, "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "m1",
            "invoice_date": "2026-08-10",
            "items": [
                {
                    "name": "PRESIDENT BRI 200G",
                    "qty": 1,
                    "unit_price": 10,
                    "line_total": 10,
                    "barcode": "111",
                },
            ],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "m2",
            "invoice_date": "2026-08-11",
            "items": [
                {
                    "name": "Fresh Milk 2L",
                    "qty": 1,
                    "unit_price": 8,
                    "line_total": 8,
                    "barcode": "222",
                },
            ],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "m3",
            "invoice_date": "2026-08-12",
            "items": [
                {
                    "name": "COCA COLA LIGHT 1L",
                    "qty": 1,
                    "unit_price": 5,
                    "line_total": 5,
                    "barcode": "333",
                },
            ],
        },
    )
    return user


def test_normalize_categories_keeps_valid_slugs_in_order(bf):
    assert bf.purchases.normalize_categories("cheese,dairy,nope") == [CHEESE, DAIRY]
    assert bf.purchases.normalize_categories(["cheese", "dairy", "cheese"]) == [CHEESE, DAIRY]
    assert bf.purchases.normalize_categories("") == []


def test_list_products_no_category_means_all(bf):
    user = _macro_user(bf, "all@example.com")
    names = {p["name"] for p in bf.purchases.list_products(user["id"])}
    assert "PRESIDENT BRI 200G" in names
    assert "Fresh Milk 2L" in names
    assert "COCA COLA LIGHT 1L" in names


def test_list_products_filters_by_cheese(bf):
    user = _macro_user(bf, "cheese@example.com")
    names = {p["name"] for p in bf.purchases.list_products(user["id"], categories=[CHEESE])}
    assert names == {"PRESIDENT BRI 200G"}


def test_list_products_multi_select_categories(bf):
    user = _macro_user(bf, "multi@example.com")
    names = {p["name"] for p in bf.purchases.list_products(user["id"], categories=[CHEESE, DAIRY])}
    assert names == {"PRESIDENT BRI 200G", "Fresh Milk 2L"}


def test_edible_and_cheese_intersection(bf):
    user = _macro_user(bf, "edible@example.com")
    names = {
        p["name"]
        for p in bf.purchases.list_products(user["id"], dept="Edible", categories=[CHEESE])
    }
    assert names == {"PRESIDENT BRI 200G"}
    drinks_only = {
        p["name"]
        for p in bf.purchases.list_products(user["id"], dept="Drinks", categories=[CHEESE])
    }
    assert drinks_only == set()


def test_category_query_string_round_trip(bf, client):
    _macro_user(bf, "qs@example.com")
    client.post("/login", data={"email": "qs@example.com", "password": "secret1", "intent": "signin"})

    one = client.get("/purchases", params={"category": CHEESE})
    assert one.status_code == 200
    assert "PRESIDENT BRI 200G" in one.text
    assert "Fresh Milk 2L" not in one.text
    assert "All · 1" in one.text
    assert f"category={CHEESE}" in one.text

    two = client.get("/purchases", params=[("category", CHEESE), ("category", DAIRY)])
    assert "PRESIDENT BRI 200G" in two.text
    assert "Fresh Milk 2L" in two.text
    assert "COCA COLA LIGHT 1L" not in two.text
    assert "All · 2" in two.text

    comma = client.get("/purchases", params={"category": f"{CHEESE},{DAIRY}"})
    assert "PRESIDENT BRI 200G" in comma.text
    assert "Fresh Milk 2L" in comma.text


def test_category_panel_uses_store_panel_classes(bf, client):
    _macro_user(bf, "panel@example.com")
    client.post("/login", data={"email": "panel@example.com", "password": "secret1", "intent": "signin"})

    home = client.get("/dashboard")
    assert home.status_code == 200
    assert 'id="category-toggle"' in home.text
    assert 'class="store-panel"' in home.text
    assert 'id="category-panel"' in home.text
    assert "Clear all" in home.text
    assert ">Done<" in home.text
    assert "Cheese" in home.text
    assert "Milk &amp; dairy" in home.text
    assert home.text.index('id="category-toggle"') < home.text.index('aria-label="Range"')

    buys = client.get("/purchases")
    assert 'id="category-toggle"' in buys.text
    assert 'class="store-panel"' in buys.text
    assert 'id="store-panel"' in buys.text


def test_home_dashboard_category_filter(bf, client):
    _macro_user(bf, "homecat@example.com")
    client.post("/login", data={"email": "homecat@example.com", "password": "secret1", "intent": "signin"})

    html = client.get("/dashboard", params={"category": CHEESE}).text
    assert "PRESIDENT BRI 200G" in html
    assert "Fresh Milk 2L" not in html
    assert "COCA COLA LIGHT 1L" not in html


def test_macro_options_lists_all_defined_macros(bf):
    opts = purchases.macro_category_options()
    assert len(opts) == 32
    labels = {o["name"] for o in opts}
    assert "Cheese" in labels
    assert "Soft drinks" in labels


def test_ranked_products_category_filter(bf):
    user = _macro_user(bf, "rank@example.com")
    rows = bf.purchases.ranked_products(
        user["id"], categories=[SOFT_DRINKS], today=date(2026, 8, 24)
    )
    assert len(rows) == 1
    assert "COCA COLA" in rows[0]["name"]
