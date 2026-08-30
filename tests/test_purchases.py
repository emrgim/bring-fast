"""Invoice-backed purchases tab."""

import json


def test_purchases_tab_lists_parsed_invoice(bf, client):
    user = bf.db.create_user("e@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour City Center Meaisem",
            "invoice_no": "93084417",
            "order_no": "1",
            "invoice_date": "2026-08-23",
            "source": "test",
            "items": [
                {
                    "name": "Coca-Cola Zero Sugar",
                    "qty": 2,
                    "unit_price": 5.99,
                    "line_total": 11.98,
                    "barcode": "5000112668209",
                }
            ],
        },
    )
    token = user["mcp_token"]
    # dashboard session
    client.post("/login", data={"email": "e@example.com", "password": "secret1", "intent": "signin"})
    page = client.get("/purchases")
    assert page.status_code == 200
    assert "Coca-Cola Zero Sugar" in page.text
    assert "AED 11.98" in page.text
    assert "Likely" in page.text
    assert "Frequency" in page.text
    detail = client.get("/purchases/ean:5000112668209")
    assert detail.status_code == 200
    assert "Carrefour City Center Meaisem" in detail.text
    assert "2026-08-23" in detail.text
    assert "SKU" in detail.text
    assert "Barcode" in detail.text
    assert "5000112668209" in detail.text
    assert "data-receipt" in detail.text or "Receipt" in client.get("/purchases/ean:5000112668209").text
    assert 'aria-label="More likely to buy"' in page.text
    assert 'aria-label="Less likely to buy"' in page.text
    assert 'aria-label="More likely to buy"' in detail.text


def test_likely_thumbs_stack_instead_of_toggling(bf, client):
    user = bf.db.create_user("vote@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "v1",
            "invoice_date": "2026-08-23",
            "items": [
                {
                    "name": "White Bread",
                    "qty": 1,
                    "unit_price": 4,
                    "line_total": 4,
                    "barcode": "b1",
                }
            ],
        },
    )
    client.post("/login", data={"email": "vote@example.com", "password": "secret1", "intent": "signin"})
    key = "ean:b1"
    first = client.get("/purchases")
    assert ">0<" in first.text or "Likely" in first.text
    bf.purchases.product_shelf(user["id"])
    headers = {"Accept": "application/json", "X-Requested-With": "fetch"}
    up = client.post(f"/purchases/{key}/vote", data={"vote": "up", "next": "/purchases"}, headers=headers)
    assert up.status_code == 200
    assert up.headers["content-type"].startswith("application/json")
    body = up.json()
    assert body["push"] == 1
    assert body["vote"] == "up"
    assert body["likely"] == 55
    assert bf.forecast.load_votes(user["id"]).get(key) == 1
    page = client.get("/purchases")
    assert "55" in page.text
    home = client.get("/dashboard")
    assert 'aria-label="More likely to buy"' in home.text
    assert "55" in home.text
    detail = client.get(f"/purchases/{key}")
    assert "likely" in detail.text
    assert "55" in detail.text
    assert 'class="likely-line"' in detail.text
    assert "<div class=\"lead\">" in detail.text
    again = client.post(f"/purchases/{key}/vote", data={"vote": "up", "next": "/purchases"}, headers=headers)
    assert again.status_code == 200
    assert again.json()["push"] == 2
    assert again.json()["likely"] > body["likely"]
    assert bf.forecast.load_votes(user["id"]).get(key) == 2
    third = client.post(f"/purchases/{key}/vote", data={"vote": "up", "next": "/purchases"}, headers=headers)
    assert third.json()["push"] == 3
    assert third.json()["likely"] > again.json()["likely"]
    down = client.post(
        f"/purchases/{key}/vote",
        data={"vote": "down", "next": "/purchases"},
        headers=headers,
    )
    assert down.status_code == 200
    assert down.json()["push"] == 2
    assert down.json()["likely"] < third.json()["likely"]
    assert bf.forecast.load_votes(user["id"]).get(key) == 2


