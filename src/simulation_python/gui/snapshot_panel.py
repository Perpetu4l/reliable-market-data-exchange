"""Compact Snapshot / Checkpoint configuration panel."""

from PyQt6.QtWidgets import QCheckBox, QGridLayout, QGroupBox, QLabel, QSpinBox


class SnapshotPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Snapshot & Checkpoint Controls", parent)
        grid = QGridLayout(self)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        self.enabled = QCheckBox("Periodic Checkpoints")
        self.enabled.setChecked(True)
        self.on_demand = QCheckBox("On-Demand Fallback")
        self.on_demand.setChecked(True)
        self.interval = QSpinBox()
        self.interval.setRange(0, 1_000_000_000)
        self.interval.setValue(100)
        self.retention = QSpinBox()
        self.retention.setRange(0, 100_000)
        self.retention.setValue(8)

        self.status = QLabel("Ready")
        self.too_old_label = QLabel("0")
        self.bytes_label = QLabel("0 B")

        grid.addWidget(self.enabled, 0, 0, 1, 2)
        grid.addWidget(self.on_demand, 0, 2, 1, 2)
        grid.addWidget(QLabel("<b>Interval:</b>"), 1, 0)
        grid.addWidget(self.interval, 1, 1)
        grid.addWidget(QLabel("<b>Retention:</b>"), 1, 2)
        grid.addWidget(self.retention, 1, 3)
        grid.addWidget(QLabel("<b>TOO_OLD / Bytes:</b>"), 2, 0, 1, 2)
        grid.addWidget(self.too_old_label, 2, 2)
        grid.addWidget(self.bytes_label, 2, 3)
