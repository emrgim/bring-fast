from bring_fast import catalog
from bring_fast.catalog import (
    best_match,
    expand_carrefour_queries,
    parse_spinneys_html,
    parse_waitrose_html,
    rank_carrefour_results,
)


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


PERFUME = {
    "id": "8028713572821",
    "name": "Acqua Di Parma Blu Mediterraneo Mirto Di Panarea 10ml",
    "price": 60.38,
    "product_type": "NONFOOD",
    "brand": "Acqua Di Parma",
}
WATER_1L = {
    "id": "1592968",
    "name": "Oasis Blu Sparkling Water, 1L Pack of 6",
    "price": 26.99,
    "product_type": "FOOD",
    "brand": "Blu",
}
WATER_500 = {
    "id": "1602179",
    "name": "Oasis Blu Sparkling Water 500ml Pack of 6",
    "price": 10.79,
    "product_type": "FOOD",
    "brand": "Blu",
}


def test_expand_acqua_blu_searches_oasis_blu():
    q = [s.lower() for s in expand_carrefour_queries("Acqua Blu")]
    assert q[0] == "acqua blu"
    assert "oasis blu" in q
    assert "blu sparkling water" in q


def test_expand_skips_acqua_di_parma():
    q = expand_carrefour_queries("Acqua Di Parma Blu")
    assert q == ["Acqua Di Parma Blu"]


def test_acqua_blu_ranks_oasis_1l_pack_over_perfume():
    ranked = rank_carrefour_results("Acqua Blu", [PERFUME, WATER_500, WATER_1L])
    assert ranked[0]["id"] == "1592968"
    assert "Oasis Blu" in ranked[0]["name"]
    assert "1L" in ranked[0]["name"]
    assert ranked[-1]["id"] == PERFUME["id"]


def test_best_match_acqua_blu_picks_water_pack():
    hit = best_match("Acqua Blu", [PERFUME, WATER_1L, WATER_500])
    assert hit["id"] == "1592968"


def test_search_carrefour_acqua_blu_returns_water_pack(monkeypatch):
    """Constructor.io 'Acqua Blu' is perfume; rewritten queries find Oasis Blu 1592968."""

    def fake_raw(query, **_k):
        q = str(query).lower()
        if q == "acqua blu":
            return [
                {
                    "value": PERFUME["name"],
                    "data": {
                        "id": PERFUME["id"],
                        "price": PERFUME["price"],
                        "product_type": "NONFOOD",
                        "brand_name": "Acqua Di Parma",
                    },
                }
            ]
        if "oasis blu" in q or "sparkling" in q:
            return [
                {
                    "value": WATER_500["name"],
                    "data": {
                        "id": WATER_500["id"],
                        "price": WATER_500["price"],
                        "product_type": "FOOD",
                        "brand_name": "Blu",
                    },
                },
                {
                    "value": WATER_1L["name"],
                    "data": {
                        "id": WATER_1L["id"],
                        "price": WATER_1L["price"],
                        "product_type": "FOOD",
                        "brand_name": "Blu",
                    },
                },
            ]
        return []

    monkeypatch.setattr(catalog, "_cio_search_raw", fake_raw)
    out = catalog.search_carrefour("Acqua Blu", 8)
    assert out["results"]
    assert out["results"][0]["id"] == "1592968"
    assert "Oasis Blu Sparkling Water" in out["results"][0]["name"]
    assert "Pack of 6" in out["results"][0]["name"]
    assert out["results"][0]["price"] == 26.99
    ids = [it["id"] for it in out["results"]]
    assert PERFUME["id"] in ids
    assert ids.index("1592968") < ids.index(PERFUME["id"])
