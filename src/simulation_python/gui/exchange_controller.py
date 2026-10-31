"""Qt process controller for local and network exchange, subscriber, and workload processes."""

from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QProcess, pyqtSignal


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RESULT_PATTERN = re.compile(r"^WORKLOAD_RESULT\s+(.*)$")
SUB_READY_PATTERN = re.compile(r"^SUBSCRIBER_READY\s+id=(\S+)\s+feeds=(\S+)")
SUB_STATS_PATTERN = re.compile(r"^SUBSCRIBER_STATS\s+(.*)$")
SUB_RESULT_PATTERN = re.compile(r"^RELIABLE_SUBSCRIBER_RESULT\s+(.*)$")
EX_STATS_PATTERN = re.compile(r"^EXCHANGE_STATS\s+(.*)$")
EX_RESULT_PATTERN = re.compile(r"^EXCHANGE_RESULT\s+(.*)$")
CLIENT_CONN_PATTERN = re.compile(r"^CLIENT_CONNECTED\s+id=(\S+)\s+addr=(\S+)")
SUB_CONN_PATTERN = re.compile(r"^SUBSCRIBER_CONNECTED\s+id=(\S+)\s+addr=(\S+)")
SUB_DISC_PATTERN = re.compile(r"^SUBSCRIBER_DISCONNECTED\s+id=(\S+)\s+reason=(\S+)")


