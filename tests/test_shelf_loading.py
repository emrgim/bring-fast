"""The purchases tab opens on its board and the shelf arrives a batch at a time.

Switching tabs must never wait on a shelf: the page carries the first batch and
asks for the rest once it is on screen, one batch after another.
"""

import json
import re


def _shelf(bf, email, count=30, day="2026-08-10"):
    user = bf.db.create_user(email, "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "SHELF1",
            "order_no": "1",
            "invoice_date": day,
            "items": [
                {
                    "name": f"Thing {n:02d}",
                    "qty": 1,
                    # Spend descends with the number, so the display order is
                    # Thing 00 first and the batches are easy to name.
                    "unit_price": 500 - n,
                    "line_total": 500 - n,
                    "barcode": str(9000 + n),
                    "image_url": f"https://cdn.example.com/{n}.jpg",
                }
                for n in range(count)
            ],
        },
    )
    return user


def _signed_in(bf, client, email, **kw):
    user = _shelf(bf, email, **kw)
    client.post("/login", data={"email": email, "password": "secret1", "intent": "signin"})
    return user


def _cards(html):
    return re.findall(r'class="mcard"><a class="mcard-go" href="/purchases/ean:(\d+)', html)


def _note(html):
    line = html[html.index('id="buy-rest"') - 200 : html.index("</p>", html.index('id="buy-rest"'))]
    return {
        "url": (re.search(r'data-shelf="([^"]*)"', line) or [None, ""])[1].replace("&amp;", "&"),
        "next": int((re.search(r'data-next="(\d+)"', line) or [0, 0])[1]),
        "total": int((re.search(r'data-total="(\d+)"', line) or [0, 0])[1]),
        "batch": int((re.search(r'data-batch="(\d+)"', line) or [0, 0])[1]),
        "text": line,
    }


def test_the_board_is_on_screen_before_the_whole_shelf_is(bf, client):
    _signed_in(bf, client, "board@example.com", count=30)

    html = client.get("/purchases?range=all&grain=daily").text
    step = bf.purchases.SHELF_BATCH

    # The board is drawn by the page itself: spend, receipts and the bars are
    # never waiting on a shelf of products.
    assert "spend-bars" in html
    assert "receipts" in html
    assert "Daily average this period" in html
    # Only the first batch travels with it.
    assert len(_cards(html)) == step
    assert "Thing 00" in html
    assert f"Thing {step - 1:02d}" in html
    assert f"Thing {step:02d}" not in html
    note = _note(html)
    assert note["total"] == 30
    assert note["next"] == step
    assert note["batch"] == step
    assert f"{30 - step} more products loading" in note["text"]


def test_a_batch_continues_the_shelf_the_page_started(bf, client):
    _signed_in(bf, client, "next@example.com", count=70)
    page = client.get("/purchases?range=all&grain=daily").text
    note = _note(page)

    batch = client.get(f"{note['url']}&offset={note['next']}&limit={note['batch']}")
    assert batch.status_code == 200
    step = note["batch"]
    assert _cards(batch.text) == [str(9000 + n) for n in range(step, step * 2)]
    assert f'data-next="{step * 2}"' in batch.text
    assert 'data-total="70"' in batch.text
    # Both shapes travel together: the phone appends the cards, the desk the rows.
    assert 'data-shelf-cards' in batch.text
    assert 'data-shelf-rows' in batch.text
    assert batch.text.count("<tr") == step


def test_the_last_batch_says_there_is_nothing_left(bf, client):
    _signed_in(bf, client, "last@example.com", count=30)
    note = _note(client.get("/purchases?range=all&grain=daily").text)
    step = note["batch"]

    tail = client.get(f"{note['url']}&offset={step}&limit={step}")
    assert _cards(tail.text) == [str(9000 + n) for n in range(step, 30)]
    # Nowhere left to go, so the page stops asking and clears its line.
    assert 'data-next="0"' in tail.text


