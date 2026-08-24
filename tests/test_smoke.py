"""Smoke tests that do not require a live supermarket session."""

from __future__ import annotations

from bring_fast import __version__, catalog, db


def test_version_is_semver():
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_retailers_have_logos_and_urls():
    ids = {r["id"] for r in db.RETAILERS}
    assert ids == {"grandiose", "unioncoop", "carrefour", "waitrose", "spinneys", "mmi", "africaneastern"}
    assert {r["id"] for r in db.RETAILERS if r.get("enabled")} == {"grandiose"}
    assert {r["id"] for r in db.RETAILERS if r.get("shop")} == {"grandiose", "unioncoop"}
    for r in db.RETAILERS:
        assert r["url"].startswith("https://")
        assert r["logo"].startswith("/static/")


def test_catalog_unknown_retailer():
    out = catalog.search("unknown-store", "milk", 1)
    assert out["results"] == []
    assert "unknown" in (out.get("error") or "")
