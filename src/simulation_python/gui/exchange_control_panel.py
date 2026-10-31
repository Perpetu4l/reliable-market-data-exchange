"""Network and subscriber control room for the C++ exchange."""

from __future__ import annotations

import csv
import itertools
import json
import socket
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
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
    completed = pyqtSignal(str, int)  # message, executions_count

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
                self.completed.emit(f"Accepted: ORDER_ACK, client RTT {rtt_ms:.3f} ms{exec_suffix}", len(executions))
            elif response.message_type == ORDER_REJECT_TYPE:
                self.completed.emit(f"Rejected by exchange: ORDER_REJECT, client RTT {rtt_ms:.3f} ms", 0)
            else:
                self.completed.emit(f"Unexpected server response {response.message_type}", 0)
        except Exception as error:
            self.completed.emit(f"Manual request failed: {error}", 0)


class ExchangeControlPanel(QWidget):
    """Control room for local simulation, distributed network, manual orders, and saved results."""

    open_in_dashboard_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.controller = ExchangeController(self)
        self.manual_ids = itertools.count(1)
        self.manual_thread: ManualOrderThread | None = None
        self.seq_before_manual_order: int = 0
        self.sub_row_by_id: dict[str, int] = {}
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
        return widget

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("Exchange Control — Live Processes & Market-Data Subscribers")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._scroll(self._local_tab()), "Local Simulation")
        self.tabs.addTab(self._scroll(self._distributed_tab()), "Distributed Network")
        self.tabs.addTab(self._scroll(self._manual_tab()), "Manual Testing")
        self.tabs.addTab(self._scroll(self._results_tab()), "Previous Results")
        layout.addWidget(self.tabs, 4)

        self.event_log = QPlainTextEdit()
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(400)
        self.event_log.setPlaceholderText("Real exchange, subscriber, and client process events appear here.")
        layout.addWidget(QLabel("Current-session event log"))
        layout.addWidget(self.event_log, 1)

    def _local_tab(self) -> QWidget:
        page = QWidget()
        layout = QGridLayout(page)

        # 1. Exchange & Server Configuration
        config = QGroupBox("Exchange & Recovery Server Configuration")
        form = QFormLayout(config)
        self.port = self._spinbox(1024, 65535, 8080)
        self.md_interface = QLineEdit("127.0.0.1")
        self.dual_feed_check = QCheckBox("Enable Dual Feed (Feed A + Feed B)")
        self.dual_feed_check.setChecked(True)
        self.recovery_port = self._spinbox(1024, 65535, 9090)
        self.history_spin = self._spinbox(0, 1_000_000, 65536)
        self.snap_interval_spin = self._spinbox(0, 1_000_000, 100)
        self.snap_retention_spin = self._spinbox(1, 1000, 8)
        self.snap_on_demand_check = QCheckBox("On-demand snapshot fallback")
        self.snap_on_demand_check.setChecked(True)
        self.recovery_policy_combo = QComboBox()
        self.recovery_policy_combo.addItems(["fcfs", "round-robin"])
        self.recovery_workers_spin = self._spinbox(1, 16, 1)
        self.service_delay_req_spin = self._spinbox(0, 1_000_000, 0)
        self.service_delay_req_spin.setSuffix(" µs")
        self.service_delay_event_spin = self._spinbox(0, 100_000, 0)
        self.service_delay_event_spin.setSuffix(" µs")
        self.tamper_combo = QComboBox()
        self.tamper_combo.addItems(["none", "book_value", "sequence", "previous_hash", "hash"])
        self.heartbeat_spin = self._spinbox(50, 10_000, 500)
        self.heartbeat_spin.setSuffix(" ms")

        form.addRow("Order-entry TCP port", self.port)
        form.addRow("Multicast interface IPv4", self.md_interface)
        form.addRow(self.dual_feed_check)
        form.addRow("Recovery TCP port", self.recovery_port)
        form.addRow("History capacity (events)", self.history_spin)
        form.addRow("Snapshot interval (events)", self.snap_interval_spin)
        form.addRow("Snapshot retention", self.snap_retention_spin)
        form.addRow(self.snap_on_demand_check)
        form.addRow("Recovery scheduler policy", self.recovery_policy_combo)
        form.addRow("Recovery workers", self.recovery_workers_spin)
        form.addRow("Modelled server capacity (delay/req)", self.service_delay_req_spin)
        form.addRow("Modelled server capacity (delay/event)", self.service_delay_event_spin)
        form.addRow("Snapshot tamper (experiment-only)", self.tamper_combo)
        form.addRow("Heartbeat interval", self.heartbeat_spin)

        # 2. Workload & Lifecycle Controls
        actions = QGroupBox("Workload & Lifecycle Controls")
        action_form = QFormLayout(actions)
        self.client_count = self._spinbox(1, 32, 2)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0.1, 50_000.0)
        self.rate.setDecimals(1)
        self.rate.setValue(400.0)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.5, 3600.0)
        self.duration.setDecimals(1)
        self.duration.setValue(4.0)
        self.symbol = QComboBox()
        self.symbol.addItems(["AAPL", "GOOGL", "AMZN", "NFLX", "META"])
        self.seed = self._spinbox(0, 2_147_483_647, 11)

        action_form.addRow("Workload clients", self.client_count)
        action_form.addRow("Rate per client (orders/s)", self.rate)
        action_form.addRow("Duration (s)", self.duration)
        action_form.addRow("Instrument", self.symbol)
        action_form.addRow("Workload seed", self.seed)

        self.start_server_button = QPushButton("1. Start exchange")
        self.start_subscribers_button = QPushButton("2. Start subscribers")
        self.stop_subscribers_button = QPushButton("Stop subscribers (SIGINT -> reconcile)")
        self.start_workload_button = QPushButton("3. Start workload")
        self.stop_button = QPushButton("Stop all / clean up")

        self.start_server_button.clicked.connect(self._start_server)
        self.start_subscribers_button.clicked.connect(self._start_subscribers)
        self.stop_subscribers_button.clicked.connect(self.controller.stop_subscribers)
        self.start_workload_button.clicked.connect(self._start_workload)
        self.stop_button.clicked.connect(self.controller.stop_server)

        action_form.addRow(self.start_server_button)
        action_form.addRow(self.start_subscribers_button)
        action_form.addRow(self.stop_subscribers_button)
        action_form.addRow(self.start_workload_button)
        action_form.addRow(self.stop_button)

        self.server_status = QLabel("Stopped")
        self.exchange_live_stats_label = QLabel("latest_seq=0 | queue_depth=0 | subscribers=0")
        action_form.addRow("Server status:", self.server_status)
        action_form.addRow("Exchange stats:", self.exchange_live_stats_label)

        # 3. Market-data subscribers configuration group
        sub_cfg_box = QGroupBox("Market-Data Subscribers Configuration (1–32 processes)")
        sub_cfg_layout = QVBoxLayout(sub_cfg_box)
        top_sub_row = QHBoxLayout()
        self.sub_count_spin = self._spinbox(1, 32, 5)
        self.sub_count_spin.valueChanged.connect(self._sync_subscriber_cfg_rows)
        self.sub_grace_spin = self._spinbox(0, 1000, 2)
        self.sub_grace_spin.setSuffix(" ms")
        self.sub_retries_spin = self._spinbox(0, 20, 5)
        self.sub_idle_spin = self._spinbox(0, 30_000, 600)
        self.sub_idle_spin.setSuffix(" ms")

        top_sub_row.addWidget(QLabel("Subscriber count:"))
        top_sub_row.addWidget(self.sub_count_spin)
        top_sub_row.addWidget(QLabel("Gap grace:"))
        top_sub_row.addWidget(self.sub_grace_spin)
        top_sub_row.addWidget(QLabel("Retries:"))
        top_sub_row.addWidget(self.sub_retries_spin)
        top_sub_row.addWidget(QLabel("Stop-when-idle:"))
        top_sub_row.addWidget(self.sub_idle_spin)
        sub_cfg_layout.addLayout(top_sub_row)

        self.sub_cfg_table = QTableWidget(0, 9)
        self.sub_cfg_table.setHorizontalHeaderLabels([
            "ID", "Feeds (a/ab)", "Loss Model", "Drop A", "Drop B", "Corr Loss", "Seed", "Concurrency", "Role",
        ])
        self.sub_cfg_table.horizontalHeader().setStretchLastSection(True)
        self.sub_cfg_table.setMinimumHeight(140)
        sub_cfg_layout.addWidget(self.sub_cfg_table)
        self._sync_subscriber_cfg_rows(self.sub_count_spin.value())

        # 4. Live Subscriber Telemetry Table
        live_sub_box = QGroupBox("Live Subscriber Status & Final Reconciliation Table")
        live_sub_layout = QVBoxLayout(live_sub_box)
        self.live_sub_table = QTableWidget(0, 12)
        self.live_sub_table.setHorizontalHeaderLabels([
            "Subscriber", "State", "Feed A/B", "Last Seq", "Delivered",
            "Duplicates", "Gap Ranges", "Recoveries", "Snapshots",
            "Lost", "Book Match", "TCP Latency p50/p99 (µs)",
        ])
        self.live_sub_table.horizontalHeader().setStretchLastSection(True)
        self.live_sub_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.live_sub_table.setMinimumHeight(150)
        live_sub_layout.addWidget(self.live_sub_table)

        # 5. Workload Clients Table
        clients_box = QGroupBox("Order-Entry Workload Clients")
        client_layout = QVBoxLayout(clients_box)
        self.client_status_label = QLabel("No workload started")
        self.result_label = QLabel("No measured workload result yet")
        self.result_label.setWordWrap(True)
        client_layout.addWidget(self.client_status_label)
        client_layout.addWidget(self.result_label)
        self.client_table = QTableWidget(0, 2)
        self.client_table.setHorizontalHeaderLabels(["Client", "Observed status"])
        self.client_table.horizontalHeader().setStretchLastSection(True)
        self.client_table.verticalHeader().setVisible(False)
        self.client_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.client_table.setMaximumHeight(110)
        client_layout.addWidget(self.client_table)

        layout.addWidget(config, 0, 0)
        layout.addWidget(actions, 0, 1)
        layout.addWidget(sub_cfg_box, 1, 0, 1, 2)
        layout.addWidget(live_sub_box, 2, 0, 1, 2)
        layout.addWidget(clients_box, 3, 0, 1, 2)
        return page

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

        net_box = QGroupBox("Two-Laptop / LAN Distributed Configuration")
        form = QFormLayout(net_box)
        self.lan_iface_combo = QComboBox()
        for label, ip in discover_lan_ipv4_addresses():
            self.lan_iface_combo.addItem(f"{label}: {ip}", ip)
        self.lan_iface_combo.currentIndexChanged.connect(self._on_lan_iface_selected)

        self.remote_host = QLineEdit(self.lan_iface_combo.currentData() or "127.0.0.1")
        self.remote_sub_iface = QLineEdit("<SUBSCRIBER_OWN_LAN_IP>")
        self.remote_port = self._spinbox(1024, 65535, 8080)
        self.remote_recovery_port = self._spinbox(1024, 65535, 9090)

        self.start_lan_exchange_btn = QPushButton("Start exchange here for the network")
        self.start_lan_exchange_btn.clicked.connect(self._start_lan_exchange)
        self.test_conn_btn = QPushButton("Test connection to exchange (OE + Recovery TCP)")
        self.test_conn_btn.clicked.connect(self._test_remote_connection)
        self.test_conn_label = QLabel("Not tested yet")
        self.test_conn_label.setWordWrap(True)

        form.addRow("Detected local IPv4 interfaces", self.lan_iface_combo)
        form.addRow("Exchange LAN IPv4 address", self.remote_host)
        form.addRow("Remote subscriber's own LAN IP", self.remote_sub_iface)
        form.addRow("Order-Entry TCP port", self.remote_port)
        form.addRow("Recovery TCP port", self.remote_recovery_port)
        form.addRow(self.start_lan_exchange_btn)
        form.addRow(self.test_conn_btn)
        form.addRow("Connection check result", self.test_conn_label)

        self.cmd_exchange = QPlainTextEdit()
        self.cmd_exchange.setReadOnly(True)
        self.cmd_exchange.setMaximumHeight(64)
        self.cmd_workload = QPlainTextEdit()
        self.cmd_workload.setReadOnly(True)
        self.cmd_workload.setMaximumHeight(64)
        self.cmd_subscriber = QPlainTextEdit()
        self.cmd_subscriber.setReadOnly(True)
        self.cmd_subscriber.setMaximumHeight(74)

        form.addRow("1. Exchange command", self.cmd_exchange)
        form.addRow("2. Remote workload_client command", self.cmd_workload)
        form.addRow("3. Remote reliable_subscriber command", self.cmd_subscriber)

        copy_row = QHBoxLayout()
        btn_update = QPushButton("Update commands")
        btn_update.clicked.connect(self._update_remote_command)
        btn_copy_ex = QPushButton("Copy exchange command")
        btn_copy_ex.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.cmd_exchange.toPlainText()))
        btn_copy_wk = QPushButton("Copy workload_client command")
        btn_copy_wk.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.cmd_workload.toPlainText()))
        btn_copy_sub = QPushButton("Copy reliable_subscriber command")
        btn_copy_sub.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.cmd_subscriber.toPlainText()))
        copy_row.addWidget(btn_update)
        copy_row.addWidget(btn_copy_ex)
        copy_row.addWidget(btn_copy_wk)
        copy_row.addWidget(btn_copy_sub)
        form.addRow(copy_row)
        layout.addWidget(net_box)

        peers_box = QGroupBox("Live Connected Remote Clients & Subscribers")
        peers_layout = QVBoxLayout(peers_box)
        self.remote_peers_table = QTableWidget(0, 4)
        self.remote_peers_table.setHorizontalHeaderLabels(["Type", "ID", "Address / Reason", "Status"])
        self.remote_peers_table.horizontalHeader().setStretchLastSection(True)
        self.remote_peers_table.setMinimumHeight(120)
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
        for label, p in (("Order-Entry TCP", oe_p), ("Recovery TCP", rec_p)):
            try:
                with socket.create_connection((host, p), timeout=1.5):
                    results.append(f"{label} ({host}:{p}): OK (reachable)")
            except ConnectionRefusedError:
                results.append(f"{label} ({host}:{p}): FAILED — connection refused (exchange not running on port {p})")
            except socket.timeout:
                results.append(f"{label} ({host}:{p}): FAILED — timed out (firewall blocking port {p} or wrong IP)")
            except OSError as exc:
                results.append(f"{label} ({host}:{p}): FAILED — {exc} (check LAN IP / interface)")
        self.test_conn_label.setText(" | ".join(results))

    def _manual_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        box = QGroupBox("Submit One Real TCP Limit Order & Observe Subscriber Fan-Out")
        form = QFormLayout(box)
        self.manual_host = QLineEdit("127.0.0.1")
        self.manual_port = self._spinbox(1024, 65535, 8080)
        self.manual_symbol = QComboBox()
        self.manual_symbol.addItems(["AAPL", "GOOGL", "AMZN", "NFLX", "META"])
        self.manual_side = QComboBox()
        self.manual_side.addItems(["BUY", "SELL"])
        self.manual_price = QDoubleSpinBox()
        self.manual_price.setRange(0.01, 1_000_000.00)
        self.manual_price.setDecimals(2)
        self.manual_price.setValue(500.00)
        self.manual_quantity = self._spinbox(1, 1_000_000, 10)
        submit = QPushButton("Send through exchange TCP path")
        submit.clicked.connect(self._submit_manual)
        self.manual_result = QPlainTextEdit("No manual request sent")
        self.manual_result.setReadOnly(True)
        self.manual_result.setMaximumHeight(80)
        self.fanout_label = QLabel("Fan-out indicator: waiting for manual order submission")
        self.fanout_label.setWordWrap(True)

        form.addRow("Server", self.manual_host)
        form.addRow("TCP port", self.manual_port)
        form.addRow("Instrument", self.manual_symbol)
        form.addRow("Side", self.manual_side)
        form.addRow("Limit price (USD)", self.manual_price)
        form.addRow("Quantity", self.manual_quantity)
        form.addRow(submit)
        form.addRow("Result", self.manual_result)
        form.addRow("Market-data fan-out", self.fanout_label)
        layout.addWidget(box)
        layout.addStretch()
        return page

    def _results_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        explanation = QLabel(
            "Lists saved workload CSVs, deterministic reliability CSVs, aggregated preset CSVs, and live run directories "
            "(results/live/<run_id>/). Provenance columns (transport, loss_injection, git_commit, schema_version) are shown."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        btn_row = QHBoxLayout()
        refresh = QPushButton("Refresh saved results")
        refresh.clicked.connect(self._refresh_saved_results)
        self.open_in_dashboard_btn = QPushButton("Open in Reliability dashboard")
        self.open_in_dashboard_btn.clicked.connect(self._open_selected_in_dashboard)
        btn_row.addWidget(refresh)
        btn_row.addWidget(self.open_in_dashboard_btn)
        layout.addLayout(btn_row)

        self.saved_results = QListWidget()
        self.saved_results.itemSelectionChanged.connect(self._preview_saved_result)
        self.result_preview = QPlainTextEdit()
        self.result_preview.setReadOnly(True)
        self.saved_results.setMaximumHeight(160)
        self.result_preview.setMinimumHeight(220)
        layout.addWidget(self.saved_results)
        layout.addWidget(self.result_preview, 1)
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

    def _preview_saved_result(self) -> None:
        selected = self.saved_results.selectedItems()
        if not selected:
            return
        path = Path(selected[0].data(Qt.ItemDataRole.UserRole))
        try:
            if path.is_dir():
                summary_csv = path / "run-summary.csv"
                run_json = path / "run.json"
                parts: list[str] = [f"=== Run Directory: {path} ==="]
                if summary_csv.is_file():
                    with summary_csv.open(newline="", encoding="utf-8") as f:
                        rows = list(csv.DictReader(f))
                    if rows:
                        r0 = rows[0]
                        parts.append(
                            f"Provenance: schema_version={r0.get('schema_version','?')}, "
                            f"transport={r0.get('transport','?')}, "
                            f"loss_injection={r0.get('loss_injection','?')}, "
                            f"git_commit={r0.get('git_commit','?')}"
                        )
                    parts.append("\n--- run-summary.csv ---")
                    parts.append(summary_csv.read_text(encoding="utf-8")[:4000])
                elif run_json.is_file():
                    parts.append(run_json.read_text(encoding="utf-8")[:4000])
                self.result_preview.setPlainText("\n".join(parts))
            else:
                self.result_preview.setPlainText(path.read_text(encoding="utf-8")[:6000])
        except OSError as error:
            self.result_preview.setPlainText(f"Could not read result: {error}")

    def _open_selected_in_dashboard(self) -> None:
        selected = self.saved_results.selectedItems()
        if not selected:
            return
        target = str(selected[0].data(Qt.ItemDataRole.UserRole))
        self.open_in_dashboard_requested.emit(target)

    def _start_server(self) -> None:
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
        if insuff:
            lat_txt += " [insufficient <100]"
        lat_item = QTableWidgetItem(lat_txt if n_samples > 0 else "n=0 (no TCP recovery)")
        if insuff:
            lat_item.setForeground(QColor("#777777"))
        self.live_sub_table.setItem(row, 11, lat_item)

    def _on_exchange_stats(self, kv: dict[str, str]) -> None:
        self.exchange_live_stats_label.setText(
            f"latest_seq={kv.get('latest_seq','0')} | "
            f"queue_depth={kv.get('queue_depth','0')} | "
            f"subscribers={kv.get('subscribers','0')} | "
            f"md_sent={kv.get('md_sent','0')}"
        )
        self._update_fanout_indicator()

    def _on_remote_peer_event(self, kind: str, peer_id: str, addr_or_reason: str, state: str) -> None:
        # Update existing row or append new row in remote_peers_table
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
        self.client_table.setRowCount(self.client_count.value())
        for client_id in range(1, self.client_count.value() + 1):
            self.client_table.setItem(client_id - 1, 0, QTableWidgetItem(str(client_id)))
            self.client_table.setItem(client_id - 1, 1, QTableWidgetItem("Waiting to start"))
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
        self.manual_result.setPlainText("Sending real TCP request...")
        self.manual_thread = ManualOrderThread(
            self.manual_host.text().strip(), self.manual_port.value(), 90_001, next(self.manual_ids),
            self.manual_symbol.currentText(), side, round(self.manual_price.value() * 100), self.manual_quantity.value(),
        )
        self.manual_thread.completed.connect(self._on_manual_completed)
        self.manual_thread.start()

    def _on_manual_completed(self, text: str, _exec_count: int) -> None:
        self.manual_result.setPlainText(text)
        self._append_log(f"manual: {text}")
        self._update_fanout_indicator()

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
            f"Exchange latest_seq before order: {self.seq_before_manual_order} -> current latest_seq: {new_seq} | "
            f"Running subscribers with last_seq >= {new_seq}: {caught_up}/{total_subs}"
        )

    def _set_client_status(self, client_id: int, status: str) -> None:
        self.client_status_label.setText(f"Client {client_id}: {status}")
        if client_id - 1 < self.client_table.rowCount():
            self.client_table.setItem(client_id - 1, 1, QTableWidgetItem(status))

    def _set_server_status(self, status: str) -> None:
        self.server_status.setText(status)
        colour = {"Ready": "#197b30", "Starting": "#9a6700", "Stopped": "#666"}.get(status, "#b42318")
        self.server_status.setStyleSheet(f"font-weight: 700; color: {colour};")

    def _show_result(self, result: dict) -> None:
        self.result_label.setText(
            "Client {client_id}: sent={sent}, acknowledgements={acknowledgements}, "
            "rejected={rejected}, executions={executions}, timed out={timed_out}; "
            "ack p50/p95/p99 = {ack_p50_us}/{ack_p95_us}/{ack_p99_us} µs".format(**result)
        )

    def _append_log(self, message: str) -> None:
        self.event_log.appendPlainText(
            f"[{datetime.now().strftime('%H:%M:%S')}] [Exchange Control] {message}"
        )

    def shutdown(self) -> None:
        self.controller.shutdown()
        if self.manual_thread and self.manual_thread.isRunning():
            self.manual_thread.wait(1000)
