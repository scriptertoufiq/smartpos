"""Display formatting for money, quantities and dates."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal


def money(amount: Decimal, symbol: str | None = None) -> str:
    """Format an amount for display, e.g. 'BDT 1,250.00'."""
    raise NotImplementedError


def quantity(value: Decimal) -> str:
    """Format a quantity, trimming trailing zeros ('2.500' -> '2.5')."""
    raise NotImplementedError


def format_date(value: date | datetime) -> str:
    raise NotImplementedError
