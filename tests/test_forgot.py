"""Forgot / reset password."""


def test_forgot_link_is_on_sign_in(client):
    html = client.get("/login", params={"mode": "signin"}).text
    assert "Forgot your password?" in html
    assert "/forgot" in html


def test_forgot_page_is_english(client):
    r = client.get("/forgot")
    assert r.status_code == 200
    assert "Forgot" in r.text
    assert "Send reset link" in r.text


def test_forgot_does_not_reveal_missing_accounts(client):
    r = client.post("/forgot", data={"email": "nobody@example.com"})
    assert r.status_code == 200
    assert "If that email has a Bring Fast account" in r.text


def test_reset_token_sets_new_password(bf, client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1", "intent": "signup"})
    client.get("/logout")
    token = bf.db.create_reset_token("friend@example.com")
    assert token
    r = client.post("/reset", data={"token": token, "password": "newpass9"}, follow_redirects=False)
    assert r.status_code == 200
    assert "Password updated" in r.text
    ok = client.post(
        "/login",
        data={"email": "friend@example.com", "password": "newpass9", "intent": "signin"},
        follow_redirects=False,
    )
    assert ok.status_code == 303


def test_used_reset_token_fails(bf, client):
    client.post("/login", data={"email": "friend@example.com", "password": "secret1", "intent": "signup"})
    client.get("/logout")
    token = bf.db.create_reset_token("friend@example.com")
    client.post("/reset", data={"token": token, "password": "newpass9"})
    r = client.post("/reset", data={"token": token, "password": "other99"})
    assert "invalid or expired" in r.text
