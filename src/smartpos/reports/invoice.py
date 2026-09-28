"""Invoice rendering for thermal (80mm) and A4 printers."""

from __future__ import annotations

from pathlib import Path

from smartpos.models.sale import Sale


def render_thermal(sale: Sale, output: Path) -> Path:
    """Write an 80mm receipt PDF."""
    raise NotImplementedError


def render_a4(sale: Sale, output: Path) -> Path:
    """Write a full-page A4 invoice PDF."""
    raise NotImplementedError


def print_invoice(sale: Sale, printer_name: str | None = None, thermal: bool = True) -> None:
    """Send the invoice straight to a printer."""
    raise NotImplementedError
