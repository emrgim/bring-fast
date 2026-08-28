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
    assert "unioncoop_cart" in names
    assert "unioncoop_status" in names
    assert "unioncoop_checkout" not in names
    assert "carrefour_cart" in names
    assert "carrefour_status" in names
    assert "carrefour_checkout" not in names


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


def test_carrefour_offers_cart_but_not_checkout(bf):
    user = bf.db.create_user("h@example.com", "secret1")
    names = {t["name"] for t in bf.tools_catalog()}
    assert "carrefour_search" in names
    assert "carrefour_cart" in names
    assert "carrefour_status" in names
    assert "carrefour_checkout" not in names
    out = json.loads(bf._call_tool(user, "carrefour_checkout", {}))
    assert out["success"] is False
    assert "carrefour_cart" in out["error"].lower()
    snap = json.loads(bf._call_tool(user, "bf_whoami", {}))
    carrefour = next(s for s in snap["stores"] if s["store_id"] == "carrefour")
    assert carrefour["capabilities"] == ["search", "cart", "receipts"]
    assert "carrefour_cart" in carrefour["tools"]
    assert "carrefour_checkout" not in carrefour["tools"]


def test_waitrose_is_still_search_only(bf):
    user = bf.db.create_user("h2@example.com", "secret1")
    names = {t["name"] for t in bf.tools_catalog()}
    assert "waitrose_search" in names
    assert "waitrose_cart" not in names
    out = json.loads(bf._call_tool(user, "waitrose_cart", {"action": "list"}))
    assert out["success"] is False
    assert "search-only" in out["error"].lower()


def test_a_receipts_only_store_is_never_offered_a_search_tool(bf):
    user = bf.db.create_user("i@example.com", "secret1")
    names = {t["name"] for t in bf.tools_catalog()}
    assert "careem_search" not in names
    assert "careem_cart" not in names
    assert "careem_checkout" not in names
    assert "mcdonalds_search" not in names
    assert "mcdonalds_cart" not in names
    assert "mcdonalds_checkout" not in names

    snap = json.loads(bf._call_tool(user, "bf_whoami", {}))
    for store_id in ("careem", "mcdonalds"):
        store = next(s for s in snap["stores"] if s["store_id"] == store_id)
        assert store["capabilities"] == ["receipts"]
        assert store["tools"] == []
        assert store["receipts_only"] is True
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
    assert "mcdonalds" not in asked
    assert "grandiose" in asked


def test_cart_get_alias_lists_the_official_carrefour_cart(bf, monkeypatch):
    user = bf.db.create_user("get@example.com", "secret1")
    bf.db.set_retailer_account(user["id"], "carrefour", "e@mrg.im", "store-pass")
    user = bf.db.get_user_by_id(user["id"])
    seen = []

    def _live(**kw):
        seen.append(kw["action"])
        return {
            "ok": True,
            "official_count": 1,
            "items": [{"id": "1102885", "name": "Eggs", "qty": 1, "price": 12}],
            "logged_in": True,
            "session_reused": True,
            "driver": "chrome",
            "token": "t",
            "user_id": "u",
        }

    monkeypatch.setattr(bf.checkout, "official_cart", _live)
    out = json.loads(bf._call_tool(user, "bf_cart", {"retailer": "carrefour", "action": "get"}))
    assert seen == ["list"]
    assert out["success"] is True
    assert out["items"][0]["name"] == "Eggs"
    seen.clear()
    again = json.loads(bf._call_tool(user, "carrefour_cart", {"action": "read"}))
    assert seen == ["list"]
    assert again["success"] is True


def test_carrefour_cart_adds_by_name(bf, monkeypatch):
    user = bf.db.create_user("list@example.com", "secret1")
    bf.db.set_retailer_account(user["id"], "carrefour", "shopper@example.com", "store-pass")
    user = bf.db.get_user_by_id(user["id"])

    monkeypatch.setattr(
        bf.catalog,
        "search",
        lambda sid, query, limit: {
            "retailer": sid,
            "query": query,
            "results": [{"id": "11530", "name": "Almarai Fresh Milk 1L", "price": 6.5}],
        },
    )
    monkeypatch.setattr(
        bf.checkout,
        "official_cart",
        lambda **kw: {
            "ok": True,
            "official_count": 1,
            "items": [{"id": "11530", "name": "Almarai Fresh Milk 1L", "qty": 2, "price": 6.5}],
            "logged_in": True,
            "session_reused": False,
            "driver": "android",
            "token": "t",
            "user_id": "u",
        },
    )
    out = json.loads(bf._call_tool(user, "carrefour_cart", {"action": "add", "name": "milk", "qty": 2}))
    assert out["success"] is True
    assert out["picked"]["id"] == "11530"
    assert out["items"][0]["qty"] == 2

    aliased = json.loads(bf._call_tool(user, "carrefour_list", {"action": "list"}))
    assert aliased["success"] is True
    assert aliased["action"] == "list"


def test_carrefour_cart_add_without_id_or_name_fails(bf):
    user = bf.db.create_user("noid@example.com", "secret1")
    out = json.loads(bf._call_tool(user, "carrefour_cart", {"action": "add", "qty": 1}))
    assert out["success"] is False
    assert "product_id or name" in out["error"]


