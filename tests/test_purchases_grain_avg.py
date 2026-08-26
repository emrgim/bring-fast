"""The purchases headline price follows the selected grain: daily shows AED/day, weekly AED/week."""


def _seed(bf, email):
    user = bf.db.create_user(email, "secret1")
    for no, day, total in (("g1", "2026-06-02", 60), ("g2", "2026-06-10", 80)):
        bf.purchases.upsert_invoice(
            user["id"],
            {
                "retailer": "carrefour",
                "invoice_no": no,
                "invoice_date": day,
                "items": [{"name": "Rice", "qty": 1, "unit_price": total, "line_total": total, "barcode": "1"}],
            },
        )
    return user


# 2026-06-01 → 2026-06-14: exactly 14 days / 2 calendar weeks, AED 140 spent.
WINDOW = "range=custom&start=2026-06-01&end=2026-06-14"


def test_purchases_average_follows_the_grain(bf, client):
    _seed(bf, "grain@example.com")
    client.post("/login", data={"email": "grain@example.com", "password": "secret1", "intent": "signin"})

    daily = client.get(f"/purchases?{WINDOW}&grain=daily").text
    assert "Daily average this period" in daily
    assert "AED 10.00</b>" in daily
    assert "140.00 ÷ 14 days" in daily
    assert "<b>14</b> days" in daily
    assert "<b>2</b> days" not in daily

    weekly = client.get(f"/purchases?{WINDOW}&grain=weekly").text
    assert "Weekly average this period" in weekly
    assert "AED 70.00</b>" in weekly
    assert "140.00 ÷ 2 weeks" in weekly
    # Two receipts on two days is not the period. The headline and the
    # working use the same calendar span.
    assert "<b>2</b> weeks" in weekly
    assert "<b>2</b> days" not in weekly


def test_a_window_one_period_long_reads_as_one_period(bf, client):
    _seed(bf, "one@example.com")
    client.post("/login", data={"email": "one@example.com", "password": "secret1", "intent": "signin"})

    # A single period is the most common window there is — it is what a fresh
    # account sees — and it was reading "÷ 1 months".
    exact = (
        ("daily", "day", "2026-06-02", "2026-06-02"),
        ("weekly", "week", "2026-06-01", "2026-06-07"),
        ("monthly", "month", "2026-06-01", "2026-06-30"),
        ("yearly", "year", "2026-01-01", "2026-12-31"),
    )
    for grain, unit, start, end in exact:
        window = f"range=custom&start={start}&end={end}&grain={grain}"
        html = client.get(f"/purchases?{window}").text
        assert f"÷ 1 {unit}" in html, grain
        assert f"÷ 1 {unit}s" not in html, grain
        assert f"<b>1</b> {unit}" in html, grain
        # The home tab reads the same figure out of the same snapshot.
        assert f"÷ 1 {unit}" in client.get(f"/dashboard?{window}").text, grain

    # A part period is still plural: 1.5 months is months.
    assert bf.purchases.period_unit("monthly", 1.5) == "months"
    assert bf.purchases.period_unit("weekly", 2) == "weeks"
    # And a count that only rounds to one reads as one, because that is what the
    # figure beside it says.
    assert bf.purchases.period_unit("monthly", 1.02) == "month"
