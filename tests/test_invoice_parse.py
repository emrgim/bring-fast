from bring_fast.invoice_parse import parse_carrefour_text, parse_grandiose_text
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

