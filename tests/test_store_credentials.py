"""A store is read on the list and changed inside its own page.

The list page never puts a login form on screen: browsers offer to autofill the
moment a page shows a password field, which is noise on a page that is only
reading stores out. Inside a store, a saved login is printed until the person
asks to edit it.
"""

from __future__ import annotations

import json


def _caps(card: str) -> str:
    return card[card.index('class="caps"') : card.index('class="go"')]


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

    # Waitrose can be searched, but not shopped.
    card = _caps(page.split('id="store-waitrose"')[1].split("</a>")[0])
    assert 'class="cap no">Cart' in card
    assert "Cart · declared" not in card
    assert 'class="cap no">Checkout' in card
    assert 'class="cap">Search' in card


def test_carrefour_does_not_paint_cart_live_from_shop_alone(bf, client):
    _sign_in(client)
    page = client.get("/stores").text
    card = _caps(page.split('id="store-carrefour"')[1].split("</a>")[0])
    assert 'class="cap no declared"' in card
    assert "Cart · declared" in card
    assert 'class="cap">Cart' not in card
    assert 'class="cap no">Checkout' in card
    assert 'class="cap">Search' in card
    assert 'class="cap">Compare' in card
    assert 'class="cap">Receipts' in card


def test_unioncoop_card_matches_wired_magento_rest(bf, client):
    _sign_in(client)
    page = client.get("/stores").text
    card = _caps(page.split('id="store-unioncoop"')[1].split("</a>")[0])
    assert 'class="cap">Search' in card
    assert 'class="cap no declared"' in card
    assert "Cart · declared" in card
    assert 'class="cap">Cart' not in card
    assert 'class="cap">Checkout' in card
    assert 'class="cap">Login' in card
    assert 'class="cap no">Receipts' in card


def test_unioncoop_login_form_appears_when_the_store_is_on(bf, client):
    _sign_in(client)
    client.post("/retailers/unioncoop/toggle", follow_redirects=False)
    page = client.get("/stores/unioncoop").text
    assert "Turn Union Coop off" in page
    assert 'type="password"' in page
    assert "Prepare official checkout on Union Coop" in page
    assert "Payment stays on the store site" in page
    assert "No invoice reader for Union Coop yet" in page


def test_a_receipts_only_store_says_it_is_there_for_its_invoices(bf, client):
    _sign_in(client)
    page = client.get("/stores").text

    for store_id in ("careem", "mcdonalds"):
        card = page.split(f'id="store-{store_id}"')[1].split("</a>")[0]
        caps = _caps(card)
        assert 'class="cap no">Search' in caps
        assert 'class="cap no">Compare' in caps
        assert 'class="cap no">Cart' in caps
        assert 'class="cap no">Login' in caps
        assert 'class="cap">Receipts' in caps
        # No login to link — say invoices, not "Not linked".
        assert "Not linked" not in card
        assert "Invoices by mail" in card

        store = client.get(f"/stores/{store_id}").text
        # No catalog and no login: the page says why, and offers no form.
        assert "No catalog to look up" in store
        assert "invoices arrive by mail" in store
        assert 'type="password"' not in store


def test_a_domain_store_shows_mail_label_not_search_only(bf, client):
    _sign_in(client)
    page = client.get("/stores").text
    for store_id, domain in (("amazon_it", "amazon.it"), ("amazon_ae", "amazon.ae")):
        card = page.split(f'id="store-{store_id}"')[1].split("</a>")[0]
        assert f"Mail · {domain}" in card
        assert "Search only" not in card
        assert 'class="cap no">Receipts' in _caps(card)
        store = client.get(f"/stores/{store_id}").text
        assert f"Mail domain · {domain}" in store
        assert "bf_import_invoice" in store


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


def test_carrefour_cart_pill_stays_outlined_after_a_failed_list(bf, client, monkeypatch):
    """shop=True is not a live cart. Akamai/liteCart unread must not look active."""
    user = bf.db.create_user("pills@example.com", "secret1")
    bf.db.set_retailer_account(user["id"], "carrefour", "e@example.com", "store-pass")
    bf.db.set_retailer_account(user["id"], "grandiose", "e@example.com", "store-pass")
    user = bf.db.get_user_by_id(user["id"])

    def live(**kwargs):
        if kwargs.get("store") == "grandiose":
            return {
                "ok": True,
                "logged_in": True,
                "items": [{"id": "1", "name": "Coca-Cola Zero", "qty": 1}],
            }
        return {
            "ok": False,
            "logged_in": True,
            "items": [],
            "error": "unread",
            "error_code": "akamai_blocked",
        }

    monkeypatch.setattr(bf.checkout, "official_cart", live)
    failed = json.loads(bf._call_tool(user, "carrefour_cart", {"action": "list"}))
    assert failed.get("success") is False
    assert failed.get("live_cart_ok") is False
    ok = json.loads(bf._call_tool(user, "grandiose_cart", {"action": "list"}))
    assert ok.get("success") is True

    names = {t["name"] for t in bf.tools_catalog()}
    assert "carrefour_cart" in names
    assert "bf_cart" in names
    assert "grandiose_cart" in names

    client.post(
        "/login",
        data={"email": "pills@example.com", "password": "secret1", "intent": "signin"},
    )
    page = client.get("/stores").text
    carrefour = _caps(page.split('id="store-carrefour"')[1].split("</a>")[0])
    grandiose = _caps(page.split('id="store-grandiose"')[1].split("</a>")[0])
    assert 'class="cap no declared"' in carrefour
    assert "Cart · declared" in carrefour
    assert 'class="cap">Cart' not in carrefour
    assert "akamai_blocked" in carrefour
    assert 'class="cap no">Checkout' in carrefour
    assert 'class="cap">Search' in carrefour
    assert 'class="cap">Cart</span>' in grandiose
    assert "Cart · declared" not in grandiose

    detail = client.get("/stores/carrefour").text
    assert "akamai_blocked" in detail
    assert "Fill a basket on Carrefour" not in detail
    assert "last official list did not succeed" in detail


def test_grandiose_cart_pill_fills_after_a_successful_list(bf, client, monkeypatch):
    user = bf.db.create_user("gok@example.com", "secret1")
    bf.db.set_retailer_account(user["id"], "grandiose", "e@example.com", "store-pass")
    user = bf.db.get_user_by_id(user["id"])

    monkeypatch.setattr(
        bf.checkout,
        "official_cart",
        lambda **_kwargs: {
            "ok": True,
            "logged_in": True,
            "items": [{"id": "1", "name": "Coca-Cola Zero", "qty": 1}],
        },
    )
    out = json.loads(bf._call_tool(user, "grandiose_cart", {"action": "list"}))
    assert out.get("success") is True

    client.post(
        "/login",
        data={"email": "gok@example.com", "password": "secret1", "intent": "signin"},
    )
    card = _caps(client.get("/stores").text.split('id="store-grandiose"')[1].split("</a>")[0])
    assert 'class="cap">Cart</span>' in card
    assert "Cart · declared" not in card
    detail = client.get("/stores/grandiose").text
    assert "Fill a basket on Grandiose from here." in detail
