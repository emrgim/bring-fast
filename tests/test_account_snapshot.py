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


def test_carrefour_is_not_described_as_search_only(bf):
    """Stale Grok caches used to quote bf_cart as Carrefour search-only. Do not regress."""
    user = bf.db.create_user("noso@example.com", "secret1")
    tools = {t["name"]: t["description"].lower() for t in bf.tools_catalog()}
    bf_cart = tools["bf_cart"]
    assert "carrefour" in bf_cart
    assert "waitrose and spinneys are search-only" in bf_cart
    assert "carrefour, waitrose and spinneys are search-only" not in bf_cart
    assert "bf_cart retailer=carrefour" in bf_cart
    search = tools["carrefour_search"]
    assert "search only" not in search
    assert "not search-only" in search
    assert "carrefour_cart" in search
    assert "query=2288448" in search
    assert "action=add" in search
    schema = next(t["inputSchema"] for t in bf.tools_catalog() if t["name"] == "carrefour_search")
    assert "action" in schema["properties"]
    assert "product_id" in schema["properties"]
    assert "query" in schema["properties"]
    waitrose = next(t for t in bf.tools_catalog() if t["name"] == "waitrose_search")
    assert waitrose["inputSchema"].get("required") == ["query"]
    assert "action" not in waitrose["inputSchema"]["properties"]
    cart = tools["carrefour_cart"]
    assert "official" in cart
    assert "list" in cart
    snap = json.loads(bf._call_tool(user, "bf_whoami", {}))
    assert "not search-only" in snap["note"].lower()
    carrefour = next(s for s in snap["stores"] if s["store_id"] == "carrefour")
    assert "cart" in carrefour["capabilities"]
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
    assert "does not mean the supermarket login is missing" in out["note"] or "akamai_blocked" in out["note"]


def test_carrefour_add_passes_product_page_url(bf, monkeypatch):
    user = bf.db.create_user("coke@example.com", "secret1")
    bf.db.set_retailer_account(user["id"], "carrefour", "e@mrg.im", "store-pass")
    user = bf.db.get_user_by_id(user["id"])
    seen = []

    def _live(**kw):
        seen.append(kw)
        return {
            "ok": True,
            "official_count": 1,
            "items": [{"id": "2288448", "name": "Coke Zero", "qty": 1, "price": 7.49}],
            "logged_in": True,
            "session_reused": True,
            "driver": "chrome",
            "token": "t",
            "user_id": "u",
        }

    monkeypatch.setattr(bf.checkout, "official_cart", _live)
    out = json.loads(
        bf._call_tool(user, "carrefour_cart", {"action": "add", "product_id": "2288448", "qty": 1})
    )
    assert out["success"] is True
    assert seen[0]["items"][0]["id"] == "2288448"
    assert seen[0]["items"][0]["url"].endswith("/p/2288448")


def test_carrefour_list_timeout_keeps_login_saved(bf, monkeypatch):
    user = bf.db.create_user("to@example.com", "secret1")
    bf.db.set_retailer_account(user["id"], "carrefour", "e@mrg.im", "store-pass")
    user = bf.db.get_user_by_id(user["id"])

    def boom(**_kw):
        raise bf.checkout.LiveCartTimeout(
            "Live carrefour cart exceeded 16s. The supermarket login is still saved; "
            "the official cart was not read. error_code=cart_timeout."
        )

    monkeypatch.setattr(bf.checkout, "official_cart", boom)
    out = json.loads(bf._call_tool(user, "carrefour_cart", {"action": "list"}))
    assert out["success"] is False
    assert out["error_code"] == "cart_timeout"
    assert out["login_saved"] is True
    assert out["store_login_ok"] is True
    assert "cart_timeout" in out["note"]


def _link_carrefour(bf, email="shop@example.com"):
    user = bf.db.create_user(email, "secret1")
    bf.db.set_retailer_account(user["id"], "carrefour", email, "store-pass")
    return bf.db.get_user_by_id(user["id"])


