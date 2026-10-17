"""Network controls for the real C++ exchange path."""

from __future__ import annotations

import itertools
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
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
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .exchange_controller import ExchangeController, REPOSITORY_ROOT
from .network_protocol import BUY, SELL, ORDER_ACK_TYPE, ORDER_REJECT_TYPE, submit_limit_order


class ManualOrderThread(QThread):
    completed = pyqtSignal(str)

    def __init__(self, host: str, port: int, client_id: int, order_id: int, symbol: str,
                 side: int, price_cents: int, quantity: int):
        super().__init__()
        self.parameters = (host, port, client_id, order_id, symbol, side, price_cents, quantity)

    def run(self) -> None:
        try:
            response, rtt_ms = submit_limit_order(*self.parameters)
            if response.message_type == ORDER_ACK_TYPE:
                self.completed.emit(f"Accepted: ORDER_ACK, client RTT {rtt_ms:.3f} ms")
            elif response.message_type == ORDER_REJECT_TYPE:
                self.completed.emit(f"Rejected by exchange, client RTT {rtt_ms:.3f} ms")
            else:
                self.completed.emit(f"Unexpected server response {response.message_type}")
        except Exception as error:  # Surface genuine connection / protocol errors in the UI.
            self.completed.emit(f"Manual request failed: {error}")


