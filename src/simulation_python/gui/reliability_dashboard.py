"""Working GUI for the compiled deterministic reliability experiment."""

from __future__ import annotations

from datetime import datetime

import pyqtgraph as pg
from PyQt6.QtWidgets import QPlainTextEdit, QScrollArea, QTabWidget, QVBoxLayout, QWidget, QLabel

from .experiment_panel import ExperimentPanel
from .fairness_panel import FairnessPanel
from .history_integrity_panel import HistoryIntegrityPanel
from .metrics_panel import MetricsPanel
from .recovery_panel import RecoveryPanel
from .redundancy_panel import RedundancyPanel
from .reliability_controller import ReliabilityExperimentController
from .snapshot_panel import SnapshotPanel


class ReliabilityDashboard(QTabWidget):
    """Runs `reliability_demo` and presents its actual experiment output."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.controller = ReliabilityExperimentController(self)
        self.recovery = RecoveryPanel()
        self.redundancy = RedundancyPanel()
        self.metrics = MetricsPanel()
        self.experiment = ExperimentPanel()
        self.snapshot = SnapshotPanel()
        self.fairness = FairnessPanel()
        self.integrity = HistoryIntegrityPanel()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setMinimumHeight(180)
        self.summary = QLabel("Configure an experiment and press Run reliability experiment.")
        self.summary.setWordWrap(True)
        self.delivery_chart = self._chart(
            "Observed delivery outcome", "Event/message count",
            "Sent, application-level feed drops, duplicates suppressed, and replayed events from this run."
        )
        self.latency_chart = self._chart(
            "Observed recovery latency percentiles", "Microseconds",
            "p50/p95/p99 are available only when this run performed recovery."
        )

        self.addTab(self._scroll([
            self.summary, self.recovery, self.redundancy, self.metrics,
            self.delivery_chart, self.latency_chart, self.log,
        ]), "Results")
        self.addTab(self._scroll([self.experiment, self.snapshot, self.fairness]), "Configure & Run")
        self.addTab(self._scroll([self.integrity]), "Checkpoint Integrity")

        self.experiment.start_button.clicked.connect(self._start)
        self.experiment.stop_button.clicked.connect(self.controller.stop)
        self.controller.log_line.connect(self._append_log)
        self.controller.status_changed.connect(self.experiment.status.setText)
        self.controller.result_ready.connect(self._apply_result)
        self.controller.result_saved.connect(lambda path: self._append_log(f"Saved results to {path}"))
        self.experiment.mode.currentIndexChanged.connect(self.redundancy.mode.setCurrentIndex)
        self.redundancy.mode.currentIndexChanged.connect(self.experiment.mode.setCurrentIndex)

    @staticmethod
    def _scroll(widgets):
        content = QWidget()
        layout = QVBoxLayout(content)
        for widget in widgets:
            layout.addWidget(widget)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _chart(title: str, unit: str, tooltip: str) -> pg.PlotWidget:
        chart = pg.PlotWidget(title=title)
        chart.setMinimumHeight(220)
        chart.setLabel("left", unit)
        chart.setLabel("bottom", "Measured metric")
        chart.showGrid(y=True, alpha=0.25)
        chart.setToolTip(tooltip)
        return chart

    @staticmethod
    def _bars(chart: pg.PlotWidget, labels: list[str], values: list[float], colour: str) -> None:
        chart.clear()
        positions = list(range(len(values)))
        chart.addItem(pg.BarGraphItem(x=positions, height=values, width=0.62, brush=pg.mkBrush(colour)))
        axis = chart.getAxis("bottom")
        axis.setTicks([list(zip(positions, labels))])
        maximum = max(values, default=0.0)
        chart.setYRange(0, max(1.0, maximum * 1.15), padding=0)

    def _start(self) -> None:
        self.log.clear()
        self.summary.setText("Running deterministic reliability experiment...")
        self.controller.start({
            "mode": self.experiment.mode.currentData(),
            "events": self.experiment.events.value(),
            "loss_rate": self.experiment.loss_rate.value(),
            "seed": self.experiment.seed.value(),
            "history": self.experiment.history.value(),
            "snapshots_enabled": self.snapshot.enabled.isChecked(),
            "snapshot_interval": self.snapshot.interval.value(),
            "retention": self.snapshot.retention.value(),
            "scheduler": "round-robin" if self.fairness.policy.currentText() == "Round Robin" else "fcfs",
        })

    def _apply_result(self, result: dict) -> None:
        value = lambda key: result.get(key, "0")
        p50_us = float(value("p50_ns")) / 1_000.0
        p95_us = float(value("p95_ns")) / 1_000.0
        p99_us = float(value("p99_ns")) / 1_000.0
        self.metrics.throughput.setText(f"{float(value('throughput_eps')):,.0f} events/s")
        self.metrics.duplicates.setText(value("duplicates"))
        self.metrics.gaps.setText(value("gaps"))
        self.metrics.recovery_percentiles.setText(f"p50/p95/p99: {p50_us:.1f}/{p95_us:.1f}/{p99_us:.1f} µs")

        gaps = int(value("gaps"))
        self.recovery.gap_label.setText("No gap detected" if gaps == 0 else f"{gaps} detected gap(s)")
        self.recovery.request_label.setText(f"{value('recovery_requests')} request(s), {value('recovered_events')} replayed event(s)")
        self.recovery.latency_label.setText(f"p50 {p50_us:.1f} µs")

        mode_index = {"single": 0, "independent": 1, "correlated": 2}.get(result.get("mode"), 0)
        self.redundancy.mode.setCurrentIndex(mode_index)
        self.redundancy.feed_a.setText(f"{value('feed_a_dropped')} dropped packet(s)")
        self.redundancy.feed_b.setText(f"{value('feed_b_dropped')} dropped packet(s)")
        self.redundancy.avoided_gaps.setText(value("gaps_avoided"))

        self.snapshot.status.setText(f"Sequence {value('checkpoint_sequence')}")
        self.integrity.latest_hash.setText(value("checkpoint_hash"))
        self.integrity.chain_status.setText("Valid" if value("chain_valid") == "1" else "No checkpoint / invalid")

        self.fairness.policy.setCurrentIndex(1 if result.get("scheduler") == "round-robin" else 0)
        self.fairness.waiting_time.setText("Not measured in the in-process demo")
        self.fairness.service_distribution.setText(f"demo-subscriber served {value('scheduler_demo_subscriber')} request(s)")
        self.summary.setText(
            f"Completed {result.get('mode')} run: {value('packets_sent')} source events, {value('packets_lost')} application-level feed drops, "
            f"{value('gaps')} gap(s), {value('recovered_events')} replayed event(s), "
            f"{value('snapshot_recoveries')} snapshot recovery/recoveries."
        )
        self._bars(
            self.delivery_chart,
            ["Source\nevents", "Feed\ndrops", "Duplicates\nsuppressed", "Replayed\nevents"],
            [
                float(value("packets_sent")), float(value("packets_lost")),
                float(value("duplicates")), float(value("recovered_events")),
            ],
            "#2677c9",
        )
        self._bars(
            self.latency_chart,
            ["p50", "p95", "p99"], [p50_us, p95_us, p99_us], "#b35cba",
        )
        self.setCurrentIndex(0)

    def _append_log(self, line: str) -> None:
        self.log.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] [Reliability Experiment] {line}")

    def shutdown(self) -> None:
        self.controller.stop()
