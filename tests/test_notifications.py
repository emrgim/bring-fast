def test_settings_notifications_subscribe(client):
    from bring_fast import db, push

    client.post("/login", data={"email": "n@example.com", "password": "secret1", "intent": "signup"}, follow_redirects=True)
    page = client.get("/settings")
    assert page.status_code == 200
    assert "Notifications" in page.text
    assert "Enable" in page.text

    r = client.post(
        "/push/subscribe",
        json={"endpoint": "https://push.example/bf", "keys": {"p256dh": "abc", "auth": "def"}},
    )
    assert r.status_code == 200
    assert r.json()["notify"] is True
    uid = db.get_user_by_email("n@example.com")["id"]
    assert db.get_notify(uid) is True
    assert push.send_sync(uid, "New bill", "x") == 1

    off = client.post("/push/unsubscribe", json={"endpoint": "https://push.example/bf"})
    assert off.json()["notify"] is False
    assert db.get_notify(uid) is False