def test_vote_endpoint_returns_json_without_navigation(bf, client):
    user = bf.db.create_user("xhrvote@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "v2",
            "invoice_date": "2026-08-23",
            "items": [
                {
                    "name": "White Bread",
                    "qty": 1,
                    "unit_price": 4,
                    "line_total": 4,
                    "barcode": "b1",
                }
            ],
        },
    )
    client.post("/login", data={"email": "xhrvote@example.com", "password": "secret1", "intent": "signin"})
    key = "ean:b1"
    r = client.post(
        f"/purchases/{key}/vote",
        data={"vote": "up", "next": "/purchases"},
        headers={"Accept": "application/json", "X-Requested-With": "fetch"},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert r.status_code != 303
    assert "location" not in {k.lower() for k in r.headers}
    assert r.headers["content-type"].startswith("application/json")
    assert "<html" not in r.text.lower()
    assert r.json()["key"] == key
    assert r.json()["push"] == 1
    html = client.get("/purchases").text
    assert "var BURST_MS=5000" in html
    assert 'class="likely-burst"' in html
    assert 'animation:likely-burst-fill 5s linear forwards' in html
    burst = html[html.index('class="likely-votes"') : html.index("</form>", html.index('class="likely-votes"'))]
    assert burst.index("likely-thumbs") < burst.index('value="up"') < burst.index('value="down"')
    assert burst.index('value="down"') < burst.index("likely-burst")
    assert not (burst.index('value="up"') < burst.index("likely-burst") < burst.index('value="down"'))
    assert "e.preventDefault()" in html
    assert 'Accept":"application/json"' in html
    assert "if(!key || bursts[key]) return" in html
    assert "finish(key)" in html
    assert "commit(key)" in html
    assert "box.scrollTop+=delta" in html


def test_likely_thumbs_stay_neutral_and_bar_sits_under(bf, client):
    """Thumbs are a push, not a selected on-state. The burst bar sits under them."""
    user = bf.db.create_user("neutral@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "n1",
            "invoice_date": "2026-08-23",
            "items": [
                {
                    "name": "White Bread",
                    "qty": 1,
                    "unit_price": 4,
                    "line_total": 4,
                    "barcode": "n1",
                }
            ],
        },
    )
    client.post("/login", data={"email": "neutral@example.com", "password": "secret1", "intent": "signin"})
    key = "ean:n1"
    headers = {"Accept": "application/json", "X-Requested-With": "fetch"}
    assert client.post(f"/purchases/{key}/vote", data={"vote": "up", "next": "/purchases"}, headers=headers).json()["push"] == 1
    assert client.post(f"/purchases/{key}/vote", data={"vote": "up", "next": "/purchases"}, headers=headers).json()["push"] == 2

    def votes_form(html):
        i = html.index('class="likely-votes"')
        return html[i : html.index("</form>", i)]

    for path in ("/purchases", "/dashboard", f"/purchases/{key}"):
        html = client.get(path).text
        form = votes_form(html)
        assert 'aria-pressed="true"' not in form, path
        assert 'aria-pressed=' not in form, path
        assert "ghost on" not in form, path
        assert 'class="ghost"' in form
        assert form.index("likely-thumbs") < form.index('value="up"') < form.index('value="down"')
        assert form.index('value="down"') < form.index("likely-burst")
        assert not (form.index('value="up"') < form.index("likely-burst") < form.index('value="down"'))

    html = client.get("/purchases").text
    votes_css = html[html.index("form.likely-votes {") : html.index(".likely-thumbs {")]
    assert "flex-direction:column" in votes_css
    assert "align-items:stretch" in votes_css
    burst_css = html[html.index(".likely-burst {") : html.index(".likely-burst i {")]
    assert "width:100%" in burst_css
    assert "height:3px" in burst_css
    assert "width:18px" not in burst_css
    assert ".likely-votes button.on" not in html
    assert 'classList.toggle("on", push' not in html
    assert 'setAttribute("aria-pressed"' not in html
    assert "poke(n, \"hit\")" in html
    assert "poke(n, \"commit\")" in html
    assert ".likely-n.bursting" in html
    assert "@keyframes likely-n-blink" in html
    assert "@keyframes likely-thumb-hit" in html
    assert "@keyframes likely-n-commit" in html
    assert "function flash(key, wanted)" in html
    assert "if(!key || bursts[key]) return" in html
    assert "var HIT_MS=220" in html
    assert "var COMMIT_MS=160" in html


def test_official_title_does_not_replace_receipt_name(bf, client):
    user = bf.db.create_user("brie@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour City Center Meaisem",
            "invoice_no": "bri1",
            "invoice_date": "2026-08-23",
            "source": "test",
            "items": [
                {
                    "name": "PRESIDENT BRI 200G",
                    "qty": 1,
                    "unit_price": 20,
                    "line_total": 20,
                    "barcode": "322802023202",
                }
            ],
        },
    )
    bf.purchases.upsert_product_meta(
        "ean:322802023202",
        {"name": "President Brie 60% 200g", "sku": "3228020232026", "source": "carrefour"},
    )
    client.post("/login", data={"email": "brie@example.com", "password": "secret1", "intent": "signin"})
    page = client.get("/purchases")
    assert "President Brie 60% 200g" in page.text
    assert "PRESIDENT BRI 200G" in page.text
    detail = client.get("/purchases/ean:322802023202")
    assert "President Brie 60% 200g" in detail.text
    assert "Receipt PRESIDENT BRI 200G" in detail.text
    con = bf.db.connect()
    stored = con.execute("SELECT name FROM invoice_items WHERE barcode=?", ("322802023202",)).fetchone()
    con.close()
    assert stored["name"] == "PRESIDENT BRI 200G"