def _ok_add(product_id="2288448", **extra):
    def _live(**kw):
        _live.calls.append(kw)
        return {
            "ok": True,
            "official_count": 1,
            "items": [{"id": product_id, "name": "Coke Zero", "qty": kw["items"][0]["qty"] if kw.get("items") else 1, "price": 7.49}],
            "logged_in": True,
            "session_reused": True,
            "driver": "chrome",
            "token": "t",
            "user_id": "u",
            **extra,
        }

    _live.calls = []
    return _live


def test_carrefour_search_tells_stale_clients_how_to_add(bf, monkeypatch):
    user = bf.db.create_user("hint@example.com", "secret1")
    monkeypatch.setattr(
        bf.catalog,
        "search",
        lambda sid, query, limit: {
            "retailer": sid,
            "query": query,
            "results": [{"id": "2288448", "name": "Coke Zero 6pk", "price": 7.49}],
        },
    )
    out = json.loads(bf._call_tool(user, "carrefour_search", {"query": "coke zero", "limit": 8}))
    assert out["not_search_only"] is True
    assert out["official_cart"] is True
    assert out["results"][0]["add_with_this_tool"] == {"query": "2288448"}
    how = out["add_to_official_cart"]["how"].lower()
    assert "not search-only" in how
    assert "query=" in how and "2288448" in how
    assert out["add_to_official_cart"]["example"] == {"query": "2288448"}
    assert out["add_to_official_cart"]["same_tool"] == "carrefour_search"


def test_carrefour_search_numeric_query_adds_to_official_cart(bf, monkeypatch):
    """Stale Grok schema is {query, limit}; query=product_id must add, not catalog-search."""
    user = _link_carrefour(bf, "num@example.com")
    live = _ok_add()
    monkeypatch.setattr(bf.checkout, "official_cart", live)

    def no_search(*_a, **_k):
        raise AssertionError("numeric query must not catalog-search")

    monkeypatch.setattr(bf.catalog, "search", no_search)
    out = json.loads(bf._call_tool(user, "carrefour_search", {"query": "2288448", "limit": 8}))
    assert out["success"] is True
    assert live.calls[0]["action"] == "add"
    assert live.calls[0]["items"][0]["id"] == "2288448"
    assert live.calls[0]["items"][0]["qty"] == 1


def test_carrefour_search_action_add_hits_official_cart(bf, monkeypatch):
    user = _link_carrefour(bf, "act@example.com")
    live = _ok_add()
    monkeypatch.setattr(bf.checkout, "official_cart", live)
    out = json.loads(
        bf._call_tool(user, "carrefour_search", {"action": "add", "product_id": "2288448", "qty": 2})
    )
    assert out["success"] is True
    assert live.calls[0]["action"] == "add"
    assert live.calls[0]["items"][0]["qty"] == 2


def test_carrefour_search_add_743861_hits_official_cart(bf, monkeypatch):
    """Food keeper retries add product_id=743861 qty=1 after this lands."""
    user = _link_carrefour(bf, "zero@example.com")
    live = _ok_add("743861")
    monkeypatch.setattr(bf.checkout, "official_cart", live)
    out = json.loads(
        bf._call_tool(user, "carrefour_search", {"action": "add", "product_id": "743861", "qty": 1})
    )
    assert out["success"] is True
    assert live.calls[0]["action"] == "add"
    assert live.calls[0]["items"][0]["id"] == "743861"
    assert live.calls[0]["items"][0]["qty"] == 1
    assert live.calls[0]["timeout"] <= 32


