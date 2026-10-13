"""Live recovery status placeholder; future bindings will update these fields."""

from PyQt6.QtWidgets import QFormLayout, QGroupBox, QLabel


class RecoveryPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Recovery status", parent)
        layout = QFormLayout(self)
        self.gap_label = QLabel("No gap")
        self.request_label = QLabel("Idle")
        self.latency_label = QLabel("—")
        layout.addRow("Missing range", self.gap_label)
        layout.addRow("TCP recovery", self.request_label)
        layout.addRow("Last latency", self.latency_label)
