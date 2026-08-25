"""A saved store login is printed, not typed, so the tab opens without a login form.

Browsers offer to autofill the moment a page shows a password field, which is
noise on a page that already knows the credentials. The fields only come back
when the user asks to edit that one store.
"""


def _sign_in(client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1"})


def test_saved_login_is_read_only_and_offers_edit(bf, client):
    _sign_in(client)
    client.post(
        "/retailers/grandiose",
        data={"email": "me@example.com", "password": "store-pw", "address": "Villa 1"},
    )
    page = client.get("/stores").text
    assert 'type="password"' not in page
    assert 'name="email"' not in page
    assert "me@example.com" in page
    assert "Villa 1" in page
    assert "/stores?edit=grandiose" in page


def test_edit_brings_the_fields_back_for_that_store_only(bf, client):
    _sign_in(client)
    client.post(
        "/retailers/grandiose",
        data={"email": "me@example.com", "password": "store-pw", "address": "Villa 1"},
    )
    page = client.get("/stores?edit=grandiose").text
    assert 'name="email"' in page
    assert 'value="me@example.com"' in page
    assert 'type="password"' in page
    assert 'autocomplete="new-password"' in page
    assert "leave blank to keep current" in page


def test_an_unlinked_store_still_shows_the_fields(bf, client):
    _sign_in(client)
    page = client.get("/stores").text
    assert 'name="email"' in page
    assert 'type="password"' in page


def test_saving_keeps_the_password_and_returns_to_the_card(bf, client):
    _sign_in(client)
    client.post(
        "/retailers/grandiose",
        data={"email": "me@example.com", "password": "store-pw", "address": "Villa 1"},
    )
    r = client.post(
        "/retailers/grandiose",
        data={"email": "new@example.com", "password": "", "address": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/stores#store-grandiose"
    creds = bf.db.get_retailer_secret(bf.db.get_user_by_email("friend@example.com")["id"], "grandiose")
    assert creds["email"] == "new@example.com"
    assert creds["password"] == "store-pw"
    page = client.get("/stores").text
    assert "new@example.com" in page
    assert 'type="password"' not in page


def test_an_unknown_edit_target_is_ignored(bf, client):
    _sign_in(client)
    client.post(
        "/retailers/grandiose",
        data={"email": "me@example.com", "password": "store-pw", "address": "Villa 1"},
    )
    page = client.get("/stores?edit=not-a-store").text
    assert 'type="password"' not in page
