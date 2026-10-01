"""Invoice rendering for thermal (80mm) and A4 printers.

Every figure printed is read from the saved sale row, never recomputed from the
cart. A receipt that disagrees with the database is worse than no receipt.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from smartpos.core.exceptions import ValidationError
from smartpos.core.logging import get_logger

log = get_logger(__name__)

THERMAL_WIDTH = 80 * mm
MARGIN = 4 * mm

ZERO = Decimal("0.00")


def _money(value) -> Decimal:
    return Decimal(str(value or 0))


def _shop(shop: dict | None) -> dict:
    """Normalise the shop block.

    Accepts either `SettingsService.shop_profile()` (name, address, ...) or the
    raw `settings` rows (shop_name, shop_address, ...). The two shapes diverged
    once already and the invoice quietly printed the default shop name, which is
    the kind of thing nobody notices until a customer does.
    """
    shop = shop or {}

    def pick(*keys: str, fallback: str = "") -> str:
        for key in keys:
            value = shop.get(key)
            if value:
                return str(value)
        return fallback

    return {
        "name": pick("name", "shop_name", fallback="SmartPOS Retail"),
        "address": pick("address", "shop_address"),
        "phone": pick("phone", "shop_phone"),
        "currency": pick("currency", "currency_symbol", fallback="BDT"),
        "footer": pick("footer", "invoice_footer", fallback="Thank you."),
    }


def _require_sale(sale: dict) -> None:
    if not sale or "items" not in sale:
        raise ValidationError("The sale has no lines to print.")


# ------------------------------------------------------------------ thermal

def render_thermal(sale: dict, output: Path, shop: dict | None = None) -> Path:
    """Write an 80mm receipt PDF. Height grows with the number of lines."""
    _require_sale(sale)
    info = _shop(shop)
    items = sale["items"]

    # 80mm roll: header, one row per line, then the totals block.
    height = (46 * mm) + (len(items) * 5 * mm) + (34 * mm)
    output.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(output), pagesize=(THERMAL_WIDTH, height))

    y = height - 8 * mm
    centre = THERMAL_WIDTH / 2

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawCentredString(centre, y, info["name"])
    y -= 5 * mm
    pdf.setFont("Helvetica", 7)
    for text in (info["address"], info["phone"]):
        if text:
            pdf.drawCentredString(centre, y, text)
            y -= 3.5 * mm

    y -= 1 * mm
    pdf.line(MARGIN, y, THERMAL_WIDTH - MARGIN, y)
    y -= 4 * mm

    pdf.setFont("Helvetica", 7)
    pdf.drawString(MARGIN, y, f"Invoice: {sale['invoice_no']}")
    y -= 3.5 * mm
    when = sale.get("sale_date")
    pdf.drawString(MARGIN, y, f"Date: {when.strftime('%d %b %Y, %H:%M') if when else '—'}")
    y -= 3.5 * mm
    pdf.drawString(MARGIN, y, f"Customer: {sale.get('customer_name', 'Walk-in')}")
    y -= 3.5 * mm
    if sale.get("username"):
        pdf.drawString(MARGIN, y, f"Served by: {sale['username']}")
        y -= 3.5 * mm

    pdf.line(MARGIN, y, THERMAL_WIDTH - MARGIN, y)
    y -= 4 * mm

    pdf.setFont("Helvetica-Bold", 7)
    pdf.drawString(MARGIN, y, "Item")
    pdf.drawRightString(THERMAL_WIDTH - MARGIN, y, "Amount")
    y -= 3.5 * mm
    pdf.setFont("Helvetica", 7)

    for item in items:
        name = item["product_name"]
        if len(name) > 34:
            name = name[:33] + "…"
        pdf.drawString(MARGIN, y, name)
        y -= 3.2 * mm
        quantity = Decimal(str(item["quantity"])).normalize()
        detail = f"  {quantity} x {_money(item['unit_price']):,.2f}"
        if _money(item["discount"]) > ZERO:
            detail += f"  less {_money(item['discount']):,.2f}"
        pdf.drawString(MARGIN, y, detail)
        pdf.drawRightString(THERMAL_WIDTH - MARGIN, y,
                            f"{_money(item['line_total']):,.2f}")
        y -= 4.6 * mm

    pdf.line(MARGIN, y, THERMAL_WIDTH - MARGIN, y)
    y -= 4 * mm

    def row(label: str, value: Decimal, bold: bool = False) -> None:
        nonlocal y
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", 9 if bold else 7)
        pdf.drawString(MARGIN, y, label)
        pdf.drawRightString(THERMAL_WIDTH - MARGIN, y, f"{value:,.2f}")
        y -= 4.5 * mm if bold else 3.6 * mm

    row("Subtotal", _money(sale["subtotal"]))
    if _money(sale["discount"]) > ZERO:
        label = "Discount"
        if sale.get("discount_type") == "percent":
            label += f" ({_money(sale['discount_value']).normalize()}%)"
        row(label, -_money(sale["discount"]))
    if _money(sale["tax_amount"]) > ZERO:
        row("Tax", _money(sale["tax_amount"]))

    row(f"TOTAL ({info['currency']})", _money(sale["grand_total"]), bold=True)
    row("Paid", _money(sale["paid_amount"]))
    if _money(sale["change_amount"]) > ZERO:
        row("Change", _money(sale["change_amount"]))

    due = _money(sale["grand_total"]) - _money(sale["paid_amount"])
    if due > ZERO:
        row("Still due", due)

    y -= 2 * mm
    pdf.setFont("Helvetica-Oblique", 6.5)
    pdf.drawCentredString(centre, y, info["footer"])

    pdf.showPage()
    pdf.save()
    log.info("Receipt written: %s", output)
    return output


# ----------------------------------------------------------------------- A4

def render_a4(sale: dict, output: Path, shop: dict | None = None) -> Path:
    """Write a full-page A4 invoice PDF."""
    _require_sale(sale)
    info = _shop(shop)
    items = sale["items"]

    output.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(output), pagesize=A4)
    width, height = A4
    left, right = 20 * mm, width - 20 * mm
    y = height - 22 * mm

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(left, y, info["name"])
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawRightString(right, y, "INVOICE")
    y -= 6 * mm

    pdf.setFont("Helvetica", 9)
    for text in (info["address"], info["phone"]):
        if text:
            pdf.drawString(left, y, text)
            y -= 4.5 * mm

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawRightString(right, height - 28 * mm, sale["invoice_no"])
    when = sale.get("sale_date")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(right, height - 33 * mm,
                        when.strftime("%d %b %Y, %H:%M") if when else "")

    y -= 4 * mm
    pdf.line(left, y, right, y)
    y -= 7 * mm

    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(left, y, "Billed to")
    pdf.setFont("Helvetica", 9)
    y -= 5 * mm
    pdf.drawString(left, y, sale.get("customer_name", "Walk-in Customer"))
    if sale.get("customer_phone"):
        y -= 4.5 * mm
        pdf.drawString(left, y, sale["customer_phone"])
    y -= 9 * mm

    columns = [left, left + 92 * mm, left + 118 * mm,
               left + 142 * mm, right]
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(columns[0], y, "Product")
    pdf.drawRightString(columns[1], y, "Qty")
    pdf.drawRightString(columns[2], y, "Price")
    pdf.drawRightString(columns[3], y, "Discount")
    pdf.drawRightString(columns[4], y, "Amount")
    y -= 2 * mm
    pdf.line(left, y, right, y)
    y -= 5.5 * mm

    pdf.setFont("Helvetica", 9)
    for item in items:
        if y < 45 * mm:                      # keep the totals block on the page
            pdf.showPage()
            y = height - 25 * mm
            pdf.setFont("Helvetica", 9)
        name = item["product_name"]
        pdf.drawString(columns[0], y, name[:52])
        pdf.drawRightString(columns[1], y,
                            f"{Decimal(str(item['quantity'])).normalize()}")
        pdf.drawRightString(columns[2], y, f"{_money(item['unit_price']):,.2f}")
        pdf.drawRightString(columns[3], y, f"{_money(item['discount']):,.2f}")
        pdf.drawRightString(columns[4], y, f"{_money(item['line_total']):,.2f}")
        y -= 5.5 * mm

    y -= 2 * mm
    pdf.line(columns[2], y, right, y)
    y -= 6 * mm

    def total_row(label: str, value: Decimal, bold: bool = False) -> None:
        nonlocal y
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", 11 if bold else 9)
        pdf.drawRightString(columns[3], y, label)
        pdf.drawRightString(columns[4], y, f"{value:,.2f}")
        y -= 6.5 * mm if bold else 5 * mm

    total_row("Subtotal", _money(sale["subtotal"]))
    if _money(sale["discount"]) > ZERO:
        label = "Discount"
        if sale.get("discount_type") == "percent":
            label += f" ({_money(sale['discount_value']).normalize()}%)"
        total_row(label, -_money(sale["discount"]))
    if _money(sale["tax_amount"]) > ZERO:
        total_row("Tax", _money(sale["tax_amount"]))
    total_row(f"Total ({info['currency']})", _money(sale["grand_total"]), bold=True)
    total_row("Paid", _money(sale["paid_amount"]))
    if _money(sale["change_amount"]) > ZERO:
        total_row("Change", _money(sale["change_amount"]))
    due = _money(sale["grand_total"]) - _money(sale["paid_amount"])
    if due > ZERO:
        total_row("Still due", due)

    pdf.setFont("Helvetica-Oblique", 8)
    pdf.drawCentredString(width / 2, 16 * mm, info["footer"])
    if sale.get("username"):
        pdf.drawString(left, 16 * mm, f"Served by {sale['username']}")

    pdf.showPage()
    pdf.save()
    log.info("Invoice written: %s", output)
    return output


def print_invoice(sale: dict, printer_name: str | None = None,
                  thermal: bool = True, shop: dict | None = None) -> Path:
    """Render the invoice and send it to a printer.

    The PDF is written first and its path returned either way, so a failed
    print still leaves something the shop can open and print by hand.
    """
    import subprocess
    import tempfile

    target = Path(tempfile.gettempdir()) / f"{sale['invoice_no'].replace('/', '-')}.pdf"
    written = render_thermal(sale, target, shop) if thermal else render_a4(sale, target, shop)

    command = ["lp"] + (["-d", printer_name] if printer_name else []) + [str(written)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=20)
        log.info("Sent %s to %s", written.name, printer_name or "the default printer")
    except FileNotFoundError:
        log.warning("No printing command available; the PDF is at %s", written)
    except subprocess.CalledProcessError as exc:
        log.error("Printing failed (%s); the PDF is at %s",
                  exc.stderr.decode(errors="replace").strip(), written)
    except subprocess.TimeoutExpired:
        log.error("The printer did not respond; the PDF is at %s", written)
    return written
