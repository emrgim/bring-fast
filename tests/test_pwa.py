def test_pwa_manifest_and_icons(client):
    man = client.get("/manifest.webmanifest")
    assert man.status_code == 200
    assert "manifest" in (man.headers.get("content-type") or "")
    data = man.json()
    assert data["display"] == "standalone"
    assert data["start_url"] == "/dashboard"
    assert data["name"] == "Bring Fast"
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert "192x192" in sizes
    assert "512x512" in sizes


def test_pwa_service_worker(client):
    sw = client.get("/sw.js")
    assert sw.status_code == 200
    assert "javascript" in (sw.headers.get("content-type") or "")
    assert "skipWaiting" in sw.text
    assert sw.headers.get("cache-control", "").startswith("no-cache") or "no-cache" in (
        sw.headers.get("cache-control") or ""
    )


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
