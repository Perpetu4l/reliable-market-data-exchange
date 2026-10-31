"""Visual network and subscriber control room for the C++ exchange."""

from __future__ import annotations

import csv
import itertools
import json
import socket
import subprocess
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque

import pyqtgraph as pg
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .exchange_controller import ExchangeController, REPOSITORY_ROOT
from .network_protocol import BUY, SELL, ORDER_ACK_TYPE, ORDER_REJECT_TYPE, submit_limit_order


def discover_lan_ipv4_addresses() -> list[tuple[str, str]]:
    """Return (interface_or_label, ipv4_address) pairs on macOS and Linux."""
    found: list[tuple[str, str]] = [("loopback (lo)", "127.0.0.1")]
    seen = {"127.0.0.1"}
    try:
        out = subprocess.check_output(["ifconfig"], text=True, stderr=subprocess.DEVNULL, timeout=2)
        current_iface = "iface"
        for line in out.splitlines():
            if line and not line[0].isspace() and ":" in line:
                current_iface = line.split(":", 1)[0].strip()
            elif "inet " in line:
                parts = line.strip().split()
                if len(parts) >= 2:
                    ip = parts[1]
                    if ip not in seen and "." in ip:
                        seen.add(ip)
                        found.append((current_iface, ip))
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip not in seen:
                seen.add(ip)
                found.append(("primary-lan", ip))
    except Exception:
        pass
    return found


class ManualOrderThread(QThread):
    completed = pyqtSignal(str, int, float)  # message, executions_count, rtt_ms

    def __init__(self, host: str, port: int, client_id: int, order_id: int, symbol: str,
                 side: int, price_cents: int, quantity: int):
        super().__init__()
        self.parameters = (host, port, client_id, order_id, symbol, side, price_cents, quantity)

    def run(self) -> None:
        try:
            response, rtt_ms, executions = submit_limit_order(*self.parameters)
            exec_suffix = ""
            if executions:
                fills = ", ".join(
                    f"{e['execution_quantity']} @ ${e['execution_price_cents']/100.0:.2f} (leaves {e['leaves_quantity']})"
                    for e in executions
                )
                exec_suffix = f" | EXECUTION_REPORT: {fills}"
            if response.message_type == ORDER_ACK_TYPE:
                self.completed.emit(f"Accepted: ORDER_ACK, client RTT {rtt_ms:.3f} ms{exec_suffix}", len(executions), rtt_ms)
            elif response.message_type == ORDER_REJECT_TYPE:
                self.completed.emit(f"Rejected by exchange: ORDER_REJECT, client RTT {rtt_ms:.3f} ms", 0, rtt_ms)
            else:
                self.completed.emit(f"Unexpected server response {response.message_type}", 0, rtt_ms)
        except Exception as error:
            self.completed.emit(f"Manual request failed: {error}", 0, 0.0)


