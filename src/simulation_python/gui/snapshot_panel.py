"""Snapshot/checkpoint settings and telemetry panel."""

from PyQt6.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QLabel, QSpinBox


class SnapshotPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Snapshots", parent)
        layout = QFormLayout(self)
        self.enabled = QCheckBox()
        self.enabled.setChecked(True)
        self.on_demand = QCheckBox()
        self.on_demand.setChecked(True)
        self.interval = QSpinBox()
        self.interval.setRange(0, 1_000_000_000)
        self.interval.setValue(100)
        self.retention = QSpinBox()
        self.retention.setRange(0, 100_000)
        self.retention.setValue(8)
        self.status = QLabel("No checkpoint data")
        self.too_old_label = QLabel("0")
        self.bytes_label = QLabel("0 B")
        layout.addRow("Periodic checkpoints enabled", self.enabled)
        layout.addRow("On-demand checkpoint fallback", self.on_demand)
        layout.addRow("Interval (events)", self.interval)
        layout.addRow("Retention count", self.retention)
        layout.addRow("Checkpoint status", self.status)
        layout.addRow("TOO_OLD responses", self.too_old_label)
        layout.addRow("Snapshot payload bytes", self.bytes_label)
