"""Live System and Replay Recovery status panel."""

from PyQt6.QtWidgets import QFormLayout, QGroupBox, QLabel


class RecoveryPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Live System & Replay Recovery Status", parent)
        layout = QFormLayout(self)
        self.feed_status_label = QLabel("No active run")
        self.sequence_progress_label = QLabel("No active run")
        self.gap_label = QLabel("No data — run an experiment or load saved results")
        self.request_label = QLabel("No run yet")
        self.latency_label = QLabel("—")
        layout.addRow("Feed A / Feed B status", self.feed_status_label)
        layout.addRow("Sequence progress", self.sequence_progress_label)
        layout.addRow("Missing ranges / gaps", self.gap_label)
        layout.addRow("Replay recovery", self.request_label)
        layout.addRow("Headline TCP recovery latency", self.latency_label)
