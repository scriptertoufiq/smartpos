"""Figures behind the dashboard and the report screens.

Returns plain rows; turning them into a PDF or a spreadsheet is the job of
`smartpos.reports.exporters`.

Profit is computed from `sale_items.unit_cost` -- the cost stamped on the line
the day it sold -- so editing a product's purchase price tomorrow cannot change
what last month earned. Tax is excluded from profit: it is collected on behalf
of the government, not earned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from smartpos.core.exceptions import ValidationError
from smartpos.core.logging import get_logger
from smartpos.db.report_repository import ReportRepository
from smartpos.services.auth_service import AuthService

log = get_logger(__name__)

VIEW = "report.view"
ZERO = Decimal("0.00")


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(ZERO)


@dataclass
class Report:
    """A table of rows, ready to show or export."""

    title: str
    columns: list[str]
    rows: list[dict]
    totals: dict[str, Any] = field(default_factory=dict)
    note: str = ""


class ReportService:
    def __init__(self, auth: AuthService | None = None) -> None:
        self._repo = ReportRepository()
        self._auth = auth

    # ------------------------------------------------------------- dashboard

    def dashboard_summary(self, on: date | None = None,
                          warehouse_id: int | None = None) -> dict[str, Any]:
        """Today's sales, today's collection, low-stock count and a trend."""
        on = on or date.today()
        today = self._repo.sales_totals(on, on, warehouse_id)
        collected = self._repo.collected_on(on)
        trend = self._repo.sales_by_day(on - timedelta(days=13), on, warehouse_id)
        by_day = {row["day"]: _money(row["billed"]) for row in trend}

        return {
            "date": on,
            "invoices_today": int(today.get("invoices") or 0),
            "billed_today": _money(today.get("billed")),
            "collected_today": _money(collected.get("collected")),
            "low_stock": self._repo.low_stock_count(warehouse_id),
            "stock_value": _money(self._repo.stock_value(warehouse_id)),
            "trend": [(on - timedelta(days=offset),
                       by_day.get(on - timedelta(days=offset), ZERO))
                      for offset in range(13, -1, -1)],
        }

    # --------------------------------------------------------------- reports

    def sales_report(self, date_from: date, date_to: date,
                     warehouse_id: int | None = None) -> Report:
        rows = self._repo.sales_detail(date_from, date_to, warehouse_id)
        totals = self._repo.sales_totals(date_from, date_to, warehouse_id)
        return Report(
            title=f"Sales · {date_from} to {date_to}",
            columns=["invoice_no", "sale_date", "customer", "outlet", "subtotal",
                     "discount", "tax_amount", "grand_total", "paid_amount",
                     "payment_status", "served_by"],
            rows=rows,
            totals={"invoices": int(totals.get("invoices") or 0),
                    "billed": _money(totals.get("billed")),
                    "collected": _money(totals.get("collected")),
                    "outstanding": _money(totals.get("billed")) - _money(totals.get("collected"))},
            note="Held and cancelled sales are excluded.")

    def purchase_report(self, date_from: date, date_to: date) -> Report:
        rows = self._repo.purchases_detail(date_from, date_to)
        billed = sum((_money(r["grand_total"]) for r in rows), ZERO)
        paid = sum((_money(r["paid_amount"]) for r in rows), ZERO)
        return Report(
            title=f"Purchases · {date_from} to {date_to}",
            columns=["reference_no", "purchase_date", "supplier", "outlet",
                     "subtotal", "discount", "tax_amount", "grand_total",
                     "paid_amount", "payment_status"],
            rows=rows,
            totals={"purchases": len(rows), "billed": billed, "paid": paid,
                    "outstanding": billed - paid})

    def sale_return_report(self, date_from: date, date_to: date) -> Report:
        rows = self._repo.returns_detail(date_from, date_to)
        return Report(
            title=f"Sale returns · {date_from} to {date_to}",
            columns=["reference_no", "return_date", "invoice_no", "customer",
                     "outlet", "total_amount", "refund_method", "reason"],
            rows=rows,
            totals={"returns": len(rows),
                    "refunded": sum((_money(r["total_amount"]) for r in rows), ZERO)})

    def stock_report(self, warehouse_id: int | None = None) -> Report:
        rows = self._repo.stock_detail(warehouse_id)
        return Report(
            title="Stock on hand",
            columns=["sku", "product", "outlet", "quantity", "reorder_level",
                     "purchase_price", "sale_price", "stock_value"],
            rows=rows,
            totals={"lines": len(rows),
                    "value": sum((_money(r["stock_value"]) for r in rows), ZERO)},
            note="Valued at the latest purchase price.")

    def profit_loss(self, date_from: date, date_to: date,
                    warehouse_id: int | None = None) -> dict[str, Decimal]:
        """Revenue less the cost of what was sold, net of returns.

        Every figure is returned, not only the answer, so a number that looks
        wrong can be traced to the part that produced it.
        """
        sales = self._repo.sales_totals(date_from, date_to, warehouse_id)
        cogs_row = self._repo.cost_of_goods_sold(date_from, date_to, warehouse_id)
        returns = self._repo.returns_totals(date_from, date_to, warehouse_id)

        line_revenue = _money(cogs_row.get("line_revenue"))
        header_discount = _money(sales.get("discount"))
        returned = _money(returns.get("amount"))
        cogs = _money(cogs_row.get("cogs"))
        returned_cogs = _money(returns.get("cogs"))

        net_revenue = line_revenue - header_discount - returned
        net_cogs = cogs - returned_cogs

        return {
            "line_revenue": line_revenue,
            "header_discount": header_discount,
            "returned": returned,
            "net_revenue": net_revenue,
            "cogs": cogs,
            "returned_cogs": returned_cogs,
            "net_cogs": net_cogs,
            "gross_profit": net_revenue - net_cogs,
            "tax_collected": _money(sales.get("tax")),
            "invoices": int(sales.get("invoices") or 0),
        }

    def profit_report(self, date_from: date, date_to: date,
                      warehouse_id: int | None = None) -> Report:
        figures = self.profit_loss(date_from, date_to, warehouse_id)
        labels = {
            "line_revenue": "Revenue from lines",
            "header_discount": "Less invoice discounts",
            "returned": "Less returns",
            "net_revenue": "Net revenue",
            "cogs": "Cost of goods sold",
            "returned_cogs": "Less cost of returns",
            "net_cogs": "Net cost",
            "gross_profit": "Gross profit",
            "tax_collected": "Tax collected (not profit)",
        }
        rows = [{"figure": label, "amount": figures[key]}
                for key, label in labels.items()]
        return Report(
            title=f"Profit and loss · {date_from} to {date_to}",
            columns=["figure", "amount"], rows=rows,
            totals={"gross_profit": figures["gross_profit"],
                    "invoices": figures["invoices"]},
            note="Cost is what each line cost on the day it sold, not today's "
                 "purchase price. Tax is excluded from profit.")

    def top_products(self, date_from: date, date_to: date,
                     limit: int = 10) -> Report:
        rows = self._repo.top_products(date_from, date_to, limit)
        for row in rows:
            row["net_sold"] = Decimal(str(row["sold"])) - Decimal(str(row["returned"]))
            row["profit"] = _money(row["revenue"]) - _money(row["cost"])
        return Report(
            title=f"Top products · {date_from} to {date_to}",
            columns=["sku", "product", "sold", "returned", "net_sold",
                     "revenue", "cost", "profit"],
            rows=rows, totals={"products": len(rows)})

    def slow_moving_products(self, days: int = 30, limit: int = 50) -> Report:
        since = date.today() - timedelta(days=days)
        rows = self._repo.slow_moving(since, limit)
        return Report(
            title=f"Slow moving · nothing sold since {since}",
            columns=["sku", "product", "on_hand", "last_sold"],
            rows=rows, totals={"products": len(rows)},
            note="Products holding stock that have not sold in the period.")

    def customer_dues(self) -> Report:
        rows = self._repo.customer_dues()
        return Report(
            title="Customer dues", columns=["customer", "phone", "due"],
            rows=rows,
            totals={"customers": len(rows),
                    "due": sum((_money(r["due"]) for r in rows), ZERO)})

    def supplier_payables(self) -> Report:
        rows = self._repo.supplier_payables()
        return Report(
            title="Supplier payables", columns=["supplier", "phone", "payable"],
            rows=rows,
            totals={"suppliers": len(rows),
                    "payable": sum((_money(r["payable"]) for r in rows), ZERO)})

    # ------------------------------------------------------------- dispatch

    AVAILABLE = (
        ("sales", "Sales"),
        ("profit", "Profit and loss"),
        ("purchases", "Purchases"),
        ("returns", "Sale returns"),
        ("stock", "Stock on hand"),
        ("top", "Top products"),
        ("slow", "Slow moving"),
        ("dues", "Customer dues"),
        ("payables", "Supplier payables"),
    )

    def run(self, key: str, date_from: date, date_to: date,
            warehouse_id: int | None = None) -> Report:
        """Build one report by name, for the report screen's picker."""
        self._require()
        if date_from > date_to:
            raise ValidationError("The start date is after the end date.")

        builders = {
            "sales": lambda: self.sales_report(date_from, date_to, warehouse_id),
            "profit": lambda: self.profit_report(date_from, date_to, warehouse_id),
            "purchases": lambda: self.purchase_report(date_from, date_to),
            "returns": lambda: self.sale_return_report(date_from, date_to),
            "stock": lambda: self.stock_report(warehouse_id),
            "top": lambda: self.top_products(date_from, date_to),
            "slow": lambda: self.slow_moving_products(),
            "dues": self.customer_dues,
            "payables": self.supplier_payables,
        }
        if key not in builders:
            raise ValidationError(f"Unknown report {key!r}.")
        return builders[key]()

    def _require(self):
        if self._auth is None:
            return None
        return self._auth.require(VIEW)
