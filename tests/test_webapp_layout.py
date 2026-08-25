"""Installed, Bring Fast has to behave like an app: no zoom, no clipping, no dead ends."""


def _signed_in(bf, client, email="app@example.com"):
    bf.db.create_user(email, "secret1")
    client.post("/login", data={"email": email, "password": "secret1", "intent": "signin"})


def test_signing_out_stays_reachable_on_a_phone(bf, client):
    _signed_in(bf, client)
    html = client.get("/dashboard").text

    # The dock carries the tabs, so the phone header only drops the address —
    # hiding the whole block would leave no way out of the account.
    assert "nav.topnav .tabs { display:none !important; }" in html
    assert "nav.topnav .user { display:none" not in html
    assert "nav.topnav .user .who { display:none; }" in html
    assert '<span class="who">app@example.com · </span><a href="/logout">Sign out</a>' in html


def test_a_landscape_notch_never_clips_a_row(bf, client):
    html = client.get("/login").text

    assert "padding-left:env(safe-area-inset-left); padding-right:env(safe-area-inset-right);" in html
    # The fixed dock sits above the home bar and inside the side insets.
    assert "padding-left:max(8px, env(safe-area-inset-left));" in html
    assert "calc(8px + env(safe-area-inset-bottom))" in html


def test_date_fields_do_not_zoom_the_page_on_ios(bf, client):
    _signed_in(bf, client, "zoom@example.com")
    html = client.get("/purchases").text

    # Under 16px iOS zooms in on focus and never zooms back out.
    assert ".filters input[type=date] { font-size:16px;" in html


def test_taps_land_at_once_and_never_zoom(bf, client):
    html = client.get("/login").text

    assert "touch-action:manipulation" in html
    assert "-webkit-tap-highlight-color:transparent" in html
    # iOS draws its own inputs and buttons unless told not to.
    assert "-webkit-appearance:none; appearance:none;" in html


def _installed_block(html):
    block = html[html.index("@media (display-mode: standalone)") :]
    return block[: block.index("\n    }\n")]


def test_the_installed_app_does_not_rubber_band(bf, client):
    block = _installed_block(client.get("/login").text)

    assert "overscroll-behavior-y:contain" in block
    assert "env(safe-area-inset-top)" in block
    # Nothing offers to install an app that is already installed.
    assert "#pwa-install, #ios-install { display:none !important; }" in block


def test_the_installed_app_cannot_be_pinched(bf, client):
    _signed_in(bf, client, "pinch@example.com")
    html = client.get("/purchases").text

    # Zooming out past scale 1 parks the sticky header and the dock off the
    # screen, so installed the app takes the pan and refuses the pinch.
    assert "touch-action:pan-x pan-y" in _installed_block(html)
    assert (
        'v.setAttribute("content","width=device-width, initial-scale=1, '
        'maximum-scale=1, user-scalable=no, viewport-fit=cover")' in html
    )
    # WebKit keeps its pinch out of reach of the viewport rules.
    for name in ("gesturestart", "gesturechange", "gestureend"):
        assert name in html
    assert "{passive:false}" in html
    # Home-screen Safari that predates the display-mode query still gets it.
    assert ":root.installed, :root.installed body { touch-action:pan-x pan-y;" in html


def test_the_buy_page_is_never_wider_than_the_phone(bf, client):
    _signed_in(bf, client, "wide@example.com")
    html = client.get("/purchases").text
    phone = html[html.index("@media (max-width:720px)") :]
    board = phone[phone.index(".purchases-board {\n") :]
    board = board[: board.index("}")]

    # The filter row runs edge to edge by cancelling the wrap padding exactly.
    # Any leftover desktop nudge on the board makes the page wider than the
    # screen, and a page wider than the screen can be dragged and pinched.
    assert ".wrap { padding:10px 12px" in phone
    assert "margin:0 -12px 10px" in phone
    assert "margin:0;" in board


def test_a_browser_tab_can_still_be_zoomed(bf, client):
    html = client.get("/login").text

    # A page in a tab is still a page: pinching it is the reader's business.
    assert '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>' in html
    assert html.count("user-scalable=no") == 1  # the installed branch only


def test_ios_is_told_where_its_install_button_is(bf, client):
    html = client.get("/login").text

    # Safari has no beforeinstallprompt to defer, so it gets directions.
    assert 'id="ios-install"' in html
    assert "Add to Home Screen" in html
    assert "navigator.standalone===true" in html
    # And it is asked once: "Not now" sticks.
    assert 'localStorage.setItem(KEY,"off")' in html
    assert 'id="ios-install-hide"' in html


def test_the_full_height_board_falls_back_before_dvh(bf, client):
    html = client.get("/login").text

    # Safari before 15.4 has no dvh, and an unread height collapses the board.
    assert "height:calc(100vh - var(--sticky-top, 56px) - 12px);" in html
    assert "height:calc(100dvh - var(--sticky-top, 56px) - 12px);" in html


def test_the_open_tab_is_announced_not_just_coloured(bf, client):
    _signed_in(bf, client, "aria@example.com")
    html = client.get("/purchases").text

    dock = html[html.index('<footer class="dock"') : html.index("</footer>")]
    assert dock.count('aria-current="page"') == 1
    assert '>Buys<' in dock
    assert html.count('aria-current="page"') == 2  # once in the dock, once in the tabs


def test_the_offline_chip_says_how_old_the_page_is(bf, client):
    _signed_in(bf, client, "age@example.com")
    html = client.get("/dashboard").text

    # A tooltip cannot be reached on a phone, so the age is on screen.
    assert '" · saved "+saved' in html
    assert 'type:"bf-page-info"' in html


def test_a_pdf_receipt_offers_a_way_out_on_ios(bf, client, tmp_path):
    user = bf.db.create_user("pdf@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "PDF1",
            "order_no": "1",
            "invoice_date": "2026-08-20",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 3, "line_total": 3, "barcode": "777"}],
        },
    )
    folder = bf.purchases.receipt_dir() / "carrefour"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "PDF1.pdf").write_bytes(b"%PDF-1.4 test")
    client.post("/login", data={"email": "pdf@example.com", "password": "secret1", "intent": "signin"})

    html = client.get("/receipts/carrefour/PDF1").text
    # iPhone and iPad draw nothing inside <object>, so the link is always there.
    assert 'class="pdf-out"' in html
    assert "Open the PDF" in html
    assert "/receipts/carrefour/PDF1/file.pdf" in html
    # And the receipt viewer carries the app font like every other page.
    assert "/static/fonts/ibm-plex-mono-400-latin.woff2" in html
    assert "fonts.googleapis.com" not in html
    # A receipt is a scan of paper: unlike the app screens it stays pinchable,
    # because reading the small print is the only reason to open it.
    assert "user-scalable=no" not in html
