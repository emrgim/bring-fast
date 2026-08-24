from bring_fast.stores import africaneastern


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = ""

    def json(self):
        return self._body


def test_ae_login_license_dxb(monkeypatch):
    seen = {}

    class Fake:
        headers = {}

        def post(self, url, json=None, headers=None, timeout=25):
            seen["json"] = json
            q = (json or {}).get("query") or ""
            if "customerTokenForLicenseDXB" in q:
                return _Resp(200, {"data": {"customerTokenForLicenseDXB": {"token": "tok-ae"}}})
            if "customer {" in q:
                return _Resp(200, {"data": {"customer": {"email": "e@mrg.im"}}})
            return _Resp(200, {"data": {}})

    monkeypatch.setattr(africaneastern, "_client", lambda: Fake())
    out = africaneastern.login("e@mrg.im", "secret")
    assert out["ok"] is True
    assert out["token"] == "tok-ae"


def test_verify_login_dispatches_ae(monkeypatch):
    from bring_fast import checkout

    monkeypatch.setattr(
        africaneastern, "login", lambda e, p: {"ok": True, "token": "t", "user_id": "u", "error": None}
    )
    out = checkout.verify_login(store="africaneastern", email="e@mrg.im", password="x")
    assert out["ok"] is True
