"""Measured metrics panel with per-class latency and TCP request RTT."""

from PyQt6.QtWidgets import QFormLayout, QGroupBox, QLabel


class MetricsPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Metrics (Receive-Window Throughput & Per-Event Latency by Class)", parent)
        layout = QFormLayout(self)
        self.provenance = QLabel("No run loaded")
        self.provenance.setWordWrap(True)
        self.throughput = QLabel("—")
        self.duplicates = QLabel("0")
        self.gaps = QLabel("0")
        self.recovery_percentiles = QLabel("p50/p95/p99/p99.9/max: —")
        self.recovery_percentiles.setWordWrap(True)
        self.other_feed_percentiles = QLabel("p50/p95/p99: —")
        self.snapshot_percentiles = QLabel("p50/p95/p99: —")
        self.tcp_rtt_percentiles = QLabel("p50/p95/p99: — (per-request TCP RTT, excludes grace wait)")

        layout.addRow("Provenance", self.provenance)
        layout.addRow("Receive-window throughput", self.throughput)
        layout.addRow("Duplicates suppressed", self.duplicates)
        layout.addRow("Gap detections / ranges requested", self.gaps)
        layout.addRow("Class tcp_recovery latency", self.recovery_percentiles)
        layout.addRow("Class other_feed latency", self.other_feed_percentiles)
        layout.addRow("Class snapshot latency", self.snapshot_percentiles)
        layout.addRow("Separate tcp_request_rtt", self.tcp_rtt_percentiles)
