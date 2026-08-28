"""Match Magento cart lines against the live Grandiose basket. No invented SKUs."""

from bring_fast.stores.cart_match import match_cart_line, missing_line_error

# Emiliano's live Grandiose cart (read-only fixture; tests never write it).
LIVE = [
    {"id": "6291021213119", "item_id": "12118284", "uid": "MTIxMTgyODQ=", "name": "Blu Sparkling Water 1L", "qty": 24},
    {"id": "5000112668209", "item_id": "12115690", "uid": "MTIxMTU2OTA=", "name": "Coca-Cola Zero Calories", "qty": 2},
    {"id": "5283003399547", "item_id": "12112312", "uid": "MTIxMTIzMTI=", "name": "Master Kettle Cooked Salt Potato Chips", "qty": 1},
]


def test_coca_cola_name_hits_zero_calories_line():
    hit = match_cart_line(LIVE, name="Coca-Cola")
    assert hit["id"] == "5000112668209"
    assert hit["item_id"] == "12115690"


def test_coca_cola_zero_hits_the_same_line():
    hit = match_cart_line(LIVE, name="Coca-Cola Zero")
    assert hit["id"] == "5000112668209"
    assert hit["item_id"] == "12115690"


def test_take_out_the_coca_cola_hits_the_coke_line():
    hit = match_cart_line(LIVE, name="take out the Coca-Cola")
    assert hit["item_id"] == "12115690"
    assert hit["id"] == "5000112668209"


def test_togli_la_coca_cola_hits_the_coke_line():
    hit = match_cart_line(LIVE, name="togli la Coca-Cola")
    assert hit["id"] == "5000112668209"
    assert hit["item_id"] == "12115690"


def test_wrong_catalog_sku_still_hits_coke_by_name():
    hit = match_cart_line(LIVE, sku="9999999999999", name="Coca-Cola")
    assert hit["id"] == "5000112668209"


def test_exact_sku_hits_coke():
    hit = match_cart_line(LIVE, sku="5000112668209")
    assert hit["item_id"] == "12115690"


def test_item_id_hits_coke():
    hit = match_cart_line(LIVE, item_id="12115690")
    assert hit["id"] == "5000112668209"


def test_numeric_item_id_is_not_the_ean():
    """item_id is Magento's quote item id; the EAN lives on id/sku."""
    assert match_cart_line(LIVE, item_id="5000112668209") is None
    hit = match_cart_line(LIVE, sku="5000112668209")
    assert hit["item_id"] == "12115690"


def test_coca_cola_does_not_hit_blu_or_chips():
    hit = match_cart_line(LIVE, name="Coca-Cola")
    assert hit["id"] != "6291021213119"
    assert "Blu" not in hit["name"]
    assert "Chips" not in hit["name"]


def test_missing_sku_is_none():
    assert match_cart_line(LIVE, sku="0000000000000") is None


def test_missing_line_error_lists_what_is_in_the_cart():
    msg = missing_line_error("Diet Sprite", LIVE, store="Grandiose")
    assert "not in the official Grandiose cart" in msg
    assert "Coca-Cola Zero Calories" in msg
    assert "Blu Sparkling Water 1L" in msg
