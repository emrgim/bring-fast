"""The buys tab opens at once and fills itself in, while the figures stay whole."""

FIRST = 24


def _shelf(bf, count=40, email="shelf@example.com"):
    """One receipt carrying `count` different products, each with a picture."""
    user = bf.db.create_user(email, "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "SHELF1",
            "order_no": "1",
            "invoice_date": "2026-08-10",
            "items": [
                {
                    "name": "Product %03d" % i,
                    "qty": 1,
                    "unit_price": 100 - i,
                    "line_total": 100 - i,
                    "barcode": "70000%03d" % i,
                    "image_url": "https://cdn.example.com/p%03d.jpg" % i,
                }
                for i in range(count)
            ],
        },
    )
    return user


def _signed_in(bf, client, count=40, email="shelf@example.com"):
    user = _shelf(bf, count, email)
    client.post("/login", data={"email": email, "password": "secret1", "intent": "signin"})
    return user


def _drawn(html):
    """The part of the page the browser lays out, without the text payload."""
    return html.split('<script type="text/html"')[0]


def _payload(html):
    """The rows handed over as text, without the script that pours them."""
    rest = html.split('<script type="text/html"')[1:]
    return "".join(part.split("</script>")[0] for part in rest)


def test_the_page_draws_a_screenful_and_hands_the_rest_over_as_text(bf, client):
    _signed_in(bf, client, count=40)
    html = client.get("/purchases").text
    drawn = _drawn(html)

    # Hundreds of rows and hundreds of shop pictures at once are what made a
    # tap on this tab wait, so only the first screenful is laid out.
    assert drawn.count('class="mcard"') == FIRST
    assert drawn.count('class="rowlink"') == FIRST
    # The rest travels as text the browser only tokenises: no row is laid out
    # and no picture is asked for until it is poured into the page.
    assert '<script type="text/html" class="drip" data-drip="#buy-cards"' in html
    assert '<script type="text/html" class="drip" data-drip="#buy-rows"' in html
    assert _payload(html).count("<!--r-->") == (40 - FIRST) * 2


def test_every_product_is_on_the_page_even_when_it_is_not_drawn_yet(bf, client):
    _signed_in(bf, client, count=40, email="all@example.com")
    html = client.get("/purchases").text

    for i in range(40):
        assert "Product %03d" % i in html, i
    # And the page says what is still on its way.
    assert 'data-count="16"' in html
    assert "16 more products loading" in html


def test_the_figures_count_every_product_not_the_drawn_ones(bf, client):
    user = _signed_in(bf, client, count=40, email="sums@example.com")
    html = client.get("/purchases").text

    rows = bf.purchases.list_products(user["id"])
    spent = sum(r["spend_total"] for r in rows)
    assert len(rows) == 40
    # The header is the whole shelf: 40 products, not the 24 on screen.
    assert "AED %.0f</b> spent" % spent in html
    assert "<b>1</b> receipts" in html or "<b>1</b>" in html


def test_a_short_shelf_needs_no_pouring_at_all(bf, client):
    _signed_in(bf, client, count=6, email="short@example.com")
    html = client.get("/purchases").text

    assert html.count('class="mcard"') == 6
    assert 'type="text/html"' not in html
    assert 'id="buy-rest"' not in html


def test_the_first_products_drawn_are_the_ones_the_sort_asked_for(bf, client):
    _signed_in(bf, client, count=40, email="sort@example.com")
    drawn = _drawn(client.get("/purchases?sort=spend&dir=desc").text)

    # Sorting happens over every product on the server, so the screenful the
    # page opens with is the top of the real order, not the top of a page.
    assert "Product 000" in drawn
    assert "Product 039" not in drawn
    assert drawn.index("Product 000") < drawn.index("Product 001")


def test_the_page_pours_the_rest_in_when_the_browser_is_idle(bf, client):
    html = client.get("/login").text

    assert "requestIdleCallback" in html
    # A batch waits while the pictures already asked for are still coming, so
    # the wire stays free for the next tap.
    assert "ON_THE_WIRE" in html
    assert "__bfShots" in html
    # A stalled shop cannot stop the list, and scrolling to the end skips ahead.
    assert "NUDGE_MS" in html
    assert "NEAR_END" in html
    # Nothing is poured into the list the layout is not showing.
    assert "offsetParent!==null" in html


def test_a_product_shot_sweeps_until_it_lands_and_leaves_a_letter_if_it_never_does(bf, client):
    _signed_in(bf, client, count=30, email="shot@example.com")
    html = client.get("/purchases").text

    # The box is the right size from the start and says it is waiting.
    assert "@keyframes thumb-wait" in html
    assert "img.thumb.ok { background:#fff; animation:none; }" in html
    assert 'width="56" height="56"' in html
    # A picture's load never reaches window, so the listeners sit on document.
    assert 'document.addEventListener("load"' in html
    assert 'document.addEventListener("error"' in html
    # And a shot that never arrives leaves the product's letter behind.
    assert 'data-letter="P"' in html
    assert 'letter.className="thumb empty"' in html
    # A phone that dislikes movement gets a still box.
    assert "@media (prefers-reduced-motion: reduce) { img.thumb { animation:none; } }" in html


def test_the_buys_tab_carries_the_same_bar_as_the_home_tab(bf, client):
    _signed_in(bf, client, count=30, email="pin@example.com")

    home = client.get("/dashboard").text
    buys = client.get("/purchases").text
    for html in (home, buys):
        assert '<div class="spend-pin" id="spend-pin" aria-hidden="true">' in html
        assert 'class="pin-avg"' in html
        assert 'id="spend-avg"' in html
    # Scrolling past the figure leaves it on the bar, in both tabs.
    assert 'pin.classList.toggle("on", avg.getBoundingClientRect().bottom < top)' in buys
    # Buys also carries what was spent, the other figure in its header.
    assert 'class="pin-spent"' in buys
