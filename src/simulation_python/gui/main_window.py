import random
import sys
import time
from collections import deque
from pathlib import Path
from typing import Deque

import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Allow the GUI module to be imported directly (for tests or tools) as well as
# through main.py. The local pybind11 modules are built into <repo>/orderbook.
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import orderbook
from market_simulator import MarketSimulator
from gui.exchange_control_panel import ExchangeControlPanel
from gui.reliability_dashboard import ReliabilityDashboard

LINE_WIDTH = 3
COLOUR_RED = (220, 38, 38)
COLOUR_GREEN = (22, 163, 74)
COLOUR_BLUE = (37, 99, 235)

FRAME_RATE = 60
MAX_AGE_SECONDS = 10
SCALING_LOOKBACK_SECONDS = 5
MAX_RENDERED_DEPTHS = 15


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Reliable Market-Data Exchange — Visual Control Room & Simulation")
        self.setGeometry(60, 60, 1680, 1020)

        self.max_history = MAX_AGE_SECONDS * FRAME_RATE
        self.time_data: Deque[float] = deque(maxlen=self.max_history)
        self.bid_price_data: Deque[float] = deque(maxlen=self.max_history)
        self.ask_price_data: Deque[float] = deque(maxlen=self.max_history)
        self.volume_data: Deque[float] = deque(maxlen=self.max_history)

        self.setup_ui()

        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_data)
        self.update_timer.start(int(1000 / FRAME_RATE))

        self.security = orderbook.Security("Apple Inc", "AAPL", 1)
        self.order_book = orderbook.OrderBook(self.security)
        self._initialize_book()

        self.market_simulator = MarketSimulator(self.order_book)
        self.market_simulator.start_simulation()

        self.start_time = time.time()
        self.last_volume_check = 0

    def _initialize_book(self):
        base_price = 50000

        initial_bid = orderbook.create_order("init_user", 1, base_price - 200, 1000, True)  # $498.00
        initial_ask = orderbook.create_order("init_user", 1, base_price + 200, 1000, False)  # $502.00

        self.order_book.add_order(initial_bid)
        self.order_book.add_order(initial_ask)

        for i in range(1, 6):
            bid_price = base_price - 200 - (i * 100)
            ask_price = base_price + 200 + (i * 100)
            quantity = random.randint(100, 500)

            bid_order = orderbook.create_order("init_user", 1, bid_price, quantity, True)
            ask_order = orderbook.create_order("init_user", 1, ask_price, quantity, False)

            self.order_book.add_order(bid_order)
            self.order_book.add_order(ask_order)

    def setup_ui(self):
        tabs = QTabWidget()
        tabs.setStyleSheet(
            "QTabBar::tab { padding: 8px 18px; font-weight: 600; font-size: 13px; } "
            "QTabBar::tab:selected { background: #1d4ed8; color: white; border-radius: 4px; }"
        )
        self.setCentralWidget(tabs)
        central_widget = QWidget()
        main_layout = QGridLayout(central_widget)
        main_layout.setSpacing(10)

        self.setup_price_chart()
        self.setup_depth_chart()
        self.setup_volume_chart()
        self.setup_controls()

        main_layout.addWidget(self.price_chart, 0, 0, 1, 1)
        main_layout.addWidget(self.depth_chart, 0, 1, 1, 1)
        main_layout.addWidget(self.volume_chart, 1, 0, 1, 1)
        main_layout.addWidget(self.controls_widget, 1, 1, 1, 1)

        main_layout.setColumnStretch(0, 3)
        main_layout.setColumnStretch(1, 2)
        main_layout.setRowStretch(0, 3)
        main_layout.setRowStretch(1, 2)

        tabs.addTab(central_widget, "1. Market Visualisation (Live Order Book)")
        self.exchange_control = ExchangeControlPanel(self)
        tabs.addTab(self.exchange_control, "2. Exchange Control (Live Processes & Graphs)")
        self.reliability_dashboard = ReliabilityDashboard(self)
        tabs.addTab(self.reliability_dashboard, "3. Reliability Experiments (6-Graph Dashboard)")
        self.exchange_control.open_in_dashboard_requested.connect(
            lambda path: (tabs.setCurrentWidget(self.reliability_dashboard), self.reliability_dashboard.load_path(path))
        )

    def setup_price_chart(self):
        self.price_chart = pg.PlotWidget(title="Best Bid & Ask Price (USD)")
        self.price_chart.setLabel("left", "Price", "USD")
        self.price_chart.setLabel("bottom", "Time Elapsed", "s")
        self.price_chart.showGrid(x=True, y=True, alpha=0.3)
        self.price_chart.setBackground("white")

        self.bid_line = self.price_chart.plot(
            pen=pg.mkPen(color=COLOUR_GREEN, width=LINE_WIDTH), name="Best Bid"
        )
        self.ask_line = self.price_chart.plot(
            pen=pg.mkPen(color=COLOUR_RED, width=LINE_WIDTH), name="Best Ask"
        )
        self.price_chart.addLegend()

    def setup_depth_chart(self):
        self.depth_chart = pg.PlotWidget(title="Order Book Depth by Price Level")
        self.depth_chart.setLabel("left", "Price Level", "USD")
        self.depth_chart.setLabel("bottom", "Resting Quantity (Contracts)")
        self.depth_chart.showGrid(x=True, y=True, alpha=0.3)
        self.depth_chart.setBackground("white")

    def setup_volume_chart(self):
        self.volume_chart = pg.PlotWidget(title="Matched Trade Volume (1s Rolling Window)")
        self.volume_chart.setLabel("left", "Contracts")
        self.volume_chart.setLabel("bottom", "Time Elapsed", "s")
        self.volume_chart.showGrid(x=True, y=True, alpha=0.3)
        self.volume_chart.setBackground("white")

        self.volume_bars = pg.BarGraphItem(
            x=[0], y=[0], width=0.5, height=[0],
            brush=pg.mkBrush(color=COLOUR_BLUE)
        )
        self.volume_chart.addItem(self.volume_bars)

    def setup_controls(self):
        self.controls_widget = QGroupBox("Order Pressure Controls & Live Book Telemetry")
        layout = QVBoxLayout(self.controls_widget)

        self.simulator_scope_label = QLabel("Mode: In-Process AAPL Order Book (60 FPS)")
        self.simulator_scope_label.setStyleSheet(
            "padding: 6px 10px; background: #eff6ff; border: 1px solid #93c5fd; "
            "border-radius: 4px; font-weight: 600; color: #1e3a8a;"
        )
        layout.addWidget(self.simulator_scope_label)

        button_layout = QHBoxLayout()
        self.buy_button = QPushButton("▲ Add Buy Pressure")
        self.buy_button.clicked.connect(self.on_buy_pressure)
        self.buy_button.setMinimumHeight(48)
        self.buy_button.setStyleSheet(
            "QPushButton { background: #16a34a; color: white; font-weight: bold; font-size: 14px; border-radius: 6px; }"
            "QPushButton:hover { background: #15803d; }"
        )

        self.sell_button = QPushButton("▼ Add Sell Pressure")
        self.sell_button.clicked.connect(self.on_sell_pressure)
        self.sell_button.setMinimumHeight(48)
        self.sell_button.setStyleSheet(
            "QPushButton { background: #dc2626; color: white; font-weight: bold; font-size: 14px; border-radius: 6px; }"
            "QPushButton:hover { background: #b91c1c; }"
        )

        button_layout.addWidget(self.buy_button)
        button_layout.addWidget(self.sell_button)
        layout.addLayout(button_layout)

        # Clean structured metrics table instead of loose labels
        self.stats_table = QTableWidget(4, 4)
        self.stats_table.setHorizontalHeaderLabels(["Metric", "Value", "Metric", "Value"])
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.stats_table.setAlternatingRowColors(True)
        self.stats_table.setMinimumHeight(145)

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
                self.stats_table.setItem(r_idx, c_idx, item)

        layout.addWidget(self.stats_table)

        # Keep hidden/compatible label attributes in case external callers inspect them
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

        self.time_data.append(current_time)
        self.bid_price_data.append(best_bid / 100.0)
        self.ask_price_data.append(best_ask / 100.0)

        recent_volume = self.market_simulator.get_recent_volume(1.0)
        self.volume_data.append(recent_volume)

        if len(self.time_data) > 1:
            self.bid_line.setData(list(self.time_data), list(self.bid_price_data))
            self.ask_line.setData(list(self.time_data), list(self.ask_price_data))

            scaling_lookback = SCALING_LOOKBACK_SECONDS * FRAME_RATE
            if len(self.bid_price_data) > scaling_lookback:
                recent_bids = list(self.bid_price_data)[-scaling_lookback:]
                recent_asks = list(self.ask_price_data)[-scaling_lookback:]
                min_price = min(min(recent_bids), min(recent_asks)) - 0.1
                max_price = max(max(recent_bids), max(recent_asks)) + 0.1
                self.price_chart.setYRange(min_price, max_price)

            self.price_chart.setXRange(max(0, current_time - 8), current_time)

        if len(self.volume_data) > 1:
            self.volume_bars.setOpts(
                x=list(self.time_data),
                y=[0] * len(self.time_data),
                height=list(self.volume_data),
                width=0.05
            )
            self.volume_chart.setXRange(max(0, current_time - 8), current_time)
            max_volume = max(self.volume_data) if self.volume_data else 100
            self.volume_chart.setYRange(0, max_volume * 1.1, padding=0)

        self.update_depth_chart()
        self.update_statistics()

        if len(self.bid_price_data) > 1:
            price_y_range = self.price_chart.getViewBox().viewRange()[1]
            self.depth_chart.setYRange(price_y_range[0], price_y_range[1])

    def update_depth_chart(self):
        bid_quantities = self.order_book.get_bid_quantities()
        ask_quantities = self.order_book.get_ask_quantities()

        bid_prices = sorted(bid_quantities.keys(), reverse=True)[:MAX_RENDERED_DEPTHS]
        ask_prices = sorted(ask_quantities.keys())[:MAX_RENDERED_DEPTHS]
        self.depth_chart.clear()

        if bid_prices:
            bid_y = [price / 100.0 for price in bid_prices]
            bid_widths = [bid_quantities[price] for price in bid_prices]
            bid_bars = pg.BarGraphItem(
                x=[0] * len(bid_y), y=bid_y, width=bid_widths, height=0.02,
                brush=pg.mkBrush(color=COLOUR_GREEN)
            )
            self.depth_chart.addItem(bid_bars)

        if ask_prices:
            ask_y = [price / 100.0 for price in ask_prices]
            ask_widths = [ask_quantities[price] for price in ask_prices]
            ask_bars = pg.BarGraphItem(
                x=[0] * len(ask_y), y=ask_y, width=ask_widths, height=0.02,
                brush=pg.mkBrush(color=COLOUR_RED)
            )
            self.depth_chart.addItem(ask_bars)

        all_quantities = [bid_quantities[p] for p in bid_prices] + [ask_quantities[p] for p in ask_prices]
        if all_quantities:
            max_quantity = max(all_quantities)
            self.depth_chart.setXRange(0, max_quantity * 1.1, padding=0)

    def update_statistics(self):
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

        bid_str = f"${best_bid / 100:.2f}" if best_bid is not None else "-"
        ask_str = f"${best_ask / 100:.2f}" if best_ask is not None else "-"
        spread_str = f"${spread / 100:.2f}" if spread is not None else "-"

        self.order_count_label.setText(f"Order Count: {order_count}")
        self.spread_label.setText(f"Spread: {spread_str}")
        self.best_bid_label.setText(f"Best Bid: {bid_str}")
        self.best_ask_label.setText(f"Best Ask: {ask_str}")
        self.bid_depth_label.setText(f"Best Bid Depth: {bid_depth}")
        self.ask_depth_label.setText(f"Best Ask Depth: {ask_depth}")

        bid_item = QTableWidgetItem(bid_str)
        bid_item.setForeground(QColor("#15803d"))
        ask_item = QTableWidgetItem(ask_str)
        ask_item.setForeground(QColor("#b91c1c"))
        self.stats_table.setItem(0, 1, bid_item)
        self.stats_table.setItem(0, 3, ask_item)
        self.stats_table.setItem(1, 1, QTableWidgetItem(f"{bid_depth:,}"))
        self.stats_table.setItem(1, 3, QTableWidgetItem(f"{ask_depth:,}"))
        self.stats_table.setItem(2, 1, QTableWidgetItem(spread_str))
        self.stats_table.setItem(2, 3, QTableWidgetItem(f"{order_count:,}"))
        self.stats_table.setItem(3, 1, QTableWidgetItem(f"{matched:,}"))
        self.stats_table.setItem(3, 3, QTableWidgetItem("inprocess (AAPL)"))

        self.simulator_scope_label.setText(
            f"In-Process AAPL Book (60 FPS)  |  Resting: {order_count:,}  |  Matched: {matched:,}  |  Spread: {spread_str}"
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
