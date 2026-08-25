"""The page never goes blank: it counts a restart down and says when it is offline."""


def _signed_in(bf, client, email="offline@example.com"):
    bf.db.create_user(email, "secret1")
    client.post("/login", data={"email": email, "password": "secret1", "intent": "signin"})


def test_pressing_update_hands_over_to_a_countdown(bf, client):
    _signed_in(bf, client)
    html = client.get("/stores").text

    assert 'id="upd-veil"' in html
    assert 'id="upd-count"' in html
    assert 'id="upd-step"' in html
    # The overlay covers the page instead of leaving it half-updated.
    assert ".veil {\n      position:fixed; inset:0; z-index:300" in html
    assert "Restarting Bring Fast" in html
    # The countdown reads the seconds the server itself reported.
    assert "d.ready_in" in html
    assert "d.restart_in" in html


def test_the_page_reloads_only_once_the_new_server_answers(bf, client):
    _signed_in(bf, client, "boot@example.com")
    html = client.get("/stores").text

    # A blind reload during a restart lands on a browser error page.
    assert 'fetch("/health"' in html
    assert "now===before" in html
    assert "location.reload()" in html
    # And if the restart drags on, the page stays put with a way out.
    assert 'id="upd-reload"' in html
    assert "Still restarting" in html


def test_a_lost_apply_response_is_treated_as_a_restart(bf, client):
    _signed_in(bf, client, "lost@example.com")
    html = client.get("/stores").text

    assert "restart(before, {ready_in:12, restart_in:1})" in html


def test_offline_pages_say_so_and_name_the_next_try(bf, client):
    _signed_in(bf, client, "chip@example.com")
    html = client.get("/stores").text

    assert 'id="net-chip"' in html
    assert "Offline" in html
    # Ten minutes between offline retries; online checks happen at once.
    assert "OFFLINE_MS=600000" in html
    assert 'window.addEventListener("online"' in html
    assert 'window.addEventListener("offline"' in html


def test_signed_out_pages_are_offline_aware_too(client):
    html = client.get("/login").text

    assert 'id="net-chip"' in html
    assert "OFFLINE_MS=600000" in html
    # No update controls without a session.
    assert 'id="upd-veil"' not in html


def test_update_state_is_never_cached(bf, client):
    _signed_in(bf, client, "nostore@example.com")
    r = client.get("/update/status")
    assert r.status_code == 200
    assert "no-store" in (r.headers.get("cache-control") or "")
    assert r.json()["boot"]
