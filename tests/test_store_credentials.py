"""A store is read on the list and changed inside its own page.

The list page never puts a login form on screen: browsers offer to autofill the
moment a page shows a password field, which is noise on a page that is only
reading stores out. Inside a store, a saved login is printed until the person
asks to edit it.
"""


def _sign_in(client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1"})


def test_the_list_reads_the_stores_out_and_changes_nothing(bf, client):
    _sign_in(client)
    client.post(
        "/retailers/grandiose",
        data={"email": "me@example.com", "password": "store-pw", "address": "Villa 1"},
    )
    page = client.get("/stores").text

    # Nothing to type and nothing to submit: no login form, no toggle.
    assert 'type="password"' not in page
    assert 'name="email"' not in page
    assert "/retailers/grandiose/toggle" not in page
    # A card says what the store is and what it can do, and opens it.
    assert 'href="/stores/grandiose"' in page
    assert "me@example.com" in page
    for label in ("Search", "Compare", "Cart", "Checkout", "Receipts", "Login"):
        assert ">" + label + "<" in page, label


def test_a_store_that_cannot_shop_says_so_on_its_card(bf, client):
    _sign_in(client)
    page = client.get("/stores").text

    # Carrefour can be searched and its receipts read, but not shopped.
    card = page.split('id="store-carrefour"')[1].split("</a>")[0]
    assert 'class="cap no">Cart' in card
    assert 'class="cap no">Checkout' in card
    assert 'class="cap">Search' in card
    assert 'class="cap">Receipts' in card


def test_a_receipts_only_store_says_it_is_there_for_its_invoices(bf, client):
    _sign_in(client)
    page = client.get("/stores").text

    card = page.split('id="store-careem"')[1].split("</a>")[0]
    assert 'class="cap no">Search' in card
    assert 'class="cap no">Compare' in card
    assert 'class="cap no">Cart' in card
    assert 'class="cap no">Login' in card
    assert 'class="cap">Receipts' in card

    store = client.get("/stores/careem").text
    # No catalog and no login: the page says why, and offers no form.
    assert "No catalog to look up" in store
    assert "invoices arrive by mail" in store
    assert 'type="password"' not in store


def test_a_saved_login_is_printed_inside_the_store_and_offers_edit(bf, client):
    _sign_in(client)
    client.post(
        "/retailers/grandiose",
        data={"email": "me@example.com", "password": "store-pw", "address": "Villa 1"},
    )
    page = client.get("/stores/grandiose").text

    assert 'type="password"' not in page
    assert 'name="email"' not in page
    assert "me@example.com" in page
    assert "Villa 1" in page
    assert "/stores/grandiose?edit=1" in page


def test_edit_brings_the_fields_back(bf, client):
    _sign_in(client)
    client.post(
        "/retailers/grandiose",
        data={"email": "me@example.com", "password": "store-pw", "address": "Villa 1"},
    )
    page = client.get("/stores/grandiose?edit=1").text

    assert 'name="email"' in page
    assert 'value="me@example.com"' in page
    assert 'type="password"' in page
    assert 'autocomplete="new-password"' in page
    assert "leave blank to keep current" in page


def test_an_unlinked_store_opens_straight_into_the_fields(bf, client):
    _sign_in(client)
    page = client.get("/stores/grandiose").text

    # Nothing is saved yet, so there is nothing to print: the form is the page.
    assert 'name="email"' in page
    assert 'type="password"' in page


def test_saving_keeps_the_password_and_returns_to_the_store(bf, client):
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
    assert r.headers["location"] == "/stores/grandiose"
    creds = bf.db.get_retailer_secret(bf.db.get_user_by_email("friend@example.com")["id"], "grandiose")
    assert creds["email"] == "new@example.com"
    assert creds["password"] == "store-pw"
    page = client.get("/stores/grandiose").text
    assert "new@example.com" in page
    assert 'type="password"' not in page


def test_a_store_nobody_has_heard_of_goes_back_to_the_list(bf, client):
    _sign_in(client)
    r = client.get("/stores/not-a-store", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/stores"


def test_turning_a_store_off_answers_on_the_store_page(bf, client):
    _sign_in(client)
    r = client.post("/retailers/grandiose/toggle", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"].startswith("/stores/grandiose?")
    assert "disabled" in r.headers["location"]
    # Off, the store keeps its card on the list but not its login form.
    page = client.get("/stores/grandiose").text
    assert 'type="password"' not in page
    assert "Turn Grandiose on" in page


def test_a_store_page_needs_a_sign_in(bf, client):
    r = client.get("/stores/grandiose", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/login?mode=signin&next=/stores/grandiose"
