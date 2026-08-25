"""Nothing waits for a scroll, nothing waits on a font CDN, nothing is fetched twice."""

WEIGHTS = ("400", "500", "600", "700")


def _shelf(bf, email="load@example.com"):
    user = bf.db.create_user(email, "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": "LOAD1",
            "order_no": "1",
            "invoice_date": "2026-08-10",
            "items": [
                {
                    "name": "Milk",
                    "qty": 1,
                    "unit_price": 9,
                    "line_total": 9,
                    "barcode": "5001",
                    "image_url": "https://cdn.example.com/milk.jpg",
                },
                {
                    "name": "Rice",
                    "qty": 2,
                    "unit_price": 20,
                    "line_total": 40,
                    "barcode": "5002",
                    "image_url": "https://cdn.example.com/rice.jpg",
                },
            ],
        },
    )
    return user


def _signed_in(bf, client, email="load@example.com"):
    user = _shelf(bf, email)
    client.post("/login", data={"email": email, "password": "secret1", "intent": "signin"})
    return user


def test_no_picture_waits_for_a_scroll(bf, client):
    _signed_in(bf, client)

    for path in ("/dashboard", "/purchases", "/purchases/ean:5001"):
        html = client.get(path).text
        assert 'loading="lazy"' not in html, path
        assert 'loading="eager"' in html, path
        assert "cdn.example.com" in html, path


def test_pictures_reserve_their_box_before_they_arrive(bf, client):
    _signed_in(bf, client, "box@example.com")
    html = client.get("/purchases").text

    # Width and height with the source, so a slow shop CDN cannot shove the
    # rows around as each thumbnail lands.
    assert 'width="56" height="56"' in html
    assert 'width="48" height="48"' in html
    # Decoding off the main thread is not lazy loading: the request still goes
    # out at once, only the paint is handed to the compositor.
    assert 'decoding="async"' in html


def test_the_product_shot_leads_its_own_page(bf, client):
    _signed_in(bf, client, "hero@example.com")
    html = client.get("/purchases/ean:5001").text

    assert 'fetchpriority="high"' in html
    assert 'width="120" height="120"' in html


def test_pages_wait_on_no_font_cdn(bf, client):
    _signed_in(bf, client, "font@example.com")

    for path in ("/login", "/dashboard", "/purchases", "/stores"):
        html = client.get(path).text
        assert "fonts.googleapis.com" not in html, path
        assert "fonts.gstatic.com" not in html, path
        assert "/static/fonts/ibm-plex-mono-400-latin.woff2" in html, path
        assert 'rel="preload"' in html, path


def test_the_font_is_served_by_the_app_and_asked_for_once(client):
    for weight in WEIGHTS:
        for subset in ("latin", "latin-ext"):
            r = client.get(f"/static/fonts/ibm-plex-mono-{weight}-{subset}.woff2")
            assert r.status_code == 200, weight
            assert r.content[:4] == b"wOF2", weight
            # The filename names the subset, so the bytes behind it never change.
            assert "immutable" in (r.headers.get("cache-control") or "")
            assert "max-age=31536000" in (r.headers.get("cache-control") or "")


def test_the_font_covers_every_weight_the_app_draws(client):
    css = client.get("/login").text
    for weight in WEIGHTS:
        assert f"font-weight:{weight}" in css
    # Text shows in the fallback face while the subset lands, never invisibly.
    assert "font-display:swap" in css


def test_a_page_is_always_asked_for_again_but_a_logo_is_not(bf, client):
    _signed_in(bf, client, "cache@example.com")

    page = client.get("/dashboard")
    assert page.headers.get("cache-control") == "no-cache"

    logo = client.get("/static/logos/carrefour.svg")
    if logo.status_code == 200:
        assert "max-age=86400" in (logo.headers.get("cache-control") or "")
        assert "immutable" not in (logo.headers.get("cache-control") or "")


def test_live_state_still_overrides_the_page_policy(bf, client):
    _signed_in(bf, client, "live@example.com")

    assert "no-store" in (client.get("/update/status").headers.get("cache-control") or "")
    assert "no-cache" in (client.get("/sw.js").headers.get("cache-control") or "")
    assert "no-cache" in (client.get("/manifest.webmanifest").headers.get("cache-control") or "")
