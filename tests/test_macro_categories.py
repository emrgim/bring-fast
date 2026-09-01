"""Macro-category constants and receipt import assignment."""

from bring_fast import purchases
from bring_fast.macro_categories import (
    ALCOHOL,
    CHEESE,
    DAIRY,
    FISH_SEAFOOD,
    HOUSEHOLD_CLEANING,
    MACRO_CATEGORIES,
    MEAT,
    OTHER,
    SOFT_DRINKS,
    WATER,
    classify_macro,
    is_valid_macro,
    macro_label,
    normalize_macro,
)


def test_all_macros_are_valid_slugs():
    assert len(MACRO_CATEGORIES) == 32
    for slug in MACRO_CATEGORIES:
        assert is_valid_macro(slug)
        assert macro_label(slug)


def test_classify_examples():
    assert classify_macro("PRESIDENT BRI 200G") == CHEESE
    assert classify_macro("IGOR GORG 150G") == CHEESE
    assert classify_macro("AUS BF RIBEYE STEA") == MEAT
    assert classify_macro("TUNA STEAK") == FISH_SEAFOOD
    assert classify_macro("COCA COLA LIGHT 1L") == SOFT_DRINKS
    assert classify_macro("Oasis Blu Sparkling Water, 1L") == WATER
    assert classify_macro("Heineken Can 24 x 50CL") == ALCOHOL
    assert classify_macro("Fresh Milk 2L") == DAIRY
    assert classify_macro("PLAST SHOPPING BAG") == HOUSEHOLD_CLEANING
    assert classify_macro("??? MYSTERY SKU ???") == OTHER


def test_normalize_macro():
    assert normalize_macro("fromage") == ""
    assert normalize_macro(CHEESE) == CHEESE


def test_macro_sticky_on_reimport(bf):
    user = bf.db.create_user("macro@example.com", "secret12")
    parsed = {
        "retailer": "carrefour",
        "invoice_no": "MACRO-1",
        "invoice_date": "2026-01-01",
        "items": [
            {"name": "PRESIDENT BRI 200G", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "3228020232026"},
        ],
    }
    purchases.upsert_invoice(user["id"], parsed)
    key = purchases.product_key("3228020232026", "PRESIDENT BRI 200G")
    meta = purchases.get_product_meta(key)
    assert meta["macro_category"] == CHEESE

    # Second receipt with a name that would classify differently — must stay cheese.
    parsed2 = {
        "retailer": "carrefour",
        "invoice_no": "MACRO-2",
        "invoice_date": "2026-02-01",
        "items": [
            {"name": "Fresh Milk 2L", "qty": 1, "unit_price": 8, "line_total": 8, "barcode": "3228020232026"},
        ],
    }
    purchases.upsert_invoice(user["id"], parsed2)
    meta2 = purchases.get_product_meta(key)
    assert meta2["macro_category"] == CHEESE


def test_upsert_meta_does_not_overwrite_macro(bf):
    key = "name:test-cheese-lock"
    purchases.upsert_product_meta(
        key,
        {"name": "Brie", "macro_category": CHEESE, "source": "test"},
    )
    purchases.upsert_product_meta(
        key,
        {"name": "Brie Updated", "macro_category": DAIRY, "source": "test2"},
    )
    meta = purchases.get_product_meta(key)
    assert meta["macro_category"] == CHEESE
    assert meta["official_name"] == "Brie Updated"
