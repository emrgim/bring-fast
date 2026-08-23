from bring_fast.catalog import parse_spinneys_html, parse_waitrose_html


WAITROSE = """
<div class="js-product-wrapper" data-product-id="1135">
  <h5><a href="/en/products/almarai-full-fat-milk-2ltr_1135/">Almarai full fat milk 2ltr</a></h5>
  <span>AED 15.75</span>
</div>
"""

SPINNEYS = """
<div class="js-product-wrapper" data-product-id="335">
  <a href="/en-ae/catalogue/milupa-aptamil-growing-up-toddler-milk-stage-3-200ml_335/">x</a>
  <span class="price">12.00</span>
  <p class="product-name">
    <a href="/en-ae/catalogue/milupa-aptamil-growing-up-toddler-milk-stage-3-200ml_335/">Aptamil Growing Up Milk Formula 1-3 Years 200ml</a>
  </p>
</div>
"""


def test_parse_waitrose_html_has_price():
    items = parse_waitrose_html(WAITROSE)
    assert items[0]["name"] == "Almarai full fat milk 2ltr"
    assert items[0]["price"] == 15.75
    assert items[0]["id"] == "1135"


def test_parse_spinneys_html_has_price():
    items = parse_spinneys_html(SPINNEYS)
    assert "Aptamil" in items[0]["name"]
    assert items[0]["price"] == 12.0
    assert items[0]["id"] == "335"