def test_carrefour_search_add_1592968_qty_2_hits_official_cart(bf, monkeypatch):
    """Food keeper: 2 packs of Oasis Blu 1L x6 (product_id=1592968)."""
    user = _link_carrefour(bf, "bluadd@example.com")
    live = _ok_add("1592968")
    monkeypatch.setattr(bf.checkout, "official_cart", live)
    out = json.loads(
        bf._call_tool(
            user,
            "carrefour_search",
            {
                "action": "add",
                "product_id": "1592968",
                "qty": 2,
                "name": "Oasis Blu Sparkling Water, 1L Pack of 6",
            },
        )
    )
    assert out["success"] is True
    assert live.calls[0]["action"] == "add"
    assert live.calls[0]["items"][0]["id"] == "1592968"
    assert live.calls[0]["items"][0]["qty"] == 2
    assert out["items"][0]["id"] == "1592968"


def test_carrefour_search_acqua_blu_returns_oasis_water_pack(bf, monkeypatch):
    user = bf.db.create_user("acqua@example.com", "secret1")

    def fake_raw(query, **_k):
        q = str(query).lower()
        if q == "acqua blu":
            return [
                {
                    "value": "Acqua Di Parma Blu Mediterraneo Mirto Di Panarea 10ml",
                    "data": {
                        "id": "8028713572821",
                        "price": 60.38,
                        "product_type": "NONFOOD",
                        "brand_name": "Acqua Di Parma",
                    },
                }
            ]
        return [
            {
                "value": "Oasis Blu Sparkling Water, 1L Pack of 6",
                "data": {"id": "1592968", "price": 26.99, "product_type": "FOOD", "brand_name": "Blu"},
            }
        ]

    monkeypatch.setattr(bf.catalog, "_cio_search_raw", fake_raw)
    out = json.loads(bf._call_tool(user, "carrefour_search", {"query": "Acqua Blu", "limit": 8}))
    assert out["results"][0]["id"] == "1592968"
    assert "Oasis Blu" in out["results"][0]["name"]
    assert out["results"][0]["price"] == 26.99
    assert out["not_search_only"] is True


def test_carrefour_add_1592968_empty_cart_after_500_is_not_success(bf, monkeypatch):
    """1.10.11 SKU-in-items success must not fire when the write left the cart empty."""
    user = _link_carrefour(bf, "emptyblu@example.com")

    def _live(**kw):
        return {
            "ok": False,
            "official_count": 0,
            "items": [],
            "logged_in": True,
            "session_reused": True,
            "driver": "chrome",
            "maf_error": "Internal Server Error",
            "item_errors": [{"id": "1592968", "maf_error": "Internal Server Error"}],
            "token": "t",
            "user_id": "u",
        }

    monkeypatch.setattr(bf.checkout, "official_cart", _live)
    out = json.loads(
        bf._call_tool(
            user,
            "carrefour_search",
            {"action": "add", "product_id": "1592968", "qty": 2},
        )
    )
    assert out["success"] is False
    assert out["official_ok"] is False
    assert out["items"] == []
    assert out["maf_error"] == "Internal Server Error"


def test_carrefour_search_add_743861_success_despite_internal_error(bf, monkeypatch):
    """MAF Internal Server Error is a false negative when 743861 is already in the cart."""
    from bring_fast.stores import carrefour as api

    user = _link_carrefour(bf, "ise@example.com")
    catalog = {
        "376161": {"value": "Coca-Cola Original 330ml x6", "data": {"id": "376161", "price": 14.99}},
        "743861": {"value": "Coca-Cola Zero 330ml Can", "data": {"id": "743861", "price": 1.99}},
    }
    monkeypatch.setattr(api, "_cio_browse_ids", lambda ids: [catalog[i] for i in ids if i in catalog])
    monkeypatch.setattr(api, "_cio_search", lambda q: [catalog[q]] if q in catalog else [])

    def _live(**kw):
        return {
            "ok": False,
            "official_count": 4,
            "items": [
                {"id": "376161", "name": "", "qty": 1, "price": None, "currency": "AED"},
                {"id": "2311515", "name": "", "qty": 1, "price": None, "currency": "AED"},
                {"id": "7630477854474", "name": "", "qty": 1, "price": None, "currency": "AED"},
                {"id": "743861", "name": "", "qty": 1, "price": None, "currency": "AED"},
            ],
            "logged_in": True,
            "session_reused": True,
            "driver": "chrome",
            "maf_error": "Internal Server Error",
            "item_errors": [{"id": "743861", "maf_error": "Internal Server Error"}],
            "token": "t",
            "user_id": "u",
        }

    monkeypatch.setattr(bf.checkout, "official_cart", _live)
    out = json.loads(
        bf._call_tool(user, "carrefour_search", {"action": "add", "product_id": "743861", "qty": 1})
    )
    assert out["success"] is True
    assert out["official_ok"] is True
    zero = next(i for i in out["items"] if i["id"] == "743861")
    assert "Coca-Cola" in zero["name"]
    assert zero["price"] == 1.99
    assert zero["qty"] == 1