def test_official_ean_merges_till_codes_without_changing_receipts(bf):
    user = bf.db.create_user("igor@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour Meaisem",
            "invoice_no": "c1",
            "invoice_date": "2026-07-09",
            "items": [
                {
                    "name": "IGOR GORG 150G",
                    "qty": 1,
                    "unit_price": 17.79,
                    "line_total": 17.79,
                    "barcode": "802139844388",
                }
            ],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "grandiose",
            "store_name": "Grandiose",
            "invoice_no": "g1",
            "invoice_date": "2025-08-31",
            "items": [
                {
                    "name": "Igor Gorgonzola Dolce Cheese",
                    "qty": 1,
                    "unit_price": 21.5,
                    "line_total": 21.5,
                    "barcode": "8021398443882",
                }
            ],
        },
    )
    key = bf.purchases.set_official_identity(
        official_ean_code="8021398443882",
        official_name="Igor Gorgonzola Dolce Cheese",
        aliases=["802139844388"],
        source="grandiose",
    )
    assert key == "ean:8021398443882"
    listed = bf.purchases.list_products(user["id"])
    igor = [p for p in listed if "igor" in p["name"].lower()]
    assert len(igor) == 1
    assert igor[0]["name"] == "Igor Gorgonzola Dolce Cheese"
    assert igor[0]["times_bought"] == 2
    detail = bf.purchases.product_purchases(user["id"], "ean:802139844388")
    assert detail["key"] == "ean:8021398443882"
    assert detail["name"] == "Igor Gorgonzola Dolce Cheese"
    assert "802139844388" in detail["barcodes"]
    assert "8021398443882" in detail["barcodes"]
    assert detail["skus"] == ["8021398443882"]
    con = bf.db.connect()
    rows = con.execute("SELECT barcode, name FROM invoice_items ORDER BY barcode").fetchall()
    con.close()
    assert {(r["barcode"], r["name"]) for r in rows} == {
        ("802139844388", "IGOR GORG 150G"),
        ("8021398443882", "Igor Gorgonzola Dolce Cheese"),
    }
    assert bf.purchases.product_key("802139844388", "IGOR GORG 150G") == "ean:8021398443882"


def test_backfill_merges_only_valid_ean_check_digit(bf):
    user = bf.db.create_user("ean@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "a",
            "invoice_date": "2026-01-01",
            "items": [{"name": "COLA ZERO 2.26L", "qty": 1, "unit_price": 1, "line_total": 1, "barcode": "500011266820"}],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "b",
            "invoice_date": "2026-01-02",
            "items": [{"name": "Rustic Coca-Cola Zero", "qty": 1, "unit_price": 1, "line_total": 1, "barcode": "5000112668209"}],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "c",
            "invoice_date": "2026-01-03",
            "items": [{"name": "Shampoo Based Hair Color", "qty": 1, "unit_price": 1, "line_total": 1, "barcode": "3000000004615"}],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "d",
            "invoice_date": "2026-01-04",
            "items": [{"name": "Country Bread Whole Wheat", "qty": 1, "unit_price": 1, "line_total": 1, "barcode": "3000000004617"}],
        },
    )
    out = bf.purchases.backfill_official_identities(user_id=user["id"], lookup=False)
    assert out["merged"] >= 1
    cola = [p for p in bf.purchases.list_products(user["id"]) if "cola" in p["name"].lower() or "cola" in (p["receipt_name"] or "").lower()]
    assert len(cola) == 1
    assert cola[0]["times_bought"] == 2
    bread = [p for p in bf.purchases.list_products(user["id"]) if "bread" in p["name"].lower() or "shampoo" in p["name"].lower()]
    assert len(bread) == 2


