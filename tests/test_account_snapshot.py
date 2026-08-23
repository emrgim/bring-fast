"""Grok must see saved supermarket logins, never a local cart or order copy."""

from __future__ import annotations

import json


def _user_with_carrefour(bf):
    user = bf.db.create_user("e@example.com", "secret1")
    bf.db.set_retailer_account(user["id"], "carrefour", "e@example.com", "store-pass")
    return bf.db.get_user_by_id(user["id"])


def test_whoami_shows_linked_login_not_a_local_cart(bf):
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
    assert "recent_orders" not in carrefour
    assert "last_seen_cart" not in carrefour
    waitrose = next(s for s in out["stores"] if s["store_id"] == "waitrose")
    assert waitrose["login_saved"] is False
    blob = json.dumps(out["stores"]).lower()
    assert "voiello" not in blob


def test_stores_does_not_treat_empty_dashboard_address_as_no_login(bf):
    user = _user_with_carrefour(bf)
    out = json.loads(bf._call_tool(user, "bf_stores", {}))
    carrefour = next(s for s in out["stores"] if s["store_id"] == "carrefour")
    assert carrefour["login_saved"] is True
    assert "carrefour" in out["linked_stores"]
    assert "do not say a store has no login" in out["note"].lower()


def test_status_does_not_invent_items_when_live_cart_fails(bf, monkeypatch):
    user = _user_with_carrefour(bf)

    def boom(**_kwargs):
        return {"ok": False, "logged_in": False, "items": [], "error": "unread"}

    monkeypatch.setattr(bf.checkout, "official_cart", boom)
    out = json.loads(bf._call_tool(user, "carrefour_status", {}))
    assert out["login_saved"] is True
    assert out["login_email"] == "e@example.com"
    assert out.get("items") == []
    assert "recent_orders" not in out
    assert "last_seen_cart" not in out
    assert out["success"] is True
    assert out["live_cart_ok"] is False


def test_whoami_never_asks_to_set_address_on_bring_fast(bf):
    user = _user_with_carrefour(bf)
    out = json.loads(bf._call_tool(user, "bf_whoami", {}))
    blob = json.dumps(out).lower()
    assert "dashboard store card" not in blob
    assert "add it on the bring fast" not in blob
