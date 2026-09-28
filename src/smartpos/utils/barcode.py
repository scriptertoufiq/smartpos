"""Barcode generation and label sheets."""

from __future__ import annotations

from pathlib import Path


def generate_code(product_id: int) -> str:
    """Build an EAN-13 code for a product that has no barcode yet."""
    raise NotImplementedError


def render_label(code: str, output: Path) -> Path:
    """Render one barcode image (python-barcode + Pillow)."""
    raise NotImplementedError


def render_label_sheet(codes: list[str], output: Path, per_row: int = 3) -> Path:
    """Lay several labels out on one printable page."""
    raise NotImplementedError