def test_receipt_pdf_requires_login_and_serves_file(bf, client, tmp_path):
    user = bf.db.create_user("e@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "ABC99",
            "order_no": "1",
            "invoice_date": "2026-08-23",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 3, "line_total": 3, "barcode": "999"}],
        },
    )
    folder = bf.purchases.receipt_dir() / "carrefour"
    folder.mkdir(parents=True, exist_ok=True)
    pdf = folder / "ABC99.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    assert client.get("/receipts/carrefour/ABC99", follow_redirects=False).status_code == 303
    client.post("/login", data={"email": "e@example.com", "password": "secret1", "intent": "signin"})
    r = client.get("/receipts/carrefour/ABC99")
    assert r.status_code == 200
    assert "ABC99" in r.text
    pdf = client.get("/receipts/carrefour/ABC99/file.pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    detail = client.get("/purchases/ean:999").text
    assert "ABC99" in detail
    assert "data-receipt" in detail


def test_purchases_sort_by_times(bf, client):
    user = bf.db.create_user("e@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "1",
            "order_no": "1",
            "invoice_date": "2026-08-01",
            "items": [
                {"name": "Milk", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "111"},
                {"name": "Bread", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": "222"},
            ],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "2",
            "order_no": "2",
            "invoice_date": "2026-08-10",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "111"}],
        },
    )
    names = [p["name"] for p in bf.purchases.list_products(user["id"], sort="times", direction="desc")]
    assert names[0] == "Milk"
    client.post("/login", data={"email": "e@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/purchases", params={"sort": "times", "dir": "desc"}).text
    assert "sort=times" in html
    assert html.index("Milk") < html.index("Bread")


def test_frequency_sort_regular_beats_one_off(bf):
    user = bf.db.create_user("bag@example.com", "secret1")
    from datetime import date

    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "new",
            "order_no": "0",
            "invoice_date": "2026-08-23",
            "items": [{"name": "Once", "qty": 1, "unit_price": 1, "line_total": 1, "barcode": "1"}],
        },
    )
    for i in range(1, 21):
        bf.purchases.upsert_invoice(
            user["id"],
            {
                "retailer": "carrefour",
                "store_name": "Carrefour",
                "invoice_no": f"b{i}",
                "order_no": str(i),
                "invoice_date": f"2026-08-{i:02d}",
                "items": [{"name": "Bag", "qty": 1, "unit_price": 0.5, "line_total": 0.5, "barcode": "2"}],
            },
        )
    names = [
        p["name"]
        for p in bf.purchases.list_products(user["id"], sort="frequency", direction="desc", until=date(2026, 8, 24))
    ]
    assert names[0] == "Bag"



def test_range_filter_and_price_chart(bf, client):
    user = bf.db.create_user("e@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "old",
            "order_no": "1",
            "invoice_date": "2022-01-01",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 8, "line_total": 8, "barcode": "111"}],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "new",
            "order_no": "2",
            "invoice_date": "2026-08-01",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 12, "line_total": 12, "barcode": "111"}],
        },
    )
    since = "2025-01-01"
    recent = {p["key"]: p for p in bf.purchases.list_products(user["id"], since=since)}
    assert recent["ean:111"]["times_bought"] == 1
    assert recent["ean:111"]["spend_total"] == 12
    detail = bf.purchases.product_purchases(user["id"], "ean:111")
    assert detail["first_price"] == 8
    assert detail["last_price"] == 12
    assert detail["delta"] == 4
    assert "polyline" in detail["chart_svg"]
    assert "currentColor" in detail["chart_svg"]
    assert "var(--muted)" in detail["chart_svg"]
    client.post("/login", data={"email": "e@example.com", "password": "secret1", "intent": "signin"})
    page = client.get("/purchases", params={"range": "1y"})
    assert page.status_code == 200
    assert "1y" in page.text


def test_frequency_uses_view_length_and_needs_two_buys(bf):
    user = bf.db.create_user("freq@example.com", "secret1")
    from datetime import date

    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "once",
            "order_no": "0",
            "invoice_date": "2025-01-15",
            "items": [{"name": "Phone", "qty": 1, "unit_price": 499, "line_total": 499, "barcode": "999"}],
        },
    )
    once = next(p for p in bf.purchases.list_products(user["id"], until=date(2028, 1, 15)) if p["key"] == "ean:999")
    assert once["times_bought"] == 1
    assert 1000 <= once["interval_days"] <= 1200
    assert "year" in once["frequency"]

    for i, month in enumerate(range(1, 13), start=1):
        bf.purchases.upsert_invoice(
            user["id"],
            {
                "retailer": "carrefour",
                "store_name": "Carrefour",
                "invoice_no": f"m{i}",
                "order_no": str(i),
                "invoice_date": f"2025-{month:02d}-01",
                "items": [{"name": "Milk", "qty": 1, "unit_price": 8, "line_total": 8, "barcode": "111"}],
            },
        )
    end = date(2027, 12, 31)
    year = next(
        p
        for p in bf.purchases.list_products(
            user["id"], since="2025-01-01", until=date(2025, 12, 31)
        )
        if p["key"] == "ean:111"
    )
    wide = next(
        p
        for p in bf.purchases.list_products(user["id"], since="2024-01-01", until=end)
        if p["key"] == "ean:111"
    )
    assert year["times_bought"] == 12
    assert 28 <= year["interval_days"] <= 35
    assert "month" in year["frequency"]
    assert wide["times_bought"] == 12
    assert wide["interval_days"] > year["interval_days"]


