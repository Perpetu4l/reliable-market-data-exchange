"""Results from the compiled in-process replay-recovery experiment."""

from PyQt6.QtWidgets import QFormLayout, QGroupBox, QLabel


class RecoveryPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Replay recovery status", parent)
        layout = QFormLayout(self)
        self.gap_label = QLabel("No gap")
        self.request_label = QLabel("No run yet")
        self.latency_label = QLabel("—")
        layout.addRow("Missing range", self.gap_label)
        layout.addRow("Replay recovery", self.request_label)
        layout.addRow("Last latency", self.latency_label)
