from bring_fast.depts import classify_dept, matches_dept, normalize_dept


def test_classify_edible_and_drinks():
    assert classify_dept("Oasis Blu Sparkling Water, 1L") == "Drinks"
    assert classify_dept("COCA COLA LIGHT 1L") == "Drinks"
    assert classify_dept("Barilla Olive Pasta Sauce with") == "Edible"
    assert classify_dept("PRESIDENT BRI 200G") == "Edible"
    assert classify_dept("President Brie 60%") == "Edible"
    assert classify_dept("PLAST SHOPPING BAG") == ""
    assert classify_dept("HMD PULSE PRO") == ""
    assert classify_dept("Heineken Can 24 x 50CL") == "Drinks"
    assert normalize_dept("Food") == "Edible"
    assert normalize_dept("Edible") == "Edible"


def test_drink_tokens_are_whole_words():
    """Substring 'tea'/'gin'/'rum' used to dump steak, oil, and chips into Drinks."""
    assert classify_dept("AUS BF RIBEYE STEA") == "Edible"
    assert classify_dept("TUNA STEAK") == "Edible"
    assert classify_dept("Steak Burrito") == "Edible"
    assert classify_dept("Pringles Original Potato Chips, 200g") == "Edible"
    assert classify_dept("Philadelphia Original Cream Cheese 280g") == "Edible"
    assert classify_dept("Monini Bios Extra Virgin Olive Oil 500ml") == "Edible"
    assert classify_dept("Ciliegine 200g") == "Edible"
    assert classify_dept("Kinder Tronky Cocoa wafer with biscuit crumbs filling") == "Edible"
    assert classify_dept("Dove Antiperspirant Deodorant Roll-On, Original, 50ml") == ""
    assert classify_dept("Organic Larder Apple Cider Vinegar, 1L") == "Edible"
    assert classify_dept("Lindt Lindor Salted Caramel Milk Chocolate Bar, 100g") == "Edible"
    assert classify_dept("Kuche Dubai Chocolate Milk 100G") == "Drinks"


def test_official_name_can_move_a_receipt_into_drinks():
    assert classify_dept("6PK BTL") == "Edible"
    assert classify_dept("6PK BTL", "Oasis Blu Sparkling Water, 1L") == "Drinks"
    assert matches_dept("Drinks", "6PK BTL", "Oasis Blu Sparkling Water, 1L")
    assert not matches_dept("Edible", "6PK BTL", "Oasis Blu Sparkling Water, 1L")
