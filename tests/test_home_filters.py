"""HOME filter windows, divisors, departments, and price-trend basket."""

from datetime import date, timedelta


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


def test_range_chips_are_inclusive_calendar_days(bf):
    until = date(2026, 8, 30)
    cases = {"1w": 7, "2w": 14, "1m": 30, "3m": 90, "1y": 365, "2y": 730, "3y": 1095}
    for key, days in cases.items():
        since, end, got = bf.purchases.window(key, end=until.isoformat())
        assert got == key
        assert end == until
        assert (until - date.fromisoformat(since)).days + 1 == days


def test_one_week_is_exactly_one_weekly_period(bf):
    for offset in range(7):
        until = date(2026, 8, 24) + timedelta(days=offset)
        since = (until - timedelta(days=6)).isoformat()
        assert bf.purchases.period_span(since, until, "weekly") == 1.0


def test_one_year_monthly_average_divides_by_twelve(bf):
    """1y + monthly prints ÷ 12 and uses 12 — the screenshot arithmetic."""
    until = date(2026, 8, 30)
    since, end, key = bf.purchases.window("1y", end=until.isoformat())
    assert key == "1y"
    assert since == "2025-08-31"
    assert end == until
    assert bf.purchases.period_span(since, until, "monthly") == 12.0
    head = bf.purchases.period_headline(23249.46, since, until, "monthly")
    assert head["periods_text"] == "12"
    assert head["period_unit"] == "months"
    assert head["period_avg"] == round(23249.46 / 12, 2)


def test_printed_divisor_is_the_divisor(bf):
    until = date(2026, 8, 30)
    since = "2025-08-30"
    raw = bf.purchases.period_span(since, until, "monthly")
    assert abs(raw - 12.03) < 0.01
    head = bf.purchases.period_headline(23249.46, since, until, "monthly")
    assert head["periods_text"] == "12"
    assert head["period_avg"] == round(23249.46 / 12, 2)


def test_empty_months_still_count_in_the_divisor(bf):
    user = bf.db.create_user("empty@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "jan",
        "2026-01-15",
        [{"name": "Rice", "qty": 1, "unit_price": 90, "line_total": 90, "barcode": "1"}],
    )
    _buy(
        bf,
        user["id"],
        "mar",
        "2026-03-15",
        [{"name": "Rice", "qty": 1, "unit_price": 90, "line_total": 90, "barcode": "1"}],
    )
    since, until = "2026-01-01", date(2026, 3, 31)
    days = bf.purchases.daily_spend(user["id"], since=since, until=until)
    assert {d["date"][:7] for d in days} == {"2026-01", "2026-03"}
    head = bf.purchases.period_headline(180, since, until, "monthly")
    assert head["periods_text"] == "3"
    assert head["period_avg"] == 60.0


def test_grain_changes_the_divisor_not_the_window(bf, client):
    user = bf.db.create_user("grainwin@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "r1",
        "2026-06-15",
        [{"name": "Rice", "qty": 1, "unit_price": 365, "line_total": 365, "barcode": "1"}],
    )
    client.post("/login", data={"email": "grainwin@example.com", "password": "secret1", "intent": "signin"})
    avgs = []
    for grain in ("daily", "weekly", "monthly", "yearly"):
        html = client.get(f"/dashboard?range=1y&grain={grain}&end=2026-08-30").text
        assert "2025-08-31" in html
        assert "2026-08-30" in html
        assert "÷" in html
        start = html.index("average this period")
        avgs.append(html[start : start + 120])
    assert len(set(avgs)) == 4


def test_all_starts_at_the_first_invoice_1y_does_not(bf):
    user = bf.db.create_user("ally@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "old",
        "2023-08-11",
        [{"name": "Rice", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "1"}],
    )
    _buy(
        bf,
        user["id"],
        "new",
        "2026-08-20",
        [{"name": "Milk", "qty": 1, "unit_price": 20, "line_total": 20, "barcode": "2"}],
    )
    all_since, all_until, all_key = bf.purchases.resolve_window(user["id"], "all", end="2026-08-30")
    y_since, y_until, y_key = bf.purchases.resolve_window(user["id"], "1y", end="2026-08-30")
    assert all_key == "all" and y_key == "1y"
    assert all_since == "2023-08-11"
    assert y_since == "2025-08-31"
    assert all_until == y_until
    all_total = sum(d["spend"] for d in bf.purchases.daily_spend(user["id"], since=all_since, until=all_until))
    y_total = sum(d["spend"] for d in bf.purchases.daily_spend(user["id"], since=y_since, until=y_until))
    assert all_total == 30
    assert y_total == 20


def test_custom_dates_matching_1y_match_the_chip(bf, client):
    user = bf.db.create_user("customy@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "in",
        "2026-01-10",
        [{"name": "Rice", "qty": 1, "unit_price": 40, "line_total": 40, "barcode": "1"}],
    )
    _buy(
        bf,
        user["id"],
        "out",
        "2025-08-30",
        [{"name": "Rice", "qty": 1, "unit_price": 9, "line_total": 9, "barcode": "1"}],
    )
    client.post("/login", data={"email": "customy@example.com", "password": "secret1", "intent": "signin"})
    chip = client.get("/dashboard?range=1y&grain=monthly&end=2026-08-30").text
    custom = client.get("/dashboard?range=custom&start=2025-08-31&end=2026-08-30&grain=monthly").text
    assert "AED 40.00" in chip
    assert "AED 9.00" not in chip
    assert "AED 40.00" in custom
    assert "÷ 12 months" in chip
    assert "÷ 12 months" in custom


