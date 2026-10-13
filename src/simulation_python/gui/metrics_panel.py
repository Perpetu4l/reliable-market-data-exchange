"""Telemetry view placeholder for future pybind11-backed metric snapshots."""

from PyQt6.QtWidgets import QFormLayout, QGroupBox, QLabel


class MetricsPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Metrics", parent)
        layout = QFormLayout(self)
        self.throughput = QLabel("—")
        self.duplicates = QLabel("0")
        self.gaps = QLabel("0")
        self.recovery_percentiles = QLabel("p50/p95/p99: —")
        layout.addRow("Throughput", self.throughput)
        layout.addRow("Duplicates", self.duplicates)
        layout.addRow("Gaps", self.gaps)
        layout.addRow("Recovery latency", self.recovery_percentiles)
