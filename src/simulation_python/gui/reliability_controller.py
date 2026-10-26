"""Controller for the compiled deterministic reliability experiment."""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, pyqtSignal


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RESULT_PATTERN = re.compile(r"^RELIABILITY_RESULT\s+(.*)$")


class ReliabilityExperimentController(QObject):
    log_line = pyqtSignal(str)
    status_changed = pyqtSignal(str)
    result_ready = pyqtSignal(dict)
    result_saved = pyqtSignal(str)
    live_result_ready = pyqtSignal(dict)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.process: QProcess | None = None
        self.csv_path: Path | None = None
        self.run_configuration: dict[str, str] = {}
        self.live_run = False
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

    def start(self, config: dict[str, str | int | float | bool]) -> bool:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.log_line.emit("Reliability experiment already running.")
            return False
        executable = self._binary()
        if not executable:
            self.status_changed.emit("Build required")
            self.log_line.emit("reliability_demo binary not found. Build the C++ targets first.")
            return False

        output_dir = REPOSITORY_ROOT / "results"
        output_dir.mkdir(exist_ok=True)
        csv_path = output_dir / f"reliability-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
        self.csv_path = csv_path
        interval = int(config["snapshot_interval"]) if config["snapshots_enabled"] else 0
        self.run_configuration = {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "experiment": "deterministic_reliability_demo",
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
            self.status_changed.emit("Failed to start")
            self.log_line.emit(process.errorString())
            return False
        self.process = process
        self.live_run = False
        self.status_changed.emit("Running")
        self.log_line.emit("Started deterministic reliability experiment.")
        return True

    @staticmethod
    def _live_build_directory() -> Path | None:
        configured = os.environ.get("EXCHANGE_BINARY_DIR")
        roots = [Path(configured)] if configured else []
        roots.extend([REPOSITORY_ROOT / "build-local", REPOSITORY_ROOT / "build", REPOSITORY_ROOT / "build-python"])
        for root in roots:
            if (root / "src" / "exchange").is_file() and (root / "src" / "reliable_subscriber").is_file():
                return root
        return None

    def start_live(self, config: dict[str, str | int | float | bool]) -> bool:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.log_line.emit("Reliability experiment already running.")
            return False
        build_dir = self._live_build_directory()
        runner = REPOSITORY_ROOT / "src" / "reliability_runner" / "live_reliability_runner.py"
        if not build_dir or not runner.is_file():
            self.status_changed.emit("Build required")
            self.log_line.emit("Live runner requires built exchange and reliable_subscriber targets.")
            return False
        interval = int(config["snapshot_interval"]) if config["snapshots_enabled"] else 0
        mode = str(config["mode"])
        arguments = [str(runner), "--build-dir", str(build_dir), "--duration", str(config["duration"]),
                     "--history", str(config["history"]), "--snapshot-interval", str(interval),
                     "--snapshot-retention", str(config["retention"]), "--fault-seed", str(config["seed"]),
                     "--recovery-policy", str(config["scheduler"])]
        loss = str(config["loss_rate"])
        if mode == "independent":
            arguments.extend(["--dual-feed", "--feed-a-loss", loss, "--feed-b-loss", loss])
        elif mode == "correlated":
            arguments.extend(["--dual-feed", "--correlated-loss", loss])
        else:
            arguments.extend(["--feed-a-loss", loss])
        self.live_output = bytearray()
        self.live_run = True
        process = QProcess(self)
        process.setWorkingDirectory(str(REPOSITORY_ROOT))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_output)
        process.errorOccurred.connect(lambda _error: self.status_changed.emit("Failed"))
        process.finished.connect(self._finished)
        process.start(sys.executable, arguments)
        if not process.waitForStarted(3000):
            self.status_changed.emit("Failed to start")
            self.log_line.emit(process.errorString())
            return False
        self.process = process
        self.status_changed.emit("Running live local experiment")
        self.log_line.emit("Started live exchange/subscriber runner; result will be persisted as JSON.")
        return True

    def _read_output(self) -> None:
        if not self.process:
            return
        raw_output = bytes(self.process.readAllStandardOutput())
        if self.live_run:
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
        if self.live_run:
            try:
                result = json.loads(bytes(self.live_output).decode(errors="replace"))
                self.live_result_ready.emit(result)
                self.result_saved.emit(str(result.get("result_path", "")))
            except json.JSONDecodeError as error:
                self.log_line.emit(f"Could not parse live-run result: {error}")
            self.status_changed.emit("Completed" if code == 0 else f"Failed ({code})")
            self.process = None
            self.live_run = False
            return
        self.status_changed.emit("Completed" if code == 0 else f"Failed ({code})")
        if code == 0 and self.csv_path and self.csv_path.is_file():
            self._enrich_csv(self.csv_path)
            self.result_saved.emit(str(self.csv_path))
        self.process = None

    def _enrich_csv(self, path: Path) -> None:
        """Persist the exact GUI configuration alongside measured C++ metrics."""
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
        self.live_run = False
        self.status_changed.emit("Stopped")
