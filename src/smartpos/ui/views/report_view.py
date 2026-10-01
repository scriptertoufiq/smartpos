"""Report picker, date range, results table and export."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from PyQt5.QtCore import QDate, Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from smartpos.config import get_settings
from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.reports import exporters
from smartpos.services.auth_service import AuthService
from smartpos.services.report_service import Report, ReportService
from smartpos.services.warehouse_service import WarehouseService

log = get_logger(__name__)

# Reports that ignore the date range, so the pickers can be greyed out.
UNDATED = {"stock", "dues", "payables", "slow"}
NUMERIC_HINTS = ("total", "amount", "price", "cost", "value", "due", "payable",
                 "revenue", "profit", "paid", "discount", "tax", "quantity",
                 "sold", "returned", "on_hand", "net_sold", "level")


class ReportView(QWidget):
    """Choose a report, set the period, read it, export it."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._service = ReportService(auth)
        self._warehouses = WarehouseService(auth)
        self._report: Report | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        heading = QLabel("Reports")
        heading.setObjectName("viewHeading")
        layout.addWidget(heading)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.picker = QComboBox()
        for key, label in ReportService.AVAILABLE:
            self.picker.addItem(label, key)
        self.picker.setMinimumHeight(34)
        self.picker.setMinimumWidth(190)
        bar.addWidget(self.picker)

        bar.addWidget(QLabel("From"))
        self.date_from = QDateEdit(QDate.currentDate().addDays(-30))
        self.date_from.setCalendarPopup(True)
        self.date_from.setMinimumHeight(34)
        bar.addWidget(self.date_from)
        bar.addWidget(QLabel("to"))
        self.date_to = QDateEdit(QDate.currentDate())
        self.date_to.setCalendarPopup(True)
        self.date_to.setMinimumHeight(34)
        bar.addWidget(self.date_to)

        self.outlet = QComboBox()
        self.outlet.setMinimumHeight(34)
        self.outlet.setMinimumWidth(150)
        bar.addWidget(self.outlet)

        self.run_button = QPushButton("Run")
        self.run_button.setProperty("primary", True)
        self.run_button.setMinimumHeight(34)
        bar.addWidget(self.run_button)
        bar.addStretch(1)
        layout.addLayout(bar)

        shortcuts = QHBoxLayout()
        shortcuts.setSpacing(6)
        shortcuts.addWidget(QLabel("Quick range:"))
        for label, days in (("Today", 0), ("7 days", 6), ("30 days", 29),
                            ("This year", None)):
            button = QPushButton(label)
            button.setMinimumHeight(26)
            button.clicked.connect(lambda _c, d=days: self._quick_range(d))
            shortcuts.addWidget(button)
        shortcuts.addStretch(1)
        layout.addLayout(shortcuts)

        self.title = QLabel("Choose a report and press Run.")
        self.title.setObjectName("panelHeading")
        layout.addWidget(self.title)

        self.table = QTableWidget(0, 0)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.summary = QLabel("")
        self.summary.setObjectName("hintLabel")
        self.summary.setWordWrap(True)
        footer.addWidget(self.summary, 1)
        self.pdf_button = QPushButton("Export PDF")
        self.excel_button = QPushButton("Export Excel")
        for b in (self.pdf_button, self.excel_button):
            b.setMinimumHeight(32)
            b.setEnabled(False)
            footer.addWidget(b)
        layout.addLayout(footer)

        self.run_button.clicked.connect(self.run)
        self.picker.currentIndexChanged.connect(self._picker_changed)
        self.pdf_button.clicked.connect(lambda: self._export("pdf"))
        self.excel_button.clicked.connect(lambda: self._export("excel"))
        self._load_outlets()
        self._picker_changed()

    # ------------------------------------------------------------- helpers

    def _load_outlets(self) -> None:
        self.outlet.blockSignals(True)
        self.outlet.clear()
        self.outlet.addItem("All outlets", None)
        try:
            for w in self._warehouses.list_all(active_only=True):
                self.outlet.addItem(w.name, w.id)
        except Exception:
            log.exception("Could not load outlets")
        self.outlet.blockSignals(False)

    def _picker_changed(self) -> None:
        dated = self.picker.currentData() not in UNDATED
        for widget in (self.date_from, self.date_to):
            widget.setEnabled(dated)
        self.outlet.setEnabled(self.picker.currentData() in
                               {"sales", "profit", "stock"})

    def _quick_range(self, days: int | None) -> None:
        today = QDate.currentDate()
        self.date_to.setDate(today)
        if days is None:
            self.date_from.setDate(QDate(today.year(), 1, 1))
        else:
            self.date_from.setDate(today.addDays(-days))
        self.run()

    def _report_error(self, exc: Exception) -> None:
        if isinstance(exc, SmartPOSError):
            QMessageBox.warning(self, "Cannot run that report", str(exc))
        else:
            log.exception("Unexpected error building a report")
            QMessageBox.critical(
                self, "Something went wrong",
                "The report failed. The details were written to the log file.")

    @staticmethod
    def _text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, Decimal):
            return f"{value:,.2f}"
        if isinstance(value, datetime):
            return value.strftime("%d %b %Y, %H:%M")
        if isinstance(value, date):
            return value.strftime("%d %b %Y")
        if isinstance(value, float):
            return f"{value:,.2f}"
        return str(value)

    # -------------------------------------------------------------- actions

    def refresh(self) -> None:
        """Re-run whatever is on screen, or nothing on first open."""
        self._load_outlets()
        if self._report is not None:
            self.run()

    def run(self) -> None:
        key = self.picker.currentData()
        try:
            report = self._service.run(
                key, self.date_from.date().toPyDate(),
                self.date_to.date().toPyDate(), self.outlet.currentData())
        except Exception as exc:
            self._report_error(exc)
            return

        self._report = report
        self.title.setText(report.title)

        self.table.setSortingEnabled(False)
        self.table.clear()
        self.table.setColumnCount(len(report.columns))
        self.table.setHorizontalHeaderLabels(
            [c.replace("_", " ").capitalize() for c in report.columns])
        self.table.setRowCount(len(report.rows))

        for row_index, row in enumerate(report.rows):
            for col_index, column in enumerate(report.columns):
                item = QTableWidgetItem(self._text(row.get(column)))
                if any(hint in column.lower() for hint in NUMERIC_HINTS):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row_index, col_index, item)

        self.table.resizeColumnsToContents()
        if report.columns:
            self.table.horizontalHeader().setSectionResizeMode(
                1 if len(report.columns) > 1 else 0, QHeaderView.Stretch)
        self.table.setSortingEnabled(True)

        bits = [f"{len(report.rows)} row(s)"]
        bits += [f"{k.replace('_', ' ')}: {self._text(v)}"
                 for k, v in report.totals.items()]
        if report.note:
            bits.append(report.note)
        self.summary.setText("  ·  ".join(bits))

        has_rows = bool(report.columns)
        self.pdf_button.setEnabled(has_rows)
        self.excel_button.setEnabled(has_rows)

    def _export(self, kind: str) -> None:
        if self._report is None:
            return
        stem = self._report.title.split("·")[0].strip().replace(" ", "-").lower()
        suffix = "pdf" if kind == "pdf" else "xlsx"
        target, _ = QFileDialog.getSaveFileName(
            self, f"Export {suffix.upper()}",
            str(Path.home() / f"{stem}-{date.today()}.{suffix}"),
            f"{suffix.upper()} (*.{suffix})")
        if not target:
            return
        try:
            writer = exporters.to_pdf if kind == "pdf" else exporters.to_excel
            written = writer(self._report.title, self._report.columns,
                             self._report.rows, Path(target),
                             self._report.totals, self._report.note)
        except Exception as exc:
            self._report_error(exc)
            return
        QMessageBox.information(self, "Exported",
                                f"{len(self._report.rows)} row(s) written to\n{written}")