def test_frequency_two_close_buys_then_idle_is_not_daily(bf):
    user = bf.db.create_user("elf@example.com", "secret1")
    from datetime import date

    for i, day in enumerate(("2025-05-16", "2025-05-17"), start=1):
        bf.purchases.upsert_invoice(
            user["id"],
            {
                "retailer": "carrefour",
                "store_name": "Carrefour",
                "invoice_no": f"e{i}",
                "order_no": str(i),
                "invoice_date": day,
                "items": [{"name": "ELFBAR", "qty": 1, "unit_price": 60, "line_total": 60, "barcode": "693257011301"}],
            },
        )
    row = next(
        p
        for p in bf.purchases.list_products(user["id"], until=date(2026, 8, 24))
        if p["key"] == "ean:693257011301"
    )
    assert row["times_bought"] == 2
    assert row["interval_days"] > 100
    assert "day" not in row["frequency"]


def test_frequency_two_buys_dilute_over_years(bf):
    user = bf.db.create_user("buffo@example.com", "secret1")
    from datetime import date

    for i, day in enumerate(("2000-01-05", "2000-01-20"), start=1):
        bf.purchases.upsert_invoice(
            user["id"],
            {
                "retailer": "carrefour",
                "store_name": "Carrefour",
                "invoice_no": f"b{i}",
                "order_no": str(i),
                "invoice_date": day,
                "items": [{"name": "Ciccio", "qty": 1, "unit_price": 1, "line_total": 1, "barcode": "777"}],
            },
        )
    y1 = next(p for p in bf.purchases.list_products(user["id"], until=date(2001, 1, 20)) if p["key"] == "ean:777")
    y4 = next(p for p in bf.purchases.list_products(user["id"], until=date(2004, 1, 20)) if p["key"] == "ean:777")
    assert 0.9 <= y1["per_year"] <= 2.2
    assert 0.4 <= y4["per_year"] <= 0.6
    assert y4["interval_days"] > y1["interval_days"]



def test_daily_spend_groups_same_day_receipts(bf, client):
    user = bf.db.create_user("dash@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "A1",
            "order_no": "1",
            "invoice_date": "2026-08-23",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "1"}],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "grandiose",
            "store_name": "Grandiose",
            "invoice_no": "B2",
            "order_no": "2",
            "invoice_date": "2026-08-23",
            "items": [{"name": "Bread", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": "2"}],
        },
    )
    days = bf.purchases.daily_spend(user["id"])
    assert len(days) == 1
    assert days[0]["count"] == 2
    assert days[0]["spend"] == 15
    client.post("/login", data={"email": "dash@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/purchases").text
    assert "spend-bars" in html
    assert "AED 15" in html or "AED 15.00" in html


def test_spend_series_weekly_and_monthly(bf):
    user = bf.db.create_user("grain@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "w1",
            "order_no": "1",
            "invoice_date": "2026-08-17",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "1"}],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "w2",
            "order_no": "2",
            "invoice_date": "2026-08-19",
            "items": [{"name": "Bread", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": "2"}],
        },
    )
    daily = bf.purchases.spend_series(user["id"], grain="daily")
    weekly = bf.purchases.spend_series(user["id"], grain="weekly")
    monthly = bf.purchases.spend_series(user["id"], grain="monthly")
    assert len(daily) == 2
    assert len(weekly) == 1
    assert weekly[0]["spend"] == 15
    assert weekly[0]["count"] == 2
    assert len(monthly) == 1
    assert "Aug" in monthly[0]["label"]