def test_carrefour_cart_list_includes_name_and_price(bf, monkeypatch):
    from bring_fast.stores import carrefour as api

    user = _link_carrefour(bf, "names@example.com")
    catalog = {
        "376161": {"value": "Coca-Cola Original 330ml x6", "data": {"id": "376161", "price": 14.99}},
        "743861": {"value": "Coca-Cola Zero 330ml Can", "data": {"id": "743861", "price": 1.99}},
        "2311515": {"value": "Epson EcoTank L3351", "data": {"id": "2311515", "price": 599}},
        "7630477854474": {"value": "Nespresso Inissia Coffee Machine", "data": {"id": "7630477854474", "ean": "7630477854474", "price": 408}},
    }
    monkeypatch.setattr(api, "_cio_browse_ids", lambda ids: [catalog[i] for i in ids if i in catalog])
    monkeypatch.setattr(api, "_cio_search", lambda q: [catalog[q]] if q in catalog else [])

    def _live(**kw):
        return {
            "ok": True,
            "official_count": 4,
            "items": [
                {"id": "376161", "name": "", "qty": 1, "price": None, "currency": "AED"},
                {"id": "2311515", "name": "", "qty": 1, "price": None, "currency": "AED"},
                {"id": "7630477854474", "name": "", "qty": 1, "price": None, "currency": "AED"},
                {"id": "743861", "name": "", "qty": 1, "price": None, "currency": "AED"},
            ],
            "logged_in": True,
            "session_reused": True,
            "driver": "chrome",
            "token": "t",
            "user_id": "u",
        }

    monkeypatch.setattr(bf.checkout, "official_cart", _live)
    out = json.loads(bf._call_tool(user, "carrefour_cart", {"action": "list"}))
    assert out["success"] is True
    by_id = {it["id"]: it for it in out["items"]}
    assert "Coca-Cola Original" in by_id["376161"]["name"]
    assert by_id["376161"]["price"] == 14.99
    assert "Coca-Cola Zero" in by_id["743861"]["name"]
    assert by_id["743861"]["price"] == 1.99
    assert by_id["2311515"]["name"]
    assert by_id["7630477854474"]["name"]
    assert all(it.get("name") for it in out["items"])
    assert all(it.get("price") not in (None, "") for it in out["items"])


def test_carrefour_search_add_timeout_keeps_login_saved(bf, monkeypatch):
    user = _link_carrefour(bf, "toadd@example.com")

    def boom(**_kw):
        raise bf.checkout.LiveCartTimeout(
            "Live carrefour cart exceeded 32s. The supermarket login is still saved; "
            "the official cart was not read. error_code=cart_timeout."
        )

    monkeypatch.setattr(bf.checkout, "official_cart", boom)
    out = json.loads(
        bf._call_tool(user, "carrefour_search", {"action": "add", "product_id": "743861", "qty": 1})
    )
    assert out["success"] is False
    assert out["error_code"] == "cart_timeout"
    assert out["login_saved"] is True
    assert out["store_login_ok"] is True
    assert out["items"] == []


