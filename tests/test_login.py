"""Bring Fast account: Sign up for new visitors, Sign in for existing accounts."""

from fastapi.testclient import TestClient


def test_first_visit_is_sign_up_only(client):
    html = client.get("/").text
    assert "Create account" in html
    assert "Create <em>account</em>" in html or "Create account" in html
    assert "Sign in and authorize" not in html
    assert "Already have an account?" in html
    assert "Sign up" not in html or "Create account" in html


def test_sign_in_page_is_sign_in_only(client):
    html = client.get("/login", params={"mode": "signin"}).text
    assert ">Sign in<" in html or "Sign in" in html
    assert "Create an account" in html
    assert "Already have an account?" not in html


def test_first_login_creates_the_account_and_lands_on_the_dashboard(client):
    r = client.post(
        "/login",
        data={"email": "Friend@Example.com", "password": "secret1", "intent": "signup"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/stores?welcome=1"
    assert "Your" in client.get("/").text


def test_known_account_signs_in_again(bf, client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1", "intent": "signup"})
    fresh = TestClient(bf.app)
    r = fresh.post(
        "/login",
        data={"email": "friend@example.com", "password": "secret1", "intent": "signin"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_signup_does_not_sign_in_existing(bf, client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1", "intent": "signup"})
    fresh = TestClient(bf.app)
    r = fresh.post(
        "/login",
        data={"email": "friend@example.com", "password": "secret1", "intent": "signup"},
        follow_redirects=False,
    )
    assert r.status_code == 401
    assert "already has a Bring Fast account" in r.text


def test_email_case_and_spacing_do_not_create_a_second_account(bf, client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1", "intent": "signup"})
    fresh = TestClient(bf.app)
    r = fresh.post(
        "/login",
        data={"email": "  Friend@Example.COM ", "password": "secret1", "intent": "signin"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert bf.db.get_user_by_email("friend@example.com")


def test_wrong_password_keeps_the_typed_email(bf, client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1", "intent": "signup"})
    fresh = TestClient(bf.app)
    r = fresh.post(
        "/login",
        data={"email": "friend@example.com", "password": "nope", "intent": "signin"},
        follow_redirects=False,
    )
    assert r.status_code == 401
    assert 'value="friend@example.com"' in r.text
    assert "password does not match" in r.text


def test_short_password_on_a_new_email_explains_itself(client):
    r = client.post(
        "/login",
        data={"email": "new@example.com", "password": "abc", "intent": "signup"},
        follow_redirects=False,
    )
    assert r.status_code == 401
    assert "at least 6 characters" in r.text


def test_login_returns_to_where_the_user_was_going(client):
    r = client.post(
        "/login",
        data={
            "email": "friend@example.com",
            "password": "secret1",
            "intent": "signup",
            "next": "/oauth/authorize?client_id=x",
        },
        follow_redirects=False,
    )
    assert r.headers["location"] == "/oauth/authorize?client_id=x"


def test_next_cannot_leave_the_app(client):
    for target in ("https://evil.example/steal", "//evil.example/steal"):
        r = client.post(
            "/login",
            data={"email": f"a{len(target)}@example.com", "password": "secret1", "intent": "signup", "next": target},
            follow_redirects=False,
        )
        assert r.headers["location"] in ("/", "/dashboard", "/stores?welcome=1")


def test_signed_in_user_never_sees_the_login_form_again(client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1", "intent": "signup"})
    r = client.get("/login", params={"next": "/"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_login_form_is_shown_to_visitors(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Create account" in r.text


def test_logout_clears_the_session(client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1", "intent": "signup"})
    client.get("/logout")
    assert "Create account" in client.get("/").text


def test_stale_session_falls_back_to_the_login_form(bf, client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1", "intent": "signup"})
    user = bf.db.get_user_by_email("friend@example.com")
    con = bf.db.connect()
    con.execute("DELETE FROM users WHERE id=?", (user["id"],))
    con.commit()
    con.close()
    assert "Create account" in client.get("/").text


def test_register_path_still_accepts_the_account_form(client):
    r = client.post("/register", data={"email": "friend@example.com", "password": "secret1"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/stores?welcome=1"
