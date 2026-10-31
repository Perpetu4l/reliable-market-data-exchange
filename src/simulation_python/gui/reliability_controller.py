"""Controller for in-process baseline, live runs, and preset sweeps."""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QProcess, pyqtSignal


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RESULT_PATTERN = re.compile(r"^RELIABILITY_RESULT\s+(.*)$")


class ReliabilityExperimentController(QObject):
    log_line = pyqtSignal(str)
    status_changed = pyqtSignal(str)
    progress_changed = pyqtSignal(int)
    result_ready = pyqtSignal(dict)
    result_saved = pyqtSignal(str)
    live_result_ready = pyqtSignal(dict)
    preset_result_ready = pyqtSignal(dict)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.process: QProcess | None = None
        self.csv_path: Path | None = None
        self.run_configuration: dict[str, str] = {}
        self.run_kind = "demo"  # "demo", "live", "preset"
        self.live_output = bytearray()

    @staticmethod
    def _binary() -> str | None:
        configured = os.environ.get("EXCHANGE_BINARY_DIR")
        roots = [Path(configured)] if configured else []
        roots.extend([
            REPOSITORY_ROOT / "build-local",
            REPOSITORY_ROOT / "build",
            REPOSITORY_ROOT / "build-python",
        ])
        for root in roots:
            candidate = root / "src" / "reliability_demo"
            if candidate.is_file():
                return str(candidate)
        return None

    @staticmethod
    def _live_build_directory() -> Path | None:
        configured = os.environ.get("EXCHANGE_BINARY_DIR")
        roots = [Path(configured)] if configured else []
        roots.extend([REPOSITORY_ROOT / "build-local", REPOSITORY_ROOT / "build", REPOSITORY_ROOT / "build-python"])
        for root in roots:
            if (root / "src" / "exchange").is_file() and (root / "src" / "reliable_subscriber").is_file():
                return root
        return None

    def start(self, config: dict[str, Any]) -> bool:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.log_line.emit("Reliability experiment already running.")
            return False
        executable = self._binary()
        if not executable:
            self.status_changed.emit("Failed: reliability_demo binary not found (build C++ targets first)")
            self.log_line.emit("ERROR: reliability_demo binary not found. Build the C++ targets first.")
            return False

        output_dir = REPOSITORY_ROOT / "results"
        output_dir.mkdir(exist_ok=True)
        csv_path = output_dir / f"reliability-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
        self.csv_path = csv_path
        interval = int(config["snapshot_interval"]) if config["snapshots_enabled"] else 0
        self.run_configuration = {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "experiment": "deterministic_reliability_demo",
            "transport": "inprocess",
            "loss_injection": "app_level_publisher_side" if float(config["loss_rate"]) > 0 else "none",
            "mode": str(config["mode"]),
            "events": str(config["events"]),
            "application_loss_rate": str(config["loss_rate"]),
            "seed": str(config["seed"]),
            "history_capacity": str(config["history"]),
            "snapshot_interval": str(interval),
            "snapshot_retention": str(config["retention"]),
            "scheduler": str(config["scheduler"]),
            "run_status": "completed",
        }
        arguments = [
            "--events", str(config["events"]),
            "--loss", str(config["loss_rate"]),
            "--mode", str(config["mode"]),
            "--seed", str(config["seed"]),
            "--history", str(config["history"]),
            "--snapshot-interval", str(interval),
            "--retention", str(config["retention"]),
            "--scheduler", str(config["scheduler"]),
            "--csv", str(csv_path),
        ]
        process = QProcess(self)
        process.setWorkingDirectory(str(REPOSITORY_ROOT))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_output)
        process.errorOccurred.connect(lambda _error: self.status_changed.emit("Failed"))
        process.finished.connect(self._finished)
        process.start(executable, arguments)
        if not process.waitForStarted(3000):
            self.status_changed.emit(f"Failed to start: {process.errorString()}")
            self.log_line.emit(process.errorString())
            return False
        self.process = process
        self.run_kind = "demo"
        self.progress_changed.emit(25)
        self.status_changed.emit("Running in-process deterministic baseline...")
        return True

    def start_live(self, config: dict[str, Any]) -> bool:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.log_line.emit("Reliability experiment already running.")
            return False
        build_dir = self._live_build_directory()
        runner = REPOSITORY_ROOT / "src" / "reliability_runner" / "live_reliability_runner.py"
        if not build_dir or not runner.is_file():
            self.status_changed.emit("Failed: exchange/reliable_subscriber binaries missing")
            self.log_line.emit("ERROR: Live runner requires built exchange, reliable_subscriber, and workload_client targets.")
            return False
        interval = int(config["snapshot_interval"]) if config["snapshots_enabled"] else 0
        mode = str(config["mode"])
        interface = str(config.get("interface", "127.0.0.1"))
        arguments = [
            str(runner),
            "--build-dir", str(build_dir),
            "--interface", interface,
            "--duration", str(config["duration"]),
            "--subscribers", str(config["subscribers"]),
            "--history", str(config["history"]),
            "--snapshot-interval", str(interval),
            "--snapshot-retention", str(config["retention"]),
            "--fault-seed", str(config["seed"]),
            "--recovery-policy", str(config["scheduler"]),
            "--recovery-workers", str(config.get("workers", 1)),
            "--service-delay-us-per-request", str(config.get("delay_per_req_us", 0)),
            "--service-delay-us-per-event", str(config.get("delay_per_event_us", 0)),
            "--tamper-snapshot", str(config.get("tamper_mode", "none")),
        ]
        if not config.get("snapshot_on_demand", True):
            arguments.append("--no-snapshot-on-demand")
        if str(config.get("tamper_mode", "none")) != "none":
            arguments.append("--expect-failure")
        loss = str(config["loss_rate"])
        if mode == "independent":
            arguments.extend(["--dual-feed", "--feed-a-loss", loss, "--feed-b-loss", loss])
        elif mode == "correlated":
            arguments.extend(["--dual-feed", "--correlated-loss", loss])
        else:
            arguments.extend(["--feed-a-loss", loss])

        self.live_output = bytearray()
        self.run_kind = "live"
        process = QProcess(self)
        process.setWorkingDirectory(str(REPOSITORY_ROOT))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_output)
        process.errorOccurred.connect(lambda _error: self.status_changed.emit("Failed"))
        process.finished.connect(self._finished)
        process.start(sys.executable, arguments)
        if not process.waitForStarted(3000):
            self.status_changed.emit(f"Failed to start: {process.errorString()}")
            self.log_line.emit(process.errorString())
            return False
        self.process = process
        self.progress_changed.emit(20)
        self.status_changed.emit(f"Running live experiment ({config['subscribers']} subscriber(s))...")
        return True

    def start_preset(self, preset: str, size: str, interface: str = "127.0.0.1") -> bool:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.log_line.emit("An experiment or preset is already running.")
            return False
        build_dir = self._live_build_directory()
        exp_script = REPOSITORY_ROOT / "src" / "reliability_runner" / "experiments.py"
        if not build_dir or not exp_script.is_file():
            self.status_changed.emit("Failed: exchange/reliable_subscriber binaries missing")
            self.log_line.emit("ERROR: Preset runner requires built exchange, reliable_subscriber, and workload_client targets.")
            return False
        arguments = [
            str(exp_script),
            "--preset", preset,
            "--size", size,
            "--build-dir", str(build_dir),
            "--interface", interface,
        ]
        self.live_output = bytearray()
        self.run_kind = "preset"
        process = QProcess(self)
        process.setWorkingDirectory(str(REPOSITORY_ROOT))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_output)
        process.errorOccurred.connect(lambda _error: self.status_changed.emit("Failed"))
        process.finished.connect(self._finished)
        process.start(sys.executable, arguments)
        if not process.waitForStarted(3000):
            self.status_changed.emit(f"Failed to start preset: {process.errorString()}")
            return False
        self.process = process
        self.progress_changed.emit(15)
        self.status_changed.emit(f"Running preset '{preset}' ({size})...")
        return True

    def _read_output(self) -> None:
        if not self.process:
            return
        raw_output = bytes(self.process.readAllStandardOutput())
        if self.run_kind in ("live", "preset"):
            self.live_output.extend(raw_output)
            for line in raw_output.decode(errors="replace").splitlines():
                self.log_line.emit(line)
            return
        for line in raw_output.decode(errors="replace").splitlines():
            match = RESULT_PATTERN.match(line)
            if match:
                result = {
                    key: value for key, value in
                    (part.split("=", 1) for part in match.group(1).split() if "=" in part)
                }
                self.result_ready.emit(result)
            else:
                self.log_line.emit(line)

    def _finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        if self.process:
            self._read_output()
        self.progress_changed.emit(100)
        if self.run_kind == "live":
            text = bytes(self.live_output).decode(errors="replace").strip()
            try:
                idx = text.find("{")
                result = json.loads(text[idx:] if idx >= 0 else text)
                self.live_result_ready.emit(result)
                self.result_saved.emit(str(result.get("result_path", "")))
            except json.JSONDecodeError as error:
                self.log_line.emit(f"Could not parse live-run result: {error}")
            self.status_changed.emit("Completed" if code == 0 else f"Failed (exit code {code})")
            self.process = None
            return
        if self.run_kind == "preset":
            text = bytes(self.live_output).decode(errors="replace").strip()
            try:
                idx = text.find("{")
                result = json.loads(text[idx:] if idx >= 0 else text)
                self.preset_result_ready.emit(result)
            except json.JSONDecodeError as error:
                self.log_line.emit(f"Could not parse preset output: {error}")
            self.status_changed.emit("Preset completed" if code == 0 else f"Preset failed (exit code {code})")
            self.process = None
            return
        self.status_changed.emit("Completed" if code == 0 else f"Failed (exit code {code})")
        if code == 0 and self.csv_path and self.csv_path.is_file():
            self._enrich_csv(self.csv_path)
            self.result_saved.emit(str(self.csv_path))
        self.process = None

    def _enrich_csv(self, path: Path) -> None:
        try:
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            if not rows:
                return
            rows = [{**self.run_configuration, **row} for row in rows]
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        except OSError as error:
            self.log_line.emit(f"Could not add run configuration to CSV: {error}")

    def stop(self) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(1000):
                self.process.kill()
        self.process = None
        self.progress_changed.emit(0)
        self.status_changed.emit("Stopped / Cancelled")