def test_carrefour_cart_create_empties_the_official_basket(bf, monkeypatch):
    user = bf.db.create_user("empty@example.com", "secret1")
    seen = []

    def _live(**kw):
        seen.append(kw["action"])
        return {
            "ok": True,
            "official_count": 0,
            "items": [],
            "logged_in": True,
            "session_reused": False,
            "driver": "android",
            "token": "t",
            "user_id": "u",
        }

    monkeypatch.setattr(bf.checkout, "official_cart", _live)
    for alias in ("create", "empty", "new", "clear"):
        out = json.loads(bf._call_tool(user, "carrefour_cart", {"action": alias}))
        assert out["success"] is True
        assert out["action"] == "clear"
        assert out["items"] == []
    assert seen == ["clear", "clear", "clear", "clear"]

    listed = json.loads(bf._call_tool(user, "carrefour_shopping_list", {"action": "list"}))
    assert listed["success"] is True
    italian = json.loads(bf._call_tool(user, "carrefour_lista", {"action": "list"}))
    assert italian["success"] is True


def test_normalize_mcp_prefixed_carrefour_tools(bf):
    name, args = bf._normalize_tool("bring_fast___carrefour_cart", {"action": "list"})
    assert name == "carrefour_cart"
    assert args["action"] == "list"
    name, _ = bf._normalize_tool("bring_fast___carrefour_status", {})
    assert name == "carrefour_status"
    name, _ = bf._normalize_tool("Bring Fast carrefour cart", {"action": "add"})
    assert name == "carrefour_cart"


def test_carrefour_cart_overlays_live_delivery_address(bf, monkeypatch):
    user = bf.db.create_user("addr@example.com", "secret1")
    bf.db.set_retailer_account(user["id"], "carrefour", "shopper@example.com", "store-pass")
    user = bf.db.get_user_by_id(user["id"])
    monkeypatch.setattr(
        bf.checkout,
        "official_cart",
        lambda **kw: {
            "ok": True,
            "official_count": 0,
            "items": [],
            "logged_in": True,
            "session_reused": True,
            "driver": "chrome",
            "token": "t",
            "user_id": "u",
            "delivery_address": "Element Meaisam 731",
            "food_pos": "073",
            "area": "Dubai Production City",
            "polygon_id": "DXB_DubProdCty_01",
        },
    )
    out = json.loads(bf._call_tool(user, "bring_fast___carrefour_cart", {"action": "list"}))
    assert out["success"] is True
    assert out["delivery_address"] == "Element Meaisam 731"
    assert out["food_pos"] == "073"


def test_carrefour_cart_surfaces_needs_delivery_slot(bf, monkeypatch):
    user = bf.db.create_user("slot@example.com", "secret1")
    bf.db.set_retailer_account(user["id"], "carrefour", "shopper@example.com", "store-pass")
    user = bf.db.get_user_by_id(user["id"])
    monkeypatch.setattr(
        bf.checkout,
        "official_cart",
        lambda **kw: {
            "ok": False,
            "official_count": None,
            "items": [],
            "logged_in": True,
            "session_reused": True,
            "driver": "chrome",
            "token": "t",
            "user_id": "u",
            "error": "Carrefour needs a bound delivery store before add-to-cart (error_code=needs_delivery_slot).",
            "error_code": "needs_delivery_slot",
            "maf_error": "SLOTTED is not a valid intent for product, available purchase indicators  are null",
        },
    )
    out = json.loads(bf._call_tool(user, "bf_cart", {"retailer": "carrefour", "action": "add", "product_id": "11811", "qty": 2}))
    assert out["success"] is False
    assert out["error_code"] == "needs_delivery_slot"
    assert "purchase indicators" in (out.get("maf_error") or "").lower()
    assert out["live_cart_ok"] is False
    assert out["login_saved"] is True


def test_carrefour_akamai_unread_keeps_login_saved(bf, monkeypatch):
    user = bf.db.create_user("cf@example.com", "secret1")
    bf.db.set_retailer_account(user["id"], "carrefour", "shopper@example.com", "store-pass")
    user = bf.db.get_user_by_id(user["id"])

    monkeypatch.setattr(
        bf.checkout,
        "official_cart",
        lambda **kw: {
            "ok": False,
            "official_count": None,
            "items": [],
            "logged_in": False,
            "session_reused": False,
            "driver": "chrome",
            "error": (
                "Carrefour blocked the HTTP API from this server (Akamai). "
                "The saved store login is still present. Official cart unread."
            ),
            "token": "",
            "user_id": "",
        },
    )
    out = json.loads(bf._call_tool(user, "carrefour_cart", {"action": "list"}))
    assert out["success"] is False
    assert out["items"] == []
    assert out["item_count"] == 0
    assert out["login_saved"] is True
    assert out["login_linked"] is True
    assert out["store_login_ok"] is True
    assert "akamai" in out["what_happens"].lower()
    assert "unread" in out["what_happens"].lower()
    assert "login_saved=True" in out["note"]
    assert "does not mean the supermarket login is missing" in out["note"]
