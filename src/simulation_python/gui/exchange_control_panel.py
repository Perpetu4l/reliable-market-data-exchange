"""Professional Exchange Operations Control Room for local, manual, LAN, and saved-result workflows."""

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
from PyQt6.QtGui import QColor, QFont, QGuiApplication
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
from .theme import (
    AMBER_WARN,
    BG_ELEVATED,
    BLUE_INFO,
    BORDER_SUBTLE,
    CYAN_ACCENT,
    GREEN_BRIGHT,
    GREEN_OK,
    PURPLE_ANALYTIC,
    RED_BRIGHT,
    RED_ERR,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    CollapsibleConsole,
    MetricCard,
    create_terminal_chart,
    render_bar_chart,
    render_empty_chart,
    render_grouped_bar_chart,
    style_readonly_table,
)


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
    """Visual control room for local simulation, manual orders, distributed LAN setup, and saved results."""

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
        widget.setMinimumHeight(30)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return widget

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    @staticmethod
    def _make_chart(title: str, y_label: str, x_label: str, min_height: int = 235) -> pg.PlotWidget:
        return create_terminal_chart(title, y_label, x_label, min_height=min_height)

    @staticmethod
    def _style_table(table: QTableWidget, min_height: int = 185, row_height: int = 30) -> None:
        style_readonly_table(table, min_height=min_height, row_height=row_height)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 8, 10, 8)

        # Live Operational Status Strip (always visible at the top of Exchange Operations)
        status_strip = QHBoxLayout()
        status_strip.setSpacing(10)
        self.kpi_exchange_status = MetricCard("Exchange Status", "STOPPED", "Order-Entry & Multicast Publisher", TEXT_SECONDARY)
        self.kpi_recovery_status = MetricCard("Recovery Server", "STANDBY", "TCP Retransmission & Snapshot Server", BLUE_INFO)
        self.kpi_active_subs = MetricCard("Active Subscribers", "0", "Connected UDP/TCP Processes", CYAN_ACCENT)
        self.kpi_latest_seq = MetricCard("Latest Sequence", "0", "Authoritative Monotonic Sequence", GREEN_BRIGHT)
        self.kpi_queue_depth = MetricCard("Queue Depth", "0", "Pending Recovery Requests", AMBER_WARN)
        self.kpi_md_sent = MetricCard("MD Events Sent", "0", "Total Multicast Datagrams", PURPLE_ANALYTIC)

        for card in (
            self.kpi_exchange_status,
            self.kpi_recovery_status,
            self.kpi_active_subs,
            self.kpi_latest_seq,
            self.kpi_queue_depth,
            self.kpi_md_sent,
        ):
            status_strip.addWidget(card, 1)
        layout.addLayout(status_strip)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._scroll(self._local_tab()), "1. Process Control && Live Telemetry")
        self.tabs.addTab(self._scroll(self._manual_tab()), "2. Manual Order Entry && RTT Analysis")
        self.tabs.addTab(self._scroll(self._distributed_tab()), "3. Distributed Multi-Node LAN Setup")
        self.tabs.addTab(self._scroll(self._results_tab()), "4. Saved Run Explorer && Preview")
        layout.addWidget(self.tabs, 1)

        self.event_log = CollapsibleConsole("Exchange & Process Event Console", collapsed_by_default=True)
        layout.addWidget(self.event_log)

    def _local_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.setContentsMargins(8, 8, 8, 8)

        # Top Row: 4 Clearly Separated Configuration & Control Panels
        top_grid = QGridLayout()
        top_grid.setSpacing(10)

        # 1. Exchange Configuration
        ex_box = QGroupBox("1. Exchange Configuration")
        ex_grid = QGridLayout(ex_box)
        ex_grid.setContentsMargins(12, 14, 12, 10)
        ex_grid.setHorizontalSpacing(10)
        ex_grid.setVerticalSpacing(8)
        ex_grid.setColumnStretch(1, 1)
        ex_grid.setColumnStretch(3, 1)

        self.port = self._spinbox(1024, 65535, 8080)
        self.md_interface = QLineEdit("127.0.0.1")
        self.md_interface.setMinimumHeight(30)
        self.dual_feed_check = QCheckBox("Dual Feed (A+B)")
        self.dual_feed_check.setChecked(True)
        self.heartbeat_spin = self._spinbox(50, 10_000, 500)
        self.heartbeat_spin.setSuffix(" ms")

        ex_grid.addWidget(QLabel("<b>OE TCP Port:</b>"), 0, 0)
        ex_grid.addWidget(self.port, 0, 1)
        ex_grid.addWidget(QLabel("<b>MD Interface:</b>"), 0, 2)
        ex_grid.addWidget(self.md_interface, 0, 3)
        ex_grid.addWidget(QLabel("<b>Heartbeat:</b>"), 1, 0)
        ex_grid.addWidget(self.heartbeat_spin, 1, 1)
        ex_grid.addWidget(self.dual_feed_check, 1, 2, 1, 2)

        # 2. Recovery Configuration
        rec_box = QGroupBox("2. Recovery && Snapshot Server Configuration")
        rec_grid = QGridLayout(rec_box)
        rec_grid.setContentsMargins(12, 14, 12, 10)
        rec_grid.setHorizontalSpacing(10)
        rec_grid.setVerticalSpacing(8)
        for c in (1, 3, 5):
            rec_grid.setColumnStretch(c, 1)

        self.recovery_port = self._spinbox(1024, 65535, 9090)
        self.history_spin = self._spinbox(0, 1_000_000, 65536)
        self.snap_interval_spin = self._spinbox(0, 1_000_000, 100)
        self.snap_retention_spin = self._spinbox(1, 1000, 8)
        self.snap_on_demand_check = QCheckBox("On-Demand Snap")
        self.snap_on_demand_check.setChecked(True)
        self.recovery_policy_combo = QComboBox()
        self.recovery_policy_combo.addItems(["fcfs", "round-robin"])
        self.recovery_policy_combo.setMinimumHeight(30)
        self.recovery_workers_spin = self._spinbox(1, 16, 1)
        self.service_delay_req_spin = self._spinbox(0, 1_000_000, 0)
        self.service_delay_req_spin.setSuffix(" µs")
        self.service_delay_event_spin = self._spinbox(0, 100_000, 0)
        self.service_delay_event_spin.setSuffix(" µs")
        self.tamper_combo = QComboBox()
        self.tamper_combo.addItems(["none", "book_value", "sequence", "previous_hash", "hash"])
        self.tamper_combo.setMinimumHeight(30)

        rec_grid.addWidget(QLabel("<b>Rec Port:</b>"), 0, 0)
        rec_grid.addWidget(self.recovery_port, 0, 1)
        rec_grid.addWidget(QLabel("<b>History:</b>"), 0, 2)
        rec_grid.addWidget(self.history_spin, 0, 3)
        rec_grid.addWidget(QLabel("<b>Policy:</b>"), 0, 4)
        rec_grid.addWidget(self.recovery_policy_combo, 0, 5)

        rec_grid.addWidget(QLabel("<b>Snap Intv:</b>"), 1, 0)
        rec_grid.addWidget(self.snap_interval_spin, 1, 1)
        rec_grid.addWidget(QLabel("<b>Snap Ret:</b>"), 1, 2)
        rec_grid.addWidget(self.snap_retention_spin, 1, 3)
        rec_grid.addWidget(QLabel("<b>Workers:</b>"), 1, 4)
        rec_grid.addWidget(self.recovery_workers_spin, 1, 5)

        rec_grid.addWidget(QLabel("<b>Delay/Req:</b>"), 2, 0)
        rec_grid.addWidget(self.service_delay_req_spin, 2, 1)
        rec_grid.addWidget(QLabel("<b>Delay/Ev:</b>"), 2, 2)
        rec_grid.addWidget(self.service_delay_event_spin, 2, 3)
        rec_grid.addWidget(QLabel("<b>Tamper:</b>"), 2, 4)
        rec_grid.addWidget(self.tamper_combo, 2, 5)
        rec_grid.addWidget(self.snap_on_demand_check, 3, 0, 1, 2)

        # 3. Workload Configuration
        wk_box = QGroupBox("3. Workload Generator Configuration")
        wk_grid = QGridLayout(wk_box)
        wk_grid.setContentsMargins(12, 14, 12, 10)
        wk_grid.setHorizontalSpacing(10)
        wk_grid.setVerticalSpacing(8)
        wk_grid.setColumnStretch(1, 1)
        wk_grid.setColumnStretch(3, 1)

        self.client_count = self._spinbox(1, 32, 2)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0.1, 50_000.0)
        self.rate.setDecimals(1)
        self.rate.setValue(400.0)
        self.rate.setMinimumHeight(30)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.5, 3600.0)
        self.duration.setDecimals(1)
        self.duration.setValue(4.0)
        self.duration.setMinimumHeight(30)
        self.symbol = QComboBox()
        self.symbol.addItems(["AAPL", "GOOGL", "AMZN", "NFLX", "META"])
        self.symbol.setMinimumHeight(30)
        self.seed = self._spinbox(0, 2_147_483_647, 11)

        wk_grid.addWidget(QLabel("<b>Clients:</b>"), 0, 0)
        wk_grid.addWidget(self.client_count, 0, 1)
        wk_grid.addWidget(QLabel("<b>Orders/s:</b>"), 0, 2)
        wk_grid.addWidget(self.rate, 0, 3)
        wk_grid.addWidget(QLabel("<b>Duration (s):</b>"), 1, 0)
        wk_grid.addWidget(self.duration, 1, 1)
        wk_grid.addWidget(QLabel("<b>Symbol:</b>"), 1, 2)
        wk_grid.addWidget(self.symbol, 1, 3)

        # 4. Process Controls
        ctrl_box = QGroupBox("4. Process Lifecycle Controls")
        ctrl_layout = QVBoxLayout(ctrl_box)
        ctrl_layout.setContentsMargins(12, 14, 12, 10)
        ctrl_layout.setSpacing(8)

        btn_grid = QGridLayout()
        btn_grid.setSpacing(8)
        self.start_server_button = QPushButton("1. Start Exchange")
        self.start_server_button.setMinimumHeight(34)
        self.start_server_button.setStyleSheet(
            f"QPushButton {{ background: #065f46; color: #ecfdf5; border: 1px solid {GREEN_OK}; font-weight: 700; border-radius: 5px; }}"
            f"QPushButton:hover {{ background: #047857; }}"
            f"QPushButton:disabled {{ background: #0f172a; color: #475569; border-color: #1e293b; }}"
        )
        self.start_subscribers_button = QPushButton("2. Start Subscribers")
        self.start_subscribers_button.setMinimumHeight(34)
        self.start_subscribers_button.setStyleSheet(
            f"QPushButton {{ background: #1e3a8a; color: #eff6ff; border: 1px solid {BLUE_INFO}; font-weight: 700; border-radius: 5px; }}"
            f"QPushButton:hover {{ background: #1d4ed8; }}"
        )
        self.start_workload_button = QPushButton("3. Start Workload")
        self.start_workload_button.setMinimumHeight(34)
        self.start_workload_button.setStyleSheet(
            f"QPushButton {{ background: #4c1d95; color: #f5f3ff; border: 1px solid {PURPLE_ANALYTIC}; font-weight: 700; border-radius: 5px; }}"
            f"QPushButton:hover {{ background: #5b21b6; }}"
        )
        self.stop_subscribers_button = QPushButton("Reconcile && Stop Subs")
        self.stop_subscribers_button.setMinimumHeight(34)
        self.stop_subscribers_style = (
            f"QPushButton {{ background: #78350f; color: #fef3c7; border: 1px solid {AMBER_WARN}; font-weight: 700; border-radius: 5px; }}"
            f"QPushButton:hover {{ background: #92400e; }}"
        )
        self.stop_subscribers_button.setStyleSheet(self.stop_subscribers_style)
        self.stop_button = QPushButton("Stop All Processes")
        self.stop_button.setMinimumHeight(34)
        self.stop_button.setStyleSheet(
            f"QPushButton {{ background: #7f1d1d; color: #fef2f2; border: 1px solid {RED_ERR}; font-weight: 700; border-radius: 5px; }}"
            f"QPushButton:hover {{ background: #991b1b; }}"
        )
        self.open_latest_dash_button = QPushButton("Inspect in Reliability Experiments")
        self.open_latest_dash_button.setMinimumHeight(34)
        self.open_latest_dash_button.setStyleSheet(
            f"QPushButton {{ background: #164e63; color: #ecfeff; border: 1px solid {CYAN_ACCENT}; font-weight: 700; border-radius: 5px; }}"
            f"QPushButton:hover {{ background: #155e75; }}"
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
        ctrl_layout.addLayout(btn_grid)

        status_line = QHBoxLayout()
        self.server_status = QLabel("Stopped")
        self.server_status.setMinimumHeight(26)
        self.server_status.setStyleSheet(
            f"font-weight: 700; background: {BG_ELEVATED}; color: {TEXT_SECONDARY}; padding: 3px 10px; border-radius: 4px;"
        )
        self.exchange_live_stats_label = QLabel("latest_seq=0 | queue_depth=0 | subscribers=0")
        self.exchange_live_stats_label.setMinimumHeight(26)
        self.exchange_live_stats_label.setStyleSheet(
            f"font-weight: 600; background: {BG_ELEVATED}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER_SUBTLE}; padding: 3px 10px; border-radius: 4px;"
        )
        status_line.addWidget(QLabel("<b>State:</b>"))
        status_line.addWidget(self.server_status)
        status_line.addWidget(self.exchange_live_stats_label, 1)
        ctrl_layout.addLayout(status_line)

        top_grid.addWidget(ex_box, 0, 0)
        top_grid.addWidget(wk_box, 1, 0)
        top_grid.addWidget(rec_box, 0, 1)
        top_grid.addWidget(ctrl_box, 1, 1)
        top_grid.setColumnStretch(0, 2)
        top_grid.setColumnStretch(1, 3)
        layout.addLayout(top_grid)

        # 2 Live Visual Charts
        charts_box = QGroupBox("Live Operational Telemetry Charts (Subscriber Delivery && Workload ACK Latency)")
        charts_layout = QHBoxLayout(charts_box)
        charts_layout.setContentsMargins(10, 14, 10, 10)
        charts_layout.setSpacing(12)
        self.live_sub_chart = self._make_chart(
            "Subscriber Delivered vs TCP Recovered Events", "Events (count)", "Subscriber ID", 240
        )
        self.live_exchange_chart = self._make_chart(
            "Exchange Sequence Progression / Workload Client ACK RTT", "Sequence / RTT (µs)", "Sample / Client Percentile", 240
        )
        charts_layout.addWidget(self.live_sub_chart, 1)
        charts_layout.addWidget(self.live_exchange_chart, 1)
        layout.addWidget(charts_box)
        self._render_initial_local_charts()

        # Subscribers Configuration Table
        sub_cfg_box = QGroupBox("Market-Data Subscribers Configuration Matrix (1–32 Processes)")
        sub_cfg_layout = QVBoxLayout(sub_cfg_box)
        sub_cfg_layout.setContentsMargins(10, 14, 10, 10)
        sub_cfg_layout.setSpacing(8)
        top_sub_row = QHBoxLayout()
        top_sub_row.setSpacing(12)
        self.sub_count_spin = self._spinbox(1, 32, 5)
        self.sub_count_spin.valueChanged.connect(self._sync_subscriber_cfg_rows)
        self.sub_grace_spin = self._spinbox(0, 1000, 2)
        self.sub_grace_spin.setSuffix(" ms")
        self.sub_retries_spin = self._spinbox(0, 20, 5)
        self.sub_idle_spin = self._spinbox(0, 30_000, 600)
        self.sub_idle_spin.setSuffix(" ms")

        top_sub_row.addWidget(QLabel("<b>Subscribers:</b>"))
        top_sub_row.addWidget(self.sub_count_spin)
        top_sub_row.addWidget(QLabel("<b>Gap Grace:</b>"))
        top_sub_row.addWidget(self.sub_grace_spin)
        top_sub_row.addWidget(QLabel("<b>Max Retries:</b>"))
        top_sub_row.addWidget(self.sub_retries_spin)
        top_sub_row.addWidget(QLabel("<b>Stop-When-Idle:</b>"))
        top_sub_row.addWidget(self.sub_idle_spin)
        top_sub_row.addStretch()
        sub_cfg_layout.addLayout(top_sub_row)

        self.sub_cfg_table = QTableWidget(0, 9)
        self.sub_cfg_table.setHorizontalHeaderLabels([
            "Subscriber ID", "Feeds (a/ab)", "Loss Model", "Drop A", "Drop B", "Corr Loss", "Seed", "Concurrency", "Role",
        ])
        self._style_table(self.sub_cfg_table, min_height=180, row_height=30)
        sub_cfg_layout.addWidget(self.sub_cfg_table)
        self._sync_subscriber_cfg_rows(self.sub_count_spin.value())
        layout.addWidget(sub_cfg_box)

        # Bottom Row: Live Subscriber Telemetry Table + Workload Clients Table side by side
        bottom_tables = QHBoxLayout()
        bottom_tables.setSpacing(12)

        live_sub_box = QGroupBox("Live Subscriber Status && Final Book Reconciliation")
        live_sub_layout = QVBoxLayout(live_sub_box)
        live_sub_layout.setContentsMargins(10, 14, 10, 10)
        self.live_sub_table = QTableWidget(0, 12)
        self.live_sub_table.setHorizontalHeaderLabels([
            "Subscriber", "State", "Feed A/B", "Last Seq", "Delivered",
            "Duplicates", "Gap Ranges", "Recoveries", "Snapshots",
            "Lost", "Book Match", "TCP Latency p50/p99 (µs)",
        ])
        self.live_sub_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._style_table(self.live_sub_table, min_height=190, row_height=30)
        live_sub_layout.addWidget(self.live_sub_table)

        clients_box = QGroupBox("Order-Entry Workload Clients")
        client_layout = QVBoxLayout(clients_box)
        client_layout.setContentsMargins(10, 14, 10, 10)
        self.client_status_label = QLabel("Idle — awaiting workload start")
        self.client_status_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-weight: 600;")
        self.result_label = QLabel("Start exchange + workload to measure client order ACK RTT percentiles.")
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        client_layout.addWidget(self.client_status_label)
        client_layout.addWidget(self.result_label)
        self.client_table = QTableWidget(0, 2)
        self.client_table.setHorizontalHeaderLabels(["Client ID", "Observed Status & ACK Latency"])
        self.client_table.verticalHeader().setVisible(False)
        self.client_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._style_table(self.client_table, min_height=145, row_height=30)
        client_layout.addWidget(self.client_table)

        bottom_tables.addWidget(live_sub_box, 3)
        bottom_tables.addWidget(clients_box, 2)
        layout.addLayout(bottom_tables)

        return page

    def _render_initial_local_charts(self) -> None:
        render_empty_chart(
            self.live_sub_chart,
            "No subscriber telemetry yet — start Exchange, Subscribers, and Workload above",
        )
        render_empty_chart(
            self.live_exchange_chart,
            "No sequence / ACK telemetry yet — start Exchange and Workload above",
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
                delivered_vals.append(float(del_it.text()) if del_it and del_it.text().replace(".", "", 1).isdigit() else 0.0)
            except ValueError:
                delivered_vals.append(0.0)
            try:
                rec_vals.append(float(rec_it.text()) if rec_it and rec_it.text().replace(".", "", 1).isdigit() else 0.0)
            except ValueError:
                rec_vals.append(0.0)

        render_grouped_bar_chart(
            self.live_sub_chart,
            labels,
            delivered_vals,
            rec_vals,
            name_a="Delivered Events",
            name_b="TCP Recovered",
            color_a=GREEN_OK,
            color_b=BLUE_INFO,
            title="Live Subscriber Delivered vs TCP Recovered Events",
            y_label="Events (count)",
        )

    def _refresh_live_exchange_chart(self) -> None:
        if self.client_ack_bars:
            labels: list[str] = []
            vals: list[float] = []
            colors: list[str] = []
            for cid, (p50, p95, p99) in sorted(self.client_ack_bars.items()):
                labels.extend([f"C{cid}\np50", f"C{cid}\np95", f"C{cid}\np99"])
                vals.extend([p50, p95, p99])
                colors.extend([GREEN_OK, BLUE_INFO, PURPLE_ANALYTIC])
            render_bar_chart(
                self.live_exchange_chart,
                labels,
                vals,
                colors,
                title="Workload Client Order ACK RTT Percentiles (µs)",
                y_label="ACK RTT (µs)",
                unit_suffix=" µs",
                decimals=0,
            )
        elif self.seq_history:
            self.live_exchange_chart.clear()
            xs = list(range(len(self.seq_history)))
            ys = list(self.seq_history)
            self.live_exchange_chart.plot(
                xs,
                ys,
                pen=pg.mkPen(CYAN_ACCENT, width=2.4),
                symbol="o",
                symbolSize=6,
                symbolBrush=pg.mkBrush(CYAN_ACCENT),
            )
            self.live_exchange_chart.getAxis("bottom").setTicks([])
            self.live_exchange_chart.setTitle("Live Exchange Published Sequence Progression (1 Hz)", color=TEXT_PRIMARY, size="10.5pt")
            ymax = max(ys, default=10)
            self.live_exchange_chart.setYRange(0, max(10.0, ymax * 1.2), padding=0)

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
                it = QTableWidgetItem(val)
                if col in (3, 4, 5, 6, 7):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.sub_cfg_table.setItem(idx, col, it)

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
        layout.setSpacing(12)
        layout.setContentsMargins(10, 10, 10, 10)

        net_box = QGroupBox("Multi-Node / Two-Laptop LAN Configuration && Generated CLI Commands")
        grid = QGridLayout(net_box)
        grid.setContentsMargins(14, 18, 14, 14)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self.lan_iface_combo = QComboBox()
        self.lan_iface_combo.setMinimumHeight(32)
        self.lan_iface_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        for label, ip in discover_lan_ipv4_addresses():
            self.lan_iface_combo.addItem(f"{label}: {ip}", ip)
        self.lan_iface_combo.currentIndexChanged.connect(self._on_lan_iface_selected)

        self.remote_host = QLineEdit(self.lan_iface_combo.currentData() or "127.0.0.1")
        self.remote_host.setMinimumHeight(32)

        self.remote_sub_iface = QLineEdit("<SUBSCRIBER_OWN_LAN_IP>")
        self.remote_sub_iface.setMinimumHeight(32)

        self.remote_port = self._spinbox(1024, 65535, 8080)
        self.remote_recovery_port = self._spinbox(1024, 65535, 9090)

        grid.addWidget(QLabel("<b>Detected Local IPv4 Interfaces:</b>"), 0, 0)
        grid.addWidget(self.lan_iface_combo, 0, 1)
        grid.addWidget(QLabel("<b>Exchange LAN IPv4 Address:</b>"), 0, 2)
        grid.addWidget(self.remote_host, 0, 3)

        grid.addWidget(QLabel("<b>Remote Subscriber LAN IP:</b>"), 1, 0)
        grid.addWidget(self.remote_sub_iface, 1, 1)
        ports_row = QHBoxLayout()
        ports_row.setSpacing(10)
        ports_row.addWidget(QLabel("<b>OE Port:</b>"))
        ports_row.addWidget(self.remote_port, 1)
        ports_row.addWidget(QLabel("<b>Recovery Port:</b>"))
        ports_row.addWidget(self.remote_recovery_port, 1)
        grid.addWidget(QLabel("<b>TCP Ports:</b>"), 1, 2)
        grid.addLayout(ports_row, 1, 3)

        self.start_lan_exchange_btn = QPushButton("Start Exchange Here on LAN Interface")
        self.start_lan_exchange_btn.setMinimumHeight(36)
        self.start_lan_exchange_btn.setStyleSheet(
            f"QPushButton {{ background: #065f46; color: #ecfdf5; border: 1px solid {GREEN_OK}; font-weight: 700; padding: 6px 14px; border-radius: 5px; }}"
            f"QPushButton:hover {{ background: #047857; }}"
        )
        self.start_lan_exchange_btn.clicked.connect(self._start_lan_exchange)

        self.test_conn_btn = QPushButton("Test TCP Reachability (Order-Entry + Recovery)")
        self.test_conn_btn.setMinimumHeight(36)
        self.test_conn_btn.setStyleSheet(
            f"QPushButton {{ background: #1e3a8a; color: #eff6ff; border: 1px solid {BLUE_INFO}; font-weight: 700; padding: 6px 14px; border-radius: 5px; }}"
            f"QPushButton:hover {{ background: #1d4ed8; }}"
        )
        self.test_conn_btn.clicked.connect(self._test_remote_connection)

        action_btns_row = QHBoxLayout()
        action_btns_row.setSpacing(12)
        action_btns_row.addWidget(self.start_lan_exchange_btn, 1)
        action_btns_row.addWidget(self.test_conn_btn, 1)
        grid.addLayout(action_btns_row, 2, 0, 1, 4)

        self.test_conn_label = QLabel("Not tested yet — click 'Test TCP Reachability' above")
        self.test_conn_label.setWordWrap(True)
        self.test_conn_label.setMinimumHeight(40)
        self.test_conn_label.setStyleSheet(
            f"padding: 8px 12px; background: {BG_ELEVATED}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER_SUBTLE}; border-radius: 5px; font-weight: 600;"
        )
        grid.addWidget(QLabel("<b>Reachability Status:</b>"), 3, 0)
        grid.addWidget(self.test_conn_label, 3, 1, 1, 3)

        cmd_style = (
            "QPlainTextEdit { font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace; font-size: 11.5px; "
            f"background: #070b12; color: {TEXT_PRIMARY}; padding: 6px 10px; border: 1px solid {BORDER_SUBTLE}; border-radius: 5px; }}"
        )
        self.cmd_exchange = QPlainTextEdit()
        self.cmd_exchange.setReadOnly(True)
        self.cmd_exchange.setMinimumHeight(64)
        self.cmd_exchange.setMaximumHeight(82)
        self.cmd_exchange.setStyleSheet(cmd_style)

        self.cmd_workload = QPlainTextEdit()
        self.cmd_workload.setReadOnly(True)
        self.cmd_workload.setMinimumHeight(64)
        self.cmd_workload.setMaximumHeight(82)
        self.cmd_workload.setStyleSheet(cmd_style)

        self.cmd_subscriber = QPlainTextEdit()
        self.cmd_subscriber.setReadOnly(True)
        self.cmd_subscriber.setMinimumHeight(68)
        self.cmd_subscriber.setMaximumHeight(88)
        self.cmd_subscriber.setStyleSheet(cmd_style)

        grid.addWidget(QLabel("<b>1. Exchange Command:</b>"), 4, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.cmd_exchange, 4, 1, 1, 3)

        grid.addWidget(QLabel("<b>2. Remote workload_client:</b>"), 5, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.cmd_workload, 5, 1, 1, 3)

        grid.addWidget(QLabel("<b>3. Remote reliable_subscriber:</b>"), 6, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.cmd_subscriber, 6, 1, 1, 3)

        copy_row = QHBoxLayout()
        copy_row.setSpacing(10)
        btn_update = QPushButton("Refresh Commands")
        btn_update.clicked.connect(self._update_remote_command)
        btn_copy_ex = QPushButton("Copy Exchange CLI")
        btn_copy_ex.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.cmd_exchange.toPlainText()))
        btn_copy_wk = QPushButton("Copy Workload CLI")
        btn_copy_wk.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.cmd_workload.toPlainText()))
        btn_copy_sub = QPushButton("Copy Subscriber CLI")
        btn_copy_sub.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.cmd_subscriber.toPlainText()))

        for b in (btn_update, btn_copy_ex, btn_copy_wk, btn_copy_sub):
            b.setMinimumHeight(34)
            copy_row.addWidget(b, 1)
        grid.addLayout(copy_row, 7, 0, 1, 4)

        layout.addWidget(net_box)

        peers_box = QGroupBox("Connected Remote Clients && Subscribers")
        peers_layout = QVBoxLayout(peers_box)
        peers_layout.setContentsMargins(10, 14, 10, 10)
        self.remote_peers_table = QTableWidget(0, 4)
        self.remote_peers_table.setHorizontalHeaderLabels(["Peer Type", "Peer ID", "Network Endpoint / Detail", "Connection State"])
        self._style_table(self.remote_peers_table, min_height=175, row_height=30)
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
                f"padding: 8px 12px; background: #064e3b; color: {GREEN_BRIGHT}; border: 1px solid {GREEN_OK}; border-radius: 5px; font-weight: 700;"
            )
        else:
            self.test_conn_label.setStyleSheet(
                f"padding: 8px 12px; background: #450a0a; color: {RED_BRIGHT}; border: 1px solid {RED_ERR}; border-radius: 5px; font-weight: 700;"
            )

    def _manual_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setSpacing(12)
        layout.setContentsMargins(10, 10, 10, 10)

        box = QGroupBox("Submit Real TCP Limit Order")
        grid = QGridLayout(box)
        grid.setContentsMargins(14, 18, 14, 14)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        self.manual_host = QLineEdit("127.0.0.1")
        self.manual_host.setMinimumHeight(32)
        self.manual_port = self._spinbox(1024, 65535, 8080)
        self.manual_symbol = QComboBox()
        self.manual_symbol.addItems(["AAPL", "GOOGL", "AMZN", "NFLX", "META"])
        self.manual_symbol.setMinimumHeight(32)
        self.manual_side = QComboBox()
        self.manual_side.addItems(["BUY", "SELL"])
        self.manual_side.setMinimumHeight(32)
        self.manual_price = QDoubleSpinBox()
        self.manual_price.setRange(0.01, 1_000_000.00)
        self.manual_price.setDecimals(2)
        self.manual_price.setValue(500.00)
        self.manual_price.setMinimumHeight(32)
        self.manual_quantity = self._spinbox(1, 1_000_000, 10)

        submit = QPushButton("Send Limit Order Over TCP")
        submit.setMinimumHeight(38)
        submit.setStyleSheet(
            f"QPushButton {{ background: #1e3a8a; color: #eff6ff; border: 1px solid {BLUE_INFO}; font-weight: 700; font-size: 13px; border-radius: 5px; }}"
            f"QPushButton:hover {{ background: #1d4ed8; }}"
        )
        submit.clicked.connect(self._submit_manual)

        self.manual_result = QPlainTextEdit("No manual request sent")
        self.manual_result.setReadOnly(True)
        self.manual_result.setMinimumHeight(76)
        self.manual_result.setMaximumHeight(110)

        self.fanout_label = QLabel("Fan-out: waiting for manual order")
        self.fanout_label.setWordWrap(True)
        self.fanout_label.setMinimumHeight(40)
        self.fanout_label.setStyleSheet(
            f"padding: 6px 10px; background: {BG_ELEVATED}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER_SUBTLE}; border-radius: 5px; font-weight: 600;"
        )

        grid.addWidget(QLabel("<b>Server IPv4:</b>"), 0, 0)
        grid.addWidget(self.manual_host, 0, 1)
        grid.addWidget(QLabel("<b>TCP Port:</b>"), 1, 0)
        grid.addWidget(self.manual_port, 1, 1)
        grid.addWidget(QLabel("<b>Instrument:</b>"), 2, 0)
        grid.addWidget(self.manual_symbol, 2, 1)
        grid.addWidget(QLabel("<b>Side:</b>"), 3, 0)
        grid.addWidget(self.manual_side, 3, 1)
        grid.addWidget(QLabel("<b>Limit Price ($):</b>"), 4, 0)
        grid.addWidget(self.manual_price, 4, 1)
        grid.addWidget(QLabel("<b>Quantity:</b>"), 5, 0)
        grid.addWidget(self.manual_quantity, 5, 1)
        grid.addWidget(submit, 6, 0, 1, 2)
        grid.addWidget(QLabel("<b>Latest Response:</b>"), 7, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.manual_result, 7, 1)
        grid.addWidget(QLabel("<b>Subscriber Fan-Out:</b>"), 8, 0)
        grid.addWidget(self.fanout_label, 8, 1)
        grid.setRowStretch(9, 1)

        right_col = QGroupBox("Manual Order TCP Round-Trip Latency && History")
        right_layout = QVBoxLayout(right_col)
        right_layout.setContentsMargins(10, 14, 10, 10)
        right_layout.setSpacing(10)
        self.manual_rtt_chart = self._make_chart("Manual Order TCP Round-Trip Time (ms)", "RTT (ms)", "Order #", 240)
        render_empty_chart(self.manual_rtt_chart, "Submit a TCP limit order on the left to plot round-trip latency")
        self.manual_history_table = QTableWidget(0, 6)
        self.manual_history_table.setHorizontalHeaderLabels(["Order #", "Symbol", "Side", "Price ($)", "Qty", "Response & RTT"])
        self._style_table(self.manual_history_table, min_height=200, row_height=30)
        right_layout.addWidget(self.manual_rtt_chart)
        right_layout.addWidget(self.manual_history_table)

        layout.addWidget(box, 2)
        layout.addWidget(right_col, 3)
        return page

    def _results_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        refresh = QPushButton("Refresh Saved Runs")
        refresh.setMinimumHeight(34)
        refresh.clicked.connect(self._refresh_saved_results)
        self.open_in_dashboard_btn = QPushButton("Open Selected Run in Reliability Experiments (All 6 Charts)")
        self.open_in_dashboard_btn.setMinimumHeight(34)
        self.open_in_dashboard_btn.setStyleSheet(
            f"QPushButton {{ background: #1e3a8a; color: #eff6ff; border: 1px solid {BLUE_INFO}; font-weight: 700; padding: 6px 14px; border-radius: 5px; }}"
            f"QPushButton:hover {{ background: #1d4ed8; }}"
        )
        self.open_in_dashboard_btn.clicked.connect(self._open_selected_in_dashboard)
        btn_row.addWidget(refresh)
        btn_row.addWidget(self.open_in_dashboard_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        top_split = QHBoxLayout()
        top_split.setSpacing(12)
        self.saved_results = QListWidget()
        self.saved_results.itemSelectionChanged.connect(self._preview_saved_result)
        self.saved_results.setMinimumHeight(230)

        self.saved_result_chart = self._make_chart("Selected Run Metric Preview", "Value", "Subscriber / Config", 230)
        render_empty_chart(self.saved_result_chart, "Select a saved run on the left to preview its metrics")
        top_split.addWidget(self.saved_results, 2)
        top_split.addWidget(self.saved_result_chart, 3)
        layout.addLayout(top_split)

        self.saved_result_table = QTableWidget(0, 0)
        self.saved_result_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._style_table(self.saved_result_table, min_height=200, row_height=30)
        layout.addWidget(self.saved_result_table)

        self.result_preview = QPlainTextEdit()
        self.result_preview.setReadOnly(True)
        self.result_preview.setMinimumHeight(80)
        self.result_preview.setMaximumHeight(110)
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
            render_empty_chart(self.saved_result_chart, "Selected artifact contains no tabular rows")
            return
        cols = list(rows[0].keys())[:14]
        self.saved_result_table.setColumnCount(len(cols))
        self.saved_result_table.setHorizontalHeaderLabels(cols)
        self.saved_result_table.setRowCount(len(rows))
        for r_idx, row in enumerate(rows):
            for c_idx, col_name in enumerate(cols):
                val_str = str(row.get(col_name, ""))
                it = QTableWidgetItem(val_str)
                if val_str.replace(".", "", 1).replace("-", "", 1).isdigit():
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.saved_result_table.setItem(r_idx, c_idx, it)
        self.saved_result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        candidate_cols = [
            "delivered", "delivered_mean", "events_needing_recovery_pct_mean",
            "server_wait_p99_ns_mean", "snapshot_recoveries_mean", "acknowledgements", "recovered_events",
        ]
        chosen_col = next((c for c in candidate_cols if c in rows[0]), None)
        if chosen_col:
            labels = [
                str(r.get("subscriber_id") or r.get("mode_group") or r.get("recovery_policy") or r.get("client_id") or f"R{i+1}")
                for i, r in enumerate(rows[:12])
            ]
            vals = []
            for r in rows[:12]:
                try:
                    vals.append(float(r.get(chosen_col, 0) or 0))
                except ValueError:
                    vals.append(0.0)
            render_bar_chart(
                self.saved_result_chart,
                labels,
                vals,
                BLUE_INFO,
                title=f"Preview: {title} ({chosen_col})",
                y_label=chosen_col,
            )

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
        self.kpi_active_subs.set_value(str(len(specs)), f"Grace {self.sub_grace_spin.value()} ms", CYAN_ACCENT)
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
            item.setForeground(QColor(RED_BRIGHT))
        elif status.startswith("Connected") or status == "completed":
            item.setForeground(QColor(GREEN_BRIGHT))
        self.live_sub_table.setItem(row, 1, item)

    def _on_subscriber_stats(self, sid: str, kv: dict[str, str]) -> None:
        row = self.sub_row_by_id.get(sid)
        if row is None:
            return
        feed_str = f"A:{kv.get('feed_a','?')}/B:{kv.get('feed_b','?')}"
        self.live_sub_table.setItem(row, 2, QTableWidgetItem(feed_str))
        for col, key in (
            (3, "last_seq"),
            (4, "delivered"),
            (5, "duplicates"),
            (6, "gap_ranges"),
            (7, "recovered"),
            (8, "snapshot_recoveries"),
            (9, "lost_so_far"),
        ):
            it = QTableWidgetItem(kv.get(key, "0"))
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.live_sub_table.setItem(row, col, it)
        self._refresh_live_sub_chart()
        self._update_fanout_indicator()

    def _on_subscriber_result(self, sid: str, kv: dict[str, str]) -> None:
        row = self.sub_row_by_id.get(sid)
        if row is None:
            return
        for col, key in (
            (4, "delivered"),
            (5, "duplicates_suppressed"),
            (6, "gap_ranges_requested"),
            (7, "recovered_events"),
            (8, "snapshot_recoveries"),
            (9, "permanently_lost"),
        ):
            it = QTableWidgetItem(kv.get(key, "0"))
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.live_sub_table.setItem(row, col, it)

        bm = kv.get("book_matches_exchange", "n/a")
        bm_item = QTableWidgetItem("MATCH (1)" if bm == "1" else f"MISMATCH ({bm})")
        bm_item.setForeground(QColor(GREEN_BRIGHT) if bm == "1" else QColor(RED_BRIGHT))
        bm_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.live_sub_table.setItem(row, 10, bm_item)

        n_samples = int(kv.get("recovery_n", "0") or 0)
        insuff = kv.get("insufficient_samples", "1") == "1"
        p50_us = float(kv.get("recovery_p50_ns", "0") or 0) / 1000.0
        p99_us = float(kv.get("recovery_p99_ns", "0") or 0) / 1000.0
        lat_txt = f"{p50_us:.1f}/{p99_us:.1f} µs (n={n_samples})"
        lat_item = QTableWidgetItem(lat_txt if n_samples > 0 else "n=0 (no TCP recovery)")
        if insuff:
            lat_item.setForeground(QColor(TEXT_MUTED))
        self.live_sub_table.setItem(row, 11, lat_item)
        self._refresh_live_sub_chart()

    def _on_exchange_stats(self, kv: dict[str, str]) -> None:
        seq = int(kv.get("latest_seq", "0") or 0)
        md_s = int(kv.get("md_sent", "0") or 0)
        q_depth = kv.get("queue_depth", "0")
        subs = kv.get("subscribers", "0")
        self.seq_history.append(seq)
        self.md_sent_history.append(md_s)

        self.kpi_latest_seq.set_value(f"{seq:,}", "Authoritative Monotonic Sequence", GREEN_BRIGHT)
        self.kpi_queue_depth.set_value(str(q_depth), f"Policy: {self.recovery_policy_combo.currentText()}", AMBER_WARN if int(q_depth or 0) > 0 else TEXT_SECONDARY)
        self.kpi_active_subs.set_value(str(subs), "Active Subscribers", CYAN_ACCENT)
        self.kpi_md_sent.set_value(f"{md_s:,}", f"Dual Feed: {'ON' if self.dual_feed_check.isChecked() else 'OFF'}", PURPLE_ANALYTIC)

        self.exchange_live_stats_label.setText(
            f"latest_seq={seq} | queue_depth={q_depth} | subscribers={subs} | md_sent={md_s}"
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
            it = QTableWidgetItem(val)
            if c_idx == 2:
                it.setForeground(QColor(GREEN_BRIGHT if val == "BUY" else RED_BRIGHT))
            self.manual_history_table.setItem(row, c_idx, it)

        if rtt_ms > 0:
            self.manual_rtt_history.append((f"#{row + 1}", rtt_ms))
            lbls = [k for k, _ in self.manual_rtt_history]
            ys = [v for _, v in self.manual_rtt_history]
            render_bar_chart(
                self.manual_rtt_chart,
                lbls,
                ys,
                BLUE_INFO,
                title="Manual Order TCP Round-Trip Time (ms)",
                y_label="RTT (ms)",
                unit_suffix=" ms",
                decimals=2,
            )

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
        is_ready = status == "Ready"
        is_starting = status == "Starting"
        self.start_server_button.setEnabled(not (is_ready or is_starting))

        if is_ready:
            bg, fg = "#064e3b", GREEN_BRIGHT
            self.kpi_exchange_status.set_value("ONLINE", f"OE TCP :{self.port.value()} | MD {self.md_interface.text()}", GREEN_BRIGHT)
            self.kpi_recovery_status.set_value("LISTENING", f"Recovery TCP :{self.recovery_port.value()} ({self.recovery_policy_combo.currentText()})", BLUE_INFO)
        elif is_starting:
            bg, fg = "#78350f", AMBER_WARN
            self.kpi_exchange_status.set_value("STARTING", "Initializing sockets...", AMBER_WARN)
            self.kpi_recovery_status.set_value("STARTING", "Binding recovery port...", AMBER_WARN)
        elif status == "Stopped":
            bg, fg = BG_ELEVATED, TEXT_SECONDARY
            self.kpi_exchange_status.set_value("STOPPED", "Order-Entry & Multicast Publisher", TEXT_SECONDARY)
            self.kpi_recovery_status.set_value("STANDBY", "TCP Retransmission & Snapshot Server", TEXT_SECONDARY)
        else:
            bg, fg = "#450a0a", RED_BRIGHT
            self.kpi_exchange_status.set_value(status.upper(), "Check console log for details", RED_BRIGHT)

        self.server_status.setStyleSheet(
            f"font-weight: 700; background: {bg}; color: {fg}; padding: 3px 10px; border-radius: 4px;"
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
            it.setForeground(QColor(GREEN_BRIGHT))
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
