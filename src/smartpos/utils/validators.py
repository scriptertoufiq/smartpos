"""Input checks shared by the UI forms and the services."""

from __future__ import annotations

from decimal import Decimal


def require_non_empty(value: str, field: str) -> str:
    """Raise ValidationError if the string is blank; return it stripped."""
    raise NotImplementedError


def require_positive(value: Decimal, field: str) -> Decimal:
    raise NotImplementedError


def valid_phone(value: str) -> bool:
    raise NotImplementedError


def valid_email(value: str) -> bool:
    raise NotImplementedError
