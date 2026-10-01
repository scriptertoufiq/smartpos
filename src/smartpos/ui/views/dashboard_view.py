"""Opening screen: today's figures, a two-week trend and what needs attention.

The trend is one series, so it carries no legend -- the heading names it -- and
only the tallest and the most recent bars are labelled. A number on every bar
is noise on a screen somebody glances at.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import matplotlib
matplotlib.use("QtAgg")                               # before pyplot is touched

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from smartpos.config import get_settings
from smartpos.core.exceptions import SmartPOSError
from smartpos.core.logging import get_logger
from smartpos.services.auth_service import AuthService
from smartpos.services.report_service import ReportService
from smartpos.services.stock_service import StockService

log = get_logger(__name__)

# One accent for the single series, plus recessive ink for everything else.
ACCENT = "#2563eb"
ACCENT_MUTED = "#bfdbfe"
INK = "#111827"
INK_MUTED = "#6b7280"
GRID = "#e5e7eb"
SURFACE = "#ffffff"


class StatTile(QFrame):
    """One headline figure. No chart, because a single number is not a chart."""

    def __init__(self, caption: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("statTile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(2)

        self.caption = QLabel(caption)
        self.caption.setObjectName("statCaption")
        layout.addWidget(self.caption)

        self.value = QLabel("—")
        self.value.setObjectName("statValue")
        layout.addWidget(self.value)

        self.detail = QLabel("")
        self.detail.setObjectName("statDetail")
        layout.addWidget(self.detail)

    def set(self, value: str, detail: str = "") -> None:
        self.value.setText(value)
        self.detail.setText(detail)


class TrendChart(FigureCanvasQTAgg):
    """Fourteen days of takings. One series, one axis, no legend."""

    def __init__(self, parent=None) -> None:
        self.figure = Figure(figsize=(7, 2.3), dpi=100, facecolor=SURFACE)
        super().__init__(self.figure)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(190)

    def plot(self, trend: list[tuple[date, Decimal]], currency: str) -> None:
        self.figure.clear()
        axes = self.figure.add_subplot(111)
        axes.set_facecolor(SURFACE)

        days = [point[0] for point in trend]
        values = [float(point[1]) for point in trend]

        if not any(values):
            axes.text(0.5, 0.5, "No sales in the last 14 days",
                      ha="center", va="center", color=INK_MUTED, fontsize=10)
            axes.set_axis_off()
            self.figure.tight_layout()
            self.draw()
            return

        peak = max(values)
        # The newest bar and the best day are the two a shopkeeper looks for;
        # everything else stays recessive.
        colours = [ACCENT if (v == peak or index == len(values) - 1) else ACCENT_MUTED
                   for index, v in enumerate(values)]
        bars = axes.bar(range(len(values)), values, color=colours, width=0.62)

        axes.set_ylim(0, peak * 1.28)
        axes.set_xticks(range(len(days)))
        axes.set_xticklabels([d.strftime("%d %b") for d in days],
                             fontsize=7.5, color=INK_MUTED, rotation=0)
        # Fourteen labels collide at this width; every other one is enough.
        for index, label in enumerate(axes.get_xticklabels()):
            label.set_visible(index % 2 == 1 or index == len(days) - 1)

        axes.tick_params(axis="y", labelsize=7.5, colors=INK_MUTED, length=0)
        axes.yaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        axes.grid(axis="y", color=GRID, linewidth=0.8)
        axes.set_axisbelow(True)
        for side in ("top", "right", "left"):
            axes.spines[side].set_visible(False)
        axes.spines["bottom"].set_color(GRID)

        # Label the two bars that matter, in ink -- never in the series colour.
        for index in {values.index(peak), len(values) - 1}:
            if values[index] <= 0:
                continue
            bar = bars[index]
            axes.annotate(f"{currency} {values[index]:,.0f}",
                          xy=(bar.get_x() + bar.get_width() / 2, values[index]),
                          xytext=(0, 4), textcoords="offset points",
                          ha="center", fontsize=8, color=INK, fontweight="bold")

        self.figure.tight_layout(pad=0.6)
        self.draw()


class DashboardView(QWidget):
    """Today's sales and collection, low-stock count and a sales trend chart."""

    def __init__(self, auth: AuthService, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._reports = ReportService(auth)
        self._stock = StockService(auth)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        header = QHBoxLayout()
        heading = QLabel("Dashboard")
        heading.setObjectName("viewHeading")
        header.addWidget(heading)
        header.addStretch(1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setMinimumHeight(30)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        tiles = QGridLayout()
        tiles.setSpacing(12)
        self.tile_sales = StatTile("Sales today")
        self.tile_collected = StatTile("Collected today")
        self.tile_low = StatTile("At or below reorder level")
        self.tile_value = StatTile("Stock on hand, at cost")
        for column, tile in enumerate((self.tile_sales, self.tile_collected,
                                       self.tile_low, self.tile_value)):
            tiles.addWidget(tile, 0, column)
        layout.addLayout(tiles)

        chart_frame = QFrame()
        chart_frame.setObjectName("panelCard")
        chart_layout = QVBoxLayout(chart_frame)
        chart_layout.setContentsMargins(14, 12, 14, 10)
        chart_layout.setSpacing(4)
        self.chart_heading = QLabel("Takings, last 14 days")
        self.chart_heading.setObjectName("panelHeading")
        chart_layout.addWidget(self.chart_heading)
        self.chart = TrendChart(chart_frame)
        chart_layout.addWidget(self.chart)
        layout.addWidget(chart_frame)

        low_frame = QFrame()
        low_frame.setObjectName("panelCard")
        low_layout = QVBoxLayout(low_frame)
        low_layout.setContentsMargins(14, 12, 14, 12)
        low_layout.setSpacing(6)
        self.low_heading = QLabel("Needs reordering")
        self.low_heading.setObjectName("panelHeading")
        low_layout.addWidget(self.low_heading)
        self.low_table = QTableWidget(0, 4)
        self.low_table.setHorizontalHeaderLabels(
            ["Product", "Outlet", "On hand", "Reorder at"])
        self.low_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.low_table.verticalHeader().setVisible(False)
        self.low_table.setAlternatingRowColors(True)
        self.low_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.low_table.setMaximumHeight(190)
        low_layout.addWidget(self.low_table)
        layout.addWidget(low_frame)

        self.refresh_button.clicked.connect(self.refresh)

    # -------------------------------------------------------------- behaviour

    def _warehouse_id(self):
        return getattr(self.window(), "active_warehouse_id", None)

    def refresh(self) -> None:
        """Reload this screen's data from its service."""
        symbol = get_settings().currency_symbol
        try:
            summary = self._reports.dashboard_summary(date.today(), self._warehouse_id())
            low = self._stock.low_stock(self._warehouse_id())
        except Exception as exc:
            if isinstance(exc, SmartPOSError):
                self.tile_sales.set("—", str(exc))
            else:
                log.exception("Could not load the dashboard")
                self.tile_sales.set("—", "Could not load today's figures.")
            return

        invoices = summary["invoices_today"]
        self.tile_sales.set(
            f"{symbol} {summary['billed_today']:,.2f}",
            f"{invoices} invoice{'s' if invoices != 1 else ''}")
        outstanding = summary["billed_today"] - summary["collected_today"]
        self.tile_collected.set(
            f"{symbol} {summary['collected_today']:,.2f}",
            f"{symbol} {outstanding:,.2f} still to collect" if outstanding > 0
            else "all settled")
        self.tile_low.set(
            str(summary["low_stock"]),
            "nothing to reorder" if not summary["low_stock"] else "needs attention")
        self.tile_value.set(f"{symbol} {summary['stock_value']:,.2f}")

        self.chart.plot(summary["trend"], symbol)

        self.low_table.setRowCount(len(low))
        for row, item in enumerate(low):
            cells = [item["product_name"], item["warehouse_name"],
                     f"{Decimal(str(item['quantity'])):,.3f}",
                     f"{Decimal(str(item['reorder_level'])):,.3f}"]
            for column, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if column >= 2:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column == 2 and Decimal(str(item["quantity"])) <= 0:
                    cell.setForeground(Qt.darkRed)
                self.low_table.setItem(row, column, cell)
        self.low_table.resizeColumnsToContents()
        self.low_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.low_heading.setText(
            f"Needs reordering ({len(low)})" if low else "Needs reordering — none")