def test_carrefour_cart_list_uses_short_timeout(bf, monkeypatch):
    user = _link_carrefour(bf, "lst2@example.com")
    seen = []

    def _live(**kw):
        seen.append(kw)
        return {
            "ok": True,
            "official_count": 0,
            "items": [],
            "logged_in": True,
            "session_reused": True,
            "driver": "playwright",
            "token": "t",
            "user_id": "u",
        }

    monkeypatch.setattr(bf.checkout, "official_cart", _live)
    out = json.loads(bf._call_tool(user, "carrefour_cart", {"action": "list"}))
    assert out["success"] is True
    assert seen[0]["action"] == "list"
    assert seen[0]["timeout"] == 28


def test_carrefour_search_add_prefix_adds_by_name(bf, monkeypatch):
    user = _link_carrefour(bf, "pref@example.com")
    monkeypatch.setattr(
        bf.catalog,
        "search",
        lambda sid, query, limit: {
            "retailer": sid,
            "query": query,
            "results": [{"id": "2288448", "name": "Coke Zero 6pk", "price": 7.49}],
        },
    )
    live = _ok_add()
    monkeypatch.setattr(bf.checkout, "official_cart", live)
    out = json.loads(bf._call_tool(user, "carrefour_search", {"query": "aggiungi coke zero"}))
    assert out["success"] is True
    assert live.calls[0]["action"] == "add"
    assert live.calls[0]["items"][0]["id"] == "2288448"


def test_carrefour_search_list_query_reads_official_cart(bf, monkeypatch):
    user = _link_carrefour(bf, "lst@example.com")
    seen = []

    def _live(**kw):
        seen.append(kw["action"])
        return {
            "ok": True,
            "official_count": 0,
            "items": [],
            "logged_in": True,
            "session_reused": True,
            "driver": "chrome",
            "token": "t",
            "user_id": "u",
        }

    monkeypatch.setattr(bf.checkout, "official_cart", _live)
    out = json.loads(bf._call_tool(user, "carrefour_search", {"query": "carrello"}))
    assert seen == ["list"]
    assert out["success"] is True


def test_bf_search_scoped_to_carrefour_numeric_query_adds(bf, monkeypatch):
    user = _link_carrefour(bf, "bfs@example.com")
    live = _ok_add()
    monkeypatch.setattr(bf.checkout, "official_cart", live)
    out = json.loads(
        bf._call_tool(user, "bf_search", {"query": "2288448", "retailer": "carrefour", "limit": 6})
    )
    assert out["success"] is True
    assert live.calls[0]["action"] == "add"
    assert live.calls[0]["items"][0]["id"] == "2288448"


def test_waitrose_search_does_not_add_even_with_action(bf, monkeypatch):
    user = bf.db.create_user("w@example.com", "secret1")
    monkeypatch.setattr(
        bf.catalog,
        "search",
        lambda sid, query, limit: {"retailer": sid, "query": query, "results": [{"id": "1135", "name": "Milk"}]},
    )

    def no_cart(**_k):
        raise AssertionError("waitrose has no official cart")

    monkeypatch.setattr(bf.checkout, "official_cart", no_cart)
    out = json.loads(bf._call_tool(user, "waitrose_search", {"query": "1135", "action": "add", "product_id": "1135"}))
    assert out.get("not_search_only") is not True
    assert "add_to_official_cart" not in out
    assert out["results"][0]["id"] == "1135"


def test_barcode_query_still_searches_carrefour(bf, monkeypatch):
    user = bf.db.create_user("ean@example.com", "secret1")
    monkeypatch.setattr(
        bf.catalog,
        "search",
        lambda sid, query, limit: {
            "retailer": sid,
            "query": query,
            "results": [{"id": "2288448", "name": "Coke Zero", "price": 7.49}],
        },
    )

    def no_cart(**_k):
        raise AssertionError("13-digit EAN must catalog-search, not add")

    monkeypatch.setattr(bf.checkout, "official_cart", no_cart)
    out = json.loads(bf._call_tool(user, "carrefour_search", {"query": "5449000131805"}))
    assert out["results"][0]["id"] == "2288448"
    assert out["not_search_only"] is True