def test_every_product_arrives_once_and_only_once(bf, client):
    _signed_in(bf, client, "walk@example.com", count=61)
    page = client.get("/purchases?range=all&grain=daily").text
    note = _note(page)

    seen = _cards(page)
    at = note["next"]
    while at:
        batch = client.get(f"{note['url']}&offset={at}&limit={note['batch']}").text
        seen += _cards(batch)
        at = int(re.search(r'data-next="(\d+)"', batch)[1])

    assert len(seen) == len(set(seen)) == 61
    assert seen == [str(9000 + n) for n in range(61)]


def test_a_batch_keeps_the_window_and_the_order_of_its_page(bf, client):
    user = _signed_in(bf, client, "filter@example.com", count=0)
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "MIX1",
            "invoice_date": "2026-08-10",
            "items": [
                {"name": f"Water {n}", "qty": 1, "unit_price": 5, "line_total": 5, "barcode": str(7000 + n)}
                for n in range(40)
            ]
            + [
                {"name": f"Rice {n}", "qty": 1, "unit_price": 9, "line_total": 9, "barcode": str(7100 + n)}
                for n in range(40)
            ],
        },
    )
    page = client.get("/purchases?range=all&grain=daily&dept=Drinks&sort=name&dir=asc").text
    note = _note(page)

    assert note["total"] == 40
    assert "dept=Drinks" in note["url"]
    assert "sort=name" in note["url"]
    assert "dir=asc" in note["url"]

    names = sorted(f"Water {n}" for n in range(40))
    assert re.findall(r"<b>(Water \d+)</b>", page) == names[: note["batch"]]

    batch = client.get(f"{note['url']}&offset={note['next']}&limit={note['batch']}").text
    # A batch is the same shelf continued, not a second one under another
    # filter: only drinks, in the same order, picking up where the page stopped.
    assert "Rice" not in batch
    assert re.findall(r"<b>(Water \d+)</b>", batch) == names[note["batch"] :]


def test_a_batch_is_never_the_whole_shelf(bf, client):
    _signed_in(bf, client, "cap@example.com", count=90)
    note = _note(client.get("/purchases?range=all&grain=daily").text)

    asked = client.get(f"{note['url']}&offset=12&limit=9999")
    assert len(_cards(asked.text)) == bf.purchases.SHELF_BATCH_MAX


def test_a_batch_without_a_session_carries_no_products(bf, client):
    _signed_in(bf, client, "auth@example.com", count=20)
    note = _note(client.get("/purchases?range=all&grain=daily").text)
    client.get("/logout")

    locked = client.get(f"{note['url']}&offset=12&limit=12")
    assert locked.status_code == 401
    assert "Thing" not in locked.text


def test_the_shelf_is_counted_once_for_all_of_its_batches(bf, client, monkeypatch):
    _signed_in(bf, client, "memo@example.com", count=30)
    counted = []
    real = bf.purchases.list_products

    def once(*a, **kw):
        counted.append(kw)
        return real(*a, **kw)

    bf.purchases.forget_shelf()
    monkeypatch.setattr(bf.purchases, "list_products", once)

    note = _note(client.get("/purchases?range=all&grain=daily").text)
    client.get(f"{note['url']}&offset=12&limit=12")
    client.get(f"{note['url']}&offset=24&limit=12")

    # Reading the shelf in batches must not make the last batch cost as much
    # as the first: the whole history is counted once and held.
    assert len(counted) == 1


def test_a_new_receipt_is_never_read_off_a_held_shelf(bf, client):
    user = _signed_in(bf, client, "fresh@example.com", count=13)
    assert _note(client.get("/purchases?range=all&grain=daily").text)["total"] == 13

    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "grandiose",
            "store_name": "Grandiose",
            "invoice_no": "NEW1",
            "invoice_date": "2026-08-11",
            "items": [{"name": "Brand New", "qty": 1, "unit_price": 1000, "line_total": 1000, "barcode": "4242"}],
        },
    )

    fresh = client.get("/purchases?range=all&grain=daily").text
    assert _note(fresh)["total"] == 14
    assert "Brand New" in fresh


