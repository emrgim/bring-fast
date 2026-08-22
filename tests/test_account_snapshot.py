"""Grok must see saved supermarket logins and official orders without a live Chrome cart."""

from __future__ import annotations

import json


def _user_with_carrefour(bf):
    user = bf.db.create_user("e@example.com", "secret1")
    bf.db.set_retailer_account(user["id"], "carrefour", "e@example.com", "store-pass")
    bf.db.create_order(
        user["id"],
        "carrefour",
        [{"id": "1551683", "name": "Voiello Spaghetti", "qty": 1, "price": 16.49}],
        "Hotel Element Meaisam 731, Dubai Production City",
        "https://www.carrefouruae.com/mafuae/en/cart",
    )
    return bf.db.get_user_by_id(user["id"])


def test_whoami_shows_linked_login_and_official_order(bf):
    user = _user_with_carrefour(bf)
    out = json.loads(bf._call_tool(user, "bf_whoami", {}))
    assert out["success"] is True
    assert out["email"] == "e@example.com"
    assert "carrefour" in out["linked_stores"]
    assert "waitrose" in out["unlinked_stores"]
    carrefour = next(s for s in out["stores"] if s["store_id"] == "carrefour")
    assert carrefour["login_saved"] is True
    assert carrefour["linked"] is True
    assert carrefour["login_email"] == "e@example.com"
    assert carrefour["recent_orders"][0]["items"][0]["name"] == "Voiello Spaghetti"
    assert "Hotel Element" in (carrefour["last_delivery_address"] or "")
    assert "Bring Fast" in carrefour["address_note"]
    waitrose = next(s for s in out["stores"] if s["store_id"] == "waitrose")
    assert waitrose["login_saved"] is False


def test_stores_does_not_treat_empty_dashboard_address_as_no_login(bf):
    user = _user_with_carrefour(bf)
    out = json.loads(bf._call_tool(user, "bf_stores", {}))
    carrefour = next(s for s in out["stores"] if s["store_id"] == "carrefour")
    assert carrefour["login_saved"] is True
    assert "carrefour" in out["linked_stores"]
    assert "do not say a store has no login" in out["note"].lower()


def test_status_keeps_saved_login_when_live_cart_fails(bf, monkeypatch):
    user = _user_with_carrefour(bf)

    def boom(**_kwargs):
        return {"ok": False, "logged_in": False, "items": [], "error": "chrome down"}

    monkeypatch.setattr(bf.checkout, "official_cart", boom)
    out = json.loads(bf._call_tool(user, "carrefour_status", {}))
    assert out["login_saved"] is True
    assert out["login_email"] == "e@example.com"
    assert out["recent_orders"]
    assert out["success"] is True
    assert out["live_cart_ok"] is False
    assert "login_saved=True" in out["what_happens"]


def test_whoami_never_asks_to_set_address_on_bring_fast(bf):
    user = _user_with_carrefour(bf)
    out = json.loads(bf._call_tool(user, "bf_whoami", {}))
    blob = json.dumps(out).lower()
    assert "dashboard store card" not in blob
    assert "add it on the bring fast" not in blob
