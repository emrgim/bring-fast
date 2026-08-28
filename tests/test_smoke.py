"""Smoke tests that do not require a live supermarket session."""

from __future__ import annotations

from pathlib import Path

from bring_fast import __version__, catalog, checkout, db


def test_version_is_semver():
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_retailers_have_logos_and_urls():
    ids = {r["id"] for r in db.RETAILERS}
    assert ids == {
        "grandiose",
        "unioncoop",
        "carrefour",
        "waitrose",
        "spinneys",
        "mmi",
        "africaneastern",
        "careem",
        "mcdonalds",
    }
    assert {r["id"] for r in db.RETAILERS if r.get("enabled")} == {"grandiose", "carrefour"}
    assert {r["id"] for r in db.RETAILERS if r.get("shop")} == {"grandiose", "unioncoop", "carrefour"}
    assert {r["id"] for r in db.RETAILERS if r.get("checkout")} == {"grandiose", "unioncoop"}
    assert {r["id"] for r in db.RETAILERS if r.get("shop")} == set(checkout.WIRED_CART_STORES)
    assert {r["id"] for r in db.RETAILERS if r.get("checkout")} == set(checkout.WIRED_CHECKOUT_STORES)
    assert db.store_can_shop("carrefour") is True
    assert db.store_can_checkout("carrefour") is False
    assert db.store_can_shop("grandiose") is True
    assert db.store_can_checkout("grandiose") is True
    assert db.store_can_shop("unioncoop") is True
    assert db.store_can_checkout("unioncoop") is True
    assert db.store_can_shop("waitrose") is False
    assert db.store_can_checkout("waitrose") is False
    assert checkout.WIRED_HTTP_LOGIN_STORES >= {"grandiose", "unioncoop", "carrefour"}
    from bring_fast.stores import unioncoop as unioncoop_api
    from bring_fast.stores import grandiose as grandiose_api

    assert callable(unioncoop_api.official_cart)
    assert callable(unioncoop_api.official_checkout)
    assert callable(unioncoop_api.search)
    assert callable(grandiose_api.remove_item)
    union = {c["key"]: c["on"] for c in db.store_capabilities("unioncoop")}
    assert union == {
        "search": True,
        "compare": True,
        "cart": True,
        "checkout": True,
        "receipts": False,
        "login": True,
    }
    grandiose = {c["key"]: c["on"] for c in db.store_capabilities("grandiose")}
    assert grandiose == {
        "search": True,
        "compare": True,
        "cart": True,
        "checkout": True,
        "receipts": True,
        "login": True,
    }
    for r in db.RETAILERS:
        assert r["url"].startswith("https://")
        assert r["logo"].startswith("/static/")
        logo = Path(db.__file__).resolve().parent / r["logo"].lstrip("/")
        assert logo.is_file(), r["logo"]


def test_a_store_that_can_be_searched_has_something_to_search_it_with():
    """Every searchable store has a searcher, and every searcher has a store.

    The two lists are what tells a catalog store from a receipts-only one, so
    they drifting apart is how a store starts answering errors.
    """
    assert {r["id"] for r in db.searchable_retailers()} == set(catalog.SEARCHERS)


def test_a_receipts_only_store_is_left_out_of_search():
    assert db.store_can_search("grandiose") is True
    assert db.store_can_search("careem") is False
    assert db.store_can_search("mcdonalds") is False
    assert "careem" not in catalog.SEARCHERS
    assert "mcdonalds" not in catalog.SEARCHERS
    caps = {c["key"]: c["on"] for c in db.store_capabilities("careem")}
    assert caps == {
        "search": False,
        "compare": False,
        "cart": False,
        "checkout": False,
        "receipts": True,
        "login": False,
    }
    assert {c["key"]: c["on"] for c in db.store_capabilities("mcdonalds")} == caps


def test_catalog_unknown_retailer():
    out = catalog.search("unknown-store", "milk", 1)
    assert out["results"] == []
    assert "unknown" in (out.get("error") or "")