class ExchangeControlPanel(QWidget):
    """Home-style navigation for local, distributed, manual and saved-result use."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.controller = ExchangeController(self)
        self.manual_ids = itertools.count(1)
        self.manual_thread: ManualOrderThread | None = None
        self._setup_ui()
        self.controller.log_message.connect(self._append_log)
        self.controller.server_status.connect(self._set_server_status)
        self.controller.client_status.connect(self._set_client_status)
        self.controller.result_available.connect(self._show_result)
        self.controller.results_saved.connect(lambda _path: self._refresh_saved_results())

    @staticmethod
    def _spinbox(minimum: int, maximum: int, value: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        return widget

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("Exchange control - real TCP processes")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        subtitle = QLabel(
            "Run real TCP order-entry tests here. Local Simulation starts the server and workload clients as independent "
            "processes on this machine; Distributed Network gives the command for a second machine."
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._local_tab(), "Local Simulation")
        self.tabs.addTab(self._distributed_tab(), "Distributed Network")
        self.tabs.addTab(self._manual_tab(), "Manual Testing")
        self.tabs.addTab(self._results_tab(), "Previous Results")
        layout.addWidget(self.tabs, 3)

        self.event_log = QPlainTextEdit()
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(300)
        self.event_log.setPlaceholderText("Actual exchange and client process events appear here.")
        layout.addWidget(QLabel("Current-session event log"))
        layout.addWidget(self.event_log, 2)

    def _local_tab(self) -> QWidget:
        page = QWidget()
        layout = QGridLayout(page)
        workflow = QLabel(
            "Workflow: 1. Configure.  2. Start exchange and wait for Ready.  3. Start workload.  "
            "4. Confirm every client connects.  5. Inspect results or stop cleanly."
        )
        workflow.setWordWrap(True)
        workflow.setStyleSheet("padding: 6px; font-weight: 600;")
        layout.addWidget(workflow, 0, 0, 1, 2)
        config = QGroupBox("Run configuration")
        form = QFormLayout(config)
        self.port = self._spinbox(1024, 65535, 8080)
        self.client_count = self._spinbox(1, 32, 2)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0.1, 50_000.0)
        self.rate.setDecimals(1)
        self.rate.setValue(20.0)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.1, 3600.0)
        self.duration.setDecimals(1)
        self.duration.setValue(5.0)
        self.symbol = QComboBox()
        self.symbol.addItems(["AAPL", "GOOGL", "AMZN", "NFLX", "META"])
        self.seed = self._spinbox(0, 2_147_483_647, 11)
        form.addRow("TCP port", self.port)
        form.addRow("Independent clients", self.client_count)
        form.addRow("Rate per client (orders/s)", self.rate)
        form.addRow("Duration (s)", self.duration)
        form.addRow("Instrument", self.symbol)
        form.addRow("Random seed", self.seed)

        actions = QGroupBox("Lifecycle")
        action_layout = QVBoxLayout(actions)
        self.start_server_button = QPushButton("1. Start exchange")
        self.start_workload_button = QPushButton("2. Start workload")
        self.stop_button = QPushButton("Stop / clean up")
        self.start_server_button.clicked.connect(self._start_server)
        self.start_workload_button.clicked.connect(self._start_workload)
        self.stop_button.clicked.connect(self.controller.stop_server)
        action_layout.addWidget(self.start_server_button)
        action_layout.addWidget(self.start_workload_button)
        action_layout.addWidget(self.stop_button)
        self.server_status = QLabel("Stopped")
        action_layout.addWidget(QLabel("Server status:"))
        action_layout.addWidget(self.server_status)
        action_layout.addStretch()

        clients_box = QGroupBox("Client status and real results")
        client_layout = QVBoxLayout(clients_box)
        self.client_status_label = QLabel("No workload started")
        self.client_status_label.setWordWrap(True)
        self.result_label = QLabel("No measured workload result yet")
        self.result_label.setWordWrap(True)
        client_layout.addWidget(self.client_status_label)
        client_layout.addWidget(self.result_label)
        self.client_table = QTableWidget(0, 2)
        self.client_table.setHorizontalHeaderLabels(["Client", "Observed status"])
        self.client_table.horizontalHeader().setStretchLastSection(True)
        self.client_table.verticalHeader().setVisible(False)
        self.client_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        client_layout.addWidget(self.client_table)

        layout.addWidget(config, 1, 0)
        layout.addWidget(actions, 1, 1)
        layout.addWidget(clients_box, 2, 0, 1, 2)
        layout.setRowStretch(2, 1)
        return page

    def _distributed_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        box = QGroupBox("Run a real workload client from another machine")
        form = QFormLayout(box)
        self.remote_host = QLineEdit()
        self.remote_host.setPlaceholderText("Exchange machine LAN IPv4 address")
        self.remote_port = self._spinbox(1024, 65535, 8080)
        form.addRow("Exchange IPv4 address", self.remote_host)
        form.addRow("TCP port", self.remote_port)
        self.remote_command = QPlainTextEdit()
        self.remote_command.setReadOnly(True)
        self.remote_command.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.remote_command.setMinimumHeight(84)
        form.addRow("Remote command", self.remote_command)
        buttons = QHBoxLayout()
        update = QPushButton("Update command")
        update.clicked.connect(self._update_remote_command)
        copy = QPushButton("Copy command")
        copy.clicked.connect(self._copy_remote_command)
        buttons.addWidget(update)
        buttons.addWidget(copy)
        form.addRow(buttons)
        layout.addWidget(box)
        notes = QLabel(
            "Start `exchange --mode client --oe-port <port>` on the exchange machine. The TCP listener binds to all IPv4 "
            "interfaces. Find that machine's LAN address with `ipconfig getifaddr en0` (macOS) or `hostname -I` (Linux), "
            "then enter it above. Build and run the displayed command on the client machine. WORKLOAD_READY confirms the "
            "actual TCP handshake; WORKLOAD_RESULT contains observed acknowledgement measurements. This page cannot launch "
            "a process on another computer. UDP market data is a separate multicast path, not a TCP workload response."
        )
        notes.setWordWrap(True)
        layout.addWidget(notes)
        layout.addStretch()
        self._update_remote_command()
        return page

    def _manual_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        box = QGroupBox("Submit one real TCP limit order")
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
        self.manual_result.setMaximumBlockCount(20)
        self.manual_result.setMinimumHeight(72)
        form.addRow("Server", self.manual_host)
        form.addRow("TCP port", self.manual_port)
        form.addRow("Instrument", self.manual_symbol)
        form.addRow("Side", self.manual_side)
        form.addRow("Limit price (USD)", self.manual_price)
        form.addRow("Quantity", self.manual_quantity)
        form.addRow(submit)
        form.addRow("Result", self.manual_result)
        layout.addWidget(box)
        note = QLabel("A manual request opens a TCP connection, performs CLIENT_HELLO / SERVER_WELCOME, then sends NEW_ORDER. The full response is retained above and in the event log.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        return page

    def _results_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        explanation = QLabel(
            "Only completed runs are shown. Workload CSVs contain one row per independent TCP client; reliability CSVs "
            "contain the selected deterministic-demo configuration and measured result. Select a file to inspect the exact "
            "persisted values. Incomplete or stopped workloads are deliberately not saved as successful runs."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        refresh = QPushButton("Refresh saved results")
        refresh.clicked.connect(self._refresh_saved_results)
        layout.addWidget(refresh)
        self.saved_results = QListWidget()
        self.saved_results.itemSelectionChanged.connect(self._preview_saved_result)
        self.result_preview = QPlainTextEdit()
        self.result_preview.setReadOnly(True)
        self.result_preview.setMaximumBlockCount(50)
        self.saved_results.setMaximumHeight(135)
        self.result_preview.setMinimumHeight(240)
        layout.addWidget(self.saved_results)
        layout.addWidget(self.result_preview, 1)
        self._refresh_saved_results()
        layout.addStretch()
        return page

    def _refresh_saved_results(self) -> None:
        if not hasattr(self, "saved_results"):
            return
        self.saved_results.clear()
        output_dir = REPOSITORY_ROOT / "results"
        paths = sorted(
            [*output_dir.glob("workload-*.csv"), *output_dir.glob("reliability-*.csv")],
            reverse=True,
        )
        for path in paths:
            category = "Workload" if path.name.startswith("workload-") else "Reliability"
            item = QListWidgetItem(f"[{category}] {path.name}")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.saved_results.addItem(item)
        if self.saved_results.count():
            self.saved_results.setCurrentRow(0)
        else:
            self.result_preview.setPlainText("No saved workload results yet. Run Local Simulation first.")

    def _preview_saved_result(self) -> None:
        selected = self.saved_results.selectedItems()
        if not selected:
            return
        path = Path(selected[0].data(Qt.ItemDataRole.UserRole))
        try:
            self.result_preview.setPlainText(path.read_text(encoding="utf-8"))
        except OSError as error:
            self.result_preview.setPlainText(f"Could not read result: {error}")

    def _start_server(self) -> None:
        self.controller.start_server(self.port.value())

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
        self.remote_command.setPlainText(
            "./workload_client --server " + host + " --port " +
            str(self.remote_port.value()) + " --client-id 101 --duration 10 --rate 20 --symbol AAPL --side buy --seed 101"
        )

    def _copy_remote_command(self) -> None:
        QGuiApplication.clipboard().setText(self.remote_command.toPlainText())
        self._append_log("[Distributed Network] Remote command copied to clipboard.")

    def _submit_manual(self) -> None:
        if self.manual_thread and self.manual_thread.isRunning():
            self.manual_result.setPlainText("A manual request is already in progress")
            return
        side = BUY if self.manual_side.currentText() == "BUY" else SELL
        self.manual_result.setPlainText("Sending real TCP request...")
        self.manual_thread = ManualOrderThread(
            self.manual_host.text().strip(), self.manual_port.value(), 90_001, next(self.manual_ids),
            self.manual_symbol.currentText(), side, round(self.manual_price.value() * 100), self.manual_quantity.value(),
        )
        self.manual_thread.completed.connect(self.manual_result.setPlainText)
        self.manual_thread.completed.connect(lambda text: self._append_log(f"manual: {text}"))
        self.manual_thread.start()

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