def test_edible_and_drinks_spend_exclude_the_other_aisle(bf, client):
    user = bf.db.create_user("aisle@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "mix",
        "2026-08-10",
        [
            {"name": "AUS BF RIBEYE STEA", "qty": 1, "unit_price": 119, "line_total": 119, "barcode": "steak"},
            {"name": "Monini Extra Virgin Olive Oil", "qty": 1, "unit_price": 38, "line_total": 38, "barcode": "oil"},
            {"name": "Heineken Can 24 x 50CL", "qty": 1, "unit_price": 131, "line_total": 131, "barcode": "beer"},
            {"name": "Oasis Blu Sparkling Water, 1L", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": "water"},
        ],
    )
    client.post("/login", data={"email": "aisle@example.com", "password": "secret1", "intent": "signin"})
    all_html = client.get("/dashboard?range=all&grain=daily").text
    assert "AED 293.00" in all_html
    edible = client.get("/dashboard?range=all&grain=daily&dept=Edible").text
    assert "AED 157.00" in edible
    assert "RIBEYE" in edible
    assert "Heineken" not in edible
    drinks = client.get("/dashboard?range=all&grain=daily&dept=Drinks").text
    assert "AED 136.00" in drinks
    assert "Heineken" in drinks
    assert "RIBEYE" not in drinks


def test_price_trend_basket_follows_dept(bf):
    user = bf.db.create_user("trendb@example.com", "secret1")
    for no, day, steak, beer in (("a", "2026-01-01", 10, 20), ("b", "2026-06-01", 12, 18)):
        _buy(
            bf,
            user["id"],
            no,
            day,
            [
                {"name": "AUS BF RIBEYE STEA", "qty": 1, "unit_price": steak, "line_total": steak, "barcode": "steak"},
                {"name": "Heineken Can 24 x 50CL", "qty": 1, "unit_price": beer, "line_total": beer, "barcode": "beer"},
            ],
        )
    drinks = bf.purchases.price_trend(user["id"], dept="Drinks")
    assert drinks["products"] == 1
    assert drinks["down"] == 1
    edible = bf.purchases.price_trend(user["id"], dept="Edible")
    assert edible["products"] == 1
    assert edible["up"] == 1
    all_t = bf.purchases.price_trend(user["id"])
    assert all_t["products"] == 2
    assert all_t["up"] == 1 and all_t["down"] == 1


def test_price_trend_uses_the_selected_window(bf):
    user = bf.db.create_user("trendw@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "old1",
        "2024-01-01",
        [{"name": "Milk", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": "m"}],
    )
    _buy(
        bf,
        user["id"],
        "old2",
        "2024-06-01",
        [{"name": "Milk", "qty": 1, "unit_price": 15, "line_total": 15, "barcode": "m"}],
    )
    _buy(
        bf,
        user["id"],
        "new1",
        "2026-01-01",
        [{"name": "Milk", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "m"}],
    )
    _buy(
        bf,
        user["id"],
        "new2",
        "2026-06-01",
        [{"name": "Milk", "qty": 1, "unit_price": 11, "line_total": 11, "barcode": "m"}],
    )
    y = bf.purchases.price_trend(user["id"], since="2025-08-31", until=date(2026, 8, 30))
    assert y["products"] == 1
    assert y["avg_pct"] == 10.0
    whole = bf.purchases.price_trend(user["id"], since="2024-01-01", until=date(2026, 8, 30))
    assert whole["avg_pct"] == 120.0


def test_official_name_tags_spend_and_trend_the_same_way(bf):
    user = bf.db.create_user("oasis@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "a",
        "2026-01-01",
        [{"name": "6PK BTL", "qty": 1, "unit_price": 20, "line_total": 20, "barcode": "6291021000887"}],
    )
    _buy(
        bf,
        user["id"],
        "b",
        "2026-06-01",
        [{"name": "6PK BTL", "qty": 1, "unit_price": 24, "line_total": 24, "barcode": "6291021000887"}],
    )
    bf.purchases.upsert_product_meta(
        "ean:6291021000887",
        {"name": "Oasis Blu Sparkling Water, 1L Pack of 6", "sku": "6291021000887"},
    )
    drinks_spend = bf.purchases.daily_spend(user["id"], dept="Drinks")
    assert round(sum(d["spend"] for d in drinks_spend), 2) == 44.0
    edible_spend = bf.purchases.daily_spend(user["id"], dept="Edible")
    assert edible_spend == []
    listed = bf.purchases.list_products(user["id"], dept="Drinks")
    assert listed and listed[0]["dept"] == "Drinks"
    trend = bf.purchases.price_trend(user["id"], dept="Drinks")
    assert trend["products"] == 1
    assert trend["up"] == 1


def test_home_headline_formula_matches_the_amount(bf, client):
    user = bf.db.create_user("formula@example.com", "secret1")
    _buy(
        bf,
        user["id"],
        "r",
        "2026-06-15",
        [{"name": "Rice", "qty": 1, "unit_price": 120, "line_total": 120, "barcode": "1"}],
    )
    client.post("/login", data={"email": "formula@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/dashboard?range=custom&start=2026-06-01&end=2026-06-30&grain=monthly").text
    assert "AED 120.00 ÷ 1 month" in html
    assert "AED 120.00</b>" in html
    yearly = client.get("/dashboard?range=custom&start=2026-01-01&end=2026-12-31&grain=monthly").text
    assert "÷ 12 months" in yearly
    assert "AED 10.00</b>" in yearly