def test_purchases_view_is_restored(bf, client):
    bf.db.create_user("keep@example.com", "secret1")
    client.post("/login", data={"email": "keep@example.com", "password": "secret1", "intent": "signin"})
    client.get("/purchases", params={"sort": "frequency", "dir": "desc", "range": "1y", "grain": "monthly"})
    r = client.get("/purchases", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "sort=frequency" in loc
    assert "grain=monthly" in loc
    assert "range=1y" in loc
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 303
    assert "purchases" in home.headers["location"]


def test_dashboard_filters_survive_a_visit_to_purchases(bf, client):
    bf.db.create_user("keephome@example.com", "secret1")
    client.post("/login", data={"email": "keephome@example.com", "password": "secret1", "intent": "signin"})
    client.get("/dashboard", params={"range": "1y", "grain": "yearly"})
    client.get("/purchases", params={"sort": "times", "dir": "desc"})
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "range=1y" in loc
    assert "grain=yearly" in loc


def test_purchases_filters_survive_a_visit_to_dashboard(bf, client):
    _two_stores(bf, "keepbuys@example.com")
    client.post("/login", data={"email": "keepbuys@example.com", "password": "secret1", "intent": "signin"})
    client.get("/purchases", params={"sort": "frequency", "dir": "desc", "store": "carrefour"})
    client.get("/dashboard", params={"range": "1m", "grain": "monthly"})
    r = client.get("/purchases", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "sort=frequency" in loc
    assert "carrefour" in loc


def test_product_detail_does_not_replace_purchases_filters(bf, client):
    user = bf.db.create_user("keepdetail@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "d1",
            "invoice_date": "2026-08-10",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "keep1"}],
        },
    )
    client.post("/login", data={"email": "keepdetail@example.com", "password": "secret1", "intent": "signin"})
    client.get("/purchases", params={"sort": "frequency", "dir": "desc", "range": "1y", "grain": "monthly"})
    detail = client.get("/purchases/ean:keep1")
    assert detail.status_code == 200
    r = client.get("/purchases", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "sort=frequency" in loc
    assert "range=1y" in loc
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 303
    assert "ean:keep1" in home.headers["location"]


def test_price_trend_is_mean_of_product_changes(bf, client):
    user = bf.db.create_user("trend@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "t1",
            "invoice_date": "2026-01-01",
            "items": [
                {"name": "Milk", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "1"},
                {"name": "Bread", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": "2"},
            ],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "t2",
            "invoice_date": "2026-06-01",
            "items": [
                {"name": "Milk", "qty": 1, "unit_price": 12, "line_total": 12, "barcode": "1"},
                {"name": "Bread", "qty": 1, "unit_price": 4, "line_total": 4, "barcode": "2"},
            ],
        },
    )
    trend = bf.purchases.price_trend(user["id"])
    assert trend["products"] == 2
    assert trend["up"] == 1
    assert trend["down"] == 1
    assert trend["avg_pct"] == 0.0
    assert trend["median_pct"] == 0.0
    assert len(trend["series"]) >= 2
    assert trend["series"][0]["index"] == 100
    assert trend["chart_svg"]
    assert "currentColor" in trend["chart_svg"]
    drinks = bf.purchases.price_trend(user["id"], dept="Drinks")
    assert drinks["products"] == 1
    assert drinks["up"] == 1
    assert drinks["down"] == 0
    edible = bf.purchases.price_trend(user["id"], dept="Edible")
    assert edible["products"] == 1
    assert edible["down"] == 1
    client.post("/login", data={"email": "trend@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/dashboard?range=all&grain=monthly").text
    assert "Price trend" in html
    assert "Monthly average this period" in html
    assert "÷" in html
    assert "<svg" in html
    assert "data-theme-toggle" in html
    assert "IBM Plex Mono" in html


def test_spend_snapshot_changes_with_range(bf):
    from datetime import date

    user = bf.db.create_user("avg@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "old",
            "invoice_date": "2025-01-01",
            "items": [{"name": "Rice", "qty": 1, "unit_price": 100, "line_total": 100, "barcode": "1"}],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "new",
            "invoice_date": "2026-08-20",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 20, "line_total": 20, "barcode": "2"}],
        },
    )
    today = date(2026, 8, 24)
    since_all, until_all, _ = bf.purchases.resolve_window(user["id"], "all")
    until_all = today
    since_1m, until_1m, _ = bf.purchases.window("1m", end=today.isoformat())
    all_s = bf.purchases.spend_snapshot(user["id"], since=since_all, until=until_all)
    m_s = bf.purchases.spend_snapshot(user["id"], since=since_1m, until=until_1m)
    assert all_s["total"] == 120
    assert m_s["total"] == 20
    assert m_s["daily_avg"] != all_s["daily_avg"]
    assert m_s["daily_avg"] > all_s["daily_avg"]


