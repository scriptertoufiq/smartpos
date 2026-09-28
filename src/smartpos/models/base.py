"""Shared pieces for the model dataclasses.

These are plain data holders that mirror one table row each. They carry no
persistence logic -- that lives in the repositories.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Entity:
    """Columns every table shares."""

    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
