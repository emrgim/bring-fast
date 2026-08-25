"""The purchases tab opens on its board and the shelf arrives a batch at a time.

Switching tabs must never wait on a shelf: the page carries the first batch and
asks for the rest once it is on screen, one batch after another.
"""

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
    return re.findall(r'class="mcard" href="/purchases/ean:(\d+)', html)


def _note(html):
    line = html[html.index('id="shelf-note"') - 200 : html.index("</p>", html.index('id="shelf-note"'))]
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
    assert f"{step} of 30 products" in note["text"]


def test_a_batch_continues_the_shelf_the_page_started(bf, client):
    _signed_in(bf, client, "next@example.com", count=30)
    page = client.get("/purchases?range=all&grain=daily").text
    note = _note(page)

    batch = client.get(f"{note['url']}&offset={note['next']}&limit={note['batch']}")
    assert batch.status_code == 200
    step = note["batch"]
    assert _cards(batch.text) == [str(9000 + n) for n in range(step, step * 2)]
    assert f'data-next="{step * 2}"' in batch.text
    assert 'data-total="30"' in batch.text
    # Both shapes travel together: the phone appends the cards, the desk the rows.
    assert 'data-shelf-cards' in batch.text
    assert 'data-shelf-rows' in batch.text
    assert batch.text.count("<tr") == step


def test_the_last_batch_says_there_is_nothing_left(bf, client):
    _signed_in(bf, client, "last@example.com", count=20)
    note = _note(client.get("/purchases?range=all&grain=daily").text)

    tail = client.get(f"{note['url']}&offset=12&limit=12")
    assert _cards(tail.text) == [str(9000 + n) for n in range(12, 20)]
    # Nowhere left to go, so the page stops asking and clears its line.
    assert 'data-next="0"' in tail.text


def test_every_product_arrives_once_and_only_once(bf, client):
    _signed_in(bf, client, "walk@example.com", count=41)
    page = client.get("/purchases?range=all&grain=daily").text
    note = _note(page)

    seen = _cards(page)
    at = note["next"]
    while at:
        batch = client.get(f"{note['url']}&offset={at}&limit={note['batch']}").text
        seen += _cards(batch)
        at = int(re.search(r'data-next="(\d+)"', batch)[1])

    assert len(seen) == len(set(seen)) == 41
    assert seen == [str(9000 + n) for n in range(41)]


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
                for n in range(14)
            ]
            + [
                {"name": f"Rice {n}", "qty": 1, "unit_price": 9, "line_total": 9, "barcode": str(7100 + n)}
                for n in range(14)
            ],
        },
    )
    page = client.get("/purchases?range=all&grain=daily&dept=Drinks&sort=name&dir=asc").text
    note = _note(page)

    assert note["total"] == 14
    assert "dept=Drinks" in note["url"]
    assert "sort=name" in note["url"]
    assert "dir=asc" in note["url"]

    names = sorted(f"Water {n}" for n in range(14))
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
    _signed_in(bf, client, "prio@example.com", count=20)
    note = _note(client.get("/purchases?range=all&grain=daily").text)

    batch = client.get(f"{note['url']}&offset=12&limit=12").text
    # Still not lazy: a batch is fetched because the app asked for it, not
    # because something was scrolled into view. It just yields the wire to the
    # page the reader may be opening next.
    assert 'loading="lazy"' not in batch
    assert 'loading="eager"' in batch
    assert 'fetchpriority="low"' in batch
    # And it reserves its box, so a landing thumbnail shoves nothing around.
    assert 'width="56" height="56"' in batch
    assert 'width="48" height="48"' in batch
    # The page's own first batch is not held back behind anything.
    page = client.get("/purchases?range=all&grain=daily").text
    shelf = page[page.index('id="shelf-cards"') : page.index('id="shelf-note"')]
    assert 'fetchpriority="low"' not in shelf


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

    # One after another, not all at once — and a shop CDN that never answers
    # cannot hold the rest of the shelf back for ever.
    assert 'img.addEventListener("load", done, {once:true})' in html
    assert 'img.addEventListener("error", done, {once:true})' in html
    assert "Promise.race" in html
    assert "SHOT_MS=2500" in html
    assert "requestIdleCallback" in html
    # And the first batch is asked for only once the page it came with is done.
    assert 'document.readyState==="complete"' in html


def test_the_shelf_line_says_where_the_app_is_up_to(bf, client):
    _signed_in(bf, client, "line@example.com", count=30)
    html = client.get("/purchases?range=all&grain=daily").text

    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert "Loading the shelf · 12 of 30 products" in _note(html)["text"]
    # A shelf that fits in one batch has nothing to announce.
    _signed_in(bf, client, "short@example.com", count=4)
    short = client.get("/purchases?range=all&grain=daily").text
    assert "Loading the shelf" not in _note(short)["text"]
    assert _note(short)["next"] == 0


def test_an_empty_range_still_says_so(bf, client):
    bf.db.create_user("none@example.com", "secret1")
    client.post("/login", data={"email": "none@example.com", "password": "secret1", "intent": "signin"})

    html = client.get("/purchases?range=all&grain=daily").text
    assert "No invoices in this range." in html
    assert _note(html)["total"] == 0


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
    assert "bf-pwa-v6" in sw
