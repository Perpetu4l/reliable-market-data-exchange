import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque

import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import orderbook
from market_simulator import MarketSimulator
from gui.exchange_control_panel import ExchangeControlPanel
from gui.reliability_dashboard import ReliabilityDashboard
from gui.theme import (
    BG_CARD,
    BG_MAIN,
    BORDER_ACCENT,
    COLOR_AMBER,
    COLOR_BLUE,
    COLOR_GREEN,
    COLOR_PURPLE,
    COLOR_RED,
    GREEN_BRIGHT,
    RED_BRIGHT,
    TERMINAL_STYLESHEET,
    TEXT_MUTED,
    TEXT_PRIMARY,
    MetricCard,
    button_style,
    create_terminal_chart,
    style_ReadOnly_table,
)

FRAME_RATE = 30
MAX_HISTORY_SECONDS = 60
MAX_CHART_LEVELS_PER_SIDE = 6
MAX_TABLE_LEVELS_PER_SIDE = 8


class NumericPriceAxisItem(pg.AxisItem):
    """AxisItem that always formats price ticks to 2 decimal places (e.g. $500.11, $500.12)."""

    def tickStrings(self, values, scale, spacing):
        return [f"${v * scale:.2f}" for v in values]


class MainWindow(QMainWindow):
    """Professional Financial Trading Terminal & Real-Time Exchange Monitoring Dashboard."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Reliable Market-Data Exchange — Financial Trading & Reliability Control Room")
        self.setGeometry(40, 40, 1720, 1040)
        self.setStyleSheet(TERMINAL_STYLESHEET)

        self.max_history = MAX_HISTORY_SECONDS * FRAME_RATE
        self.time_data: Deque[float] = deque(maxlen=self.max_history)
        self.bid_price_data: Deque[float] = deque(maxlen=self.max_history)
        self.ask_price_data: Deque[float] = deque(maxlen=self.max_history)
        self.mid_price_data: Deque[float] = deque(maxlen=self.max_history)
        self.volume_buckets: Deque[tuple[float, float]] = deque(maxlen=240)
        self.visible_window_seconds: float = 10.0

        self.security = orderbook.Security("Apple Inc", "AAPL", 1)
        self.order_book = orderbook.OrderBook(self.security)
        self._initialize_book()

        self.market_simulator = MarketSimulator(self.order_book)
        self.start_time = time.time()

        self.setup_ui()

        self.market_simulator.start_simulation()

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_data)
        self.update_timer.start(int(1000 / FRAME_RATE))

    def _initialize_book(self):
        base_price = 500_00  # $500.00
        for i in range(1, 14):
            bid_price = base_price - (i * 4)
            ask_price = base_price + (i * 4)
            quantity = 160 + (14 - i) * 30
            self.order_book.add_order(orderbook.create_order("init_mm", 1, bid_price, quantity, True))
            self.order_book.add_order(orderbook.create_order("init_mm", 1, ask_price, quantity, False))

    def setup_ui(self):
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setStyleSheet(
            f"""
            QTabWidget::pane {{
                border-top: 1px solid {BORDER_ACCENT};
                background-color: {BG_MAIN};
            }}
            QTabBar {{
                background-color: #090d16;
            }}
            QTabBar::tab {{
                background-color: #111827;
                color: #94a3b8;
                border: 1px solid #1e293b;
                border-bottom: none;
                padding: 9px 24px;
                margin-right: 6px;
                margin-top: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 700;
                font-size: 13px;
            }}
            QTabBar::tab:selected {{
                background-color: #1d4ed8;
                color: #ffffff;
                border-color: #3b82f6;
            }}
            QTabBar::tab:hover:!selected {{
                background-color: #1e293b;
                color: #f8fafc;
            }}
            """
        )

        header_status = QWidget()
        hs_layout = QHBoxLayout(header_status)
        hs_layout.setContentsMargins(8, 2, 14, 2)
        hs_layout.setSpacing(10)
        self.global_status_badge = QLabel("● MATCHING ENGINE ONLINE  |  SYMBOL: AAPL  |  UDP MULTICAST + TCP RECOVERY v2")
        self.global_status_badge.setStyleSheet(
            f"color: {COLOR_GREEN}; background: rgba(16, 185, 129, 0.12); "
            f"border: 1px solid {COLOR_GREEN}; border-radius: 4px; padding: 3px 10px; font-weight: 700; font-size: 11px;"
        )
        hs_layout.addWidget(self.global_status_badge)
        tabs.setCornerWidget(header_status, Qt.Corner.TopRightCorner)

        self.setCentralWidget(tabs)

        market_overview_page = self._build_market_overview_page()
        tabs.addTab(market_overview_page, "A. Market Overview")

        self.exchange_control = ExchangeControlPanel(self)
        tabs.addTab(self.exchange_control, "B. Exchange Operations")

        self.reliability_dashboard = ReliabilityDashboard(self)
        tabs.addTab(self.reliability_dashboard, "C. Reliability Experiments")

        self.exchange_control.open_in_dashboard_requested.connect(
            lambda path: (tabs.setCurrentWidget(self.reliability_dashboard), self.reliability_dashboard.load_path(path))
        )

    def _build_market_overview_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(10)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        self.card_best_bid = MetricCard("Best Bid (AAPL)", "$499.96", "Bid Depth: —", COLOR_GREEN)
        self.card_best_ask = MetricCard("Best Ask (AAPL)", "$500.04", "Ask Depth: —", COLOR_RED)
        self.card_spread = MetricCard("Bid-Ask Spread", "$0.08", "Mid: $500.00", COLOR_AMBER)
        self.card_volume = MetricCard("Matched Volume (1s)", "0", "Total Matched: 0", COLOR_BLUE)
        self.card_book_state = MetricCard("Book Liquidity", "0 Orders", "Engine: 60 FPS C++", COLOR_PURPLE)

        top_bar.addWidget(self.card_best_bid, 1)
        top_bar.addWidget(self.card_best_ask, 1)
        top_bar.addWidget(self.card_spread, 1)
        top_bar.addWidget(self.card_volume, 1)
        top_bar.addWidget(self.card_book_state, 1)

        action_card = QFrame()
        action_card.setStyleSheet(
            f"QFrame {{ background-color: {BG_CARD}; border: 1px solid {BORDER_ACCENT}; border-radius: 5px; }}"
        )
        act_layout = QVBoxLayout(action_card)
        act_layout.setContentsMargins(10, 6, 10, 6)
        act_layout.setSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.buy_button = QPushButton("▲ Buy Pressure")
        self.buy_button.setMinimumHeight(32)
        self.buy_button.setStyleSheet(button_style("#059669", "#047857"))
        self.buy_button.clicked.connect(self.on_buy_pressure)

        self.sell_button = QPushButton("▼ Sell Pressure")
        self.sell_button.setMinimumHeight(32)
        self.sell_button.setStyleSheet(button_style("#dc2626", "#b91c1c"))
        self.sell_button.clicked.connect(self.on_sell_pressure)
        btn_row.addWidget(self.buy_button)
        btn_row.addWidget(self.sell_button)

        win_row = QHBoxLayout()
        win_row.setSpacing(6)
        win_lbl = QLabel("<b>Time Window:</b>")
        win_lbl.setStyleSheet("border: none; font-size: 11px;")
        self.window_combo = QComboBox()
        self.window_combo.addItem("Recent (8s)", 8.0)
        self.window_combo.addItem("Medium (20s)", 20.0)
        self.window_combo.addItem("Extended (45s)", 45.0)
        self.window_combo.addItem("Full Session", 0.0)
        self.window_combo.setCurrentIndex(0)
        self.window_combo.currentIndexChanged.connect(self._on_window_changed)
        win_row.addWidget(win_lbl)
        win_row.addWidget(self.window_combo, 1)

        act_layout.addLayout(btn_row)
        act_layout.addLayout(win_row)
        top_bar.addWidget(action_card, 1)

        outer.addLayout(top_bar)

        grid = QGridLayout()
        grid.setSpacing(10)

        self.setup_price_chart()
        self.setup_depth_panel()
        self.setup_volume_chart()
        self.setup_controls()

        grid.addWidget(self.price_chart, 0, 0)
        grid.addWidget(self.depth_panel, 0, 1)
        grid.addWidget(self.volume_chart, 1, 0)
        grid.addWidget(self.controls_widget, 1, 1)

        grid.setColumnStretch(0, 6)
        grid.setColumnStretch(1, 6)
        grid.setRowStretch(0, 6)
        grid.setRowStretch(1, 4)

        outer.addLayout(grid, 1)
        return page

    def _on_window_changed(self) -> None:
        val = float(self.window_combo.currentData() or 0.0)
        self.visible_window_seconds = val

    def setup_price_chart(self):
        self.price_chart = create_terminal_chart(
            "Best Bid, Best Ask & Mid-Price (AAPL — USD)",
            "Price (USD)",
            "Elapsed Time (s)",
            min_height=340,
        )
        # Format left price axis to always show 2 decimals ($500.11, $500.12)
        left_ax = NumericPriceAxisItem(orientation="left")
        left_ax.enableAutoSIPrefix(False)
        left_ax.setPen(pg.mkPen(color="#334155", width=1))
        left_ax.setTextPen(pg.mkPen(color="#cbd5e1"))
        left_ax.setStyle(tickFont=QFont("Menlo", 9), tickTextOffset=6)
        self.price_chart.getPlotItem().setAxisItems({"left": left_ax})
        self.price_chart.getPlotItem().setLabel(
            "left", "Price (USD)", **{"color": TEXT_MUTED, "font-size": "9.5pt", "font-weight": "600"}
        )
        self.price_chart.getPlotItem().showGrid(x=True, y=True, alpha=0.18)

        legend = self.price_chart.addLegend(offset=(12, 12))
        legend.setBrush(pg.mkBrush(15, 23, 42, 210))
        legend.setPen(pg.mkPen("#334155"))
        legend.setLabelTextColor("#e2e8f0")

        self.bid_line = self.price_chart.plot(
            pen=pg.mkPen(color=COLOR_GREEN, width=2.6),
            name="Best Bid ($)",
        )
        self.ask_line = self.price_chart.plot(
            pen=pg.mkPen(color=COLOR_RED, width=2.6),
            name="Best Ask ($)",
        )
        self.mid_line = self.price_chart.plot(
            pen=pg.mkPen(color="#60a5fa", width=1.4, style=Qt.PenStyle.DashLine),
            name="Mid-Price ($)",
        )

    def setup_depth_panel(self):
        """Build the Order Book Liquidity Depth panel combining:
        1. A clean Level-2 Order Book Price Ladder Table with exact 2-decimal prices ($500.11, $500.12)
        2. A Swapped-Axis Vertical Depth Chart (X-axis = Price Level in USD, Y-axis = Resting Quantity)
        """
        self.depth_panel = QGroupBox("Order Book Liquidity Depth — L2 Price Ladder && Depth Profile")
        layout = QVBoxLayout(self.depth_panel)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(8)

        # 1. Swapped-Axis Depth Chart: X-axis = Price Level ($500.11, $500.12...), Y-axis = Resting Quantity
        self.depth_chart = create_terminal_chart(
            "Liquidity Depth by Price Level (X: Price USD  |  Y: Resting Contracts)",
            "Resting Quantity (Contracts)",
            "Price Level (USD — Exact Cent Tick)",
            min_height=195,
        )
        layout.addWidget(self.depth_chart, 5)

        # 2. Institutional Level-2 Order Book Ladder Table (Top 8 Bid & Ask Levels side by side)
        self.depth_ladder_table = QTableWidget(MAX_TABLE_LEVELS_PER_SIDE, 6)
        self.depth_ladder_table.setHorizontalHeaderLabels([
            "Bid Cumul",
            "Bid Qty",
            "Bid Price ($)",
            "Ask Price ($)",
            "Ask Qty",
            "Ask Cumul",
        ])
        self.depth_ladder_table.verticalHeader().setVisible(False)
        self.depth_ladder_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        style_ReadOnly_table(self.depth_ladder_table, min_height=205, row_height=24)
        layout.addWidget(self.depth_ladder_table, 6)

    def setup_volume_chart(self):
        self.volume_chart = create_terminal_chart(
            "Matched Trade Volume (Rolling 1s Window)",
            "Matched Contracts",
            "Elapsed Time (s)",
            min_height=220,
        )
        self.volume_bars = pg.BarGraphItem(
            x=[0.0],
            height=[0.0],
            width=0.18,
            brush=pg.mkBrush(COLOR_BLUE),
            pen=pg.mkPen("#60a5fa", width=1),
        )
        self.volume_chart.addItem(self.volume_bars)

    def setup_controls(self):
        self.controls_widget = QGroupBox("Order Book Telemetry && Recent Executions")
        layout = QVBoxLayout(self.controls_widget)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(8)

        self.simulator_scope_label = QLabel("Mode: In-Process AAPL Order Book (60 FPS)")
        self.simulator_scope_label.setStyleSheet(
            f"padding: 5px 10px; background: {BG_CARD}; border: 1px solid {BORDER_ACCENT}; "
            f"border-radius: 4px; font-weight: 600; color: {TEXT_PRIMARY}; font-size: 11px;"
        )
        layout.addWidget(self.simulator_scope_label)

        tables_split = QHBoxLayout()
        tables_split.setSpacing(8)

        self.stats_table = QTableWidget(4, 4)
        self.stats_table.setHorizontalHeaderLabels(["Book Metric", "Value", "Book Metric", "Value"])
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        style_ReadOnly_table(self.stats_table, min_height=145, row_height=30)

        rows = [
            ("Best Bid", "$0.00", "Best Ask", "$0.00"),
            ("Best Bid Depth", "0", "Best Ask Depth", "0"),
            ("Bid-Ask Spread", "$0.00", "Resting Orders", "0"),
            ("Matched Units", "0", "Transport", "inprocess"),
        ]
        for r_idx, (k1, v1, k2, v2) in enumerate(rows):
            for c_idx, txt in enumerate((k1, v1, k2, v2)):
                item = QTableWidgetItem(txt)
                if c_idx in (0, 2):
                    f = QFont()
                    f.setBold(True)
                    item.setFont(f)
                    item.setForeground(QColor(TEXT_MUTED))
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.stats_table.setItem(r_idx, c_idx, item)

        self.recent_trades_table = QTableWidget(0, 4)
        self.recent_trades_table.setHorizontalHeaderLabels(["Time", "Side", "Price ($)", "Qty"])
        self.recent_trades_table.verticalHeader().setVisible(False)
        self.recent_trades_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        style_ReadOnly_table(self.recent_trades_table, min_height=145, row_height=26)

        tables_split.addWidget(self.stats_table, 6)
        tables_split.addWidget(self.recent_trades_table, 5)
        layout.addLayout(tables_split)

        self.order_count_label = QLabel("Order Count: 0")
        self.spread_label = QLabel("Spread: 0")
        self.best_bid_label = QLabel("Best Bid: $0.00")
        self.best_ask_label = QLabel("Best Ask: $0.00")
        self.bid_depth_label = QLabel("Best Bid Depth: 0")
        self.ask_depth_label = QLabel("Best Ask Depth: 0")

    def update_data(self):
        current_time = time.time() - self.start_time
        best_bid = self.order_book.get_best_bid_price()
        best_ask = self.order_book.get_best_ask_price()

        if best_bid is None or best_ask is None:
            return

        bid_usd = best_bid / 100.0
        ask_usd = best_ask / 100.0
        mid_usd = (bid_usd + ask_usd) / 2.0

        self.time_data.append(current_time)
        self.bid_price_data.append(bid_usd)
        self.ask_price_data.append(ask_usd)
        self.mid_price_data.append(mid_usd)

        recent_volume = float(self.market_simulator.get_recent_volume(1.0))
        bucket_idx = int(current_time * 4.0)
        bucket_t = bucket_idx * 0.25
        if self.volume_buckets and self.volume_buckets[-1][0] == bucket_t:
            self.volume_buckets[-1] = (bucket_t, max(self.volume_buckets[-1][1], recent_volume))
        else:
            self.volume_buckets.append((bucket_t, recent_volume))

        if len(self.time_data) > 1:
            t_list = list(self.time_data)
            b_list = list(self.bid_price_data)
            a_list = list(self.ask_price_data)
            m_list = list(self.mid_price_data)

            self.bid_line.setData(t_list, b_list)
            self.ask_line.setData(t_list, a_list)
            self.mid_line.setData(t_list, m_list)

            if self.visible_window_seconds > 0:
                x_min = max(0.0, current_time - self.visible_window_seconds)
                x_max = max(self.visible_window_seconds, current_time + 0.15)
            else:
                x_min = 0.0
                x_max = max(5.0, current_time + 0.25)

            self.price_chart.setXRange(x_min, x_max, padding=0)

            vis_bids = [b for t, b in zip(t_list, b_list) if t >= x_min] or b_list[-60:]
            vis_asks = [a for t, a in zip(t_list, a_list) if t >= x_min] or a_list[-60:]
            low_p = min(min(vis_bids), min(vis_asks))
            high_p = max(max(vis_bids), max(vis_asks))
            center_p = (low_p + high_p) / 2.0
            span_p = max(0.35, high_p - low_p)
            margin = span_p * 0.32
            self.price_chart.setYRange(center_p - span_p * 0.5 - margin, center_p + span_p * 0.5 + margin, padding=0)

            vis_vols = [(bt, bv) for bt, bv in self.volume_buckets if bt >= x_min - 0.25]
            if vis_vols:
                vx = [bt for bt, _ in vis_vols]
                vh = [bv for _, bv in vis_vols]
                bar_w = max(0.14, min(0.65, (x_max - x_min) / 55.0))
                self.volume_bars.setOpts(x=vx, y=[0.0] * len(vx), height=vh, width=bar_w)
                self.volume_chart.setXRange(x_min, x_max, padding=0)
                max_v = max(vh, default=100.0)
                self.volume_chart.setYRange(0.0, max(100.0, max_v * 1.25), padding=0)

        self.update_depth_chart(bid_usd, ask_usd)
        self.update_statistics(recent_volume)

    def update_depth_chart(self, best_bid_usd: float, best_ask_usd: float):
        """Render both the Swapped-Axis Discrete Depth Chart (X=Price, Y=Quantity) and the L2 Ladder Table."""
        bid_quantities = self.order_book.get_bid_quantities()
        ask_quantities = self.order_book.get_ask_quantities()

        # Top levels for the L2 Table (sorted closest to spread first: Bids descending, Asks ascending)
        top_bids_desc = sorted(bid_quantities.keys(), reverse=True)[:MAX_TABLE_LEVELS_PER_SIDE]
        top_asks_asc = sorted(ask_quantities.keys())[:MAX_TABLE_LEVELS_PER_SIDE]

        # Populate the L2 Order Book Price Ladder Table with exact 2-decimal prices ($500.11, $500.12)
        self.depth_ladder_table.setRowCount(MAX_TABLE_LEVELS_PER_SIDE)
        bid_cumul = 0
        ask_cumul = 0
        bold_font = QFont("Menlo", 10)
        bold_font.setBold(True)
        mono_font = QFont("Menlo", 9)

        for row in range(MAX_TABLE_LEVELS_PER_SIDE):
            if row < len(top_bids_desc):
                bp = top_bids_desc[row]
                bq = int(bid_quantities[bp])
                bid_cumul += bq
                bp_str = f"${bp / 100.0:.2f}"
                bq_str = f"{bq:,}"
                bc_str = f"{bid_cumul:,}"
            else:
                bp_str, bq_str, bc_str = "—", "—", "—"

            if row < len(top_asks_asc):
                ap = top_asks_asc[row]
                aq = int(ask_quantities[ap])
                ask_cumul += aq
                ap_str = f"${ap / 100.0:.2f}"
                aq_str = f"{aq:,}"
                ac_str = f"{ask_cumul:,}"
            else:
                ap_str, aq_str, ac_str = "—", "—", "—"

            cells = [
                (bc_str, TEXT_MUTED, False, Qt.AlignmentFlag.AlignRight),
                (bq_str, TEXT_PRIMARY, False, Qt.AlignmentFlag.AlignRight),
                (bp_str, GREEN_BRIGHT, True, Qt.AlignmentFlag.AlignRight),
                (ap_str, RED_BRIGHT, True, Qt.AlignmentFlag.AlignLeft),
                (aq_str, TEXT_PRIMARY, False, Qt.AlignmentFlag.AlignRight),
                (ac_str, TEXT_MUTED, False, Qt.AlignmentFlag.AlignRight),
            ]
            for col_idx, (txt, color_hex, is_bold, align) in enumerate(cells):
                it = QTableWidgetItem(txt)
                it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                it.setForeground(QColor(color_hex))
                it.setFont(bold_font if is_bold else mono_font)
                if col_idx == 2 and bp_str != "—":
                    it.setBackground(QColor(16, 185, 129, 32 if row == 0 else 16))
                elif col_idx == 3 and ap_str != "—":
                    it.setBackground(QColor(239, 68, 68, 32 if row == 0 else 16))
                self.depth_ladder_table.setItem(row, col_idx, it)

        # Render the Swapped-Axis Depth Chart:
        # X-axis = ascending Price Levels ($499.88, $499.92, ... | Mid | $500.04, $500.11, $500.12)
        # Y-axis = Resting Quantity (Contracts)
        chart_bids = sorted(top_bids_desc[:MAX_CHART_LEVELS_PER_SIDE])  # ascending up to best bid
        chart_asks = top_asks_asc[:MAX_CHART_LEVELS_PER_SIDE]          # ascending from best ask

        self.depth_chart.clear()
        total_bars = len(chart_bids) + len(chart_asks)
        if total_bars == 0:
            return

        bid_xs = list(range(len(chart_bids)))
        ask_xs = list(range(len(chart_bids), total_bars))
        bid_hs = [float(bid_quantities[p]) for p in chart_bids]
        ask_hs = [float(ask_quantities[p]) for p in chart_asks]

        max_qty = max(bid_hs + ask_hs, default=100.0)
        top_y = max(50.0, max_qty * 1.34)

        if bid_xs:
            self.depth_chart.addItem(
                pg.BarGraphItem(
                    x=bid_xs,
                    height=bid_hs,
                    width=0.56,
                    brush=pg.mkBrush(16, 185, 129, 220),
                    pen=pg.mkPen(COLOR_GREEN, width=1.2),
                )
            )
            for x_pos, h_val in zip(bid_xs, bid_hs):
                lbl = pg.TextItem(
                    html=f"<div style='color: #a7f3d0; font-size: 8pt; font-weight: 700;'>{int(h_val):,}</div>",
                    anchor=(0.5, 1.05),
                )
                lbl.setPos(x_pos, h_val + top_y * 0.015)
                self.depth_chart.addItem(lbl)

        if ask_xs:
            self.depth_chart.addItem(
                pg.BarGraphItem(
                    x=ask_xs,
                    height=ask_hs,
                    width=0.56,
                    brush=pg.mkBrush(239, 68, 68, 220),
                    pen=pg.mkPen(COLOR_RED, width=1.2),
                )
            )
            for x_pos, h_val in zip(ask_xs, ask_hs):
                lbl = pg.TextItem(
                    html=f"<div style='color: #fecaca; font-size: 8pt; font-weight: 700;'>{int(h_val):,}</div>",
                    anchor=(0.5, 1.05),
                )
                lbl.setPos(x_pos, h_val + top_y * 0.015)
                self.depth_chart.addItem(lbl)

        # Vertical Mid-Price line between Best Bid and Best Ask
        mid_usd = (best_bid_usd + best_ask_usd) / 2.0
        mid_x = len(chart_bids) - 0.5
        mid_line = pg.InfiniteLine(
            pos=mid_x,
            angle=90,
            pen=pg.mkPen("#60a5fa", width=1.6, style=Qt.PenStyle.DashLine),
            label=f"Mid ${mid_usd:.2f}",
            labelOpts={"position": 0.86, "color": "#93c5fd", "fill": (15, 23, 42, 220)},
        )
        self.depth_chart.addItem(mid_line)

        # Explicit 2-decimal-place price labels on the bottom X-axis ($500.11, $500.12, etc.)
        all_xs = bid_xs + ask_xs
        all_labels = [f"${p / 100.0:.2f}" for p in (chart_bids + chart_asks)]
        self.depth_chart.getPlotItem().getAxis("bottom").setTicks([list(zip(all_xs, all_labels))])
        self.depth_chart.setXRange(-0.7, total_bars - 0.3, padding=0)
        self.depth_chart.setYRange(0.0, top_y, padding=0)

    def update_statistics(self, recent_volume: float = 0.0):
        order_count = self.order_book.count()
        spread_obj = self.order_book.get_spread()
        spread = spread_obj.spread() if spread_obj else None
        best_bid = self.order_book.get_best_bid_price()
        best_ask = self.order_book.get_best_ask_price()

        bid_quantities = self.order_book.get_bid_quantities()
        ask_quantities = self.order_book.get_ask_quantities()
        bid_depth = bid_quantities.get(best_bid, 0) if best_bid else 0
        ask_depth = ask_quantities.get(best_ask, 0) if best_ask else 0
        matched = self.order_book.get_orders_matched()

        bid_str = f"${best_bid / 100:.2f}" if best_bid is not None else "—"
        ask_str = f"${best_ask / 100:.2f}" if best_ask is not None else "—"
        spread_str = f"${spread / 100:.2f}" if spread is not None else "—"
        mid_str = f"${(best_bid + best_ask) / 200.0:.2f}" if (best_bid and best_ask) else "—"

        self.card_best_bid.set_metric(bid_str, f"Top Level Depth: {bid_depth:,} contracts", COLOR_GREEN)
        self.card_best_ask.set_metric(ask_str, f"Top Level Depth: {ask_depth:,} contracts", COLOR_RED)
        self.card_spread.set_metric(spread_str, f"Mid-Price: {mid_str}", COLOR_AMBER)
        self.card_volume.set_metric(f"{int(recent_volume):,}", f"Cumulative Matched: {matched:,}", COLOR_BLUE)
        self.card_book_state.set_metric(f"{order_count:,} Orders", "Symbol: AAPL (C++ Engine)", COLOR_PURPLE)

        self.order_count_label.setText(f"Order Count: {order_count}")
        self.spread_label.setText(f"Spread: {spread_str}")
        self.best_bid_label.setText(f"Best Bid: {bid_str}")
        self.best_ask_label.setText(f"Best Ask: {bid_str}")
        self.bid_depth_label.setText(f"Best Bid Depth: {bid_depth}")
        self.ask_depth_label.setText(f"Best Ask Depth: {ask_depth}")

        def _cell(txt: str, color: str | None = None) -> QTableWidgetItem:
            it = QTableWidgetItem(txt)
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if color:
                it.setForeground(QColor(color))
            return it

        self.stats_table.setItem(0, 1, _cell(bid_str, COLOR_GREEN))
        self.stats_table.setItem(0, 3, _cell(ask_str, COLOR_RED))
        self.stats_table.setItem(1, 1, _cell(f"{bid_depth:,}"))
        self.stats_table.setItem(1, 3, _cell(f"{ask_depth:,}"))
        self.stats_table.setItem(2, 1, _cell(spread_str, COLOR_AMBER))
        self.stats_table.setItem(2, 3, _cell(f"{order_count:,}"))
        self.stats_table.setItem(3, 1, _cell(f"{matched:,}", COLOR_BLUE))
        self.stats_table.setItem(3, 3, _cell("inprocess (AAPL)"))

        recent_trades = self.market_simulator.get_recent_trades(6)
        self.recent_trades_table.setRowCount(len(recent_trades))
        for r_idx, tr in enumerate(recent_trades):
            ts_str = datetime.fromtimestamp(tr.timestamp).strftime("%H:%M:%S")
            side_it = QTableWidgetItem(tr.side)
            side_it.setForeground(QColor(COLOR_GREEN if tr.side == "BUY" else COLOR_RED))
            p_it = _cell(f"${tr.price / 100.0:.2f}")
            q_it = _cell(f"{tr.quantity:,}")
            self.recent_trades_table.setItem(r_idx, 0, QTableWidgetItem(ts_str))
            self.recent_trades_table.setItem(r_idx, 1, side_it)
            self.recent_trades_table.setItem(r_idx, 2, p_it)
            self.recent_trades_table.setItem(r_idx, 3, q_it)

        self.simulator_scope_label.setText(
            f"AAPL Book Telemetry  |  Mid: {mid_str}  |  Spread: {spread_str}  |  Resting: {order_count:,}  |  Matched: {matched:,}"
        )

    def on_buy_pressure(self):
        self.market_simulator.add_buy_pressure()

    def on_sell_pressure(self):
        self.market_simulator.add_sell_pressure()

    def closeEvent(self, event):
        self.market_simulator.stop_simulation()
        self.exchange_control.shutdown()
        self.reliability_dashboard.shutdown()
        event.accept()
