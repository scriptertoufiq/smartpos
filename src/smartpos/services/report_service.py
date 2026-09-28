"""Figures behind the dashboard and the report screens.

Returns plain rows; turning them into a PDF or a spreadsheet is the job of
`smartpos.reports.exporters`.
"""

from __future__ import annotations

from datetime import date
from typing import Any


class ReportService:
    def dashboard_summary(self, on: date, warehouse_id: int | None = None) -> dict[str, Any]:
        """Today's sales, today's collection, low-stock count, recent trend."""
        raise NotImplementedError

    def sales_report(
        self, date_from: date, date_to: date, warehouse_id: int | None = None
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def profit_loss(self, date_from: date, date_to: date) -> dict[str, Any]:
        """Revenue less cost of goods sold, using the unit_cost stored on each sale line."""
        raise NotImplementedError

    def top_products(self, date_from: date, date_to: date, limit: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError

    def slow_moving_products(self, days: int = 30) -> list[dict[str, Any]]:
        raise NotImplementedError

    def purchase_report(self, date_from: date, date_to: date) -> list[dict[str, Any]]:
        raise NotImplementedError

    def sale_return_report(self, date_from: date, date_to: date) -> list[dict[str, Any]]:
        raise NotImplementedError

    def stock_report(self, warehouse_id: int | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def customer_dues(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def supplier_payables(self) -> list[dict[str, Any]]:
        raise NotImplementedError