def test_a_late_batch_never_outranks_what_the_reader_asked_for(bf, client):
    _signed_in(bf, client, "prio@example.com", count=40)
    note = _note(client.get("/purchases?range=all&grain=daily").text)

    batch = client.get(f"{note['url']}&offset={note['next']}&limit={note['batch']}").text
    # Still not lazy: a batch is fetched because the app asked for it, not
    # because something was scrolled into view. It just yields the wire to the
    # page the reader may be opening next.
    assert 'loading="lazy"' not in batch
    assert 'loading="eager"' in batch
    assert 'fetchpriority="low"' in batch
    # And it reserves its box, so a landing thumbnail shoves nothing around,
    # and names the letter to fall back to if the shop never answers.
    assert 'width="56" height="56"' in batch
    assert 'width="48" height="48"' in batch
    assert 'data-letter="T"' in batch
    # The page's own first batch is not held back behind anything.
    page = client.get("/purchases?range=all&grain=daily").text
    first = page[page.index('id="buy-cards"') : page.index('id="buy-rest"')]
    assert 'fetchpriority="low"' not in first


def test_leaving_the_tab_gives_the_network_back(bf, client):
    _signed_in(bf, client, "leave@example.com", count=30)
    html = client.get("/purchases?range=all&grain=daily").text

    # A tap on a link is the reader leaving: the batch in flight is dropped so
    # the next page's own request does not queue behind product shots.
    assert "AbortController" in html
    assert 'closest("a[href]")' in html
    assert 'window.addEventListener("pagehide", pause)' in html
    assert "visibilitychange" in html
    # Coming back to a page the browser kept alive picks the shelf up again.
    assert "if(e.persisted) resume()" in html


def test_a_batch_waits_for_the_pictures_of_the_one_before_it(bf, client):
    _signed_in(bf, client, "wait@example.com", count=30)
    html = client.get("/purchases?range=all&grain=daily").text

    # One after another, not all at once: the next batch waits while the shots
    # already asked for are still on the wire.
    assert "ON_THE_WIRE=12" in html
    assert "window.__bfShots.live<ON_THE_WIRE" in html
    # A shop that never answers cannot hold the rest of the shelf back for ever,
    # and a reader already at the end is not waiting on politeness.
    assert "NUDGE_MS=1500" in html
    assert "NEAR_END=1200" in html
    assert "requestIdleCallback" in html
    # And the first batch is asked for only once the page it came with is done.
    assert 'document.readyState==="complete"' in html


def test_only_the_shape_the_layout_shows_is_put_into_the_page(bf, client):
    _signed_in(bf, client, "shape@example.com", count=40)
    note = _note(client.get("/purchases?range=all&grain=daily").text)
    html = client.get("/purchases?range=all&grain=daily").text

    # A batch carries a card and a row for every product, because it cannot know
    # which way the device is held.
    batch = client.get(f"{note['url']}&offset={note['next']}&limit={note['batch']}").text
    rest = 40 - note["next"]
    assert batch.count('class="mcard"') == rest
    assert batch.count("<tr") == rest
    # They arrive inert, so nothing in the shape nobody is reading is ever
    # asked for: a phone fetches each product's picture once, not twice.
    assert "<template data-shelf-cards>" in batch
    assert "<template data-shelf-rows>" in batch
    # Only the drawn one is put in, and turning the screen catches the other up.
    assert "offsetParent!==null" in html
    assert 'window.addEventListener("resize", flush' in html


def test_the_shelf_line_says_what_is_still_coming(bf, client):
    _signed_in(bf, client, "line@example.com", count=30)
    html = client.get("/purchases?range=all&grain=daily").text

    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert "6 more products loading" in _note(html)["text"]
    # A shelf that fits in one batch has nothing to announce.
    _signed_in(bf, client, "short@example.com", count=4)
    short = client.get("/purchases?range=all&grain=daily").text
    assert "more products loading" not in _note(short)["text"]
    assert _note(short)["next"] == 0
    assert "drip-rest off done" in short


def test_an_empty_range_still_says_so(bf, client):
    bf.db.create_user("none@example.com", "secret1")
    client.post("/login", data={"email": "none@example.com", "password": "secret1", "intent": "signin"})

    html = client.get("/purchases?range=all&grain=daily").text
    assert "No invoices in this range." in html
    assert _note(html)["total"] == 0


