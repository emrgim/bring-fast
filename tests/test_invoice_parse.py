from bring_fast.invoice_parse import (
    parse_africaneastern_html,
    parse_careem_html,
    parse_careem_text,
    parse_carrefour_text,
    parse_grandiose_confirmation_html,
    parse_grandiose_text,
    parse_mmi_text,
)
from bring_fast.sku_lookup import _human_cat


def test_sku_category_from_open_facts_tags():
    assert _human_cat(["en:beverages", "en:diet-cola-soft-drink"], "") == "diet cola soft drink"


CF = """
Invoice No. : 93084417
Invoice Date : 23-Aug-2026
Order No. : 784030019566483
City Center Meaisem
Coca-Cola Zero Sugar 2.0 2.0 5.99 5.71 11.41 5.00 0.57 0.00 11.98
Carbonated Soft Drink PET
Barcode: 5000112668209
Delivery Charge 1.0 1.0 0.00 0.00 0.00 5.00 0.00 0.00 0.00
Barcode: 2000019089384
Service Fee 1.0 1.0 3.42 3.26 3.26 5.00 0.16 0.00 3.42
Barcode: 2000019264163
"""

GR = """
TAX INVOICE
GRANDIOSE SUPERMARKET
Tax Invoice # 3325511433512 Tax invoice Date: May 18, 2026
Order # 110670729
Products SKU Qty Price Net Amount Discount VAT % VAT Amount Gross Amount
Veroni Hot Salami 9814172000000 0.25 94.28 23.57 0.00 5.00 1.18 24.75
Napoli (Non Halal)
Belgium French 9816250000000 1 21.43 21.43 0.00 5.00 1.07 22.50
Unsmoked Ham with
Skin (Non Halal)
Net Value (Excluding VAT): 60.48
"""


def test_parse_carrefour_skips_fees():
    out = parse_carrefour_text(CF)
    assert out["invoice_no"] == "93084417"
    assert out["invoice_date"] == "2026-08-23"
    assert len(out["items"]) == 1
    assert out["items"][0]["barcode"] == "5000112668209"
    assert out["items"][0]["qty"] == 2.0
    assert out["items"][0]["line_total"] == 11.98
    store = parse_carrefour_text(
        "Invoice No. : 1\nInvoice Date : 05-Jun-2026\nSAN PEL. 1L 1.0 9.89 9.42 9.42 5.00 0.47 9.89\nBarcode: 800227000134\n"
    )
    assert store["items"][0]["name"] == "SAN PEL. 1L"
    assert store["items"][0]["line_total"] == 9.89
    phone = parse_carrefour_text(
        "Invoice No. : 1\nInvoice Date : 10-May-2025\n"
        "HMD PULSE PRO 8 25 1 499.00 475.24 475.24 5.00 23.76 499.00\n"
        "Barcode: 6438409091819\n"
    )
    assert phone["items"][0]["name"] == "HMD PULSE PRO"
    assert phone["items"][0]["qty"] == 1.0
    assert phone["items"][0]["line_total"] == 499.0


def test_parse_grandiose_lines():
    out = parse_grandiose_text(GR)
    assert out["invoice_no"] == "3325511433512"
    assert out["invoice_date"] == "2026-05-18"
    assert {i["barcode"] for i in out["items"]} == {"9814172000000", "9816250000000"}


def test_parse_grandiose_sms_slip():
    text = """
TAX INVOICE
Grandiose Supermarket Sole Proprietorship LLC
Slip: 000000ST11000167487
Date: 06/08/2026 7:50 PM
Items Qty. Price Amount
3000000003617 1 19.00 19.00
Veroni Spianata Romana Slices
8005573008356 1 19.00 19.00
Veroni Salami Napoli Slices
3000000003073 1 0.60 0.60
Pacific Printed Brown Paper Bag
Total 38.60
"""
    out = parse_grandiose_text(text)
    assert out["invoice_no"] == "000000ST11000167487"
    assert out["invoice_date"] == "2026-08-06"
    assert len(out["items"]) == 3
    assert out["items"][0]["barcode"] == "3000000003617"
    assert out["items"][0]["line_total"] == 19.0