def _parse_kv(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for token in raw.split():
        if "=" in token:
            k, v = token.split("=", 1)
            out[k] = v
    return out


class ExchangeController(QObject):
    log_message = pyqtSignal(str)
    server_status = pyqtSignal(str)
    client_status = pyqtSignal(int, str)
    result_available = pyqtSignal(dict)
    results_saved = pyqtSignal(str)

    # New signals for live subscribers, remote connections, and exchange telemetry
    exchange_stats_updated = pyqtSignal(dict)
    exchange_result_available = pyqtSignal(dict)
    subscriber_ready = pyqtSignal(str, str)
    subscriber_stats_updated = pyqtSignal(str, dict)
    subscriber_result_available = pyqtSignal(str, dict)
    subscriber_status_changed = pyqtSignal(str, str)
    remote_peer_event = pyqtSignal(str, str, str, str)  # kind ("client"|"subscriber"), id, addr_or_reason, state

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.server_process: QProcess | None = None
        self.client_processes: dict[int, QProcess] = {}
        self.subscriber_processes: dict[str, QProcess] = {}
        self.client_results: list[dict[str, str]] = []
        self.subscriber_results: dict[str, dict[str, str]] = {}
        self.latest_subscriber_stats: dict[str, dict[str, str]] = {}
        self.latest_exchange_stats: dict[str, str] = {}
        self.latest_exchange_result: dict[str, str] = {}
        self.server_ready = False
        self.recovery_ready = False
        self.gui_owns_exchange = False
        self.current_run_id = ""
        self.current_run_dir: Path | None = None
        self.run_configuration: dict[str, Any] = {}
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

    def start_server(
        self,
        port: int,
        md_interface: str = "127.0.0.1",
        dual_feed: bool = True,
        recovery_port: int = 9090,
        history: int = 65536,
        snapshot_interval: int = 100,
        snapshot_retention: int = 8,
        snapshot_on_demand: bool = True,
        recovery_policy: str = "fcfs",
        recovery_workers: int = 1,
        service_delay_req_us: int = 0,
        service_delay_event_us: int = 0,
        tamper_snapshot: str = "none",
        heartbeat_ms: int = 500,
        feed_a_port: int = 9999,
        feed_b_port: int = 10000,
    ) -> bool:
        if self.server_process and self.server_process.state() != QProcess.ProcessState.NotRunning:
            self.server_status.emit("Running")
            return True
        executable = self._binary("exchange")
        if not executable:
            self.server_status.emit("Build required")
            self.log_message.emit("Exchange binary not found. Build the C++ targets first.")
            return False

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.current_run_id = f"gui-live-{stamp}"
        self.current_run_dir = REPOSITORY_ROOT / "results" / "live" / self.current_run_id
        self.current_run_dir.mkdir(parents=True, exist_ok=True)

        args = [
            "--mode", "client",
            "--oe-port", str(port),
            "--md-interface", md_interface,
            "--md-port", str(feed_a_port),
            "--feed-b-port", str(feed_b_port),
            "--recovery-port", str(recovery_port),
            "--history", str(history),
            "--snapshot-interval", str(snapshot_interval),
            "--snapshot-retention", str(snapshot_retention),
            "--snapshot-on-demand", "1" if snapshot_on_demand else "0",
            "--recovery-policy", recovery_policy,
            "--recovery-workers", str(recovery_workers),
            "--service-delay-us-per-request", str(service_delay_req_us),
            "--service-delay-us-per-event", str(service_delay_event_us),
            "--tamper-snapshot", tamper_snapshot,
            "--heartbeat-ms", str(heartbeat_ms),
            "--results-dir", str(self.current_run_dir),
            "--run-id", self.current_run_id,
        ]
        if dual_feed:
            args.append("--dual-feed")

        process = QProcess(self)
        process.setWorkingDirectory(str(REPOSITORY_ROOT))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_server_output)
        process.errorOccurred.connect(lambda _error: self.server_status.emit("Failed"))
        process.finished.connect(self._on_server_finished)
        process.start(executable, args)
        if not process.waitForStarted(3000):
            self.server_status.emit("Failed to start")
            self.log_message.emit(process.errorString())
            return False
        self.server_process = process
        self.server_ready = False
        self.recovery_ready = recovery_port == 0
        self.gui_owns_exchange = True
        self.latest_exchange_stats.clear()
        self.latest_exchange_result.clear()
        self.server_status.emit("Starting")
        self.log_message.emit(
            f"Started exchange (OE port {port}, recovery port {recovery_port}, interface {md_interface})."
        )
        return True

    def _on_server_finished(self, _code: int, _state: QProcess.ExitStatus) -> None:
        self._read_server_output()
        self.gui_owns_exchange = False
        self.server_ready = False
        self.server_status.emit("Stopped")

    def _read_server_output(self) -> None:
        if not self.server_process:
            return
        for line in bytes(self.server_process.readAllStandardOutput()).decode(errors="replace").splitlines():
            self.log_message.emit(f"server: {line}")
            if "OrderEntry server started" in line:
                self.server_ready = True
                if self.recovery_ready:
                    self.server_status.emit("Ready")
            if "Recovery TCP server listening" in line:
                self.recovery_ready = True
                if self.server_ready:
                    self.server_status.emit("Ready")
            if m := EX_STATS_PATTERN.match(line):
                kv = _parse_kv(m.group(1))
                self.latest_exchange_stats = kv
                self.exchange_stats_updated.emit(kv)
            elif m := EX_RESULT_PATTERN.match(line):
                kv = _parse_kv(m.group(1))
                self.latest_exchange_result = kv
                if self.current_run_dir:
                    (self.current_run_dir / "exchange-result.json").write_text(
                        json.dumps(kv, indent=2) + "\n", encoding="utf-8"
                    )
                self.exchange_result_available.emit(kv)
            elif m := CLIENT_CONN_PATTERN.match(line):
                self.remote_peer_event.emit("client", m.group(1), m.group(2), "Connected")
            elif m := SUB_CONN_PATTERN.match(line):
                self.remote_peer_event.emit("subscriber", m.group(1), m.group(2), "Connected")
            elif m := SUB_DISC_PATTERN.match(line):
                self.remote_peer_event.emit("subscriber", m.group(1), m.group(2), f"Disconnected ({m.group(2)})")

    def start_subscribers(
        self,
        specs: list[dict[str, Any]],
        interface_ip: str = "127.0.0.1",
        recovery_host: str = "127.0.0.1",
        recovery_port: int = 9090,
        feed_a_port: int = 9999,
        feed_b_port: int = 10000,
        grace_ms: int = 2,
        retries: int = 5,
        stop_when_idle_ms: int = 0,
    ) -> bool:
        executable = self._binary("reliable_subscriber")
        if not executable:
            self.log_message.emit("reliable_subscriber binary not found. Build the C++ targets first.")
            return False
        if self.subscriber_processes:
            self.log_message.emit("Subscribers are already running; stop them first.")
            return False

        if not self.current_run_dir:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.current_run_id = f"gui-live-{stamp}"
            self.current_run_dir = REPOSITORY_ROOT / "results" / "live" / self.current_run_id
            self.current_run_dir.mkdir(parents=True, exist_ok=True)

        self.subscriber_results.clear()
        self.latest_subscriber_stats.clear()

        for spec in specs:
            sid = str(spec["id"])
            sub_csv = self.current_run_dir / f"subscriber-{sid}.csv"
            lat_csv = self.current_run_dir / f"subscriber-{sid}-latency.csv"
            args = [
                "--interface", interface_ip,
                "--feed-a-port", str(feed_a_port),
                "--feed-b-port", str(feed_b_port),
                "--recovery-server", recovery_host,
                "--recovery-port", str(recovery_port),
                "--subscriber", sid,
                "--run-id", self.current_run_id,
                "--gap-grace-ms", str(grace_ms),
                "--recovery-retries", str(retries),
                "--recovery-concurrency", str(spec.get("concurrency", 1)),
                "--loss-model", str(spec.get("loss_model", "none")),
                "--drop-a", str(spec.get("drop_a", 0.0)),
                "--drop-b", str(spec.get("drop_b", 0.0)),
                "--corr-loss", str(spec.get("corr_loss", 0.0)),
                "--drop-seed", str(spec.get("seed", 42)),
                "--csv", str(sub_csv),
                "--latency-csv", str(lat_csv),
            ]
            if str(spec.get("feeds", "ab")) == "ab":
                args.append("--dual-feed")
            if stop_when_idle_ms > 0:
                args.extend(["--stop-when-idle-ms", str(stop_when_idle_ms)])

            proc = QProcess(self)
            proc.setWorkingDirectory(str(REPOSITORY_ROOT))
            proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            proc.readyReadStandardOutput.connect(lambda s=sid: self._read_subscriber_output(s))
            proc.finished.connect(lambda code, _st, s=sid: self._subscriber_finished(s, code))
            proc.start(executable, args)
            if not proc.waitForStarted(3000):
                self.subscriber_status_changed.emit(sid, "Failed to start")
                self.log_message.emit(f"subscriber {sid}: {proc.errorString()}")
                continue
            self.subscriber_processes[sid] = proc
            self.subscriber_status_changed.emit(sid, "Starting (waiting for SUBSCRIBER_READY)")

        return len(self.subscriber_processes) > 0

    def _read_subscriber_output(self, sid: str) -> None:
        proc = self.subscriber_processes.get(sid)
        if not proc:
            return
        for line in bytes(proc.readAllStandardOutput()).decode(errors="replace").splitlines():
            if m := SUB_READY_PATTERN.match(line):
                self.subscriber_status_changed.emit(sid, f"Connected (feeds={m.group(2)})")
                self.subscriber_ready.emit(sid, m.group(2))
                self.log_message.emit(f"subscriber {sid}: {line}")
            elif m := SUB_STATS_PATTERN.match(line):
                kv = _parse_kv(m.group(1))
                self.latest_subscriber_stats[sid] = kv
                self.subscriber_stats_updated.emit(sid, kv)
            elif m := SUB_RESULT_PATTERN.match(line):
                kv = _parse_kv(m.group(1))
                self.subscriber_results[sid] = kv
                self.subscriber_result_available.emit(sid, kv)
                self.log_message.emit(f"subscriber {sid}: {line}")
            else:
                self.log_message.emit(f"subscriber {sid}: {line}")

    def _subscriber_finished(self, sid: str, exit_code: int) -> None:
        self._read_subscriber_output(sid)
        self.subscriber_processes.pop(sid, None)
        res = self.subscriber_results.get(sid, {})
        run_status = res.get("run_status", "completed" if exit_code == 0 else f"failed({exit_code})")
        self.subscriber_status_changed.emit(sid, run_status)
        if not self.subscriber_processes and self.current_run_dir and self.subscriber_results:
            self._save_live_run_bundle()

    def _save_live_run_bundle(self) -> None:
        if not self.current_run_dir:
            return
        rows = []
        for sid, res in self.subscriber_results.items():
            sub_csv = self.current_run_dir / f"subscriber-{sid}.csv"
            if sub_csv.is_file():
                with sub_csv.open(newline="", encoding="utf-8") as f:
                    rlist = list(csv.DictReader(f))
                    if rlist:
                        rows.append(dict(rlist[0]))
                        continue
            rows.append(dict(res))
        if rows:
            summary_csv = self.current_run_dir / "run-summary.csv"
            keys = list(rows[0].keys())
            for r in rows[1:]:
                for k in r.keys():
                    if k not in keys:
                        keys.append(k)
            with summary_csv.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerows(rows)
        all_ok = all(
            r.get("run_status") == "completed" and r.get("book_matches_exchange") == "1"
            for r in self.subscriber_results.values()
        )
        run_json = {
            "schema": "reliable-market-data-live-run/v2",
            "run_id": self.current_run_id,
            "success": all_ok,
            "subscriber_metrics": self.subscriber_results,
            "exchange_metrics": self.latest_exchange_result or self.latest_exchange_stats,
            "run_dir": str(self.current_run_dir),
            "result_path": str(self.current_run_dir / "run.json"),
        }
        (self.current_run_dir / "run.json").write_text(json.dumps(run_json, indent=2) + "\n", encoding="utf-8")
        self.results_saved.emit(str(self.current_run_dir / "run.json"))

    def stop_subscribers(self) -> None:
        # Send SIGINT (via terminate on POSIX QProcess or os.kill) for graceful reconciliation.
        import signal as _sig
        for sid, proc in list(self.subscriber_processes.items()):
            if proc.state() != QProcess.ProcessState.NotRunning:
                pid = proc.processId()
                if pid > 0:
                    try:
                        os.kill(pid, _sig.SIGINT)
                    except OSError:
                        proc.terminate()
                else:
                    proc.terminate()

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
            for process in list(self.client_processes.values()):
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.terminate()
            self.client_processes.clear()
            return
        if not self.client_processes and not self.workload_cancelled and not self.workload_failed:
            if len(self.client_results) != self.expected_clients:
                self.workload_failed = True
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
        self.stop_subscribers()
        for proc in list(self.subscriber_processes.values()):
            if proc.state() != QProcess.ProcessState.NotRunning:
                if not proc.waitForFinished(3000):
                    proc.terminate()
                    proc.waitForFinished(1000)
        self.subscriber_processes.clear()
        if self.server_process and self.server_process.state() != QProcess.ProcessState.NotRunning:
            import signal as _sig
            pid = self.server_process.processId()
            if pid > 0:
                try:
                    os.kill(pid, _sig.SIGINT)
                except OSError:
                    self.server_process.terminate()
            else:
                self.server_process.terminate()
            if not self.server_process.waitForFinished(2000):
                self.server_process.kill()
        self.server_process = None
        self.server_ready = False
        self.gui_owns_exchange = False
        self.server_status.emit("Stopped")

    def shutdown(self) -> None:
        self.stop_server()