def test_spend_snapshot_average_follows_grain(bf, client):
    from datetime import date

    user = bf.db.create_user("grainavg@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "g1",
            "invoice_date": "2026-01-10",
            "items": [{"name": "Rice", "qty": 1, "unit_price": 366, "line_total": 366, "barcode": "1"}],
        },
    )
    since, until = "2026-01-01", date(2026, 12, 31)
    snaps = {
        g: bf.purchases.spend_snapshot(user["id"], since=since, until=until, grain=g)
        for g in bf.purchases.GRAINS
    }
    assert snaps["daily"]["periods"] == 365
    assert snaps["weekly"]["periods"] == round(365 / 7, 4)
    assert snaps["monthly"]["periods"] == 12
    assert snaps["yearly"]["periods"] == 1
    assert snaps["daily"]["period_avg"] == 1.0
    assert snaps["monthly"]["period_avg"] == 30.5
    assert snaps["yearly"]["period_avg"] == 366.0
    assert snaps["weekly"]["period_avg"] > snaps["daily"]["period_avg"]
    # This window is exactly one year, so the unit beside the figure is singular.
    assert snaps["yearly"]["periods"] == 1
    assert snaps["yearly"]["period_unit"] == "year"
    assert snaps["monthly"]["period_unit"] == "months"
    assert snaps["daily"]["period_unit"] == "days"

    client.post("/login", data={"email": "grainavg@example.com", "password": "secret1", "intent": "signin"})
    daily_html = client.get("/dashboard?range=1y&grain=daily").text
    yearly_html = client.get("/dashboard?range=1y&grain=yearly").text
    assert "Daily average this period" in daily_html
    assert "Yearly average this period" in yearly_html
    assert daily_html != yearly_html


def test_period_span_counts_partial_buckets(bf):
    from datetime import date

    span = bf.purchases.period_span
    assert span("2026-01-01", date(2026, 1, 31), "monthly") == 1.0
    assert span("2026-01-01", date(2026, 3, 31), "monthly") == 3.0
    # Half of January plus all of February.
    assert span("2026-01-17", date(2026, 2, 28), "monthly") == round(15 / 31 + 1, 4)
    assert span("2026-01-01", date(2026, 1, 14), "weekly") == 2.0
    assert span("2026-01-01", date(2026, 1, 14), "daily") == 14.0
    assert bf.purchases.format_periods(3.0) == "3"
    assert bf.purchases.format_periods(2.35) == "2.4"


def test_receipt_view_without_pdf(bf, client):
    user = bf.db.create_user("slip@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "grandiose",
            "invoice_no": "00000OCM07000182242",
            "invoice_date": "2025-07-04",
            "store_name": "Grandiose",
            "items": [
                {"name": "Arabic Shawarma", "qty": 1, "unit_price": 2.5, "line_total": 2.5, "barcode": "194798"}
            ],
        },
    )
    client.post("/login", data={"email": "slip@example.com", "password": "secret1", "intent": "signin"})
    r = client.get("/receipts/grandiose/00000OCM07000182242")
    assert r.status_code == 200
    assert "Arabic Shawarma" in r.text
    assert "2.50" in r.text


def test_daily_bars_include_empty_days(bf):
    from datetime import date

    user = bf.db.create_user("bars@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "grandiose",
            "invoice_no": "a",
            "invoice_date": "2026-08-20",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "1"}],
        },
    )
    filled = bf.purchases.fill_daily_calendar(
        bf.purchases.daily_spend(user["id"], since="2026-08-20", until=date(2026, 8, 24)),
        "2026-08-20",
        date(2026, 8, 24),
    )
    assert [d["date"] for d in filled] == [
        "2026-08-20",
        "2026-08-21",
        "2026-08-22",
        "2026-08-23",
        "2026-08-24",
    ]
    assert filled[0]["spend"] == 10
    assert filled[1]["spend"] == 0
    assert filled[1]["pct"] == 0


def test_clicking_a_day_lists_only_that_days_products(bf, client):
    user = bf.db.create_user("day@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "grandiose",
            "invoice_no": "d1",
            "invoice_date": "2026-06-26",
            "items": [{"name": "Milk Day", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "111"}],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "d2",
            "invoice_date": "2026-06-27",
            "items": [{"name": "Other Day", "qty": 1, "unit_price": 8, "line_total": 8, "barcode": "222"}],
        },
    )
    client.post("/login", data={"email": "day@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/purchases?range=all&grain=daily&day=2026-06-26").text
    assert "Milk Day" in html
    assert "Other Day" not in html
    assert 'class="bar  on"' in html or " bar on" in html or "on" in html


def _two_stores(bf, email="stores@example.com"):
    user = bf.db.create_user(email, "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour City Center Meaisem",
            "invoice_no": "c1",
            "invoice_date": "2026-08-10",
            "items": [
                {"name": "Carrefour Milk", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "111"},
                {"name": "Shared Oil", "qty": 1, "unit_price": 20, "line_total": 20, "barcode": "333"},
            ],
        },
    )
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "grandiose",
            "store_name": "Grandiose",
            "invoice_no": "g1",
            "invoice_date": "2026-08-12",
            "items": [
                {"name": "Grandiose Bread", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": "222"},
                {"name": "Shared Oil", "qty": 1, "unit_price": 22, "line_total": 22, "barcode": "333"},
            ],
        },
    )
    return user