class ExchangeControlPanel(QWidget):
    """Visual control room for local simulation, distributed network, manual orders, and saved results."""

    open_in_dashboard_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.controller = ExchangeController(self)
        self.manual_ids = itertools.count(1)
        self.manual_thread: ManualOrderThread | None = None
        self.seq_before_manual_order: int = 0
        self.sub_row_by_id: dict[str, int] = {}
        self.seq_history: Deque[int] = deque(maxlen=60)
        self.md_sent_history: Deque[int] = deque(maxlen=60)
        self.client_ack_bars: dict[int, tuple[float, float, float]] = {}
        self.manual_rtt_history: list[tuple[str, float]] = []
        self._setup_ui()

        self.controller.log_message.connect(self._append_log)
        self.controller.server_status.connect(self._set_server_status)
        self.controller.client_status.connect(self._set_client_status)
        self.controller.result_available.connect(self._show_result)
        self.controller.results_saved.connect(lambda _path: self._refresh_saved_results())
        self.controller.subscriber_status_changed.connect(self._on_subscriber_status)
        self.controller.subscriber_stats_updated.connect(self._on_subscriber_stats)
        self.controller.subscriber_result_available.connect(self._on_subscriber_result)
        self.controller.exchange_stats_updated.connect(self._on_exchange_stats)
        self.controller.remote_peer_event.connect(self._on_remote_peer_event)

    @staticmethod
    def _spinbox(minimum: int, maximum: int, value: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        widget.setMinimumHeight(32)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return widget

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    @staticmethod
    def _make_chart(title: str, y_label: str, x_label: str, min_height: int = 230) -> pg.PlotWidget:
        ch = pg.PlotWidget(title=title)
        ch.setMinimumHeight(min_height)
        ch.setBackground("w")
        ch.setLabel("left", y_label)
        ch.setLabel("bottom", x_label)
        ch.showGrid(x=True, y=True, alpha=0.25)
        return ch

    @staticmethod
    def _style_table(table: QTableWidget, min_height: int = 190, row_height: int = 32) -> None:
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setDefaultSectionSize(row_height)
        table.setAlternatingRowColors(True)
        table.setMinimumHeight(min_height)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._scroll(self._local_tab()), "1. Local Simulation (Live Graphs & Tables)")
        self.tabs.addTab(self._scroll(self._manual_tab()), "2. Manual Order Testing (RTT Graph & Table)")
        self.tabs.addTab(self._scroll(self._distributed_tab()), "3. Distributed LAN Setup")
        self.tabs.addTab(self._scroll(self._results_tab()), "4. Saved Results Explorer (Table & Graph)")
        layout.addWidget(self.tabs, 5)

        self.event_log = QPlainTextEdit()
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(400)
        self.event_log.setMinimumHeight(95)
        self.event_log.setMaximumHeight(125)
        self.event_log.setPlaceholderText("Live exchange, subscriber, and client process events stream here.")
        layout.addWidget(self.event_log)

    def _local_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # Top Row: Spacious Server Config + Lifecycle Action Buttons
        top_row = QHBoxLayout()
        top_row.setSpacing(14)

        config = QGroupBox("Exchange & Recovery Server Parameters")
        cfg_grid = QGridLayout(config)
        cfg_grid.setContentsMargins(14, 16, 14, 14)
        cfg_grid.setHorizontalSpacing(12)
        cfg_grid.setVerticalSpacing(10)
        cfg_grid.setColumnStretch(1, 1)
        cfg_grid.setColumnStretch(3, 1)
        cfg_grid.setColumnStretch(5, 1)

        self.port = self._spinbox(1024, 65535, 8080)
        self.md_interface = QLineEdit("127.0.0.1")
        self.md_interface.setMinimumHeight(32)
        self.dual_feed_check = QCheckBox("Dual Feed (A+B)")
        self.dual_feed_check.setChecked(True)
        self.recovery_port = self._spinbox(1024, 65535, 9090)
        self.history_spin = self._spinbox(0, 1_000_000, 65536)
        self.snap_interval_spin = self._spinbox(0, 1_000_000, 100)
        self.snap_retention_spin = self._spinbox(1, 1000, 8)
        self.snap_on_demand_check = QCheckBox("On-Demand Snapshot")
        self.snap_on_demand_check.setChecked(True)
        self.recovery_policy_combo = QComboBox()
        self.recovery_policy_combo.addItems(["fcfs", "round-robin"])
        self.recovery_policy_combo.setMinimumHeight(32)
        self.recovery_workers_spin = self._spinbox(1, 16, 1)
        self.service_delay_req_spin = self._spinbox(0, 1_000_000, 0)
        self.service_delay_req_spin.setSuffix(" µs")
        self.service_delay_event_spin = self._spinbox(0, 100_000, 0)
        self.service_delay_event_spin.setSuffix(" µs")
        self.tamper_combo = QComboBox()
        self.tamper_combo.addItems(["none", "book_value", "sequence", "previous_hash", "hash"])
        self.tamper_combo.setMinimumHeight(32)
        self.heartbeat_spin = self._spinbox(50, 10_000, 500)
        self.heartbeat_spin.setSuffix(" ms")

        cfg_grid.addWidget(QLabel("<b>OE Port:</b>"), 0, 0)
        cfg_grid.addWidget(self.port, 0, 1)
        cfg_grid.addWidget(QLabel("<b>Rec Port:</b>"), 0, 2)
        cfg_grid.addWidget(self.recovery_port, 0, 3)
        cfg_grid.addWidget(QLabel("<b>Interface:</b>"), 0, 4)
        cfg_grid.addWidget(self.md_interface, 0, 5)

        cfg_grid.addWidget(QLabel("<b>History:</b>"), 1, 0)
        cfg_grid.addWidget(self.history_spin, 1, 1)
        cfg_grid.addWidget(QLabel("<b>Snap Intv:</b>"), 1, 2)
        cfg_grid.addWidget(self.snap_interval_spin, 1, 3)
        cfg_grid.addWidget(QLabel("<b>Snap Ret:</b>"), 1, 4)
        cfg_grid.addWidget(self.snap_retention_spin, 1, 5)

        cfg_grid.addWidget(QLabel("<b>Policy:</b>"), 2, 0)
        cfg_grid.addWidget(self.recovery_policy_combo, 2, 1)
        cfg_grid.addWidget(QLabel("<b>Workers:</b>"), 2, 2)
        cfg_grid.addWidget(self.recovery_workers_spin, 2, 3)
        cfg_grid.addWidget(QLabel("<b>Delay/Req:</b>"), 2, 4)
        cfg_grid.addWidget(self.service_delay_req_spin, 2, 5)

        cfg_grid.addWidget(QLabel("<b>Delay/Ev:</b>"), 3, 0)
        cfg_grid.addWidget(self.service_delay_event_spin, 3, 1)
        cfg_grid.addWidget(QLabel("<b>Tamper:</b>"), 3, 2)
        cfg_grid.addWidget(self.tamper_combo, 3, 3)
        cfg_grid.addWidget(self.dual_feed_check, 3, 4)
        cfg_grid.addWidget(self.snap_on_demand_check, 3, 5)

        actions = QGroupBox("Workload Parameters & Process Controls")
        act_layout = QVBoxLayout(actions)
        act_layout.setContentsMargins(14, 16, 14, 14)
        act_layout.setSpacing(10)

        w_grid = QGridLayout()
        w_grid.setHorizontalSpacing(12)
        w_grid.setVerticalSpacing(10)
        w_grid.setColumnStretch(1, 1)
        w_grid.setColumnStretch(3, 1)

        self.client_count = self._spinbox(1, 32, 2)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0.1, 50_000.0)
        self.rate.setDecimals(1)
        self.rate.setValue(400.0)
        self.rate.setMinimumHeight(32)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.5, 3600.0)
        self.duration.setDecimals(1)
        self.duration.setValue(4.0)
        self.duration.setMinimumHeight(32)
        self.symbol = QComboBox()
        self.symbol.addItems(["AAPL", "GOOGL", "AMZN", "NFLX", "META"])
        self.symbol.setMinimumHeight(32)
        self.seed = self._spinbox(0, 2_147_483_647, 11)

        w_grid.addWidget(QLabel("<b>Clients:</b>"), 0, 0)
        w_grid.addWidget(self.client_count, 0, 1)
        w_grid.addWidget(QLabel("<b>Orders/s:</b>"), 0, 2)
        w_grid.addWidget(self.rate, 0, 3)
        w_grid.addWidget(QLabel("<b>Duration (s):</b>"), 1, 0)
        w_grid.addWidget(self.duration, 1, 1)
        w_grid.addWidget(QLabel("<b>Symbol:</b>"), 1, 2)
        w_grid.addWidget(self.symbol, 1, 3)
        act_layout.addLayout(w_grid)

        btn_grid = QGridLayout()
        btn_grid.setSpacing(8)
        self.start_server_button = QPushButton("1. Start Exchange")
        self.start_server_button.setMinimumHeight(38)
        self.start_server_button.setStyleSheet(
            "QPushButton { background: #16a34a; color: white; font-weight: bold; padding: 6px 12px; border-radius: 5px; }"
        )
        self.start_subscribers_button = QPushButton("2. Start Subscribers")
        self.start_subscribers_button.setMinimumHeight(38)
        self.start_subscribers_button.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; font-weight: bold; padding: 6px 12px; border-radius: 5px; }"
        )
        self.start_workload_button = QPushButton("3. Start Workload")
        self.start_workload_button.setMinimumHeight(38)
        self.start_workload_button.setStyleSheet(
            "QPushButton { background: #7c3aed; color: white; font-weight: bold; padding: 6px 12px; border-radius: 5px; }"
        )
        self.stop_subscribers_button = QPushButton("Reconcile & Stop Subs")
        self.stop_subscribers_button.setMinimumHeight(36)
        self.stop_subscribers_style = "QPushButton { background: #d97706; color: white; font-weight: bold; padding: 6px 12px; border-radius: 5px; }"
        self.stop_subscribers_button.setStyleSheet(self.stop_subscribers_style)
        self.stop_button = QPushButton("Stop All")
        self.stop_button.setMinimumHeight(36)
        self.stop_button.setStyleSheet(
            "QPushButton { background: #dc2626; color: white; font-weight: bold; padding: 6px 12px; border-radius: 5px; }"
        )

        self.open_latest_dash_button = QPushButton("📊 Open Latest in 6-Graph Dashboard")
        self.open_latest_dash_button.setMinimumHeight(36)
        self.open_latest_dash_button.setStyleSheet(
            "QPushButton { background: #0284c7; color: white; font-weight: bold; padding: 6px 12px; border-radius: 5px; }"
        )
        self.open_latest_dash_button.clicked.connect(self._open_latest_live_in_dashboard)

        self.start_server_button.clicked.connect(self._start_server)
        self.start_subscribers_button.clicked.connect(self._start_subscribers)
        self.stop_subscribers_button.clicked.connect(self.controller.stop_subscribers)
        self.start_workload_button.clicked.connect(self._start_workload)
        self.stop_button.clicked.connect(self.controller.stop_server)

        btn_grid.addWidget(self.start_server_button, 0, 0)
        btn_grid.addWidget(self.start_subscribers_button, 0, 1)
        btn_grid.addWidget(self.start_workload_button, 0, 2)
        btn_grid.addWidget(self.stop_subscribers_button, 1, 0)
        btn_grid.addWidget(self.stop_button, 1, 1)
        btn_grid.addWidget(self.open_latest_dash_button, 1, 2)
        act_layout.addLayout(btn_grid)

        status_strip = QHBoxLayout()
        self.server_status = QLabel("Stopped")
        self.server_status.setMinimumHeight(30)
        self.server_status.setStyleSheet(
            "font-weight: 700; background: #334155; color: #f8fafc; padding: 4px 10px; border-radius: 5px;"
        )
        self.exchange_live_stats_label = QLabel("latest_seq=0 | queue_depth=0 | subscribers=0")
        self.exchange_live_stats_label.setMinimumHeight(30)
        self.exchange_live_stats_label.setStyleSheet(
            "font-weight: 600; background: #1e293b; color: #f8fafc; border: 1px solid #475569; padding: 4px 10px; border-radius: 5px;"
        )
        status_strip.addWidget(QLabel("<b>Exchange:</b>"))
        status_strip.addWidget(self.server_status)
        status_strip.addWidget(self.exchange_live_stats_label, 1)
        act_layout.addLayout(status_strip)

        top_row.addWidget(config, 3)
        top_row.addWidget(actions, 2)
        layout.addLayout(top_row)

        # 2 Live Visual Charts right in Exchange Control!
        charts_box = QGroupBox("Live Visual Telemetry Charts (Subscriber Delivery & Workload ACK Latency)")
        charts_layout = QHBoxLayout(charts_box)
        charts_layout.setContentsMargins(12, 14, 12, 12)
        charts_layout.setSpacing(12)
        self.live_sub_chart = self._make_chart(
            "Live Subscriber Delivered vs Recovered Events", "Events", "Subscriber", 230
        )
        self.live_exchange_chart = self._make_chart(
            "Exchange Sequence Progress / Workload ACK RTT (µs)", "Sequence / RTT (µs)", "Tick / Client", 230
        )
        charts_layout.addWidget(self.live_sub_chart, 1)
        charts_layout.addWidget(self.live_exchange_chart, 1)
        layout.addWidget(charts_box)
        self._render_initial_local_charts()

        # Subscribers Configuration Table (spacious)
        sub_cfg_box = QGroupBox("Market-Data Subscribers Configuration Table (1–32 Processes)")
        sub_cfg_layout = QVBoxLayout(sub_cfg_box)
        sub_cfg_layout.setContentsMargins(12, 14, 12, 12)
        sub_cfg_layout.setSpacing(10)
        top_sub_row = QHBoxLayout()
        top_sub_row.setSpacing(12)
        self.sub_count_spin = self._spinbox(1, 32, 5)
        self.sub_count_spin.valueChanged.connect(self._sync_subscriber_cfg_rows)
        self.sub_grace_spin = self._spinbox(0, 1000, 2)
        self.sub_grace_spin.setSuffix(" ms")
        self.sub_retries_spin = self._spinbox(0, 20, 5)
        self.sub_idle_spin = self._spinbox(0, 30_000, 600)
        self.sub_idle_spin.setSuffix(" ms")

        top_sub_row.addWidget(QLabel("<b>Subscriber Count:</b>"))
        top_sub_row.addWidget(self.sub_count_spin)
        top_sub_row.addWidget(QLabel("<b>Gap Grace:</b>"))
        top_sub_row.addWidget(self.sub_grace_spin)
        top_sub_row.addWidget(QLabel("<b>Retries:</b>"))
        top_sub_row.addWidget(self.sub_retries_spin)
        top_sub_row.addWidget(QLabel("<b>Stop-When-Idle:</b>"))
        top_sub_row.addWidget(self.sub_idle_spin)
        top_sub_row.addStretch()
        sub_cfg_layout.addLayout(top_sub_row)

        self.sub_cfg_table = QTableWidget(0, 9)
        self.sub_cfg_table.setHorizontalHeaderLabels([
            "ID", "Feeds (a/ab)", "Loss Model", "Drop A", "Drop B", "Corr Loss", "Seed", "Concurrency", "Role",
        ])
        self._style_table(self.sub_cfg_table, min_height=205, row_height=34)
        sub_cfg_layout.addWidget(self.sub_cfg_table)
        self._sync_subscriber_cfg_rows(self.sub_count_spin.value())
        layout.addWidget(sub_cfg_box)

        # Bottom Row: Live Subscriber Telemetry Table + Workload Clients Table side by side
        bottom_tables = QHBoxLayout()
        bottom_tables.setSpacing(12)

        live_sub_box = QGroupBox("Live Subscriber Status & Reconciliation Table")
        live_sub_layout = QVBoxLayout(live_sub_box)
        live_sub_layout.setContentsMargins(12, 14, 12, 12)
        self.live_sub_table = QTableWidget(0, 12)
        self.live_sub_table.setHorizontalHeaderLabels([
            "Subscriber", "State", "Feed A/B", "Last Seq", "Delivered",
            "Duplicates", "Gap Ranges", "Recoveries", "Snapshots",
            "Lost", "Book Match", "TCP Latency p50/p99 (µs)",
        ])
        self.live_sub_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._style_table(self.live_sub_table, min_height=210, row_height=34)
        live_sub_layout.addWidget(self.live_sub_table)

        clients_box = QGroupBox("Order-Entry Workload Clients Table")
        client_layout = QVBoxLayout(clients_box)
        client_layout.setContentsMargins(12, 14, 12, 12)
        self.client_status_label = QLabel("Idle")
        self.client_status_label.setMinimumHeight(26)
        self.result_label = QLabel("Start exchange + workload to measure client ACK RTT.")
        self.result_label.setWordWrap(True)
        self.result_label.setMinimumHeight(36)
        client_layout.addWidget(self.client_status_label)
        client_layout.addWidget(self.result_label)
        self.client_table = QTableWidget(0, 2)
        self.client_table.setHorizontalHeaderLabels(["Client ID", "Observed Status & ACK Latency"])
        self.client_table.verticalHeader().setVisible(False)
        self.client_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._style_table(self.client_table, min_height=165, row_height=34)
        client_layout.addWidget(self.client_table)

        bottom_tables.addWidget(live_sub_box, 3)
        bottom_tables.addWidget(clients_box, 2)
        layout.addLayout(bottom_tables)

        return page

    def _render_initial_local_charts(self) -> None:
        self.live_sub_chart.clear()
        self.live_sub_chart.addItem(
            pg.TextItem("Start subscribers + workload to plot live per-subscriber delivery & recovery bars", color=(110, 110, 110))
        )
        self.live_exchange_chart.clear()
        self.live_exchange_chart.addItem(
            pg.TextItem("Start exchange + workload to plot live sequence progress & ACK RTT", color=(110, 110, 110))
        )

    def _refresh_live_sub_chart(self) -> None:
        rows = self.live_sub_table.rowCount()
        if rows == 0:
            return
        labels: list[str] = []
        delivered_vals: list[float] = []
        rec_vals: list[float] = []
        for r in range(rows):
            sid_it = self.live_sub_table.item(r, 0)
            del_it = self.live_sub_table.item(r, 4)
            rec_it = self.live_sub_table.item(r, 7)
            labels.append(sid_it.text() if sid_it else f"S{r+1}")
            try:
                delivered_vals.append(float(del_it.text()) if del_it and del_it.text().isdigit() else 0.0)
            except ValueError:
                delivered_vals.append(0.0)
            try:
                rec_vals.append(float(rec_it.text()) if rec_it and rec_it.text().isdigit() else 0.0)
            except ValueError:
                rec_vals.append(0.0)

        self.live_sub_chart.clear()
        xs = list(range(len(labels)))
        self.live_sub_chart.addItem(
            pg.BarGraphItem(x=[x - 0.16 for x in xs], height=delivered_vals, width=0.32, brush=pg.mkBrush("#16a34a"))
        )
        self.live_sub_chart.addItem(
            pg.BarGraphItem(x=[x + 0.16 for x in xs], height=rec_vals, width=0.32, brush=pg.mkBrush("#2563eb"))
        )
        self.live_sub_chart.getAxis("bottom").setTicks([list(zip(xs, labels))])
        ymax = max(max(delivered_vals, default=1.0), max(rec_vals, default=1.0))
        self.live_sub_chart.setYRange(0, max(5.0, ymax * 1.2), padding=0)

    def _refresh_live_exchange_chart(self) -> None:
        self.live_exchange_chart.clear()
        if self.client_ack_bars:
            labels: list[str] = []
            vals: list[float] = []
            colors: list[str] = []
            for cid, (p50, p95, p99) in sorted(self.client_ack_bars.items()):
                labels.extend([f"C{cid}\np50", f"C{cid}\np95", f"C{cid}\np99"])
                vals.extend([p50, p95, p99])
                colors.extend(["#16a34a", "#2563eb", "#7c3aed"])
            xs = list(range(len(vals)))
            for x, v, c in zip(xs, vals, colors):
                self.live_exchange_chart.addItem(pg.BarGraphItem(x=[x], height=[v], width=0.55, brush=pg.mkBrush(c)))
            self.live_exchange_chart.getAxis("bottom").setTicks([list(zip(xs, labels))])
            self.live_exchange_chart.setTitle("Workload Client Order ACK RTT Percentiles (µs)")
            self.live_exchange_chart.setYRange(0, max(10.0, max(vals, default=10.0) * 1.2), padding=0)
        elif self.seq_history:
            xs = list(range(len(self.seq_history)))
            self.live_exchange_chart.plot(xs, list(self.seq_history), pen=pg.mkPen("#2563eb", width=3), symbol="o")
            self.live_exchange_chart.setTitle("Live Exchange Published Sequence Progression (1 Hz)")

    def _sync_subscriber_cfg_rows(self, count: int) -> None:
        old_rows = self.sub_cfg_table.rowCount()
        self.sub_cfg_table.setRowCount(count)
        for idx in range(old_rows, count):
            is_noisy = idx == 0 and count > 1
            defaults = [
                f"sub-{idx + 1}",
                "a" if is_noisy else "ab",
                "independent",
                "0.30" if is_noisy else "0.01",
                "0.00" if is_noisy else "0.01",
                "0.00",
                str(42 + idx),
                "4" if is_noisy else "1",
                "noisy" if is_noisy else "quiet",
            ]
            for col, val in enumerate(defaults):
                self.sub_cfg_table.setItem(idx, col, QTableWidgetItem(val))

    def _read_subscriber_cfg_table(self) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for r in range(self.sub_cfg_table.rowCount()):
            def cell(c: int, default: str) -> str:
                it = self.sub_cfg_table.item(r, c)
                return it.text().strip() if it and it.text().strip() else default

            specs.append({
                "id": cell(0, f"sub-{r + 1}"),
                "feeds": cell(1, "ab"),
                "loss_model": cell(2, "none"),
                "drop_a": float(cell(3, "0.0")),
                "drop_b": float(cell(4, "0.0")),
                "corr_loss": float(cell(5, "0.0")),
                "seed": int(cell(6, str(42 + r))),
                "concurrency": int(cell(7, "1")),
                "role": cell(8, "default"),
            })
        return specs

    def _distributed_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)
        layout.setContentsMargins(14, 14, 14, 14)

        net_box = QGroupBox("Two-Laptop / LAN Distributed Configuration (Full-Width Controls & Commands)")
        grid = QGridLayout(net_box)
        grid.setContentsMargins(18, 20, 18, 18)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self.lan_iface_combo = QComboBox()
        self.lan_iface_combo.setMinimumHeight(36)
        self.lan_iface_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        for label, ip in discover_lan_ipv4_addresses():
            self.lan_iface_combo.addItem(f"{label}: {ip}", ip)
        self.lan_iface_combo.currentIndexChanged.connect(self._on_lan_iface_selected)

        self.remote_host = QLineEdit(self.lan_iface_combo.currentData() or "127.0.0.1")
        self.remote_host.setMinimumHeight(36)
        self.remote_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.remote_sub_iface = QLineEdit("<SUBSCRIBER_OWN_LAN_IP>")
        self.remote_sub_iface.setMinimumHeight(36)
        self.remote_sub_iface.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.remote_port = self._spinbox(1024, 65535, 8080)
        self.remote_port.setMinimumHeight(36)
        self.remote_recovery_port = self._spinbox(1024, 65535, 9090)
        self.remote_recovery_port.setMinimumHeight(36)

        # Row 0: Local Interfaces & Exchange LAN IP
        grid.addWidget(QLabel("<b>Detected Local IPv4 Interfaces:</b>"), 0, 0)
        grid.addWidget(self.lan_iface_combo, 0, 1)
        grid.addWidget(QLabel("<b>Exchange LAN IPv4 Address:</b>"), 0, 2)
        grid.addWidget(self.remote_host, 0, 3)

        # Row 1: Remote Subscriber IP & Ports
        grid.addWidget(QLabel("<b>Remote Subscriber's Own LAN IP:</b>"), 1, 0)
        grid.addWidget(self.remote_sub_iface, 1, 1)
        ports_row = QHBoxLayout()
        ports_row.setSpacing(10)
        ports_row.addWidget(QLabel("<b>OE Port:</b>"))
        ports_row.addWidget(self.remote_port, 1)
        ports_row.addWidget(QLabel("<b>Recovery Port:</b>"))
        ports_row.addWidget(self.remote_recovery_port, 1)
        grid.addWidget(QLabel("<b>TCP Ports:</b>"), 1, 2)
        grid.addLayout(ports_row, 1, 3)

        # Row 2: Action Buttons spanning full width
        self.start_lan_exchange_btn = QPushButton("▶ Start Exchange Here on LAN Interface")
        self.start_lan_exchange_btn.setMinimumHeight(42)
        self.start_lan_exchange_btn.setStyleSheet(
            "QPushButton { background: #16a34a; color: white; font-weight: bold; font-size: 13px; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:hover { background: #15803d; }"
        )
        self.start_lan_exchange_btn.clicked.connect(self._start_lan_exchange)

        self.test_conn_btn = QPushButton("🔌 Test TCP Reachability (Order-Entry + Recovery)")
        self.test_conn_btn.setMinimumHeight(42)
        self.test_conn_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; font-weight: bold; font-size: 13px; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:hover { background: #1d4ed8; }"
        )
        self.test_conn_btn.clicked.connect(self._test_remote_connection)

        action_btns_row = QHBoxLayout()
        action_btns_row.setSpacing(14)
        action_btns_row.addWidget(self.start_lan_exchange_btn, 1)
        action_btns_row.addWidget(self.test_conn_btn, 1)
        grid.addLayout(action_btns_row, 2, 0, 1, 4)

        # Row 3: Connection check result — spacious full-width banner (never clipped!)
        self.test_conn_label = QLabel("Not tested yet — click 'Test TCP Reachability' above")
        self.test_conn_label.setWordWrap(True)
        self.test_conn_label.setMinimumHeight(54)
        self.test_conn_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.test_conn_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.test_conn_label.setStyleSheet(
            "padding: 10px 14px; border: 1px solid #64748b; border-radius: 6px; font-weight: 600; font-size: 13px;"
        )
        grid.addWidget(QLabel("<b>Connection Check Result:</b>"), 3, 0)
        grid.addWidget(self.test_conn_label, 3, 1, 1, 3)

        # Rows 4, 5, 6: Full-width spacious command boxes
        cmd_style = (
            "QPlainTextEdit { font-family: Menlo, Consolas, monospace; font-size: 13px; "
            "padding: 8px 12px; border: 1px solid #475569; border-radius: 6px; }"
        )
        self.cmd_exchange = QPlainTextEdit()
        self.cmd_exchange.setReadOnly(True)
        self.cmd_exchange.setMinimumHeight(76)
        self.cmd_exchange.setMaximumHeight(96)
        self.cmd_exchange.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.cmd_exchange.setStyleSheet(cmd_style)

        self.cmd_workload = QPlainTextEdit()
        self.cmd_workload.setReadOnly(True)
        self.cmd_workload.setMinimumHeight(76)
        self.cmd_workload.setMaximumHeight(96)
        self.cmd_workload.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.cmd_workload.setStyleSheet(cmd_style)

        self.cmd_subscriber = QPlainTextEdit()
        self.cmd_subscriber.setReadOnly(True)
        self.cmd_subscriber.setMinimumHeight(82)
        self.cmd_subscriber.setMaximumHeight(104)
        self.cmd_subscriber.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.cmd_subscriber.setStyleSheet(cmd_style)

        grid.addWidget(QLabel("<b>1. Exchange Command:</b>"), 4, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.cmd_exchange, 4, 1, 1, 3)

        grid.addWidget(QLabel("<b>2. Remote workload_client Command:</b>"), 5, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.cmd_workload, 5, 1, 1, 3)

        grid.addWidget(QLabel("<b>3. Remote reliable_subscriber Command:</b>"), 6, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.cmd_subscriber, 6, 1, 1, 3)

        # Row 7: Full-width copy buttons with generous height & padding
        copy_row = QHBoxLayout()
        copy_row.setSpacing(12)
        btn_update = QPushButton("🔄 Update Commands")
        btn_update.setMinimumHeight(40)
        btn_update.setStyleSheet("padding: 8px 14px; font-weight: 600;")
        btn_update.clicked.connect(self._update_remote_command)

        btn_copy_ex = QPushButton("📋 Copy Exchange Command")
        btn_copy_ex.setMinimumHeight(40)
        btn_copy_ex.setStyleSheet("padding: 8px 14px; font-weight: 600;")
        btn_copy_ex.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.cmd_exchange.toPlainText()))

        btn_copy_wk = QPushButton("📋 Copy workload_client Command")
        btn_copy_wk.setMinimumHeight(40)
        btn_copy_wk.setStyleSheet("padding: 8px 14px; font-weight: 600;")
        btn_copy_wk.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.cmd_workload.toPlainText()))

        btn_copy_sub = QPushButton("📋 Copy reliable_subscriber Command")
        btn_copy_sub.setMinimumHeight(40)
        btn_copy_sub.setStyleSheet("padding: 8px 14px; font-weight: 600;")
        btn_copy_sub.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.cmd_subscriber.toPlainText()))

        copy_row.addWidget(btn_update, 1)
        copy_row.addWidget(btn_copy_ex, 1)
        copy_row.addWidget(btn_copy_wk, 1)
        copy_row.addWidget(btn_copy_sub, 1)
        grid.addLayout(copy_row, 7, 0, 1, 4)

        layout.addWidget(net_box)

        peers_box = QGroupBox("Live Connected Remote Clients & Subscribers Table")
        peers_layout = QVBoxLayout(peers_box)
        peers_layout.setContentsMargins(14, 16, 14, 14)
        self.remote_peers_table = QTableWidget(0, 4)
        self.remote_peers_table.setHorizontalHeaderLabels(["Type", "ID", "Address / Reason", "Status"])
        self._style_table(self.remote_peers_table, min_height=200, row_height=34)
        peers_layout.addWidget(self.remote_peers_table)
        layout.addWidget(peers_box)

        self._update_remote_command()
        return page

    def _on_lan_iface_selected(self) -> None:
        ip = self.lan_iface_combo.currentData()
        if ip:
            self.remote_host.setText(str(ip))
            self._update_remote_command()

    def _start_lan_exchange(self) -> None:
        ip = self.remote_host.text().strip() or "127.0.0.1"
        self.md_interface.setText(ip)
        self.port.setValue(self.remote_port.value())
        self.recovery_port.setValue(self.remote_recovery_port.value())
        self._start_server()

    def _test_remote_connection(self) -> None:
        host = self.remote_host.text().strip() or "127.0.0.1"
        oe_p = self.remote_port.value()
        rec_p = self.remote_recovery_port.value()
        results: list[str] = []
        all_ok = True
        for label, p in (("Order-Entry TCP", oe_p), ("Recovery TCP", rec_p)):
            try:
                with socket.create_connection((host, p), timeout=1.5):
                    results.append(f"{label} ({host}:{p}): OK (reachable)")
            except ConnectionRefusedError:
                all_ok = False
                results.append(f"{label} ({host}:{p}): FAILED — connection refused (exchange not running on port {p})")
            except socket.timeout:
                all_ok = False
                results.append(f"{label} ({host}:{p}): FAILED — timed out (firewall blocking port {p} or wrong IP)")
            except OSError as exc:
                all_ok = False
                results.append(f"{label} ({host}:{p}): FAILED — {exc} (check LAN IP / interface)")
        self.test_conn_label.setText("   |   ".join(results))
        if all_ok:
            self.test_conn_label.setStyleSheet(
                "padding: 10px 14px; background: #f0fdf4; color: #15803d; "
                "border: 2px solid #16a34a; border-radius: 6px; font-weight: 700; font-size: 13px;"
            )
        else:
            self.test_conn_label.setStyleSheet(
                "padding: 10px 14px; background: #fef2f2; color: #b91c1c; "
                "border: 2px solid #dc2626; border-radius: 6px; font-weight: 700; font-size: 13px;"
            )

    def _manual_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setSpacing(14)
        layout.setContentsMargins(14, 14, 14, 14)

        box = QGroupBox("Submit Real TCP Limit Order")
        grid = QGridLayout(box)
        grid.setContentsMargins(16, 18, 16, 16)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)

        self.manual_host = QLineEdit("127.0.0.1")
        self.manual_host.setMinimumHeight(36)
        self.manual_port = self._spinbox(1024, 65535, 8080)
        self.manual_port.setMinimumHeight(36)
        self.manual_symbol = QComboBox()
        self.manual_symbol.addItems(["AAPL", "GOOGL", "AMZN", "NFLX", "META"])
        self.manual_symbol.setMinimumHeight(36)
        self.manual_side = QComboBox()
        self.manual_side.addItems(["BUY", "SELL"])
        self.manual_side.setMinimumHeight(36)
        self.manual_price = QDoubleSpinBox()
        self.manual_price.setRange(0.01, 1_000_000.00)
        self.manual_price.setDecimals(2)
        self.manual_price.setValue(500.00)
        self.manual_price.setMinimumHeight(36)
        self.manual_quantity = self._spinbox(1, 1_000_000, 10)
        self.manual_quantity.setMinimumHeight(36)

        submit = QPushButton("▶ Send Order Over TCP")
        submit.setMinimumHeight(44)
        submit.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; font-weight: bold; font-size: 14px; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:hover { background: #1d4ed8; }"
        )
        submit.clicked.connect(self._submit_manual)

        self.manual_result = QPlainTextEdit("No manual request sent")
        self.manual_result.setReadOnly(True)
        self.manual_result.setMinimumHeight(88)
        self.manual_result.setMaximumHeight(120)
        self.manual_result.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.fanout_label = QLabel("Fan-out: waiting for manual order")
        self.fanout_label.setWordWrap(True)
        self.fanout_label.setMinimumHeight(48)
        self.fanout_label.setStyleSheet(
            "padding: 8px 12px; border: 1px solid #64748b; border-radius: 6px; font-weight: 600;"
        )

        grid.addWidget(QLabel("<b>Server IPv4:</b>"), 0, 0)
        grid.addWidget(self.manual_host, 0, 1)
        grid.addWidget(QLabel("<b>TCP Port:</b>"), 1, 0)
        grid.addWidget(self.manual_port, 1, 1)
        grid.addWidget(QLabel("<b>Instrument:</b>"), 2, 0)
        grid.addWidget(self.manual_symbol, 2, 1)
        grid.addWidget(QLabel("<b>Side:</b>"), 3, 0)
        grid.addWidget(self.manual_side, 3, 1)
        grid.addWidget(QLabel("<b>Limit Price (USD):</b>"), 4, 0)
        grid.addWidget(self.manual_price, 4, 1)
        grid.addWidget(QLabel("<b>Quantity:</b>"), 5, 0)
        grid.addWidget(self.manual_quantity, 5, 1)
        grid.addWidget(submit, 6, 0, 1, 2)
        grid.addWidget(QLabel("<b>Latest Response:</b>"), 7, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.manual_result, 7, 1)
        grid.addWidget(QLabel("<b>Subscriber Fan-Out:</b>"), 8, 0)
        grid.addWidget(self.fanout_label, 8, 1)
        grid.setRowStretch(9, 1)

        right_col = QGroupBox("Manual Order RTT Graph & Submission History Table")
        right_layout = QVBoxLayout(right_col)
        right_layout.setContentsMargins(14, 16, 14, 14)
        right_layout.setSpacing(12)
        self.manual_rtt_chart = self._make_chart("Manual Order TCP Round-Trip Time (ms)", "RTT (ms)", "Order #", 250)
        self.manual_history_table = QTableWidget(0, 6)
        self.manual_history_table.setHorizontalHeaderLabels(["Order #", "Symbol", "Side", "Price ($)", "Qty", "Response & RTT"])
        self._style_table(self.manual_history_table, min_height=220, row_height=34)
        right_layout.addWidget(self.manual_rtt_chart)
        right_layout.addWidget(self.manual_history_table)

        layout.addWidget(box, 2)
        layout.addWidget(right_col, 3)
        return page

    def _results_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.setContentsMargins(14, 14, 14, 14)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        refresh = QPushButton("🔄 Refresh Saved Results")
        refresh.setMinimumHeight(38)
        refresh.setStyleSheet("font-weight: 600; padding: 6px 16px;")
        refresh.clicked.connect(self._refresh_saved_results)
        self.open_in_dashboard_btn = QPushButton("📊 Open Selected in 6-Graph Reliability Dashboard")
        self.open_in_dashboard_btn.setMinimumHeight(38)
        self.open_in_dashboard_btn.setStyleSheet(
            "background: #2563eb; color: white; font-weight: bold; padding: 6px 16px; border-radius: 5px;"
        )
        self.open_in_dashboard_btn.clicked.connect(self._open_selected_in_dashboard)
        btn_row.addWidget(refresh)
        btn_row.addWidget(self.open_in_dashboard_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        top_split = QHBoxLayout()
        top_split.setSpacing(14)
        self.saved_results = QListWidget()
        self.saved_results.itemSelectionChanged.connect(self._preview_saved_result)
        self.saved_results.setMinimumHeight(230)

        self.saved_result_chart = self._make_chart("Selected Result Visual Preview", "Value", "Row / Metric", 230)
        top_split.addWidget(self.saved_results, 2)
        top_split.addWidget(self.saved_result_chart, 3)
        layout.addLayout(top_split)

        self.saved_result_table = QTableWidget(0, 0)
        self.saved_result_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._style_table(self.saved_result_table, min_height=220, row_height=34)
        layout.addWidget(self.saved_result_table)

        self.result_preview = QPlainTextEdit()
        self.result_preview.setReadOnly(True)
        self.result_preview.setMinimumHeight(90)
        self.result_preview.setMaximumHeight(120)
        layout.addWidget(self.result_preview)

        self._refresh_saved_results()
        return page

    def _refresh_saved_results(self) -> None:
        if not hasattr(self, "saved_results"):
            return
        self.saved_results.clear()
        output_dir = REPOSITORY_ROOT / "results"
        if not output_dir.exists():
            self.result_preview.setPlainText("No saved results directory yet.")
            return

        live_dirs = sorted((output_dir / "live").glob("*"), reverse=True) if (output_dir / "live").is_dir() else []
        for ldir in live_dirs:
            if not ldir.is_dir():
                continue
            run_json = ldir / "run.json"
            status_tag = "Live run dir"
            prov_tag = ""
            if run_json.is_file():
                try:
                    data = json.loads(run_json.read_text(encoding="utf-8"))
                    ok = bool(data.get("success"))
                    cfg = data.get("config", {})
                    prov_tag = f" [transport={cfg.get('transport', '?')}, loss={cfg.get('loss_injection', '?')}]"
                    status_tag = "Live run — PASS" if ok else "Live run — FAILED"
                except Exception:
                    status_tag = "Live run — corrupt JSON"
            item = QListWidgetItem(f"[{status_tag}] live/{ldir.name}{prov_tag}")
            item.setData(Qt.ItemDataRole.UserRole, str(ldir))
            self.saved_results.addItem(item)

        paths = sorted(
            [
                *output_dir.glob("aggregate-*.csv"),
                *output_dir.glob("workload-*.csv"),
                *output_dir.glob("reliability-*.csv"),
                *output_dir.glob("live-reliability-*.json"),
            ],
            reverse=True,
        )
        for path in paths:
            if path.name.startswith("aggregate-"):
                category = "Preset aggregate CSV"
            elif path.name.startswith("workload-"):
                category = "Workload CSV"
            elif path.name.startswith("live-reliability-"):
                try:
                    success = bool(json.loads(path.read_text(encoding="utf-8")).get("success"))
                    category = "Live reliability JSON" if success else "Live reliability JSON — failed"
                except (OSError, json.JSONDecodeError):
                    category = "Live reliability JSON — unreadable"
            else:
                category = "Reliability demo CSV"
            item = QListWidgetItem(f"[{category}] {path.name}")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.saved_results.addItem(item)
        if self.saved_results.count():
            self.saved_results.setCurrentRow(0)
        else:
            self.result_preview.setPlainText("No saved results yet.")

    def _populate_preview_table_and_chart(self, rows: list[dict[str, Any]], title: str) -> None:
        if not rows:
            self.saved_result_table.setRowCount(0)
            self.saved_result_table.setColumnCount(0)
            self.saved_result_chart.clear()
            return
        cols = list(rows[0].keys())[:14]
        self.saved_result_table.setColumnCount(len(cols))
        self.saved_result_table.setHorizontalHeaderLabels(cols)
        self.saved_result_table.setRowCount(len(rows))
        for r_idx, row in enumerate(rows):
            for c_idx, col_name in enumerate(cols):
                self.saved_result_table.setItem(r_idx, c_idx, QTableWidgetItem(str(row.get(col_name, ""))))
        self.saved_result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self.saved_result_chart.clear()
        self.saved_result_chart.setTitle(f"Preview: {title}")
        candidate_cols = [
            "delivered", "delivered_mean", "events_needing_recovery_pct_mean",
            "server_wait_p99_ns_mean", "snapshot_recoveries_mean", "acknowledgements", "recovered_events",
        ]
        chosen_col = next((c for c in candidate_cols if c in rows[0]), None)
        if chosen_col:
            labels = [
                str(r.get("subscriber_id") or r.get("mode_group") or r.get("recovery_policy") or r.get("client_id") or f"R{i+1}")
                for i, r in enumerate(rows[:16])
            ]
            vals = []
            for r in rows[:16]:
                try:
                    vals.append(float(r.get(chosen_col, 0) or 0))
                except ValueError:
                    vals.append(0.0)
            xs = list(range(len(vals)))
            self.saved_result_chart.addItem(pg.BarGraphItem(x=xs, height=vals, width=0.55, brush=pg.mkBrush("#2563eb")))
            self.saved_result_chart.getAxis("bottom").setTicks([list(zip(xs, labels))])
            self.saved_result_chart.setLabel("left", chosen_col)
            self.saved_result_chart.setYRange(0, max(1.0, max(vals, default=1.0) * 1.2), padding=0)

    def _preview_saved_result(self) -> None:
        selected = self.saved_results.selectedItems()
        if not selected:
            return
        path = Path(selected[0].data(Qt.ItemDataRole.UserRole))
        try:
            if path.is_dir():
                summary_csv = path / "run-summary.csv"
                run_json = path / "run.json"
                if summary_csv.is_file():
                    with summary_csv.open(newline="", encoding="utf-8") as f:
                        rows = list(csv.DictReader(f))
                    self._populate_preview_table_and_chart(rows, path.name)
                    self.result_preview.setPlainText(summary_csv.read_text(encoding="utf-8")[:3000])
                elif run_json.is_file():
                    data = json.loads(run_json.read_text(encoding="utf-8"))
                    subs = list((data.get("subscriber_metrics") or {}).values())
                    if subs and isinstance(subs[0], dict):
                        self._populate_preview_table_and_chart(subs, path.name)
                    self.result_preview.setPlainText(run_json.read_text(encoding="utf-8")[:3000])
            elif path.suffix == ".csv":
                with path.open(newline="", encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
                self._populate_preview_table_and_chart(rows, path.name)
                self.result_preview.setPlainText(path.read_text(encoding="utf-8")[:3000])
            elif path.suffix == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
                subs = list((data.get("subscriber_metrics") or {}).values())
                if subs and isinstance(subs[0], dict):
                    self._populate_preview_table_and_chart(subs, path.name)
                self.result_preview.setPlainText(path.read_text(encoding="utf-8")[:3000])
        except Exception as error:
            self.result_preview.setPlainText(f"Could not read result: {error}")

    def _open_selected_in_dashboard(self) -> None:
        selected = self.saved_results.selectedItems()
        if not selected:
            return
        target = str(selected[0].data(Qt.ItemDataRole.UserRole))
        self.open_in_dashboard_requested.emit(target)

    def _open_latest_live_in_dashboard(self) -> None:
        if self.controller.current_run_dir and self.controller.current_run_dir.is_dir():
            self.open_in_dashboard_requested.emit(str(self.controller.current_run_dir))
            return
        self._refresh_saved_results()
        if self.saved_results.count() > 0:
            target = str(self.saved_results.item(0).data(Qt.ItemDataRole.UserRole))
            self.open_in_dashboard_requested.emit(target)

    def _start_server(self) -> None:
        self.seq_history.clear()
        self.md_sent_history.clear()
        self.client_ack_bars.clear()
        self.controller.start_server(
            port=self.port.value(),
            md_interface=self.md_interface.text().strip() or "127.0.0.1",
            dual_feed=self.dual_feed_check.isChecked(),
            recovery_port=self.recovery_port.value(),
            history=self.history_spin.value(),
            snapshot_interval=self.snap_interval_spin.value(),
            snapshot_retention=self.snap_retention_spin.value(),
            snapshot_on_demand=self.snap_on_demand_check.isChecked(),
            recovery_policy=self.recovery_policy_combo.currentText(),
            recovery_workers=self.recovery_workers_spin.value(),
            service_delay_req_us=self.service_delay_req_spin.value(),
            service_delay_event_us=self.service_delay_event_spin.value(),
            tamper_snapshot=self.tamper_combo.currentText(),
            heartbeat_ms=self.heartbeat_spin.value(),
        )

    def _start_subscribers(self) -> None:
        specs = self._read_subscriber_cfg_table()
        self.live_sub_table.setRowCount(len(specs))
        self.sub_row_by_id.clear()
        for row_idx, sp in enumerate(specs):
            sid = str(sp["id"])
            self.sub_row_by_id[sid] = row_idx
            self.live_sub_table.setItem(row_idx, 0, QTableWidgetItem(sid))
            self.live_sub_table.setItem(row_idx, 1, QTableWidgetItem("Starting..."))
            for col in range(2, 12):
                self.live_sub_table.setItem(row_idx, col, QTableWidgetItem("—"))
        self.controller.start_subscribers(
            specs=specs,
            interface_ip=self.md_interface.text().strip() or "127.0.0.1",
            recovery_host="127.0.0.1",
            recovery_port=self.recovery_port.value(),
            grace_ms=self.sub_grace_spin.value(),
            retries=self.sub_retries_spin.value(),
            stop_when_idle_ms=self.sub_idle_spin.value(),
        )

    def _on_subscriber_status(self, sid: str, status: str) -> None:
        row = self.sub_row_by_id.get(sid)
        if row is None:
            return
        item = QTableWidgetItem(status)
        if status.startswith("failed"):
            item.setForeground(QColor("#b42318"))
        elif status.startswith("Connected") or status == "completed":
            item.setForeground(QColor("#197b30"))
        self.live_sub_table.setItem(row, 1, item)

    def _on_subscriber_stats(self, sid: str, kv: dict[str, str]) -> None:
        row = self.sub_row_by_id.get(sid)
        if row is None:
            return
        feed_str = f"A:{kv.get('feed_a','?')}/B:{kv.get('feed_b','?')}"
        self.live_sub_table.setItem(row, 2, QTableWidgetItem(feed_str))
        self.live_sub_table.setItem(row, 3, QTableWidgetItem(kv.get("last_seq", "0")))
        self.live_sub_table.setItem(row, 4, QTableWidgetItem(kv.get("delivered", "0")))
        self.live_sub_table.setItem(row, 5, QTableWidgetItem(kv.get("duplicates", "0")))
        self.live_sub_table.setItem(row, 6, QTableWidgetItem(kv.get("gap_ranges", "0")))
        self.live_sub_table.setItem(row, 7, QTableWidgetItem(kv.get("recovered", "0")))
        self.live_sub_table.setItem(row, 8, QTableWidgetItem(kv.get("snapshot_recoveries", "0")))
        self.live_sub_table.setItem(row, 9, QTableWidgetItem(kv.get("lost_so_far", "0")))
        self._refresh_live_sub_chart()
        self._update_fanout_indicator()

    def _on_subscriber_result(self, sid: str, kv: dict[str, str]) -> None:
        row = self.sub_row_by_id.get(sid)
        if row is None:
            return
        self.live_sub_table.setItem(row, 4, QTableWidgetItem(kv.get("delivered", "0")))
        self.live_sub_table.setItem(row, 5, QTableWidgetItem(kv.get("duplicates_suppressed", "0")))
        self.live_sub_table.setItem(row, 6, QTableWidgetItem(kv.get("gap_ranges_requested", "0")))
        self.live_sub_table.setItem(row, 7, QTableWidgetItem(kv.get("recovered_events", "0")))
        self.live_sub_table.setItem(row, 8, QTableWidgetItem(kv.get("snapshot_recoveries", "0")))
        self.live_sub_table.setItem(row, 9, QTableWidgetItem(kv.get("permanently_lost", "0")))

        bm = kv.get("book_matches_exchange", "n/a")
        bm_item = QTableWidgetItem("MATCH (1)" if bm == "1" else f"MISMATCH ({bm})")
        bm_item.setForeground(QColor("#197b30") if bm == "1" else QColor("#b42318"))
        self.live_sub_table.setItem(row, 10, bm_item)

        n_samples = int(kv.get("recovery_n", "0") or 0)
        insuff = kv.get("insufficient_samples", "1") == "1"
        p50_us = float(kv.get("recovery_p50_ns", "0") or 0) / 1000.0
        p99_us = float(kv.get("recovery_p99_ns", "0") or 0) / 1000.0
        lat_txt = f"{p50_us:.1f}/{p99_us:.1f} µs (n={n_samples})"
        lat_item = QTableWidgetItem(lat_txt if n_samples > 0 else "n=0 (no TCP recovery)")
        if insuff:
            lat_item.setForeground(QColor("#777777"))
        self.live_sub_table.setItem(row, 11, lat_item)
        self._refresh_live_sub_chart()

    def _on_exchange_stats(self, kv: dict[str, str]) -> None:
        seq = int(kv.get("latest_seq", "0") or 0)
        md_s = int(kv.get("md_sent", "0") or 0)
        self.seq_history.append(seq)
        self.md_sent_history.append(md_s)
        self.exchange_live_stats_label.setText(
            f"latest_seq={seq} | queue_depth={kv.get('queue_depth','0')} | "
            f"subscribers={kv.get('subscribers','0')} | md_sent={md_s}"
        )
        self._refresh_live_exchange_chart()
        self._update_fanout_indicator()

    def _on_remote_peer_event(self, kind: str, peer_id: str, addr_or_reason: str, state: str) -> None:
        for r in range(self.remote_peers_table.rowCount()):
            if (self.remote_peers_table.item(r, 0).text() == kind
                    and self.remote_peers_table.item(r, 1).text() == peer_id):
                self.remote_peers_table.setItem(r, 2, QTableWidgetItem(addr_or_reason))
                self.remote_peers_table.setItem(r, 3, QTableWidgetItem(state))
                return
        row = self.remote_peers_table.rowCount()
        self.remote_peers_table.insertRow(row)
        self.remote_peers_table.setItem(row, 0, QTableWidgetItem(kind))
        self.remote_peers_table.setItem(row, 1, QTableWidgetItem(peer_id))
        self.remote_peers_table.setItem(row, 2, QTableWidgetItem(addr_or_reason))
        self.remote_peers_table.setItem(row, 3, QTableWidgetItem(state))

    def _start_workload(self) -> None:
        self.client_ack_bars.clear()
        self.client_table.setRowCount(self.client_count.value())
        for client_id in range(1, self.client_count.value() + 1):
            self.client_table.setItem(client_id - 1, 0, QTableWidgetItem(f"Client {client_id}"))
            self.client_table.setItem(client_id - 1, 1, QTableWidgetItem("Starting..."))
        self.controller.start_workload(
            "127.0.0.1", self.port.value(), self.client_count.value(), self.rate.value(),
            self.duration.value(), self.symbol.currentText(), self.seed.value(),
        )

    def _update_remote_command(self) -> None:
        host = self.remote_host.text().strip() or "<EXCHANGE_LAN_IP>"
        sub_ip = self.remote_sub_iface.text().strip() or "<SUBSCRIBER_OWN_LAN_IP>"
        oe_p = self.remote_port.value()
        rec_p = self.remote_recovery_port.value()
        self.cmd_exchange.setPlainText(
            f"./build-local/src/exchange --mode client --oe-port {oe_p} "
            f"--md-interface {host} --dual-feed --recovery-port {rec_p} "
            f"--history 65536 --snapshot-interval 100 --snapshot-on-demand 1"
        )
        self.cmd_workload.setPlainText(
            f"./build-local/src/workload_client --server {host} --port {oe_p} "
            f"--client-id 101 --duration 10 --rate 200 --symbol AAPL --side buy --seed 101"
        )
        self.cmd_subscriber.setPlainText(
            f"./build-local/src/reliable_subscriber --interface {sub_ip} --dual-feed "
            f"--recovery-server {host} --recovery-port {rec_p} --subscriber lan-sub-1 "
            f"--loss-model independent --drop-a 0.05 --drop-b 0.05 --drop-seed 42 "
            f"--stop-when-idle-ms 1000 --csv results/lan-sub-1.csv"
        )

    def _submit_manual(self) -> None:
        if self.manual_thread and self.manual_thread.isRunning():
            self.manual_result.setPlainText("A manual request is already in progress")
            return
        self.seq_before_manual_order = int(self.controller.latest_exchange_stats.get("latest_seq", "0") or 0)
        side = BUY if self.manual_side.currentText() == "BUY" else SELL
        self._last_manual_order_meta = (
            self.manual_symbol.currentText(),
            self.manual_side.currentText(),
            f"{self.manual_price.value():.2f}",
            str(self.manual_quantity.value()),
        )
        self.manual_result.setPlainText("Sending real TCP request...")
        self.manual_thread = ManualOrderThread(
            self.manual_host.text().strip(), self.manual_port.value(), 90_001, next(self.manual_ids),
            self.manual_symbol.currentText(), side, round(self.manual_price.value() * 100), self.manual_quantity.value(),
        )
        self.manual_thread.completed.connect(self._on_manual_completed)
        self.manual_thread.start()

    def _on_manual_completed(self, text: str, _exec_count: int, rtt_ms: float) -> None:
        self.manual_result.setPlainText(text)
        self._append_log(f"manual: {text}")
        self._update_fanout_indicator()

        row = self.manual_history_table.rowCount()
        self.manual_history_table.insertRow(row)
        sym, side_s, price_s, qty_s = getattr(self, "_last_manual_order_meta", ("AAPL", "BUY", "500.00", "10"))
        for c_idx, val in enumerate((f"#{row + 1}", sym, side_s, price_s, qty_s, text)):
            self.manual_history_table.setItem(row, c_idx, QTableWidgetItem(val))

        if rtt_ms > 0:
            self.manual_rtt_history.append((f"#{row + 1}", rtt_ms))
            self.manual_rtt_chart.clear()
            xs = list(range(len(self.manual_rtt_history)))
            ys = [v for _, v in self.manual_rtt_history]
            lbls = [k for k, _ in self.manual_rtt_history]
            self.manual_rtt_chart.addItem(pg.BarGraphItem(x=xs, height=ys, width=0.5, brush=pg.mkBrush("#2563eb")))
            self.manual_rtt_chart.getAxis("bottom").setTicks([list(zip(xs, lbls))])
            self.manual_rtt_chart.setYRange(0, max(1.0, max(ys, default=1.0) * 1.25), padding=0)

    def _update_fanout_indicator(self) -> None:
        if not self.controller.gui_owns_exchange:
            self.fanout_label.setText(
                "Fan-out indicator unavailable: exchange was not started by this GUI session."
            )
            return
        new_seq = int(self.controller.latest_exchange_stats.get("latest_seq", "0") or 0)
        total_subs = len(self.controller.subscriber_processes)
        caught_up = sum(
            1 for s in self.controller.latest_subscriber_stats.values()
            if int(s.get("last_seq", "0") or 0) >= new_seq and new_seq > 0
        )
        self.fanout_label.setText(
            f"Seq before order: {self.seq_before_manual_order} -> current: {new_seq} | "
            f"Subscribers caught up: {caught_up}/{total_subs}"
        )

    def _set_client_status(self, client_id: int, status: str) -> None:
        self.client_status_label.setText(f"Client {client_id}: {status}")
        if client_id - 1 < self.client_table.rowCount():
            self.client_table.setItem(client_id - 1, 1, QTableWidgetItem(status))

    def _set_server_status(self, status: str) -> None:
        self.server_status.setText(status)
        bg, fg = {
            "Ready": ("#16a34a", "#ffffff"),
            "Starting": ("#d97706", "#ffffff"),
            "Stopped": ("#334155", "#f8fafc"),
        }.get(status, ("#dc2626", "#ffffff"))
        self.server_status.setStyleSheet(
            f"font-weight: 700; background: {bg}; color: {fg}; padding: 4px 10px; border-radius: 5px;"
        )

    def _show_result(self, result: dict) -> None:
        cid = int(result.get("client_id", 1))
        p50 = float(result.get("ack_p50_us", 0) or 0)
        p95 = float(result.get("ack_p95_us", 0) or 0)
        p99 = float(result.get("ack_p99_us", 0) or 0)
        self.client_ack_bars[cid] = (p50, p95, p99)
        summary_txt = (
            f"Completed: sent={result.get('sent')}, ack={result.get('acknowledgements')}, "
            f"exec={result.get('executions')} | RTT p50/p95/p99 = {p50:.0f}/{p95:.0f}/{p99:.0f} µs"
        )
        self.result_label.setText(f"Client {cid}: {summary_txt}")
        if cid - 1 < self.client_table.rowCount():
            it = QTableWidgetItem(summary_txt)
            it.setForeground(QColor("#15803d"))
            self.client_table.setItem(cid - 1, 1, it)
        self._refresh_live_exchange_chart()

    def _append_log(self, message: str) -> None:
        self.event_log.appendPlainText(
            f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        )

    def shutdown(self) -> None:
        self.controller.shutdown()
        if self.manual_thread and self.manual_thread.isRunning():
            self.manual_thread.wait(1000)
