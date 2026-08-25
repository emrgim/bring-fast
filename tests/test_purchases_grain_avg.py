"""The purchases headline price follows the selected grain: daily shows AED/day, weekly AED/week."""


def _seed(bf, email):
    user = bf.db.create_user(email, "secret1")
    for no, day, total in (("g1", "2026-06-02", 60), ("g2", "2026-06-10", 80)):
        bf.purchases.upsert_invoice(
            user["id"],
            {
                "retailer": "carrefour",
                "invoice_no": no,
                "invoice_date": day,
                "items": [{"name": "Rice", "qty": 1, "unit_price": total, "line_total": total, "barcode": "1"}],
            },
        )
    return user


# 2026-06-01 → 2026-06-14: exactly 14 days / 2 calendar weeks, AED 140 spent.
WINDOW = "range=custom&start=2026-06-01&end=2026-06-14"


def test_purchases_average_follows_the_grain(bf, client):
    _seed(bf, "grain@example.com")
    client.post("/login", data={"email": "grain@example.com", "password": "secret1", "intent": "signin"})

    daily = client.get(f"/purchases?{WINDOW}&grain=daily").text
    assert "Daily average this period" in daily
    assert "AED 10.00</b>" in daily
    assert "140.00 ÷ 14 days" in daily

    weekly = client.get(f"/purchases?{WINDOW}&grain=weekly").text
    assert "Weekly average this period" in weekly
    assert "AED 70.00</b>" in weekly
    assert "140.00 ÷ 2 weeks" in weekly
