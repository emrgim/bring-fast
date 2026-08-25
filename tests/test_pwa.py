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


def test_pwa_service_worker(client):
    sw = client.get("/sw.js")
    assert sw.status_code == 200
    assert "javascript" in (sw.headers.get("content-type") or "")
    assert "skipWaiting" in sw.text
    assert "bf-pwa-v3" in sw.text
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


def test_health_marks_each_boot_so_a_restart_is_visible(client):
    first = client.get("/health").json()
    assert first["boot"]
    assert "revision" in first
    # Same process, same boot id — a changed one means the update is live.
    assert client.get("/health").json()["boot"] == first["boot"]


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