def test_parse_grandiose_confirmation_html():
    from bring_fast.invoice_parse import parse_grandiose_confirmation_html

    html = """
    Your Order <span class="no-link">#110670729</span>
    <p class="product-name">Veroni Hot Salami Napoli (Non Halal)</p>
    <p class="sku">SKU: 9814172000000</p>
    <td class="item-qty">0.25</td>
    <span class="price">AED 24.75</span>
    """
    out = parse_grandiose_confirmation_html(html, date="2026-05-18")
    assert out["order_no"] == "110670729"
    assert out["invoice_no"] == "order-110670729"
    assert out["items"][0]["line_total"] == 24.75
    assert out["items"][0]["qty"] == 0.25


def test_parse_mmi_invoice_line():
    text = """
Invoice No: ARInv-12869998
Invoice Date: 01-Apr-2026
Customer Ref: 260401HVW9G0OR_Emiliano
MMI Home Delivery
02420 HEINEKEN CANS 50 CL 2.00 CS24 131.14 262.27 26.23 209.81 62.94 272.75 05 13.64 286.39
Express Delivery Charge 9.52 05 0.48 10
"""
    out = parse_mmi_text(text)
    assert out["invoice_no"] == "ARInv-12869998"
    assert out["invoice_date"] == "2026-04-01"
    assert out["items"][0]["name"] == "HEINEKEN CANS 50 CL"
    assert out["items"][0]["qty"] == 2.0
    assert out["items"][0]["line_total"] == 286.39


def test_parse_africaneastern_invoice_html():
    html = """
    Your Invoice #4000149407 for Order #4000163906
    Delivery Date: Jun 19, 2024
    African Eastern Dubai Store
    Beck's (24 Cans x 500ml)
    SKU: 90490005
    UOM: Case
    1
    132.38
    0
    132.38
    132.38
    6.62
    139.00
    """
    out = parse_africaneastern_html(html)
    assert out["invoice_no"] == "4000149407"
    assert out["order_no"] == "4000163906"
    assert out["invoice_date"] == "2024-06-19"
    assert out["items"][0]["barcode"] == "90490005"
    assert out["items"][0]["line_total"] == 139.0


CAREEM_PDF = """
Tax Invoice
Careem Networks FZ LLC
TRN 100123456700003
Invoice No: CRM-2026-884213
Order ID: 8842137755
Your order from: Al Safadi Restaurant
Invoice Date: 26 Aug 2026
2 x Chicken Shawarma Wrap AED 36.00
1 x Mixed Grill Platter AED 58.50
Hummus Beiruty AED 18.00
Delivery fee AED 5.00
Service fee AED 2.50
Subtotal AED 112.50
VAT 5% AED 5.63
Total AED 123.13
"""


def test_parse_careem_invoice_keeps_dishes_and_drops_the_totals():
    out = parse_careem_text(CAREEM_PDF, source="careem.pdf")
    assert out["retailer"] == "careem"
    assert out["invoice_no"] == "CRM-2026-884213"
    assert out["order_no"] == "8842137755"
    assert out["invoice_date"] == "2026-08-26"
    # The restaurant is what you actually bought from, so it is the store name.
    assert out["store_name"] == "Careem · Al Safadi Restaurant"
    assert [i["name"] for i in out["items"]] == [
        "Chicken Shawarma Wrap",
        "Mixed Grill Platter",
        "Hummus Beiruty",
    ]
    wrap = out["items"][0]
    assert wrap["qty"] == 2.0
    assert wrap["line_total"] == 36.0
    assert wrap["unit_price"] == 18.0
    # Food has no barcode to look a product up by.
    assert all(i["barcode"] == "" for i in out["items"])


