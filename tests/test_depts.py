from bring_fast.depts import classify_dept, normalize_dept


def test_classify_edible_and_drinks():
    assert classify_dept("Oasis Blu Sparkling Water, 1L") == "Drinks"
    assert classify_dept("COCA COLA LIGHT 1L") == "Drinks"
    assert classify_dept("Barilla Olive Pasta Sauce with") == "Edible"
    assert classify_dept("PRESIDENT BRI 200G") == "Edible"
    assert classify_dept("President Brie 60%") == "Edible"
    assert classify_dept("PLAST SHOPPING BAG") == ""
    assert classify_dept("HMD PULSE PRO") == ""
    assert classify_dept("Demi Baguette") == "Edible"
    assert normalize_dept("Food") == "Edible"
    assert normalize_dept("Edible") == "Edible"
