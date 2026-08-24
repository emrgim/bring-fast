"""Parse official Carrefour / Grandiose tax-invoice PDFs."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

SKIP_NAMES = (
    "delivery charge",
    "service fee",
    "driver tip",
    "tips",
    "shipping",
    "express delivery",
    "delivery/order",
    "miscellaneous charges",
)

NUMS = re.compile(r"(\d+(?:\.\d+)?)")
BARCODE = re.compile(r"Barcode:\s*([0-9]{6,14})", re.I)
CF_DATE = re.compile(r"Invoice Date\s*:\s*([0-9]{1,2}-[A-Za-z]{3}-[0-9]{4})")
CF_INV = re.compile(r"Invoice No\.\s*:\s*([A-Za-z0-9]+)")
CF_ORD = re.compile(r"Order No\.\s*:\s*([0-9]+)")
CF_LINE_ONLINE = re.compile(
    r"^(?P<name>.+?)\s+"
    r"(?P<ord>\d+(?:\.\d+)?)\s+(?P<del>\d+(?:\.\d+)?)\s+"
    r"(?P<uinc>\d+(?:\.\d+)?)\s+(?P<uexc>\d+(?:\.\d+)?)\s+"
    r"(?P<texc>\d+(?:\.\d+)?)\s+(?P<vatp>\d+(?:\.\d+)?)\s+"
    r"(?P<vata>\d+(?:\.\d+)?)\s+(?P<disc>\d+(?:\.\d+)?)\s+"
    r"(?P<tinc>\d+(?:\.\d+)?)\s*$"
)
CF_LINE_STORE = re.compile(
    r"^(?P<name>.+?)\s+"
    r"(?P<qty>\d+(?:\.\d+)?)\s+"
    r"(?P<uinc>\d+(?:\.\d+)?)\s+(?P<uexc>\d+(?:\.\d+)?)\s+"
    r"(?P<texc>\d+(?:\.\d+)?)\s+(?P<vatp>\d+(?:\.\d+)?)\s+"
    r"(?P<vata>\d+(?:\.\d+)?)\s+(?P<tinc>\d+(?:\.\d+)?)\s*$"
)
GR_INV = re.compile(r"Tax Invoice\s*#\s*([0-9]+)", re.I)
GR_DATE = re.compile(r"Tax invoice Date:\s*([A-Za-z]+ \d{1,2}, \d{4})", re.I)
GR_ORD = re.compile(r"Order\s*#\s*([0-9]+)", re.I)
GR_SLIP = re.compile(r"Slip:\s*([A-Za-z0-9]+)", re.I)
GR_SLIP_DATE = re.compile(r"Date:\s*(\d{1,2}/\d{1,2}/\d{4})")
GR_SLIP_LINE = re.compile(
    r"^(?P<sku>\d{8,14})\s+(?P<qty>\d+(?:\.\d+)?)\s+(?P<price>\d+(?:\.\d+)?)\s+(?P<amt>\d+(?:\.\d+)?)\s*$",
    re.M,
)
GR_LINE = re.compile(
    r"^(?P<name>.+?)\s+(?P<sku>\d{8,14})\s+"
    r"(?P<qty>\d+(?:\.\d+)?)\s+(?P<price>\d+(?:\.\d+)?)\s+"
    r"(?P<net>\d+(?:\.\d+)?)\s+(?P<disc>\d+(?:\.\d+)?)\s+"
    r"(?P<vatp>\d+(?:\.\d+)?)\s+(?P<vata>\d+(?:\.\d+)?)\s+"
    r"(?P<gross>\d+(?:\.\d+)?)\s*$"
)


def _pdf_text(path: str | Path) -> str:
    import pdfplumber

    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _first(rx: re.Pattern[str], text: str) -> str:
    m = rx.search(text)
    return m.group(1) if m else ""


def _skip(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(s in n for s in SKIP_NAMES) or not n


def _parse_cf_date(raw: str) -> str:
    return datetime.strptime(raw, "%d-%b-%Y").date().isoformat()


def _parse_gr_date(raw: str) -> str:
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.06, 0.02 * max(abs(b), 1.0))


def _cf_from_numbers(name: str, nums: list[float]) -> dict[str, Any] | None:
    """Pick qty/unit/total from the price columns. Ignore till/staff codes before them."""
    if len(nums) >= 9:
        _ord, delivered, uinc, _uexc, _texc, vatp, _vata, _disc, tinc = nums[-9:]
        if 0 < vatp <= 5.05 and _close(delivered * uinc, tinc):
            return {"name": name, "qty": delivered, "unit_price": uinc, "line_total": tinc, "barcode": ""}
        if 0 < vatp <= 5.05 and _close(_ord * uinc, tinc):
            return {"name": name, "qty": _ord, "unit_price": uinc, "line_total": tinc, "barcode": ""}
    if len(nums) >= 7:
        qty, uinc, _uexc, _texc, vatp, _vata, tinc = nums[-7:]
        if 0 < vatp <= 5.05 and _close(qty * uinc, tinc):
            return {"name": name, "qty": qty, "unit_price": uinc, "line_total": tinc, "barcode": ""}
    return None


def parse_carrefour_line(line: str) -> dict[str, Any] | None:
    parts = line.strip().split()
    if len(parts) < 8:
        return None
    tail = 0
    vals: list[float] = []
    for tok in reversed(parts):
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            vals.append(float(tok))
            tail += 1
        else:
            break
    vals.reverse()
    if len(vals) < 7:
        return None
    name = " ".join(parts[:-tail]).strip()
    if not name or not re.search(r"[A-Za-z]", name):
        return None
    return _cf_from_numbers(name, vals)


def parse_carrefour_text(text: str, *, source: str = "") -> dict[str, Any]:
    inv = _first(CF_INV, text)
    date_m = CF_DATE.search(text)
    order = _first(CF_ORD, text)
    store = "Carrefour"
    for label in ("City Center Meaisem", "Ibn Batuta Mall", "MIDTOWN BY DYAR", "City Center Deira"):
        if label.lower() in text.lower():
            store = f"Carrefour {label}"
            break
    items: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parsed = parse_carrefour_line(line)
        if parsed:
            items.append(parsed)
            continue
        bm = BARCODE.search(line)
        if bm and items and not items[-1]["barcode"]:
            items[-1]["barcode"] = bm.group(1)
    kept = [it for it in items if not _skip(it["name"])]
    return {
        "retailer": "carrefour",
        "store_name": store,
        "invoice_no": inv,
        "order_no": order,
        "invoice_date": _parse_cf_date(date_m.group(1)) if date_m else "",
        "source": source,
        "items": kept,
    }


def parse_grandiose_slip(text: str, *, source: str = "") -> dict[str, Any]:
    inv = _first(GR_SLIP, text)
    dm = GR_SLIP_DATE.search(text)
    date = ""
    if dm:
        try:
            date = datetime.strptime(dm.group(1), "%d/%m/%Y").date().isoformat()
        except ValueError:
            date = ""
    items: list[dict[str, Any]] = []
    lines = [ln.strip() for ln in text.splitlines()]
    i = 0
    while i < len(lines):
        m = GR_SLIP_LINE.match(lines[i])
        if m:
            name = lines[i + 1] if i + 1 < len(lines) else m.group("sku")
            if GR_SLIP_LINE.match(name) or name.startswith("----") or name.startswith("Total"):
                name = m.group("sku")
            else:
                i += 1
            items.append(
                {
                    "name": re.sub(r"\s+", " ", name).strip(),
                    "qty": float(m.group("qty")),
                    "unit_price": float(m.group("price")),
                    "line_total": float(m.group("amt")),
                    "barcode": m.group("sku"),
                }
            )
        i += 1
    kept = [it for it in items if not _skip(it["name"])]
    return {
        "retailer": "grandiose",
        "store_name": "Grandiose",
        "invoice_no": inv,
        "order_no": "",
        "invoice_date": date,
        "source": source,
        "items": kept,
    }


def parse_grandiose_text(text: str, *, source: str = "") -> dict[str, Any]:
    if GR_SLIP.search(text) and GR_SLIP_LINE.search(text):
        return parse_grandiose_slip(text, source=source)
    inv = _first(GR_INV, text)
    date_m = GR_DATE.search(text)
    order = _first(GR_ORD, text)
    items: list[dict[str, Any]] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("Products ") or line.startswith("Net Value"):
            continue
        candidate = f"{buf} {line}".strip() if buf else line
        m = GR_LINE.match(candidate)
        if m:
            items.append(
                {
                    "name": re.sub(r"\s+", " ", m.group("name")).strip(),
                    "qty": float(m.group("qty")),
                    "unit_price": float(m.group("gross")) / max(float(m.group("qty")), 0.0001),
                    "line_total": float(m.group("gross")),
                    "barcode": m.group("sku"),
                }
            )
            buf = ""
            continue
        if re.search(r"\d{8,14}", line) or buf:
            buf = candidate
        else:
            buf = line
    kept = [it for it in items if not _skip(it["name"])]
    return {
        "retailer": "grandiose",
        "store_name": "Grandiose",
        "invoice_no": inv,
        "order_no": order,
        "invoice_date": _parse_gr_date(date_m.group(1)) if date_m else "",
        "source": source,
        "items": kept,
    }


def parse_invoice_pdf(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    text = _pdf_text(p)
    low = text.lower()
    if "grandiose" in low:
        return parse_grandiose_text(text, source=p.name)
    if "mmi home delivery" in low or "mmihomedelivery" in low:
        return parse_mmi_text(text, source=p.name)
    if "african" in low and "eastern" in low:
        return parse_africaneastern_text(text, source=p.name)
    if "majid al futtaim" in low or "carrefour" in low or "tax invoice" in low:
        return parse_carrefour_text(text, source=p.name)
    raise ValueError(f"unrecognized invoice: {p.name}")


def parse_mmi_text(text: str, *, source: str = "") -> dict[str, Any]:
    inv = _first(re.compile(r"Invoice No:\s*([A-Za-z0-9-]+)"), text)
    date_raw = _first(re.compile(r"Invoice Date:\s*([0-9]{1,2}-[A-Za-z]{3}-[0-9]{4})"), text)
    order = _first(re.compile(r"Customer Ref:\s*([A-Za-z0-9]+)"), text)
    line_rx = re.compile(
        r"^(?P<code>[A-Z0-9-]{3,16})\s+(?P<name>.+?)\s+"
        r"(?P<qty>\d+\.\d{2})\s+(?P<uom>CS\d+|EACH|BTL|BOTTLE|CASE)\s+"
        r"(?P<unit>\d+\.\d{2})\s+.+\s+(?P<total>\d+\.\d{2})\s*$",
        re.I,
    )
    items = []
    for raw in text.splitlines():
        m = line_rx.match(raw.strip())
        if not m:
            continue
        qty = float(m.group("qty"))
        total = float(m.group("total"))
        if total < 1 or _skip(m.group("name")):
            continue
        items.append(
            {
                "name": re.sub(r"\s+", " ", m.group("name")).strip(),
                "qty": qty,
                "unit_price": round(total / max(qty, 0.0001), 2),
                "line_total": total,
                "barcode": m.group("code"),
            }
        )
    return {
        "retailer": "mmi",
        "store_name": "MMI",
        "invoice_no": inv,
        "order_no": order,
        "invoice_date": _parse_cf_date(date_raw) if date_raw else "",
        "source": source,
        "items": items,
    }


def parse_africaneastern_text(text: str, *, source: str = "") -> dict[str, Any]:
    inv = (
        _first(re.compile(r"Your Invoice\s*#\s*([0-9]+)", re.I), text)
        or _first(re.compile(r"Invoice\s*#\s*([0-9]+)", re.I), text)
    )
    order = _first(re.compile(r"Order\s*#\s*([0-9]+)", re.I), text)
    date_raw = _first(re.compile(r"Delivery Date:\s*([A-Za-z]+ \d{1,2}, \d{4})", re.I), text)
    items = []
    for m in re.finditer(
        r"(?P<name>[A-Za-z0-9][^.\n]{2,80})\s+SKU:\s*(?P<sku>\d+)\s+UOM:\s*\w+\s+"
        r"(?P<qty>\d+(?:\.\d+)?)\s+(?P<rsp>\d+(?:\.\d+)?)\s+(?P<disc>\d+(?:\.\d+)?)\s+"
        r"(?P<net>\d+(?:\.\d+)?)\s+(?P<neta>\d+(?:\.\d+)?)\s+(?P<vat>\d+(?:\.\d+)?)\s+"
        r"(?P<total>\d+(?:\.\d+)?)",
        text,
        re.I,
    ):
        qty = float(m.group("qty"))
        total = float(m.group("total"))
        if total <= 0 or _skip(m.group("name")):
            continue
        items.append(
            {
                "name": re.sub(r"\s+", " ", m.group("name")).strip(),
                "qty": qty,
                "unit_price": round(total / max(qty, 0.0001), 2),
                "line_total": total,
                "barcode": m.group("sku"),
            }
        )
    date = ""
    if date_raw:
        for fmt in ("%b %d, %Y", "%B %d, %Y"):
            try:
                date = datetime.strptime(date_raw, fmt).date().isoformat()
                break
            except ValueError:
                continue
    return {
        "retailer": "africaneastern",
        "store_name": "African + Eastern",
        "invoice_no": inv or (f"order-{order}" if order else ""),
        "order_no": order,
        "invoice_date": date,
        "source": source,
        "items": items,
    }


def parse_africaneastern_html(html: str, *, source: str = "") -> dict[str, Any]:
    text = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|tr|div|h\d|li)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return parse_africaneastern_text(text, source=source)


def parse_grandiose_confirmation_html(html: str, *, source: str = "", date: str = "") -> dict[str, Any]:
    order = (
        _first(re.compile(r'no-link">\s*#\s*([0-9]{5,})', re.I), html)
        or _first(re.compile(r"Your Order[\s\S]{0,200}#\s*([0-9]{5,})", re.I), html)
        or _first(re.compile(r"Order\s*#\s*([0-9]{5,})", re.I), html)
    )
    items = []
    for m in re.finditer(
        r'class="product-name"[^>]*>([^<]+)</p>\s*'
        r'<p class="sku"[^>]*>SKU:\s*(\d+)</p>[\s\S]{0,800}?'
        r'class="item-qty"[^>]*>([0-9.]+)</td>[\s\S]{0,500}?'
        r'class="price">\s*AED\s*([0-9.]+)',
        html,
        re.I,
    ):
        qty = float(m.group(3))
        total = float(m.group(4))
        items.append(
            {
                "name": re.sub(r"\s+", " ", m.group(1)).strip(),
                "qty": qty,
                "unit_price": total / max(qty, 0.0001),
                "line_total": total,
                "barcode": m.group(2),
            }
        )
    kept = [it for it in items if not _skip(it["name"])]
    return {
        "retailer": "grandiose",
        "store_name": "Grandiose",
        "invoice_no": f"order-{order}" if order else "",
        "order_no": order,
        "invoice_date": date,
        "source": source,
        "items": kept,
    }
