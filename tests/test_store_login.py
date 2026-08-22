"""Store logins are per Bring Fast user, even though the browser profile is shared."""

import pytest

from bring_fast import checkout


class FakeContext:
    def __init__(self):
        self.cleared = []

    def clear_cookies(self, **kwargs):
        self.cleared.append(kwargs)


class FakePage:
    """Enough of a Playwright page to drive the login decisions."""

    def __init__(self, marker="", text="", signed_in_text="Welcome back"):
        self.storage = {checkout.ACCOUNT_MARKER: marker} if marker else {}
        self.text = text
        self.signed_in_text = signed_in_text
        self.url = "https://www.carrefouruae.com/mafuae/en"
        self.context = FakeContext()
        self.logins = 0

    def goto(self, url, **kwargs):
        if "login" in url:
            self.logins += 1
            self.url = "https://www.carrefouruae.com/mafuae/en"
            self.text = self.signed_in_text
        else:
            self.url = url

    def reload(self, **kwargs):
        pass

    def wait_for_timeout(self, ms):
        pass

    def wait_for_selector(self, selector, **kwargs):
        return True

    def inner_text(self, selector):
        return self.text

    def locator(self, selector):
        return FakeLocator()

    def get_by_role(self, *args, **kwargs):
        raise RuntimeError("no such control")

    def get_by_text(self, *args, **kwargs):
        raise RuntimeError("no such control")

    def evaluate(self, script, arg=None):
        if "getItem" in script:
            return self.storage.get(checkout.ACCOUNT_MARKER, "")
        if "setItem" in script:
            self.storage[checkout.ACCOUNT_MARKER] = arg
        if "clear()" in script:
            self.storage.clear()
        return None


class FakeLocator:
    @property
    def first(self):
        return self

    def count(self):
        return 1

    def is_visible(self):
        return True

    def click(self, **kwargs):
        pass

    def fill(self, value, **kwargs):
        pass

    def press_sequentially(self, value, **kwargs):
        pass

    def press(self, key):
        pass


def test_first_call_signs_in_and_tags_the_profile():
    page = FakePage(text="Log in or Sign up")
    result = checkout.ensure_store_login(page, "carrefour", "Me@Example.com", "pw")
    assert result["logged_in"] and not result["reused"]
    assert page.storage[checkout.ACCOUNT_MARKER] == "me@example.com"
    assert page.logins == 1


def test_second_call_reuses_the_session():
    page = FakePage(text="Log in or Sign up")
    checkout.ensure_store_login(page, "carrefour", "me@example.com", "pw")
    again = checkout.ensure_store_login(page, "carrefour", "me@example.com", "pw")
    assert again["reused"] and again["logged_in"]
    assert page.logins == 1


def test_another_user_does_not_inherit_the_session():
    page = FakePage(marker="me@example.com", text="Hi Me")
    result = checkout.ensure_store_login(page, "carrefour", "friend@example.com", "pw")
    assert not result["reused"]
    assert page.storage[checkout.ACCOUNT_MARKER] == "friend@example.com"
    assert page.context.cleared == [{"domain": "carrefouruae.com"}]


def test_expired_store_session_signs_in_again():
    page = FakePage(marker="me@example.com", text="Log in or Sign up")
    result = checkout.ensure_store_login(page, "carrefour", "me@example.com", "pw")
    assert result["logged_in"] and not result["reused"]
    assert page.logins == 1


def test_a_page_that_stays_signed_out_is_reported_not_logged_in():
    page = FakePage(text="Log in or Sign up", signed_in_text="Log in or Sign up")
    result = checkout.ensure_store_login(page, "carrefour", "me@example.com", "pw")
    assert not result["logged_in"]
    assert "Bring Fast dashboard" in result["error"]


def test_missing_credentials_say_what_to_do():
    result = checkout.ensure_store_login(FakePage(), "carrefour", "", "")
    assert not result["logged_in"]
    assert "dashboard" in result["error"]


@pytest.mark.parametrize("store", ["carrefour", "grandiose", "waitrose", "spinneys"])
def test_every_store_has_a_home_page_to_check_the_session_on(store):
    assert checkout.HOME[store].startswith("https://")


def test_cart_call_without_a_saved_login_never_opens_a_browser(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("should not launch a browser without credentials")

    monkeypatch.setattr(checkout, "_launch", boom)
    result = checkout._official_cart_sync(store="carrefour", email="", password="", action="list", items=[])
    assert not result["ok"]
    assert "dashboard" in result["error"]


def test_check_login_button_reports_the_result(bf, client, monkeypatch):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1"})
    client.post("/retailers/carrefour", data={"email": "me@example.com", "password": "store-pw", "address": "Villa 1"})
    monkeypatch.setattr(
        bf.checkout, "verify_login", lambda **kwargs: {"ok": True, "reused": True, "url": "https://store"}
    )
    r = client.post("/retailers/carrefour/check", follow_redirects=False)
    assert r.status_code == 303
    assert "login+works" in r.headers["location"]

    monkeypatch.setattr(bf.checkout, "verify_login", lambda **kwargs: {"ok": False, "error": "wrong password"})
    r = client.post("/retailers/carrefour/check", follow_redirects=True)
    assert "wrong password" in r.text
