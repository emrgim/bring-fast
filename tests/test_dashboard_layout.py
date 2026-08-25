"""Dashboard chrome: one spend card, pinned while scrolling, no dead space."""


def _seed(bf, email):
    user = bf.db.create_user(email, "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "lay1",
            "invoice_date": "2026-08-10",
            "items": [{"name": "Rice", "qty": 1, "unit_price": 70, "line_total": 70, "barcode": "1"}],
        },
    )
    return user


def test_spend_card_holds_the_period_average(bf, client):
    _seed(bf, "layout@example.com")
    client.post("/login", data={"email": "layout@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/dashboard?range=1m&grain=weekly").text

    assert html.count("Weekly average this period") == 1
    assert html.count("÷") == 1
    # The average sits inside the spend card, above the bars, not in a widget of its own.
    assert html.index('<div class="dash">') < html.index("Weekly average this period")
    assert html.index("Weekly average this period") < html.index('id="spend-bars"')
    assert html.index('id="spend-bars"') < html.index('class="widgets"')
    assert "daily-avg" not in html


def test_spend_totals_stay_pinned_while_scrolling(bf, client):
    _seed(bf, "sticky@example.com")
    client.post("/login", data={"email": "sticky@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/dashboard?range=1m&grain=monthly").text

    assert 'id="spend-pin"' in html
    assert 'id="spend-avg"' in html
    # Fixed, so collapsing never reflows the page behind it.
    assert ".spend-pin {\n      display:none; position:fixed" in html
    assert 'pin.classList.toggle("on"' in html
    # The pinned bar carries only the selected grain's average.
    pin = html[html.index('id="spend-pin"') : html.index('<div class="dash">')]
    assert "Monthly avg" in pin
    assert "spent" not in pin
    assert "Weekly avg" not in pin


def test_mobile_kpis_keep_number_and_label_on_one_line(bf, client):
    """No stacked KPI numbers: "AED 2537" and "34" must never sit side by side unlabeled."""
    html = client.get("/login").text

    assert ".dash-kpis b { font-size:16px; display:block; }" not in html
    assert ".dash-kpis span + span { border-left:1px solid var(--line); padding-left:12px; }" in html


def test_theme_toggle_lives_only_in_the_top_nav(bf, client):
    _seed(bf, "toggle@example.com")
    client.post("/login", data={"email": "toggle@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/dashboard?range=1m&grain=monthly").text

    # One toggle button (the JS also mentions the attribute in querySelectorAll).
    assert html.count("data-theme-toggle>") == 1
    dock = html[html.index('<footer class="dock"') : html.index("</footer>")]
    assert "<button" not in dock


def test_mobile_header_pads_the_safe_area_only_at_the_top(bf, client):
    html = client.get("/login").text

    assert "max(8px, env(safe-area-inset-top))" in html
    assert "calc(8px + env(safe-area-inset-top))" not in html
