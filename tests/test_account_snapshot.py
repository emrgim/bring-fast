"""Grok must see saved supermarket logins, never a local cart or order copy."""

from __future__ import annotations

import json


def _user_with_grandiose(bf):
    user = bf.db.create_user("e@example.com", "secret1")
    bf.db.set_retailer_account(user["id"], "grandiose", "e@example.com", "store-pass")
    return bf.db.get_user_by_id(user["id"])


def test_whoami_shows_linked_login_not_a_local_cart(bf):
    user = _user_with_grandiose(bf)
    out = json.loads(bf._call_tool(user, "bf_whoami", {}))
    assert out["success"] is True
    assert out["email"] == "e@example.com"
    assert "grandiose" in out["linked_stores"]
    assert "carrefour" not in out["linked_stores"]
    assert "carrefour" in out["unlinked_stores"]
    grandiose = next(s for s in out["stores"] if s["store_id"] == "grandiose")
    assert grandiose["login_saved"] is True
    assert grandiose["linked"] is True
    assert grandiose["enabled"] is True
    assert grandiose["login_email"] == "e@example.com"
    assert "recent_orders" not in grandiose
    assert "last_seen_cart" not in grandiose
    blob = json.dumps(out["stores"]).lower()
    assert "voiello" not in blob


def test_stores_does_not_treat_empty_dashboard_address_as_no_login(bf):
    user = _user_with_grandiose(bf)
    out = json.loads(bf._call_tool(user, "bf_stores", {}))
    grandiose = next(s for s in out["stores"] if s["store_id"] == "grandiose")
    assert grandiose["login_saved"] is True
    assert "grandiose" in out["linked_stores"]
    assert "do not say a store has no login" in out["note"].lower()


def test_status_does_not_invent_items_when_live_cart_fails(bf, monkeypatch):
    user = _user_with_grandiose(bf)

    def boom(**_kwargs):
        return {"ok": False, "logged_in": False, "items": [], "error": "unread"}

    monkeypatch.setattr(bf.checkout, "official_cart", boom)
    out = json.loads(bf._call_tool(user, "grandiose_status", {}))
    assert out["login_saved"] is True
    assert out["login_email"] == "e@example.com"
    assert out.get("items") == []
    assert "recent_orders" not in out
    assert "last_seen_cart" not in out
    assert out["success"] is True
    assert out["live_cart_ok"] is False


def test_whoami_never_asks_to_set_address_on_bring_fast(bf):
    user = _user_with_grandiose(bf)
    out = json.loads(bf._call_tool(user, "bf_whoami", {}))
    blob = json.dumps(out).lower()
    assert "dashboard store card" not in blob
    assert "add it on the bring fast" not in blob


def test_search_tools_exist_for_every_store(bf):
    bf.db.create_user("f@example.com", "secret1")
    names = {t["name"] for t in bf.tools_catalog()}
    assert "grandiose_search" in names
    assert "unioncoop_search" in names
    assert "carrefour_search" in names
    assert "waitrose_search" in names
    assert "spinneys_search" in names
    assert "bf_compare" in names
    assert "unioncoop_cart" not in names
    assert "carrefour_cart" not in names


def test_toggle_enables_unioncoop_tools(bf):
    user = bf.db.create_user("g@example.com", "secret1")
    bf.db.set_store_enabled("unioncoop", True)
    names = {t["name"] for t in bf.tools_catalog()}
    assert "unioncoop_search" in names
    assert "unioncoop_cart" in names
    assert "unioncoop_checkout" in names
    out = json.loads(bf._call_tool(user, "bf_whoami", {}))
    ids = {s["store_id"] for s in out["stores"]}
    assert "unioncoop" in ids
    assert "grandiose" in ids


def test_non_magento_store_is_search_only(bf):
    user = bf.db.create_user("h@example.com", "secret1")
    names = {t["name"] for t in bf.tools_catalog()}
    assert "carrefour_search" in names
    assert "carrefour_cart" not in names
    assert "carrefour_checkout" not in names
    out = json.loads(bf._call_tool(user, "carrefour_cart", {"action": "list"}))
    assert out["success"] is False
    assert "search-only" in out["error"].lower() or "magento" in out["error"].lower()
    snap = json.loads(bf._call_tool(user, "bf_whoami", {}))
    carrefour = next(s for s in snap["stores"] if s["store_id"] == "carrefour")
    assert carrefour["capabilities"] == ["search", "receipts"]
    assert "union coop" in snap["note"].lower()


def test_a_receipts_only_store_is_never_offered_a_search_tool(bf):
    user = bf.db.create_user("i@example.com", "secret1")
    names = {t["name"] for t in bf.tools_catalog()}
    assert "careem_search" not in names
    assert "careem_cart" not in names
    assert "careem_checkout" not in names

    snap = json.loads(bf._call_tool(user, "bf_whoami", {}))
    careem = next(s for s in snap["stores"] if s["store_id"] == "careem")
    assert careem["capabilities"] == ["receipts"]
    assert careem["tools"] == []
    assert careem["receipts_only"] is True
    assert "receipts-only" in snap["note"].lower()


def test_asking_a_receipts_only_store_for_prices_says_why_not(bf):
    """A store with no catalog says so, rather than "unknown retailer"."""
    user = bf.db.create_user("j@example.com", "secret1")

    direct = json.loads(bf._call_tool(user, "careem_search", {"query": "shawarma"}))
    assert direct["success"] is False
    assert direct["results"] == []
    assert "receipts-only" in direct["error"].lower()
    assert "careem" in direct["error"].lower()

    scoped = json.loads(bf._call_tool(user, "bf_search", {"query": "shawarma", "retailer": "careem"}))
    assert scoped["success"] is False
    assert "receipts-only" in scoped["error"].lower()

    unknown = json.loads(bf._call_tool(user, "bf_search", {"query": "milk", "retailer": "nowhere"}))
    assert "unknown retailer" in unknown["error"]


def test_searching_every_store_skips_the_one_with_no_catalog(bf, monkeypatch):
    user = bf.db.create_user("k@example.com", "secret1")
    monkeypatch.setattr(
        bf.catalog,
        "search",
        lambda sid, query, limit: {"retailer": sid, "query": query, "results": []},
    )
    out = json.loads(bf._call_tool(user, "bf_search", {"query": "shawarma"}))
    asked = {block["retailer"] for block in out["stores"]}
    assert "careem" not in asked
    assert "grandiose" in asked
