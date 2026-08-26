"""A tap on a spend bar focuses that bucket; leaving it restores the window."""

import re


WINDOW = "range=custom&start=2026-08-01&end=2026-08-31"


def _seed(bf, email):
    user = bf.db.create_user(email, "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "grandiose",
            "invoice_no": "d1",
            "invoice_date": "2026-08-10",
            "items": [{"name": "Milk Day", "qty": 1, "unit_price": 10, "line_total": 300, "barcode": "111"}],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "d2",
            "invoice_date": "2026-08-20",
            "items": [
                {"name": "Other Day", "qty": 1, "unit_price": 12, "line_total": 100, "barcode": "222"},
                {"name": "Milk Day", "qty": 1, "unit_price": 12, "line_total": 12, "barcode": "111"},
            ],
        },
    )
    return user


def _sign_in(client, email):
    client.post("/login", data={"email": email, "password": "secret1", "intent": "signin"})


def _bars(html):
    chunk = html.split('id="spend-bars"', 1)[1].split("</div>", 1)[0]
    return re.findall(r'class="(bar[^"]*)"[^>]*href="([^"]*)"', chunk)


def test_dashboard_card_follows_the_tapped_day(bf, client):
    _seed(bf, "focus@example.com")
    _sign_in(client, "focus@example.com")

    month = client.get(f"/dashboard?{WINDOW}&grain=daily").text
    assert "AED 412.00 ÷ 31 days" in month
    assert "2026-08-01 → 2026-08-31" in month
    assert "Milk Day" in month
    assert "Other Day" in month
    assert "Prices are rising" in month
    assert 'name="start" value="2026-08-01"' in month

    day = client.get(f"/dashboard?{WINDOW}&grain=daily&day=2026-08-10").text
    assert "AED 300.00 ÷ 1 day" in day
    assert "2026-08-10 → 2026-08-10" in day
    assert "AED 412.00 ÷ 31 days" not in day
    assert "Milk Day" in day
    assert "Other Day" not in day
    assert "Need two buys on a product" in day
    # The range chips and date fields stay on the month; only the card zooms in.
    assert 'name="start" value="2026-08-01"' in day
    assert 'name="end" value="2026-08-31"' in day

    bars = _bars(day)
    selected = [href for cls, href in bars if "on" in cls.split()]
    others = [href for cls, href in bars if "on" not in cls.split()]
    assert len(selected) == 1
    assert "day=" not in selected[0]
    assert others
    assert all("day=" in href for href in others)
    assert any("day=2026-08-20" in href for href in others)


def test_dashboard_without_day_is_the_global_window_again(bf, client):
    _seed(bf, "back@example.com")
    _sign_in(client, "back@example.com")

    html = client.get(f"/dashboard?{WINDOW}&grain=daily").text
    assert "AED 412.00 ÷ 31 days" in html
    assert all("day=" in href for _, href in _bars(html))


def test_a_weekly_bar_focuses_that_week_on_the_card(bf, client):
    _seed(bf, "week@example.com")
    _sign_in(client, "week@example.com")

    html = client.get(f"/dashboard?{WINDOW}&grain=weekly&day=2026-08-10").text
    # 10 Aug 2026 is a Monday; that week is only the 300 AED receipt.
    assert "AED 300.00 ÷ 1 week" in html
    assert "2026-08-10 → 2026-08-16" in html
    assert "Milk Day" in html
    assert "Other Day" not in html


def test_purchases_card_follows_the_tapped_day(bf, client):
    _seed(bf, "buys@example.com")
    _sign_in(client, "buys@example.com")

    month = client.get(f"/purchases?{WINDOW}&grain=daily").text
    assert "AED 412.00 ÷ 31 days" in month
    assert "<b>2</b> of" in month or "<b>2</b> receipts" in month

    day = client.get(f"/purchases?{WINDOW}&grain=daily&day=2026-08-10").text
    assert "AED 300.00 ÷ 1 day" in day
    assert "2026-08-10 → 2026-08-10" in day
    assert "Milk Day" in day
    assert "Other Day" not in day
    assert "<b>1</b> of" in day or "<b>1</b> receipts" in day
    selected = [href for cls, href in _bars(day) if "on" in cls.split()]
    assert len(selected) == 1
    assert "day=" not in selected[0]


def test_escape_and_click_outside_clear_the_day(bf, client):
    _seed(bf, "esc@example.com")
    _sign_in(client, "esc@example.com")

    html = client.get(f"/dashboard?{WINDOW}&grain=daily&day=2026-08-10").text
    assert 'e.key!=="Escape"' in html
    assert 'searchParams.delete("day")' in html
    assert 'closest(".bar")' in html
    # A receipt sheet, if open, takes Escape before the day is dropped.
    assert html.index("receipt-sheet") < html.index('e.key!=="Escape"')
    assert "var days = " not in html
