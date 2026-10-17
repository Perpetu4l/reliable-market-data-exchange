"""Qt process controller for genuine local exchange benchmarks.

The controller starts the C++ exchange and every workload generator as a
separate process.  The GUI only parses their emitted measurements; it never
fabricates order traffic or calls the matching engine in-process.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, pyqtSignal


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RESULT_PATTERN = re.compile(r"^WORKLOAD_RESULT\s+(.*)$")


class ExchangeController(QObject):
    log_message = pyqtSignal(str)
    server_status = pyqtSignal(str)
    client_status = pyqtSignal(int, str)
    result_available = pyqtSignal(dict)
    results_saved = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.server_process: QProcess | None = None
        self.client_processes: dict[int, QProcess] = {}
        self.client_results: list[dict[str, str]] = []
        self.server_ready = False
        self.run_configuration: dict[str, str] = {}
        self.expected_clients = 0
        self.workload_failed = False
        self.workload_cancelled = False

    @staticmethod
    def _binary(name: str) -> str | None:
        configured = os.environ.get("EXCHANGE_BINARY_DIR")
        roots = [Path(configured)] if configured else []
        roots.extend(
            [
                REPOSITORY_ROOT / "build-local",
                REPOSITORY_ROOT / "build-verified",
                REPOSITORY_ROOT / "build",
                REPOSITORY_ROOT / "build-python",
            ]
        )
        for root in roots:
            if root and (candidate := root / "src" / name).is_file():
                return str(candidate)
        return None

    def start_server(self, port: int) -> bool:
        if self.server_process and self.server_process.state() != QProcess.ProcessState.NotRunning:
            self.server_status.emit("Running")
            return True
        executable = self._binary("exchange")
        if not executable:
            self.server_status.emit("Build required")
            self.log_message.emit("Exchange binary not found. Build the C++ targets first.")
            return False

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_server_output)
        process.errorOccurred.connect(lambda _error: self.server_status.emit("Failed"))
        process.finished.connect(lambda _code, _state: self.server_status.emit("Stopped"))
        process.start(executable, ["--mode", "client", "--oe-port", str(port), "--md-interface", "127.0.0.1"])
        if not process.waitForStarted(3000):
            self.server_status.emit("Failed to start")
            self.log_message.emit(process.errorString())
            return False
        self.server_process = process
        self.server_ready = False
        self.server_status.emit("Starting")
        self.log_message.emit(
            f"Started exchange process; waiting for its TCP listener on 0.0.0.0:{port}."
        )
        return True

    def _read_server_output(self) -> None:
        if not self.server_process:
            return
        for line in bytes(self.server_process.readAllStandardOutput()).decode(errors="replace").splitlines():
            self.log_message.emit(f"server: {line}")
            if "OrderEntry server started" in line:
                self.server_ready = True
                self.server_status.emit("Ready")

    def start_workload(
        self, host: str, port: int, clients: int, rate: float, duration: float,
        symbol: str, seed: int,
    ) -> bool:
        executable = self._binary("workload_client")
        if not executable:
            self.log_message.emit("Workload binary not found. Build the C++ targets first.")
            return False
        if not self.server_process or self.server_process.state() == QProcess.ProcessState.NotRunning:
            self.log_message.emit("Cannot start workload: the exchange process is not running.")
            return False
        if not self.server_ready:
            self.log_message.emit("Cannot start workload: wait until server status is Ready.")
            return False
        if self.client_processes:
            self.log_message.emit("A workload is already running. Stop it before starting another.")
            return False
        self.client_results.clear()
        self.expected_clients = clients
        self.workload_failed = False
        self.workload_cancelled = False
        self.run_configuration = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "server": host,
            "port": str(port),
            "configured_clients": str(clients),
            "rate_per_client": str(rate),
            "duration_seconds": str(duration),
            "symbol": symbol,
            "seed": str(seed),
            "execution_mode": "local_loopback" if host in {"127.0.0.1", "localhost"} else "network_tcp",
        }
        for client_id in range(1, clients + 1):
            process = QProcess(self)
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            process.readyReadStandardOutput.connect(lambda cid=client_id: self._read_client_output(cid))
            process.errorOccurred.connect(lambda _error, cid=client_id: self.client_status.emit(cid, "Failed"))
            process.finished.connect(lambda code, _state, cid=client_id: self._client_finished(cid, code))
            side = "buy" if client_id % 2 else "sell"
            process.start(executable, [
                "--server", host, "--port", str(port), "--client-id", str(client_id),
                "--duration", str(duration), "--rate", str(rate), "--symbol", symbol,
                "--side", side, "--seed", str(seed + client_id),
            ])
            if not process.waitForStarted(3000):
                self.client_status.emit(client_id, "Failed to start")
                self.log_message.emit(f"client {client_id}: {process.errorString()}")
                self.stop_workload()
                return False
            self.client_processes[client_id] = process
            self.client_status.emit(client_id, "Starting")
        self.log_message.emit(f"Started {clients} independent workload client process(es).")
        return True

    def _read_client_output(self, client_id: int) -> None:
        process = self.client_processes.get(client_id)
        if not process:
            return
        for line in bytes(process.readAllStandardOutput()).decode(errors="replace").splitlines():
            self.log_message.emit(f"client {client_id}: {line}")
            if line.startswith("WORKLOAD_READY"):
                self.client_status.emit(client_id, "Connected — workload active")
            match = RESULT_PATTERN.match(line)
            if match:
                result = {key: value for key, value in (item.split("=", 1) for item in match.group(1).split() if "=" in item)}
                self.client_results.append(result)
                self.result_available.emit(result)

    def _client_finished(self, client_id: int, exit_code: int) -> None:
        self.client_processes.pop(client_id, None)
        has_result = any(result.get("client_id") == str(client_id) for result in self.client_results)
        completed = exit_code == 0 and has_result
        if completed:
            self.client_status.emit(client_id, "Completed — result recorded")
        elif self.workload_cancelled:
            self.client_status.emit(client_id, "Stopped")
        else:
            self.workload_failed = True
            self.client_status.emit(client_id, f"Failed ({exit_code}) — no complete result")
            self.log_message.emit(
                f"client {client_id}: workload is incomplete; remaining clients are being stopped and no result CSV will be saved."
            )
            for process in list(self.client_processes.values()):
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.terminate()
            self.client_processes.clear()
            return
        if not self.client_processes and not self.workload_cancelled and not self.workload_failed:
            if len(self.client_results) != self.expected_clients:
                self.workload_failed = True
                self.log_message.emit(
                    "Workload is incomplete: not every configured client produced a result. No CSV was saved."
                )
                return
            self._save_results()

    def _save_results(self) -> None:
        output_dir = REPOSITORY_ROOT / "results"
        output_dir.mkdir(exist_ok=True)
        path = output_dir / f"workload-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
        rows = [{**self.run_configuration, **result, "run_status": "completed"} for result in self.client_results]
        keys = sorted({key for row in rows for key in row})
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=keys)
            writer.writeheader()
            writer.writerows(rows)
        self.log_message.emit(f"Saved actual workload results to {path.relative_to(REPOSITORY_ROOT)}.")
        self.results_saved.emit(str(path))

    def stop_workload(self) -> None:
        self.workload_cancelled = True
        for process in list(self.client_processes.values()):
            if process.state() != QProcess.ProcessState.NotRunning:
                process.terminate()
                if not process.waitForFinished(1000):
                    process.kill()
        self.client_processes.clear()

    def stop_server(self) -> None:
        self.stop_workload()
        if self.server_process and self.server_process.state() != QProcess.ProcessState.NotRunning:
            self.server_process.terminate()
            if not self.server_process.waitForFinished(1500):
                self.server_process.kill()
        self.server_process = None
        self.server_ready = False
        self.server_status.emit("Stopped")

    def shutdown(self) -> None:
        self.stop_server()
