"""HOME Today filter: spend total, toggle restore, price delta."""

import re

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


def _login(client, email):
    client.post("/login", data={"email": email, "password": "secret1", "intent": "signin"})


def test_day_spend_is_one_calendar_day(bf):
    user = bf.db.create_user("todaytot@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "a",
        "2026-09-02",
        [{"name": "Milk", "qty": 1, "unit_price": 12, "line_total": 12, "barcode": "1"}],
    )
    _buy(
        bf,
        user["id"],
        "b",
        "2026-09-01",
        [{"name": "Bread", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": "2"}],
    )
    total = purchases.day_spend(user["id"], purchases.dubai_today(end="2026-09-02"))
    assert total == 12.0


def test_today_headline_shows_dubai_day_total(bf, client):
    user = bf.db.create_user("todayhead@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "a",
        "2026-09-02",
        [
            {"name": "Milk", "qty": 1, "unit_price": 12, "line_total": 12, "barcode": "1"},
            {"name": "Bread", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": "2"},
        ],
    )
    _buy(
        bf,
        user["id"],
        "b",
        "2026-08-20",
        [{"name": "Rice", "qty": 1, "unit_price": 90, "line_total": 90, "barcode": "3"}],
    )
    _login(client, "todayhead@example.com")
    html = client.get(
        "/dashboard",
        params={
            "today": "1",
            "range": "1m",
            "grain": "monthly",
            "end": "2026-09-02",
            "prev_range": "1m",
            "prev_grain": "monthly",
        },
    ).text

    assert "Total today · 2026-09-02" in html
    assert "AED 17.00" in html
    assert "average this period" not in html
    assert ">Today<" in html
    assert 'class="brand is-today"' in html
    assert "animation: range-tick 1.8s steps(2, end) infinite" in html


def test_today_toggle_restores_previous_range(bf, client):
    user = bf.db.create_user("todaytog@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "a",
        "2026-09-02",
        [{"name": "Milk", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "1"}],
    )
    _login(client, "todaytog@example.com")

    on = client.get(
        "/dashboard",
        params={"range": "3m", "grain": "weekly", "end": "2026-09-02"},
    ).text
    assert ">Today<" in on
    assert 'href="/dashboard?today=1' in on
    assert "prev_range=3m" in on
    assert "prev_grain=weekly" in on

    active = client.get(
        "/dashboard",
        params={
            "today": "1",
            "range": "3m",
            "grain": "weekly",
            "end": "2026-09-02",
            "prev_range": "3m",
            "prev_grain": "weekly",
        },
    ).text
    assert 'class="brand is-today"' in active
    off_href = re.search(r'<a class="on is-filter" href="([^"]+)" title="Today">Today</a>', active)
    assert off_href, "Today chip should be on and link to restore"
    restored = client.get(off_href.group(1)).text
    assert 'class="brand is-today"' not in restored
    assert "Total today ·" not in restored
    assert ">Bring Fast<" in restored


def test_today_products_show_price_sparkline_and_delta(bf, client):
    user = bf.db.create_user("todayprod@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "old",
        "2026-08-01",
        [{"name": "Heineken", "qty": 1, "unit_price": 100, "line_total": 100, "barcode": "h1"}],
    )
    _buy(
        bf,
        user["id"],
        "today",
        "2026-09-02",
        [{"name": "Heineken", "qty": 1, "unit_price": 110, "line_total": 110, "barcode": "h1"}],
    )
    _buy(
        bf,
        user["id"],
        "new",
        "2026-09-02",
        [{"name": "Brie", "qty": 1, "unit_price": 15, "line_total": 15, "barcode": "b1"}],
    )
    _login(client, "todayprod@example.com")
    html = client.get(
        "/dashboard",
        params={
            "today": "1",
            "range": "all",
            "grain": "daily",
            "end": "2026-09-02",
            "prev_range": "all",
            "prev_grain": "daily",
        },
    ).text

    assert "mcard-today" in html
    assert "class=\"spark\"" in html
    assert "AED 110.00" in html
    assert "+10.0%" in html
    assert ">new<" in html or "new</small>" in html


def test_price_vs_previous_labels(bf):
    user = bf.db.create_user("delt@example.com", "secret1")
    key = purchases.product_key("x", "Thing")
    series = [
        {"date": "2026-08-01", "price": 10.0},
        {"date": "2026-09-02", "price": 12.0},
    ]
    up = purchases._price_vs_previous(series, "2026-09-02", 12.0)
    assert up["label"] == "+20.0%"
    brand_new = purchases._price_vs_previous([{"date": "2026-09-02", "price": 5.0}], "2026-09-02", 5.0)
    assert brand_new["label"] == "new"


def test_today_chip_not_on_buys(bf, client):
    user = bf.db.create_user("buys@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "a",
        "2026-09-02",
        [{"name": "Milk", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "1"}],
    )
    _login(client, "buys@example.com")
    html = client.get("/purchases").text
    assert ">Today<" not in html
