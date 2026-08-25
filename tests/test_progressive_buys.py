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


def _line(html):
    """The line under the list, without the loader that also names its words."""
    at = html.index('id="buy-rest"')
    return html[at : html.index("</p>", at)]


def test_the_page_draws_a_screenful_and_leaves_the_rest_off_it(bf, client):
    _signed_in(bf, client, count=40)
    html = client.get("/purchases").text

    # Hundreds of rows and hundreds of shop pictures at once are what made a tap
    # on this tab wait, so only the first screenful is in the page.
    assert html.count('class="mcard"') == FIRST
    assert html.count('class="rowlink"') == FIRST
    # And the rest is not in it at all — not even as text. An account with
    # hundreds of products costs the same to open as one with a screenful.
    assert 'type="text/html"' not in html
    assert "Product %03d" % (FIRST - 1) in html
    assert "Product %03d" % FIRST not in html


def test_the_page_says_what_is_still_coming_and_where_to_get_it(bf, client):
    _signed_in(bf, client, count=40, email="all@example.com")
    html = client.get("/purchases").text

    line = _line(html)
    assert 'data-total="40"' in line
    assert 'data-next="%d"' % FIRST in line
    assert "16 more products loading" in line
    # The rest is asked for from the server, a batch at a time, carrying the
    # window this page resolved.
    assert 'data-shelf="/purchases/rows?' in line


def test_the_figures_count_every_product_not_the_drawn_ones(bf, client):
    user = _signed_in(bf, client, count=40, email="sums@example.com")
    html = client.get("/purchases").text

    rows = bf.purchases.list_products(user["id"])
    spent = sum(r["spend_total"] for r in rows)
    assert len(rows) == 40
    # The header is the whole shelf: 40 products, not the 24 on screen.
    assert "AED %.0f</b> spent" % spent in html
    assert "<b>1</b> receipts" in html or "<b>1</b>" in html


def test_a_short_shelf_needs_no_batches_at_all(bf, client):
    _signed_in(bf, client, count=6, email="short@example.com")
    html = client.get("/purchases").text

    assert html.count('class="mcard"') == 6
    # Nowhere left to go, so the line has nothing to say and is not shown, and
    # the loader stops before asking the server for anything.
    assert 'data-next="0"' in _line(html)
    assert "more products loading" not in _line(html)
    assert "drip-rest off done" in html


def test_the_first_products_drawn_are_the_ones_the_sort_asked_for(bf, client):
    _signed_in(bf, client, count=40, email="sort@example.com")
    drawn = client.get("/purchases?sort=spend&dir=desc").text

    # Sorting happens over every product on the server, so the screenful the
    # page opens with is the top of the real order, not the top of a page.
    assert "Product 000" in drawn
    assert "Product 039" not in drawn
    assert drawn.index("Product 000") < drawn.index("Product 001")


def test_the_page_asks_for_the_rest_when_the_browser_is_idle(bf, client):
    _signed_in(bf, client, count=40, email="idle@example.com")
    html = client.get("/purchases").text

    assert "requestIdleCallback" in html
    # A batch waits while the pictures already asked for are still coming, so
    # the wire stays free for the next tap.
    assert "ON_THE_WIRE" in html
    assert "__bfShots" in html
    # A stalled shop cannot stop the list, and scrolling to the end skips ahead.
    assert "NUDGE_MS" in html
    assert "NEAR_END" in html
    # Nothing is put into the list the layout is not showing.
    assert "offsetParent!==null" in html
    # And nothing is asked for until the page itself is done: a picture added
    # while the document is loading holds the load event open, which left the
    # browser spinning for as long as the whole shelf took.
    assert 'if(document.readyState==="complete") idle(more);' in html
    assert 'else window.addEventListener("load", function(){ idle(more); });' in html


def test_no_page_carries_a_loader_for_a_payload_nothing_sends(bf, client):
    _signed_in(bf, client, count=40, email="lean@example.com")
    login = client.get("/login").text
    buys = client.get("/purchases").text

    # The sweep and the letter fallback belong to every page that shows a
    # picture, so they stay in the base template.
    for html in (login, buys):
        assert "__bfShots" in html
    # The pourer that read products out of the page went with the payload it read.
    # Only the tab that asks the server for batches carries a loader now.
    assert "script.drip[data-drip]" not in login
    assert "script.drip[data-drip]" not in buys
    assert "ON_THE_WIRE" not in login
    assert "ON_THE_WIRE" in buys


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
