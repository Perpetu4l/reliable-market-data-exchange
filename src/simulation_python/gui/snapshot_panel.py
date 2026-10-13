"""Snapshot/checkpoint configuration placeholder."""

from PyQt6.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QLabel, QSpinBox


class SnapshotPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Snapshots", parent)
        layout = QFormLayout(self)
        self.enabled = QCheckBox()
        self.interval = QSpinBox()
        self.interval.setRange(0, 1_000_000_000)
        self.retention = QSpinBox()
        self.retention.setRange(0, 100_000)
        self.status = QLabel("No checkpoint")
        layout.addRow("Enabled", self.enabled)
        layout.addRow("Interval (events)", self.interval)
        layout.addRow("Retention", self.retention)
        layout.addRow("Latest checkpoint", self.status)
