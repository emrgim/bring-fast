from bring_fast.stores import mmi


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = ""

    def json(self):
        return self._body


def test_mmi_login_uses_email_and_password(monkeypatch):
    seen = {}

    class Fake:
        headers = {}

        def post(self, url, json=None, timeout=20):
            seen["url"] = url
            seen["json"] = json
            return _Resp(400, {"code": 24242, "message": "Your MMI account credentials couldn't be verified."})

    monkeypatch.setattr(mmi, "_client", lambda: Fake())
    out = mmi.login("e@mrg.im", "nope")
    assert out["ok"] is False
    assert "couldn't be verified" in out["error"]
    assert seen["json"] == {"email": "e@mrg.im", "password": "nope"}
    assert seen["url"].endswith("/CALL/User/loginWithDxbLicense")


def test_mmi_login_phone_when_no_at(monkeypatch):
    seen = {}

    class Fake:
        headers = {}

        def post(self, url, json=None, timeout=20):
            seen["json"] = json
            return _Resp(400, {"code": 24198, "message": "phone or email required"})

    monkeypatch.setattr(mmi, "_client", lambda: Fake())
    mmi.login("0504771575", "x")
    assert seen["json"] == {"phone": "0504771575", "password": "x"}


def test_mmi_login_success_reads_token(monkeypatch):
    class Fake:
        headers = {}

        def post(self, url, json=None, timeout=20):
            if url.endswith("/TOKEN/auth"):
                return _Resp(200, {"accessToken": "acc-1"})
            return _Resp(200, {"token": "custom-1", "userId": "u-9"})

    monkeypatch.setattr(mmi, "_client", lambda: Fake())
    out = mmi.login("e@mrg.im", "secret")
    assert out["ok"] is True
    assert out["token"] == "acc-1"
    assert out["user_id"] == "u-9"


def test_verify_login_dispatches_mmi(monkeypatch):
    from bring_fast import checkout

    monkeypatch.setattr(mmi, "login", lambda e, p: {"ok": True, "token": "t", "user_id": "u", "error": None})
    out = checkout.verify_login(store="mmi", email="e@mrg.im", password="x")
    assert out["ok"] is True
    assert out["driver"] == "http"