def test_parse_careem_confirmation_html_reads_either_side_of_the_quantity():
    html = """
    <table>
     <tr><td>Your order from</td><td>Reem Al Bawadi</td></tr>
     <tr><td>Order ID</td><td>9911223344</td></tr>
     <tr><td>Order Date</td><td>Aug 24, 2026</td></tr>
     <tr><td>3x Falafel Plate</td><td>AED 27.00</td></tr>
     <tr><td>Fattoush Salad x 2</td><td>AED 44.00</td></tr>
     <tr><td>Delivery</td><td>AED 6.00</td></tr>
     <tr><td>Total</td><td>AED 77.00</td></tr>
    </table>
    """
    out = parse_careem_html(html, source="mail")
    # No invoice number on a confirmation, so the order carries the receipt.
    assert out["invoice_no"] == "order-9911223344"
    assert out["invoice_date"] == "2026-08-24"
    assert [(i["name"], i["qty"], i["line_total"]) for i in out["items"]] == [
        ("Falafel Plate", 3.0, 27.0),
        ("Fattoush Salad", 2.0, 44.0),
    ]


def test_parse_careem_pairs_a_dish_with_the_price_on_the_next_line():
    """A mail table puts each cell on its own line once the tags are gone."""
    text = """
Careem
Order ID: 5566778899
Delivery Date: 12 Sep 2026
Your order from: Zaroob
2x Manakish Zaatar
AED 24.00
Karak Chai x 4
AED 16.00
Delivery fee
AED 5.00
Total
AED 45.00
"""
    out = parse_careem_text(text)
    assert out["store_name"] == "Careem · Zaroob"
    assert [(i["name"], i["qty"], i["unit_price"]) for i in out["items"]] == [
        ("Manakish Zaatar", 2.0, 12.0),
        ("Karak Chai", 4.0, 4.0),
    ]


def test_parse_careem_mail_reads_qty_addons_and_paid_price():
    html = """
    <div>Your total bill: AED 71.24</div>
    <div>21 August, 12:40 PM</div>
    <div>Delivery details</div>
    <div>Sushi Cago</div>
    <div>Order ID: 168151265</div>
    <div><span>1 &times;</span> Salmon Box 16 Pieces</div>
    <div>AED 119.60</div>
    <div>AED 59.80</div>
    <div>+ Soy Sauce ( Bottle)</div>
    <div>AED 3.00 AED 1.50</div>
    <div>Original cart (incl. tax)</div>
    <div>AED 122.60</div>
    <div>Restaurant discount</div>
    <div>- AED 61.30</div>
    <div>Delivery charge (incl. tax)</div>
    <div>AED 1.99</div>
    <div>Service fee (incl. tax)</div>
    <div>AED 4.95</div>
    <div>"Reward your captain!"</div>
    <div>AED 3.00</div>
    <div>Order total (incl. tax)</div>
    <div>AED 71.24</div>
    """
    out = parse_careem_html(html, source="mail", date="2026-08-21")
    assert out["invoice_no"] == "order-168151265"
    assert out["invoice_date"] == "2026-08-21"
    assert out["store_name"] == "Careem · Sushi Cago"
    assert [(i["name"], i["qty"], i["line_total"]) for i in out["items"]] == [
        ("Salmon Box 16 Pieces", 1.0, 59.8),
        ("Soy Sauce ( Bottle)", 1.0, 1.5),
    ]


def test_parse_careem_does_not_read_the_restaurant_as_a_dish():
    """A bare line above a total is as likely to be the restaurant as an item."""
    text = """
Careem
Order ID: 1212121212
Al Safadi Restaurant
AED 88.00
Total
AED 88.00
"""
    out = parse_careem_text(text)
    assert out["items"] == []


def test_careem_invoice_lands_in_purchases(bf):
    from datetime import date

    user = bf.db.create_user("careem@example.com", "secret1")
    parsed = parse_careem_text(CAREEM_PDF, source="careem.pdf")
    invoice_id = bf.purchases.upsert_invoice(user["id"], parsed, gmail_id="mail-1")

    assert invoice_id is not None
    orders = bf.purchases.orders_report(
        user["id"], range_key="all", include_items=True, today=date(2026, 12, 31)
    )
    order = next(o for o in orders["orders"] if o["invoice_no"] == "CRM-2026-884213")
    assert order["store"] == "Careem · Al Safadi Restaurant"
    assert {i["name"] for i in order["items"]} == {
        "Chicken Shawarma Wrap",
        "Mixed Grill Platter",
        "Hummus Beiruty",
    }


