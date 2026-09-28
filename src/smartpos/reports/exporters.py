"""Export report rows to PDF and Excel."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def to_pdf(title: str, columns: list[str], rows: list[dict[str, Any]], output: Path) -> Path:
    """Render rows as a titled PDF table (ReportLab)."""
    raise NotImplementedError


def to_excel(title: str, columns: list[str], rows: list[dict[str, Any]], output: Path) -> Path:
    """Write rows to an .xlsx worksheet (openpyxl)."""
    raise NotImplementedError