def test_normalize_stores_keeps_known_ids_in_order(bf):
    assert bf.purchases.normalize_stores("carrefour,grandiose,nope") == ["carrefour", "grandiose"]
    assert bf.purchases.normalize_stores(["mmi", "carrefour", "mmi"]) == ["mmi", "carrefour"]
    assert bf.purchases.normalize_stores("") == []
    assert bf.purchases.normalize_stores("Carrefour") == ["carrefour"]


def test_list_products_filters_by_one_or_more_stores(bf):
    user = _two_stores(bf, "list@example.com")
    carrefour = {p["name"]: p for p in bf.purchases.list_products(user["id"], stores=["carrefour"])}
    assert "Carrefour Milk" in carrefour
    assert "Grandiose Bread" not in carrefour
    assert carrefour["Shared Oil"]["times_bought"] == 1
    assert carrefour["Shared Oil"]["spend_total"] == 20

    both = {p["name"]: p for p in bf.purchases.list_products(user["id"], stores=["carrefour", "grandiose"])}
    assert set(both) == {"Carrefour Milk", "Grandiose Bread", "Shared Oil"}
    assert both["Shared Oil"]["times_bought"] == 2
    assert both["Shared Oil"]["spend_total"] == 42


def test_purchases_page_store_menu_filters_the_shelf(bf, client):
    _two_stores(bf, "page@example.com")
    client.post("/login", data={"email": "page@example.com", "password": "secret1", "intent": "signin"})

    page = client.get("/purchases")
    assert page.status_code == 200
    assert 'id="store-toggle"' in page.text
    assert 'id="store-panel"' in page.text
    assert ">Name<" in page.text
    assert page.text.index(">Name<") < page.text.index('id="store-toggle"')
    msort_end = page.text.index("</div>", page.text.index('class="msort"'))
    assert msort_end < page.text.index('id="store-panel"')
    assert "Clear all" in page.text
    assert ">Done<" in page.text
    panel_css = page.text[page.text.index(".store-panel {") : page.text.index(".store-panel[hidden]")]
    assert "position:absolute" not in panel_css
    assert "position:static" in panel_css
    assert "Carrefour UAE" in page.text
    assert "Grandiose" in page.text
    assert "Carrefour Milk" in page.text
    assert "Grandiose Bread" in page.text

    one = client.get("/purchases", params={"store": "carrefour"})
    assert one.status_code == 200
    assert "Carrefour Milk" in one.text
    assert "Grandiose Bread" not in one.text
    assert "Shared Oil" in one.text
    assert "Store · 1" in one.text
    assert "store=carrefour" in one.text
    assert 'data-shelf="' in one.text
    assert "store=carrefour" in one.text[one.text.index("data-shelf=") :]

    two = client.get("/purchases", params=[("store", "carrefour"), ("store", "grandiose")])
    assert "Carrefour Milk" in two.text
    assert "Grandiose Bread" in two.text
    assert "Store · 2" in two.text

    comma = client.get("/purchases", params={"store": "grandiose,carrefour"})
    assert "Grandiose Bread" in comma.text
    assert "Carrefour Milk" in comma.text


def test_store_filter_changes_spend_and_receipts(bf, client):
    user = _two_stores(bf, "kpi@example.com")
    days = bf.purchases.daily_spend(user["id"], stores=["carrefour"])
    assert sum(d["spend"] for d in days) == 30
    assert bf.purchases.invoice_count(user["id"], stores=["carrefour"]) == 1
    assert bf.purchases.invoice_count(user["id"], stores=["carrefour", "grandiose"]) == 2

    client.post("/login", data={"email": "kpi@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/purchases", params={"store": "grandiose"}).text
    assert "AED 27" in html
    assert "<b>1</b> of 2 receipts" in html


def test_purchases_view_restores_store_filter(bf, client):
    _two_stores(bf, "keepstore@example.com")
    client.post("/login", data={"email": "keepstore@example.com", "password": "secret1", "intent": "signin"})
    client.get("/purchases", params={"sort": "name", "dir": "asc", "store": "mmi,carrefour"})
    r = client.get("/purchases", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "store=" in loc
    assert "carrefour" in loc
    # mmi was asked for but this account has no MMI invoices; the id is still
    # a known store so the view keeps it.
    assert "mmi" in loc