def test_a_bar_carries_only_what_a_tap_reads(bf, client):
    user = _shelf(bf, "bars@example.com", count=3, day="2026-08-10")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "grandiose",
            "store_name": "Grandiose Marina",
            "invoice_no": "BAR2",
            "invoice_date": "2026-08-12",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 7, "line_total": 7, "barcode": "5"}],
        },
    )
    client.post("/login", data={"email": "bars@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/purchases?range=all&grain=daily").text
    marks = json.loads(html[html.index("var days = ") + 11 : html.index(";\n  var pop")])

    # The bars are drawn in the page already, so their heights, their window
    # bounds and the selected flag have no reason to travel a second time —
    # over years of daily bars that repetition weighs more than the products.
    every = {k for m in marks for k in m}
    assert every <= {"date", "label", "spend", "count", "invoices"}
    assert "pct" not in every
    assert "win_start" not in every
    assert "selected" not in every
    # A tap still names the day, the money and the receipts behind it.
    tapped = next(m for m in marks if m["date"] == "2026-08-12")
    assert tapped["count"] == 1
    assert tapped["spend"] == 7
    assert tapped["invoices"][0]["invoice_no"] == "BAR2"
    assert tapped["invoices"][0]["store"] == "Grandiose Marina"
    # A day nobody shopped on carries no empty list, and a label that is the
    # date is left for the page to fall back to.
    quiet = next(m for m in marks if m["date"] == "2026-08-11")
    assert "invoices" not in quiet
    assert "label" not in quiet
    assert '(d.label||d.date)' in html


def test_the_dashboard_does_not_ship_a_popup_it_never_opens(bf, client):
    _signed_in(bf, client, "dash@example.com", count=3)

    html = client.get("/dashboard?range=all&grain=daily").text
    # Only the purchases board has a day popup: the dashboard's bars are links.
    assert "var days = " not in html
    assert "spend-bars" in html


def test_the_app_sends_its_markup_compressed(bf, client):
    _signed_in(bf, client, "zip@example.com", count=30)

    page = client.get("/purchases?range=all&grain=daily", headers={"accept-encoding": "gzip"})
    assert page.headers.get("content-encoding") == "gzip"
    plain = client.get("/purchases?range=all&grain=daily", headers={"accept-encoding": "identity"})
    assert not plain.headers.get("content-encoding")
    # Bars and rows are the same few characters over and over: on a phone
    # connection this is most of what opening a tab costs.
    assert int(page.headers["content-length"]) < len(plain.content) / 4

    batch = client.get("/purchases/rows?range=all&grain=daily&offset=12&limit=12", headers={"accept-encoding": "gzip"})
    assert batch.headers.get("content-encoding") == "gzip"

    # Already compressed formats are handed on untouched, and the MCP wire says
    # no-transform, so it is left exactly as it was written.
    font = client.get("/static/fonts/ibm-plex-mono-400-latin.woff2", headers={"accept-encoding": "gzip"})
    assert not font.headers.get("content-encoding")
    mcp = client.post(
        "/mcp",
        headers={"accept-encoding": "gzip", "accept": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert not mcp.headers.get("content-encoding")


def test_the_service_worker_saves_the_batches_too(client):
    sw = client.get("/sw.js").text

    # A saved tab is only readable offline if the batches it reads were saved.
    assert '"/purchases/rows"' in sw
    assert "isShelf" in sw
    assert "warmShelf" in sw
    assert "data-shelf" in sw
    # A batch is one slice of one shelf: an offset is never answered with
    # another offset's products.
    assert "ignoreSearch" not in sw[sw.index("async function batch(") : sw.index("async function trimShelf(")]
    # Bounded, so a filter tried once does not sit on the device for good.
    assert "SHELF_CAP" in sw
    assert "trimShelf" in sw
    assert "SHELF_WARM" in sw


def test_the_service_worker_warms_one_thing_at_a_time(client):
    sw = client.get("/sw.js").text

    # Warming behind an open page must not put the whole saved app on the wire
    # in front of it.
    assert "for (const url of urls || [])" in sw
    assert "for (const req of keys)" in sw
    assert "bf-pwa-v7" in sw
