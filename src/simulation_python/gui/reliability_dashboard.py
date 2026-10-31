"""Reliability dashboard with real data-driven panels and 6 pyqtgraph charts."""

from __future__ import annotations

import csv
import json
import math
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters
from PyQt6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .experiment_panel import ExperimentPanel
from .fairness_panel import FairnessPanel
from .history_integrity_panel import HistoryIntegrityPanel
from .metrics_panel import MetricsPanel
from .recovery_panel import RecoveryPanel
from .redundancy_panel import RedundancyPanel
from .reliability_controller import REPOSITORY_ROOT, ReliabilityExperimentController
from .snapshot_panel import SnapshotPanel


class ReliabilityDashboard(QTabWidget):
    """Control room for live reliability experiments, preset sweeps, and CSV-backed charts."""

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
        self.log.setMaximumBlockCount(400)
        self.log.setMinimumHeight(140)

        self.summary = QLabel(
            "Empty state — no experiment data loaded. Run a live experiment, run a preset sweep, or open a saved run directory."
        )
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("padding: 8px; border: 1px solid #888; border-radius: 4px; font-weight: 600;")

        self.provenance_banner = QLabel("Provenance: no active dataset")
        self.provenance_banner.setWordWrap(True)
        self.provenance_banner.setStyleSheet("color: #444; font-size: 12px; padding: 4px;")

        self.last_loaded_csv_path: Path | None = None

        # 6 required data-driven charts
        self.chart1_recovery_vs_loss = self._chart(
            "1. Events Needing Recovery (%) vs Loss Rate", "Recovery Needed (%)", "Application Loss Rate (%)"
        )
        self.chart2_latency_classes = self._chart(
            "2. Recovery Latency Percentiles by Class / vs Subscribers", "Latency (µs)", "Class / N Subscribers"
        )
        self.chart3_fcfs_vs_rr = self._chart(
            "3. Scheduler Contention: FCFS vs Round-Robin Queue Wait", "Queue Wait (ms)", "Policy & Subscriber Role"
        )
        self.chart4_history_snapshot = self._chart(
            "4. History Capacity & Snapshot Interval vs Fallback", "Count / Bytes", "Configuration"
        )
        self.chart5_redundancy_eff = self._chart(
            "5. Redundancy Effectiveness vs Loss Rate", "Effectiveness (0..1)", "Application Loss Rate (%)"
        )
        self.chart6_sequence_timeline = self._chart(
            "6. Subscriber Sequence Progress / Delivery Breakdown", "Sequence / Events", "Subscriber"
        )

        export_row = QHBoxLayout()
        self.export_png_btn = QPushButton("Export Charts PNG")
        self.export_png_btn.clicked.connect(self._export_charts_png)
        self.export_csv_btn = QPushButton("Export Active CSV")
        self.export_csv_btn.clicked.connect(self._export_active_csv)
        self.reload_agg_btn = QPushButton("Load Aggregated Preset CSVs from results/")
        self.reload_agg_btn.clicked.connect(self.load_aggregate_csvs)
        export_row.addWidget(self.export_png_btn)
        export_row.addWidget(self.export_csv_btn)
        export_row.addWidget(self.reload_agg_btn)

        charts_grid = QWidget()
        grid = QGridLayout(charts_grid)
        grid.addWidget(self.chart1_recovery_vs_loss, 0, 0)
        grid.addWidget(self.chart2_latency_classes, 0, 1)
        grid.addWidget(self.chart3_fcfs_vs_rr, 1, 0)
        grid.addWidget(self.chart4_history_snapshot, 1, 1)
        grid.addWidget(self.chart5_redundancy_eff, 2, 0)
        grid.addWidget(self.chart6_sequence_timeline, 2, 1)

        export_container = QWidget()
        export_container.setLayout(export_row)

        self.addTab(self._scroll([
            self.summary,
            self.provenance_banner,
            export_container,
            self.recovery,
            self.redundancy,
            self.metrics,
            self.snapshot,
            self.fairness,
            self.integrity,
            charts_grid,
            self.log,
        ]), "Results & Telemetry")
        self.addTab(self._scroll([self.experiment, self.snapshot, self.fairness, self.integrity]), "Configure & Run")

        self.experiment.start_button.clicked.connect(self._start)
        self.experiment.live_start_button.clicked.connect(self._start_live)
        self.experiment.run_preset_button.clicked.connect(self._start_preset)
        self.experiment.stop_button.clicked.connect(self.controller.stop)
        self.experiment.show_theory_checkbox.toggled.connect(lambda _v: self.load_aggregate_csvs())
        self.experiment.log_y_checkbox.toggled.connect(lambda _v: self.load_aggregate_csvs())

        self.controller.log_line.connect(self._append_log)
        self.controller.status_changed.connect(self.experiment.status.setText)
        self.controller.progress_changed.connect(self.experiment.progress.setValue)
        self.controller.result_ready.connect(self._apply_result)
        self.controller.live_result_ready.connect(self._apply_live_result)
        self.controller.preset_result_ready.connect(self._apply_preset_result)
        self.controller.result_saved.connect(lambda path: self._append_log(f"Saved results to {path}"))
        self.experiment.mode.currentIndexChanged.connect(self.redundancy.mode.setCurrentIndex)
        self.redundancy.mode.currentIndexChanged.connect(self.experiment.mode.setCurrentIndex)

        self._render_empty_charts()

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
    def _chart(title: str, y_label: str, x_label: str) -> pg.PlotWidget:
        chart = pg.PlotWidget(title=title)
        chart.setMinimumHeight(230)
        chart.setBackground("w")
        chart.setLabel("left", y_label)
        chart.setLabel("bottom", x_label)
        chart.showGrid(x=True, y=True, alpha=0.25)
        return chart

    def _render_empty_charts(self) -> None:
        for ch in (
            self.chart1_recovery_vs_loss,
            self.chart2_latency_classes,
            self.chart3_fcfs_vs_rr,
            self.chart4_history_snapshot,
            self.chart5_redundancy_eff,
            self.chart6_sequence_timeline,
        ):
            ch.clear()
            text = pg.TextItem("No data loaded — run an experiment or load saved CSVs", color=(120, 120, 120))
            ch.addItem(text)

    @staticmethod
    def _bars(chart: pg.PlotWidget, labels: list[str], values: list[float], colour: str, subtitle: str = "") -> None:
        chart.clear()
        if subtitle:
            chart.setTitle(subtitle)
        if not values:
            chart.addItem(pg.TextItem("No data available", color=(120, 120, 120)))
            return
        positions = list(range(len(values)))
        chart.addItem(pg.BarGraphItem(x=positions, height=values, width=0.6, brush=pg.mkBrush(colour)))
        axis = chart.getAxis("bottom")
        axis.setTicks([list(zip(positions, labels))])
        maximum = max(values, default=0.0)
        chart.setYRange(0, max(1.0, maximum * 1.18), padding=0)

    def _start(self) -> None:
        self.log.clear()
        self.summary.setText("Running deterministic in-process baseline...")
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

    def _start_live(self) -> None:
        self.log.clear()
        self.summary.setText("Starting live exchange, subscriber(s), and workload_client processes...")
        exec_mode = self.experiment.execution_mode.currentData()
        iface = "127.0.0.1" if exec_mode != "udp_lan" else (self.experiment.lan_interface.text().strip() or "127.0.0.1")
        self.controller.start_live({
            "mode": self.experiment.mode.currentData(),
            "interface": iface,
            "duration": self.experiment.live_duration.value(),
            "subscribers": self.experiment.live_subscribers.value(),
            "loss_rate": self.experiment.loss_rate.value(),
            "seed": self.experiment.seed.value(),
            "history": self.experiment.history.value(),
            "snapshots_enabled": self.snapshot.enabled.isChecked(),
            "snapshot_on_demand": self.snapshot.on_demand.isChecked(),
            "snapshot_interval": self.snapshot.interval.value(),
            "retention": self.snapshot.retention.value(),
            "scheduler": "round-robin" if self.fairness.policy.currentText() == "Round Robin" else "fcfs",
            "workers": self.fairness.workers.value(),
            "delay_per_req_us": self.fairness.delay_per_req_us.value(),
            "delay_per_event_us": self.fairness.delay_per_event_us.value(),
            "tamper_mode": self.integrity.tamper_mode.currentText(),
        })

    def _start_preset(self) -> None:
        self.log.clear()
        preset = str(self.experiment.preset_combo.currentData())
        size = str(self.experiment.preset_size.currentData())
        iface = self.experiment.lan_interface.text().strip() or "127.0.0.1"
        self.summary.setText(f"Running preset sweep '{preset}' ({size})...")
        self.controller.start_preset(preset, size, iface)

    def _apply_result(self, result: dict) -> None:
        value = lambda key: result.get(key, "0")
        p50_us = float(value("p50_ns")) / 1_000.0
        p95_us = float(value("p95_ns")) / 1_000.0
        p99_us = float(value("p99_ns")) / 1_000.0
        self.provenance_banner.setText(
            f"Provenance: transport=inprocess | loss_injection=app_level_publisher_side | "
            f"events={value('events')} | loss={value('loss')}"
        )
        self.metrics.provenance.setText("transport=inprocess (deterministic single-process baseline)")
        self.metrics.throughput.setText(f"{float(value('throughput_eps')):,.0f} events/s")
        self.metrics.duplicates.setText(value("duplicates"))
        self.metrics.gaps.setText(value("gaps"))
        self.metrics.recovery_percentiles.setText(f"p50/p95/p99: {p50_us:.1f}/{p95_us:.1f}/{p99_us:.1f} µs")

        gaps = int(value("gaps"))
        self.recovery.feed_status_label.setText("In-process Feed A/B active")
        self.recovery.sequence_progress_label.setText(f"{value('events')} / {value('events')} events")
        self.recovery.gap_label.setText("No gap detected" if gaps == 0 else f"{gaps} detected gap(s)")
        self.recovery.request_label.setText(f"{value('recovery_requests')} request(s), {value('recovered_events')} replayed event(s)")
        self.recovery.latency_label.setText(f"p50 {p50_us:.1f} µs")

        self.redundancy.feed_a.setText(f"{value('feed_a_dropped')} dropped packet(s)")
        self.redundancy.feed_b.setText(f"{value('feed_b_dropped')} dropped packet(s)")
        self.redundancy.potential_gaps.setText(value("feed_a_dropped"))
        self.redundancy.avoided_gaps.setText(value("gaps_avoided"))
        self.redundancy.effectiveness.setText(value("redundancy_effectiveness"))

        self.snapshot.status.setText(f"Sequence {value('checkpoint_sequence')}")
        self.integrity.latest_hash.setText(value("checkpoint_hash"))
        self.integrity.chain_status.setText("Valid" if value("chain_valid") == "1" else "No checkpoint / invalid")
        self.integrity.book_match_status.setText("In-process baseline (sequence continuity verified)")

        self.summary.setStyleSheet("padding: 8px; border: 2px solid #197b30; color: #197b30; font-weight: 700;")
        self.summary.setText(
            f"Completed in-process {result.get('mode')} baseline: {value('packets_sent')} events, "
            f"{value('gaps')} gap(s), {value('recovered_events')} replayed event(s)."
        )
        self._bars(
            self.chart6_sequence_timeline,
            ["Source\nevents", "Feed\ndrops", "Duplicates\nsuppressed", "Replayed\nevents"],
            [float(value("packets_sent")), float(value("packets_lost")),
             float(value("duplicates")), float(value("recovered_events"))],
            "#2677c9",
            "6. In-Process Delivery Breakdown (transport=inprocess)",
        )
        self._bars(
            self.chart2_latency_classes,
            ["p50", "p95", "p99"], [p50_us, p95_us, p99_us], "#b35cba",
            "2. In-Process Recovery Latency (µs)",
        )
        self.setCurrentIndex(0)

    def load_path(self, path_str: str) -> bool:
        """Load a run directory, run.json, or CSV file into the dashboard."""
        p = Path(path_str)
        if p.is_dir():
            run_json = p / "run.json"
            summary_csv = p / "run-summary.csv"
            if summary_csv.is_file():
                self.last_loaded_csv_path = summary_csv
            if run_json.is_file():
                try:
                    data = json.loads(run_json.read_text(encoding="utf-8"))
                    self._apply_live_result(data)
                    return True
                except Exception as exc:
                    self._append_log(f"Failed to load {run_json}: {exc}")
                    return False
        elif p.is_file():
            if p.suffix == ".json":
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    self._apply_live_result(data)
                    return True
                except Exception as exc:
                    self._append_log(f"Failed to load {p}: {exc}")
                    return False
            elif p.suffix == ".csv":
                self.last_loaded_csv_path = p
                self.load_aggregate_csvs(p.parent)
                return True
        return False

    def _apply_live_result(self, result: dict) -> None:
        """Render real live-run telemetry and highlight any integrity failure in red."""
        raw_metrics = result.get("subscriber_metrics") or {}
        exchange = result.get("exchange_metrics") or {}
        cfg = result.get("config") or {}
        run_dir = result.get("run_dir", "")
        if run_dir and (Path(run_dir) / "run-summary.csv").is_file():
            self.last_loaded_csv_path = Path(run_dir) / "run-summary.csv"

        records = [v for v in raw_metrics.values() if isinstance(v, dict)]
        if not records and raw_metrics and all(isinstance(v, str) for v in raw_metrics.values()):
            records = [raw_metrics]

        if not records:
            self.summary.setStyleSheet("padding: 8px; border: 2px solid #b42318; color: #b42318; font-weight: 700;")
            self.summary.setText(f"Live run failed before subscriber completion: {result.get('error', 'unknown error')}")
            self.setCurrentIndex(0)
            return

        r0 = records[0]
        transport = r0.get("transport", cfg.get("transport", "udp_loopback"))
        loss_inj = r0.get("loss_injection", cfg.get("loss_injection", "app_level_receive_side"))
        delay_req = exchange.get("service_delay_us_per_request", str(cfg.get("service_delay_us_per_request", 0)))
        delay_ev = exchange.get("service_delay_us_per_event", str(cfg.get("service_delay_us_per_event", 0)))
        prov_text = (
            f"Provenance: transport={transport} | loss_injection={loss_inj} | "
            f"subscribers={len(records)} | events={r0.get('events_expected','0')} | "
            f"modelled_delay_us(req/event)={delay_req}/{delay_ev}"
        )
        self.provenance_banner.setText(prov_text)
        self.metrics.provenance.setText(prov_text)

        # Sum count fields across subscribers
        def sum_int(field: str) -> int:
            return sum(int(rec.get(field, "0") or 0) for rec in records)

        total_delivered = sum_int("delivered")
        total_dups = sum_int("duplicates_suppressed") or sum_int("duplicates")
        total_gaps = sum_int("gap_detections") or sum_int("gaps")
        total_ranges = sum_int("gap_ranges_requested")
        total_reqs = sum_int("recovery_requests")
        total_rec_ev = sum_int("recovered_events")
        total_snaps = sum_int("snapshot_recoveries")
        total_snap_cov = sum_int("events_covered_by_snapshot")
        total_too_old = sum_int("too_old_responses")
        total_snap_rej = sum_int("snapshot_rejected")
        total_lost = sum_int("permanently_lost")
        total_pot_gaps = sum_int("potential_single_feed_gaps")
        total_avoided = sum_int("gaps_avoided_by_second_feed")

        self.recovery.feed_status_label.setText(
            f"{len(records)} subscriber(s) reconciled; feed_mode={r0.get('feed_mode','dual')}"
        )
        self.recovery.sequence_progress_label.setText(
            f"Cut sequence L={r0.get('events_expected','0')}; total delivered={total_delivered}"
        )
        self.recovery.gap_label.setText(
            f"{total_gaps} gap detection(s), {total_ranges} range(s) requested over TCP"
        )
        self.recovery.request_label.setText(
            f"{total_reqs} TCP request(s), {total_rec_ev} replayed event(s)"
        )

        # Latency formatting with sample count and insufficient-sample greying
        n_tcp = int(r0.get("recovery_n", "0") or 0)
        insuff_tcp = r0.get("insufficient_samples", "1") == "1"
        p50_tcp = float(r0.get("recovery_p50_ns", "0") or 0) / 1000.0
        p95_tcp = float(r0.get("recovery_p95_ns", "0") or 0) / 1000.0
        p99_tcp = float(r0.get("recovery_p99_ns", "0") or 0) / 1000.0
        p999_tcp = float(r0.get("recovery_p999_ns", "0") or 0) / 1000.0
        max_tcp = float(r0.get("recovery_max_ns", "0") or 0) / 1000.0
        tcp_lat_str = (
            f"p50={p50_tcp:.1f} µs, p95={p95_tcp:.1f} µs, p99={p99_tcp:.1f} µs, "
            f"p99.9={p999_tcp:.1f} µs, max={max_tcp:.1f} µs (n={n_tcp})"
            + (" [insufficient samples: n<100]" if insuff_tcp else "")
            if n_tcp > 0 else "n=0 (no TCP recovery events)"
        )
        self.recovery.latency_label.setText(tcp_lat_str)
        self.metrics.recovery_percentiles.setText(tcp_lat_str)
        self.metrics.recovery_percentiles.setStyleSheet("color: #777777;" if insuff_tcp else "color: #000000;")

        n_oth = int(r0.get("other_feed_n", "0") or 0)
        p50_oth = float(r0.get("other_feed_p50_ns", "0") or 0) / 1000.0
        p99_oth = float(r0.get("other_feed_p99_ns", "0") or 0) / 1000.0
        self.metrics.other_feed_percentiles.setText(
            f"p50={p50_oth:.1f} µs, p99={p99_oth:.1f} µs (n={n_oth})" if n_oth > 0 else "n=0"
        )

        n_snp = int(r0.get("snapshot_lat_n", "0") or 0)
        p50_snp = float(r0.get("snapshot_lat_p50_ns", "0") or 0) / 1000.0
        p99_snp = float(r0.get("snapshot_lat_p99_ns", "0") or 0) / 1000.0
        self.metrics.snapshot_percentiles.setText(
            f"p50={p50_snp:.1f} µs, p99={p99_snp:.1f} µs (n={n_snp})" if n_snp > 0 else "n=0"
        )

        n_rtt = int(r0.get("tcp_request_rtt_n", "0") or 0)
        p50_rtt = float(r0.get("tcp_request_rtt_p50_ns", "0") or 0) / 1000.0
        p99_rtt = float(r0.get("tcp_request_rtt_p99_ns", "0") or 0) / 1000.0
        self.metrics.tcp_rtt_percentiles.setText(
            f"p50={p50_rtt:.1f} µs, p99={p99_rtt:.1f} µs (n={n_rtt} requests, excludes grace wait)"
            if n_rtt > 0 else "n=0"
        )

        rate = float(r0.get("delivered_events_per_second", "0") or 0)
        self.metrics.throughput.setText(f"{rate:,.1f} events/s (receive window, subscriber 1)")
        self.metrics.duplicates.setText(str(total_dups))
        self.metrics.gaps.setText(f"{total_gaps} detections / {total_ranges} TCP ranges")

        # Redundancy panel
        self.redundancy.feed_a.setText(f"delivered_via_a={sum_int('delivered_via_feed_a')}, dropped_a={sum_int('dropped_by_injector_a')}")
        self.redundancy.feed_b.setText(f"delivered_via_b={sum_int('delivered_via_feed_b')}, dropped_b={sum_int('dropped_by_injector_b')}")
        self.redundancy.potential_gaps.setText(str(total_pot_gaps))
        self.redundancy.avoided_gaps.setText(str(total_avoided))
        eff_str = f"{(total_avoided / total_pot_gaps):.4f}" if total_pot_gaps > 0 else "n/a"
        self.redundancy.effectiveness.setText(eff_str)

        # Snapshot & History Integrity panels
        self.snapshot.status.setText(
            f"retained={exchange.get('snapshots_retained','0')}, on_demand_created={exchange.get('on_demand_snapshots','0')}, "
            f"applied={total_snaps}, covered_events={total_snap_cov}"
        )
        self.snapshot.too_old_label.setText(str(total_too_old))
        self.snapshot.bytes_label.setText(f"{exchange.get('snapshot_bytes_sent','0')} B")

        self.integrity.latest_hash.setText(r0.get("book_digest_final", exchange.get("latest_book_digest", "—")))
        all_books_match = all(rec.get("book_matches_exchange") == "1" for rec in records)
        self.integrity.chain_status.setText(
            "REJECTED (tamper detected)" if total_snap_rej > 0
            else ("Verified SHA-256 checkpoint(s)" if total_snaps > 0 else "Verified via direct event stream")
        )
        self.integrity.book_match_status.setText(
            f"1 (All {len(records)} subscriber books match authoritative exchange digest)"
            if all_books_match
            else f"0 (Mismatch reason: {r0.get('book_mismatch_reason', r0.get('reason', 'mismatch'))})"
        )
        self.integrity.book_match_status.setStyleSheet(
            "font-weight: 700; color: #197b30;" if all_books_match else "font-weight: 700; color: #b42318;"
        )
        self.integrity.tamper_result.setText(f"{total_snap_rej} rejected snapshot(s)")

        # Fairness panel
        wait_p50 = exchange.get("per_subscriber_wait_p50_ns", "none")
        wait_p99 = exchange.get("per_subscriber_wait_p99_ns", "none")
        wait_max = exchange.get("per_subscriber_wait_max_ns", "none")
        self.fairness.waiting_time.setText(f"p50={wait_p50} ns | p99={wait_p99} ns | max={wait_max} ns")
        self.fairness.service_distribution.setText(exchange.get("recovery_service_distribution", "none"))
        raw_jain = float(exchange.get("recovery_fairness_jain_ppm", "0") or 0) / 1e6
        sat_jain = float(exchange.get("recovery_fairness_saturated_jain_ppm", "0") or 0) / 1e6
        self.fairness.jain_label.setText(
            f"saturated_window={sat_jain:.4f}, overall={raw_jain:.4f} (reflects demand ratio; see queue wait)"
        )

        # Charts for this live run
        sub_labels = [rec.get("subscriber_id", f"S{i+1}") for i, rec in enumerate(records)]
        sub_deliv = [float(rec.get("delivered", "0") or 0) for rec in records]
        self._bars(
            self.chart6_sequence_timeline,
            sub_labels,
            sub_deliv,
            "#197b30" if all_books_match else "#b42318",
            f"6. Delivered Events per Subscriber ({prov_text})",
        )
        self._bars(
            self.chart2_latency_classes,
            ["tcp_recovery\np50", "tcp_recovery\np99", "other_feed\np50", "snapshot\np50", "tcp_rtt\np50"],
            [p50_tcp, p99_tcp, p50_oth, p50_snp, p50_rtt],
            "#888888" if insuff_tcp else "#b35cba",
            f"2. Per-Event Latency by Class (µs, n_tcp={n_tcp})",
        )

        all_completed = all(rec.get("run_status") == "completed" and rec.get("book_matches_exchange") == "1" for rec in records)
        expected_fail = bool(result.get("expected_failure_observed"))
        if all_completed:
            self.summary.setStyleSheet("padding: 8px; border: 2px solid #197b30; color: #197b30; font-weight: 700;")
            self.summary.setText(
                f"LIVE RUN PASSED: {len(records)} subscriber(s), L={r0.get('events_expected','0')} events accounted for, "
                f"permanently_lost=0, book_matches_exchange=1."
            )
        elif expected_fail:
            self.summary.setStyleSheet("padding: 8px; border: 2px solid #b42318; color: #b42318; font-weight: 700;")
            self.summary.setText(
                f"EXPECTED FAILURE OBSERVED (negative control / tamper): run_status={r0.get('run_status')}, "
                f"reason={r0.get('reason')}, permanently_lost={total_lost}, snapshot_rejected={total_snap_rej}."
            )
        else:
            self.summary.setStyleSheet("padding: 8px; border: 2px solid #b42318; color: #b42318; font-weight: 700;")
            self.summary.setText(
                f"LIVE RUN FAILED INTEGRITY: run_status={r0.get('run_status')}, reason={r0.get('reason')}, "
                f"permanently_lost={total_lost}, recovery_failures={sum_int('recovery_failures')}."
            )
        self.setCurrentIndex(0)

    def _apply_preset_result(self, result: dict) -> None:
        ok = bool(result.get("success"))
        presets = result.get("presets", [])
        if ok:
            self.summary.setStyleSheet("padding: 8px; border: 2px solid #197b30; color: #197b30; font-weight: 700;")
            self.summary.setText(f"Preset sweep completed ({len(presets)} preset(s)). Rendering aggregated charts from CSV...")
        else:
            self.summary.setStyleSheet("padding: 8px; border: 2px solid #b42318; color: #b42318; font-weight: 700;")
            self.summary.setText("Preset sweep reported one or more failed runs. Inspect red states and logs below.")
        self.load_aggregate_csvs()
        self.setCurrentIndex(0)

    def load_aggregate_csvs(self, results_dir: Path | None = None) -> None:
        res_dir = results_dir or (REPOSITORY_ROOT / "results")
        if not res_dir.is_dir():
            return

        # Chart 1 & Chart 5 from aggregate-redundancy_sweep.csv
        red_csv = res_dir / "aggregate-redundancy_sweep.csv"
        if red_csv.is_file():
            self.last_loaded_csv_path = red_csv
            with red_csv.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            if rows:
                r0 = rows[0]
                sub = (
                    f"transport={r0.get('transport','udp_loopback')}, "
                    f"loss={r0.get('loss_injection','app_level_receive_side')}, "
                    f"seeds={r0.get('n_runs','5')}"
                )
                self.provenance_banner.setText(f"Provenance: {sub}")
                self.chart1_recovery_vs_loss.clear()
                self.chart1_recovery_vs_loss.setTitle(f"1. Events Needing Recovery (%) vs Loss ({sub})")
                self.chart5_redundancy_eff.clear()
                self.chart5_redundancy_eff.setTitle(f"5. Redundancy Effectiveness vs Loss ({sub})")

                colours = {"single": "#d95f02", "dual_independent": "#1b9e77", "dual_correlated": "#7570b3"}
                by_mode: dict[str, list[dict[str, str]]] = {}
                for r in rows:
                    by_mode.setdefault(r.get("mode_group", "single"), []).append(r)

                all_losses: set[float] = set()
                log_y = self.experiment.log_y_checkbox.isChecked()
                for mode, mrows in by_mode.items():
                    mrows.sort(key=lambda x: float(x.get("configured_loss", "0") or 0))
                    xs = [float(x.get("configured_loss", "0") or 0) * 100.0 for x in mrows]
                    all_losses.update(float(x.get("configured_loss", "0") or 0) for x in mrows)
                    ys = [float(x.get("events_needing_recovery_pct_mean", "0") or 0) for x in mrows]
                    sds = [float(x.get("events_needing_recovery_pct_sd", "0") or 0) for x in mrows]
                    if log_y:
                        ys = [math.log10(max(1e-3, y)) for y in ys]
                    c = colours.get(mode, "#2677c9")
                    self.chart1_recovery_vs_loss.plot(xs, ys, pen=pg.mkPen(c, width=2), symbol="o", name=mode)
                    if not log_y and xs:
                        err = pg.ErrorBarItem(
                            x=np.array(xs, dtype=float),
                            y=np.array(ys, dtype=float),
                            top=np.array(sds, dtype=float),
                            bottom=np.array(sds, dtype=float),
                            beam=0.3,
                        )
                        self.chart1_recovery_vs_loss.addItem(err)

                    # Chart 5: Redundancy effectiveness
                    eff_xs = [float(x.get("configured_loss", "0") or 0) * 100.0 for x in mrows if float(x.get("configured_loss", "0") or 0) > 0]
                    eff_ys = [float(x.get("redundancy_effectiveness_mean", "0") or 0) for x in mrows if float(x.get("configured_loss", "0") or 0) > 0]
                    if eff_xs:
                        self.chart5_redundancy_eff.plot(eff_xs, eff_ys, pen=pg.mkPen(c, width=2), symbol="s", name=mode)

                if self.experiment.show_theory_checkbox.isChecked() and all_losses and not log_y:
                    ps = sorted(all_losses)
                    tx = [p * 100.0 for p in ps]
                    self.chart1_recovery_vs_loss.plot(
                        tx, [p * 100.0 for p in ps],
                        pen=pg.mkPen("#555555", width=2, style=pg.QtCore.Qt.PenStyle.DashLine),
                        name="theory: p",
                    )
                    self.chart1_recovery_vs_loss.plot(
                        tx, [(p * p) * 100.0 for p in ps],
                        pen=pg.mkPen("#111111", width=2, style=pg.QtCore.Qt.PenStyle.DotLine),
                        name="theory: p^2",
                    )

        # Chart 3 from aggregate-scheduler_contention.csv
        sched_csv = res_dir / "aggregate-scheduler_contention.csv"
        if sched_csv.is_file():
            with sched_csv.open(newline="", encoding="utf-8") as f:
                srows = list(csv.DictReader(f))
            if srows:
                labels = [f"{r.get('recovery_policy')}\n{r.get('role')}" for r in srows]
                vals_ms = [float(r.get("server_wait_p99_ns_mean", "0") or 0) / 1e6 for r in srows]
                fcfs_q = next((float(r.get("server_wait_p99_ns_mean", "0") or 0) for r in srows if r.get("recovery_policy") == "fcfs" and r.get("role") == "quiet"), 0.0)
                rr_q = next((float(r.get("server_wait_p99_ns_mean", "0") or 0) for r in srows if r.get("recovery_policy") == "round-robin" and r.get("role") == "quiet"), 0.0)
                factor_str = f"{(fcfs_q / rr_q):.2f}x lower quiet p99 wait under RR" if rr_q > 0 else ""
                self.fairness.noisy_quiet_ratio.setText(factor_str or "Measured in chart")
                self._bars(
                    self.chart3_fcfs_vs_rr,
                    labels,
                    vals_ms,
                    "#2b8cbe",
                    f"3. FCFS vs RR Queue Wait p99 (ms) — {factor_str}",
                )

        # Chart 4 from aggregate-history_snapshot_sweep.csv
        hs_csv = res_dir / "aggregate-history_snapshot_sweep.csv"
        if hs_csv.is_file():
            with hs_csv.open(newline="", encoding="utf-8") as f:
                hrows = list(csv.DictReader(f))
            if hrows:
                labels = [f"H={r.get('history_events')}\nS={r.get('snapshot_interval')}" for r in hrows]
                snaps = [float(r.get("snapshot_recoveries_mean", "0") or 0) for r in hrows]
                self._bars(
                    self.chart4_history_snapshot,
                    labels,
                    snaps,
                    "#e6550d",
                    "4. Snapshot Recoveries vs (History, Snapshot Interval)",
                )

        # Chart 2 from aggregate-scalability.csv
        sc_csv = res_dir / "aggregate-scalability.csv"
        if sc_csv.is_file():
            with sc_csv.open(newline="", encoding="utf-8") as f:
                scrows = list(csv.DictReader(f))
            if scrows:
                scrows.sort(key=lambda r: int(r.get("n_subscribers", "0") or 0))
                labels = [f"N={r.get('n_subscribers')}" for r in scrows]
                p99_us = [float(r.get("tcp_recovery_p99_ns_mean", "0") or 0) / 1e3 for r in scrows]
                self._bars(
                    self.chart2_latency_classes,
                    labels,
                    p99_us,
                    "#756bb1",
                    "2. TCP Recovery Latency p99 (µs) vs N Subscribers",
                )

    def _export_charts_png(self) -> None:
        out_dir = REPOSITORY_ROOT / "results" / "charts"
        out_dir.mkdir(parents=True, exist_ok=True)
        charts = [
            ("chart1_recovery_vs_loss.png", self.chart1_recovery_vs_loss),
            ("chart2_latency_classes.png", self.chart2_latency_classes),
            ("chart3_fcfs_vs_rr.png", self.chart3_fcfs_vs_rr),
            ("chart4_history_snapshot.png", self.chart4_history_snapshot),
            ("chart5_redundancy_eff.png", self.chart5_redundancy_eff),
            ("chart6_sequence_timeline.png", self.chart6_sequence_timeline),
        ]
        for fname, widget in charts:
            exporter = pg.exporters.ImageExporter(widget.plotItem)
            exporter.export(str(out_dir / fname))
        self._append_log(f"Exported 6 chart PNGs to {out_dir}")

    def _export_active_csv(self) -> None:
        if not self.last_loaded_csv_path or not self.last_loaded_csv_path.is_file():
            self._append_log("No active CSV loaded to export.")
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Export CSV", str(self.last_loaded_csv_path.name), "CSV Files (*.csv)")
        if dest:
            shutil.copyfile(self.last_loaded_csv_path, dest)
            self._append_log(f"Exported CSV to {dest}")

    def _append_log(self, line: str) -> None:
        self.log.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] [Reliability Dashboard] {line}")

    def shutdown(self) -> None:
        self.controller.stop()
