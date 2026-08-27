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


def test_range_filter_sits_under_the_title_bar(bf, client):
    _seed(bf, "filterbar@example.com")
    client.post("/login", data={"email": "filterbar@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/dashboard?range=all&grain=monthly").text

    # The 1w/2w/1m/3m/1y/2y/3y/All row is header chrome: immediately under
    # “Bring Fast”, not after the spend card and the price-trend widgets.
    # Department sits in the same chrome as Buys.
    head = html[html.index('<header class="app-head">') : html.index("</header>")]
    assert 'class="filters"' in head
    assert 'aria-label="Department"' in head
    assert 'aria-label="Range"' in head
    assert ">Edible<" in head
    assert ">Drinks<" in head
    assert ">2w<" in head
    assert ">2y<" in head
    assert ">3y<" in head
    assert ">All<" in head
    assert html.index("</header>") < html.index('<div class="dash">')
    assert html.count('class="filters"') == 1


def test_home_department_filter_hides_the_other_aisle(bf, client):
    user = bf.db.create_user("homedept@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "both",
            "invoice_date": "2026-08-10",
            "items": [
                {"name": "Rice", "qty": 1, "unit_price": 70, "line_total": 70, "barcode": "1"},
                {"name": "Heineken Cans 50 cl", "qty": 1, "unit_price": 15, "line_total": 15, "barcode": "2"},
            ],
        },
    )
    client.post("/login", data={"email": "homedept@example.com", "password": "secret1", "intent": "signin"})
    all_html = client.get("/dashboard?range=all&grain=daily").text
    assert "AED 85.00" in all_html
    assert "Rice" in all_html
    assert "Heineken" in all_html
    edible = client.get("/dashboard?range=all&grain=daily&dept=Edible").text
    assert "AED 70.00" in edible
    assert "Rice" in edible
    assert "Heineken" not in edible
    assert 'class="on" href="/dashboard?range=all&grain=daily&dept=Edible"' in edible
    drinks = client.get("/dashboard?range=all&grain=daily&dept=Drinks").text
    assert "AED 15.00" in drinks
    assert "Heineken" in drinks
    assert "Rice" not in drinks


def test_buys_date_fields_show_the_window_when_all_is_selected(bf, client):
    """Buys used to leave start empty on All, so the first field collapsed."""
    _seed(bf, "calfields@example.com")
    client.post("/login", data={"email": "calfields@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/purchases?range=all&grain=daily").text

    assert 'name="start" value="2026-08-10"' in html
    assert 'name="end" value="' in html
    home = client.get("/dashboard?range=all&grain=daily").text
    assert 'name="start" value="2026-08-10"' in home
    detail = client.get("/purchases/ean:1?range=all").text
    assert 'name="start" value="2026-08-10"' in detail


def test_buys_range_filter_sits_under_the_title_bar(bf, client):
    _seed(bf, "buysfilter@example.com")
    client.post("/login", data={"email": "buysfilter@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/purchases?range=all&grain=monthly").text

    # Same chrome as Home: department + range sit under “Bring Fast”, not
    # after the Purchases spend card.
    head = html[html.index('<header class="app-head">') : html.index("</header>")]
    assert 'class="filters"' in head
    assert 'aria-label="Department"' in head
    assert 'aria-label="Range"' in head
    assert ">Edible<" in head
    assert html.index("</header>") < html.index('<div class="purchases-board">')
    assert html.count('class="filters"') == 1


def test_date_fields_are_not_inside_the_range_chip_row(bf, client):
    """Two 14em dates in the Range strip clip to 07/28/20… on a phone, and a
    dock tap resets the strip so the bar looks like it grew extra filters."""
    _seed(bf, "daterow@example.com")
    client.post("/login", data={"email": "daterow@example.com", "password": "secret1", "intent": "signin"})
    for path in ("/dashboard?range=1m&grain=monthly", "/purchases?range=1m&grain=monthly"):
        html = client.get(path).text
        head = html[html.index('<header class="app-head">') : html.index("</header>")]
        range_at = head.index('aria-label="Range"')
        range_div = head[head.rindex("<div", 0, range_at) : head.index("</div>", range_at) + 6]
        assert 'type="date"' not in range_div
        assert ">Apply<" not in range_div
        assert "filter-dates" in head
        assert head.index('aria-label="Range"') < head.index('type="date"')
        hidden_css = html[html.index("input[type=hidden]") :]
        assert "display:none !important" in hidden_css


def test_a_dock_tap_keeps_the_selected_chip_in_the_strip(bf, client):
    _seed(bf, "chipscroll@example.com")
    client.post("/login", data={"email": "chipscroll@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/dashboard").text
    assert 'document.querySelectorAll(".chip-row a.on")' in html
    assert "row.scrollLeft" in html


def test_store_panel_expands_in_flow_and_inverts_when_filtered(bf, client):
    user = _seed(bf, "storeui@example.com")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "grandiose",
            "invoice_no": "g-ui",
            "invoice_date": "2026-08-11",
            "items": [{"name": "Bread", "qty": 1, "unit_price": 4, "line_total": 4, "barcode": "9"}],
        },
    )
    client.post("/login", data={"email": "storeui@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/purchases").text
    phone = html[html.index("@media (max-width:720px)") :]
    # The chip row scrolls sideways; the store list is a sibling so that
    # overflow cannot clip it, and opening it pushes the cards down.
    assert ".store-panel { margin:0 0 10px; }" in phone
    assert html.index('id="store-toggle"') < html.index('id="buy-cards"')
    assert html.index('id="store-panel"') < html.index('id="buy-cards"')
    assert 'class="store-chip"' in html
    assert 'class="store-chip on"' not in html
    assert ".msort .store-chip.on" in html
    filtered = client.get("/purchases?store=carrefour").text
    assert 'class="store-chip on"' in filtered
    assert "Store · 1" in filtered


def test_theme_toggle_lives_only_in_the_top_nav(bf, client):
    _seed(bf, "toggle@example.com")
    client.post("/login", data={"email": "toggle@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/dashboard?range=1m&grain=monthly").text

    # One toggle button (the JS also mentions the attribute in querySelectorAll).
    assert html.count("data-theme-toggle>") == 1
    dock = html[html.index('<footer class="dock"') : html.index("</footer>")]
    assert "<button" not in dock


def test_theme_toggle_cycles_light_dark_auto(client):
    html = client.get("/login").text

    assert html.count("data-theme-toggle>Auto</button>") == 1
    assert 'var ORDER=["light","dark","auto"]' in html
    assert 'LABEL={light:"Light",dark:"Dark",auto:"Auto"}' in html
    # Auto is the OS, and a change to the OS is followed while Auto is set.
    assert 'prefers-color-scheme: dark' in html
    assert 'if(pref()==="auto") apply("auto", false)' in html
    assert 't==="light"||t==="dark"||t==="auto"' in html
    # A stored Light or Dark still wins; nothing stored is Auto.
    assert 'return "auto"' in html


def test_mobile_header_pads_the_safe_area_only_at_the_top(bf, client):
    html = client.get("/login").text

    assert "max(8px, env(safe-area-inset-top))" in html
    assert "calc(8px + env(safe-area-inset-top))" not in html
