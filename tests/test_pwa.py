def test_pwa_manifest_and_icons(client):
    man = client.get("/manifest.webmanifest")
    assert man.status_code == 200
    assert "manifest" in (man.headers.get("content-type") or "")
    data = man.json()
    assert data["display"] == "standalone"
    assert data["start_url"] == "/dashboard"
    assert data["name"] == "Bring Fast"
    assert data["background_color"] == "#ffffff"
    assert data["theme_color"] == "#ffffff"
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert "192x192" in sizes
    assert "512x512" in sizes
    # An installed app opens straight on the tab you long-pressed for.
    assert {s["url"] for s in data["shortcuts"]} == {"/purchases", "/stores"}


def test_pwa_service_worker(client):
    sw = client.get("/sw.js")
    assert sw.status_code == 200
    assert "javascript" in (sw.headers.get("content-type") or "")
    assert "skipWaiting" in sw.text
    assert "bf-pwa-v6" in sw.text
    assert sw.headers.get("cache-control", "").startswith("no-cache") or "no-cache" in (
        sw.headers.get("cache-control") or ""
    )


def test_service_worker_serves_pages_offline(client):
    sw = client.get("/sw.js").text
    # Navigations are cached, so a page already seen opens without a network.
    assert 'req.mode === "navigate"' in sw
    assert "/offline" in sw
    assert "cachedPage" in sw
    # Offline clients retry on a ten minute cadence.
    assert "600000" in sw
    assert "periodicsync" in sw
    assert "bf-refresh" in sw


def test_service_worker_saves_the_tabs_before_they_are_opened(bf, client):
    sw = client.get("/sw.js").text
    # Going offline on the dashboard still leaves purchases and stores readable.
    assert "warmPages" in sw
    assert '"bf-warm"' in sw
    assert "WARM_FLOOR_MS" in sw  # asked for once a minute at most

    bf.db.create_user("warm@example.com", "secret1")
    client.post("/login", data={"email": "warm@example.com", "password": "secret1", "intent": "signin"})
    html = client.get("/stores").text
    assert 'TABS=["/dashboard","/purchases","/stores"]' in html
    assert '{type:"bf-warm", urls:TABS}' in html
    # It waits for the open page to finish first — warming is never a race.
    assert "requestIdleCallback" in html

    client.get("/logout")
    assert "bf-warm" not in client.get("/login").text


def test_service_worker_keeps_the_pictures_a_saved_page_needs(client):
    sw = client.get("/sw.js").text
    # Product shots come from the shops, so a saved page needs them saved too.
    assert "IMAGES" in sw
    assert "isImage" in sw
    assert 'res.type === "opaque"' in sw
    # And the shelf is capped instead of growing for as long as the app lives.
    assert "IMAGE_CAP" in sw
    assert "trim(" in sw
    # Warming a page fetches the page alone, so the pictures it names are
    # collected too — otherwise a tab saved but never opened shows blanks.
    assert "warmShots" in sw
    assert "<img" in sw
    # But only the shots that page draws, a few at a time: a shelf of hundreds
    # warmed all at once in the background is what made a tap wait.
    assert "SHOT_WARM = 12" in sw
    assert "WARM_AT_ONCE = 3" in sw
    assert "urls.length < SHOT_WARM" in sw
    assert "pool(urls, WARM_AT_ONCE" in sw
    # And the shelf a person did scroll through stays on the device.
    assert "IMAGE_CAP = 700" in sw


def test_service_worker_never_saves_a_page_it_was_redirected_to(client):
    sw = client.get("/sw.js").text
    # An expired session redirects to sign-in; saving that under /dashboard
    # would show a login form named "Dashboard" the next time the network drops.
    assert "samePage" in sw
    assert "res.redirected" in sw


def test_a_form_sent_with_no_network_says_nothing_was_saved(client):
    sw = client.get("/sw.js").text
    assert 'req.method !== "GET"' in sw
    assert "NOT_SAVED" in sw
    assert "Not saved" in sw
    # A cache must never answer a form: it either reaches the server or fails.
    assert "history.back()" in sw
    # And it is a screen inside the installed app, so it cannot be pinched either.
    assert "touch-action:pan-x pan-y" in sw
    assert "gesturestart" in sw


def test_service_worker_keeps_sessions_and_updates_off_the_cache(client):
    sw = client.get("/sw.js").text
    for path in ("/update/", "/health", "/oauth", "/login", "/mcp"):
        assert f'"{path}"' in sw
    # Signing out must not leave another person's pages on the device.
    assert '"/logout"' in sw
    assert "caches.delete(PAGES)" in sw


def test_offline_fallback_page(client):
    r = client.get("/offline")
    assert r.status_code == 200
    assert "text/html" in (r.headers.get("content-type") or "")
    assert "Offline" in r.text
    # It counts the ten minute retry down instead of sitting there dead.
    assert "600000" in r.text
    assert "/dashboard" in r.text
    # A reachable network is not a reachable server, so it asks before claiming
    # to be back — navigator.onLine would lie whenever only the server is down.
    assert 'fetch("/health"' in r.text
    assert "navigator.onLine" not in r.text
    # Reaching it inside the installed app must not turn the zoom back on.
    assert "user-scalable=no" in r.text
    assert "touch-action:pan-x pan-y" in r.text


def test_health_marks_each_boot_so_a_restart_is_visible(client):
    first = client.get("/health").json()
    assert first["boot"]
    assert "revision" in first
    # Same process, same boot id — a changed one means the update is live.
    assert client.get("/health").json()["boot"] == first["boot"]


def test_the_offline_shell_carries_its_own_font(client):
    sw = client.get("/sw.js").text
    # Precached with the icons: the first launch with no network still looks
    # like the app rather than falling back to the system monospace. Every
    # weight, so one met for the first time offline does not fall back either.
    for weight in ("400", "500", "600", "700"):
        for subset in ("latin", "latin-ext"):
            assert f"/static/fonts/ibm-plex-mono-{weight}-{subset}.woff2" in sw
    offline = client.get("/offline").text
    assert "@font-face" in offline
    assert "/static/fonts/" in offline


def test_pwa_apple_icon_and_head(client):
    icon = client.get("/apple-touch-icon.png")
    assert icon.status_code == 200
    assert icon.headers.get("content-type", "").startswith("image/")
    html = client.get("/login").text
    assert 'rel="manifest"' in html
    assert "apple-mobile-web-app-capable" in html
    assert "mobile-web-app-capable" in html
    assert "apple-touch-icon" in html
    assert "serviceWorker" in html
    assert 'name="color-scheme"' in html
    assert "light dark" in html
    assert "data-theme-toggle" in html
    assert "bf-theme" in html
    assert "IBM Plex Mono" in html
    assert 'data-theme' in html or "__bfTheme" in html
