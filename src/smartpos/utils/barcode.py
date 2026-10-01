"""Barcode generation and label sheets.

Codes are EAN-13. A shop's own products have no manufacturer barcode, so one is
generated from the product id using the GS1 "restricted distribution" prefix
200-299 -- the range reserved for in-store use, which will never collide with a
real manufacturer's code printed on a packet.
"""

from __future__ import annotations

from pathlib import Path

from smartpos.core.exceptions import ValidationError

# GS1 reserves 20-29 for in-store use. 200 keeps us inside it.
INTERNAL_PREFIX = "200"
EAN13_LENGTH = 13
_BODY_DIGITS = 12


def check_digit(body: str) -> str:
    """The 13th digit of an EAN-13, from the first twelve.

    Weights alternate 1, 3 from the left; the result is what makes the total a
    multiple of ten.
    """
    if len(body) != _BODY_DIGITS or not body.isdigit():
        raise ValidationError(
            f"An EAN-13 body must be exactly {_BODY_DIGITS} digits, got {body!r}.")
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    return str((10 - total % 10) % 10)


def is_valid(code: str) -> bool:
    """True if `code` is a well-formed EAN-13 with a correct check digit."""
    code = (code or "").strip()
    if len(code) != EAN13_LENGTH or not code.isdigit():
        return False
    return check_digit(code[:_BODY_DIGITS]) == code[-1]


def generate_code(product_id: int) -> str:
    """Build an EAN-13 for a product that has no barcode yet.

    Derived from the id so the same product always yields the same code, and
    two products can never be handed the same one.
    """
    if product_id <= 0:
        raise ValidationError("A product id is needed to generate a barcode.")
    serial = str(product_id)
    room = _BODY_DIGITS - len(INTERNAL_PREFIX)
    if len(serial) > room:
        raise ValidationError(
            f"Product id {product_id} is too large for an internal barcode "
            f"(limit {10 ** room - 1}).")
    body = INTERNAL_PREFIX + serial.zfill(room)
    return body + check_digit(body)


def render_label(code: str, output: Path, *, write_text: bool = True) -> Path:
    """Render one barcode image. Returns the file actually written.

    python-barcode appends its own extension, so the path it returns is what
    the caller must use.
    """
    if not is_valid(code):
        raise ValidationError(f"{code!r} is not a valid EAN-13 barcode.")
    try:
        import barcode
        from barcode.writer import ImageWriter
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ValidationError(
            "Barcode rendering needs the python-barcode and Pillow packages."
        ) from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    ean = barcode.get("ean13", code[:_BODY_DIGITS], writer=ImageWriter())
    # Passing the stem: the writer adds .png itself.
    written = ean.save(
        str(output.with_suffix("")),
        options={"write_text": write_text, "module_height": 12.0, "quiet_zone": 2.0},
    )
    return Path(written)


def render_label_sheet(codes: list[str], output: Path, per_row: int = 3) -> Path:
    """Lay several labels out on one printable page."""
    if not codes:
        raise ValidationError("No barcodes to print.")
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise ValidationError("Label sheets need the Pillow package.") from exc

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tiles = [Image.open(render_label(code, Path(tmp) / f"label_{i}.png"))
                 for i, code in enumerate(codes)]

        cell_w = max(t.width for t in tiles)
        cell_h = max(t.height for t in tiles)
        rows = (len(tiles) + per_row - 1) // per_row
        margin = 20

        sheet = Image.new(
            "RGB",
            (per_row * cell_w + margin * 2, rows * cell_h + margin * 2),
            "white",
        )
        for index, tile in enumerate(tiles):
            x = margin + (index % per_row) * cell_w
            y = margin + (index // per_row) * cell_h
            sheet.paste(tile, (x, y))

        output.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(output)
    return output
