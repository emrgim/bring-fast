from datetime import date

from bring_fast import forecast
from bring_fast.purchases import shopping_list


def _buy(bf, user_id, invoice_no, day, items):
    bf.purchases.upsert_invoice(
        user_id,
        {
            "retailer": "carrefour",
            "store_name": "Carrefour",
            "invoice_no": invoice_no,
            "invoice_date": day,
            "items": items,
        },
    )


def test_ewma_weights_recent_gaps():
    assert forecast.ewma([10, 10, 10, 4], alpha=0.4) < 9


def test_san_pellegrino_500ml_is_occasional(bf):
    user = bf.db.create_user("spark@example.com", "secret1")
    item = {
        "name": "S.Pellegrino Sparkling Natural Mineral Water, 500ml",
        "qty": 1,
        "unit_price": 3.5,
        "line_total": 3.5,
        "barcode": "8002270015223",
    }
    for i, day in enumerate(("2026-05-01", "2026-05-20", "2026-06-10", "2026-07-01", "2026-07-25")):
        _buy(bf, user["id"], f"S{i}", day, [item])
    rows = forecast.forecast(user["id"], horizon_days=7, today=date(2026, 8, 24), include_excluded=True)
    pel = next(p for p in rows if "pellegrino" in p["name"].lower())
    assert pel["include"] is False
    assert pel["reason"] == "occasional_small_pack"
    on_list = [p for p in shopping_list(user["id"], horizon_days=30, today=date(2026, 8, 24)) if "pellegrino" in p["name"].lower()]
    assert on_list
    assert on_list[0]["likely"] < 25
    assert on_list[0]["likely_reason"] == "occasional_small_pack"


def test_regular_staple_scores_higher_than_sparse(bf):
    user = bf.db.create_user("staple@example.com", "secret1")
    bread = {"name": "White Bread", "qty": 1, "unit_price": 4, "line_total": 4, "barcode": "b1"}
    wine = {"name": "Random Wine 750ml", "qty": 1, "unit_price": 40, "line_total": 40, "barcode": "w1"}
    for i, day in enumerate(("2026-07-20", "2026-07-27", "2026-08-03", "2026-08-10", "2026-08-17")):
        _buy(bf, user["id"], f"BR{i}", day, [bread])
    for i, day in enumerate(("2026-02-01", "2026-04-15", "2026-06-01", "2026-08-01")):
        _buy(bf, user["id"], f"W{i}", day, [wine])
    rows = {p["name"]: p for p in forecast.forecast(user["id"], horizon_days=7, today=date(2026, 8, 24), include_excluded=True)}
    assert rows["White Bread"]["include"] is True
    assert rows["White Bread"]["score"] >= 40
    assert rows["Random Wine 750ml"]["include"] is False
    assert rows["Random Wine 750ml"]["score"] < rows["White Bread"]["score"]
    assert rows["Random Wine 750ml"]["reason"] in ("interval_too_long", "irregular", "too_few_buys")


def test_three_buys_still_get_a_score(bf):
    user = bf.db.create_user("milk@example.com", "secret1")
    milk = {"name": "Almarai Full Fat Milk 2L", "qty": 1, "unit_price": 12, "line_total": 12, "barcode": "m1"}
    for i, day in enumerate(("2026-08-03", "2026-08-10", "2026-08-17")):
        _buy(bf, user["id"], f"M{i}", day, [milk])
    rows = forecast.forecast(user["id"], horizon_days=7, today=date(2026, 8, 24), include_excluded=True)
    hit = next(p for p in rows if "milk" in p["name"].lower())
    assert hit["score"] >= 20
    assert hit["times_bought"] == 3
