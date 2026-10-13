"""Shared recovery scheduler comparison placeholder."""

from PyQt6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QLabel


class FairnessPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Recovery fairness", parent)
        layout = QFormLayout(self)
        self.policy = QComboBox()
        self.policy.addItems(["FCFS", "Round Robin"])
        self.waiting_time = QLabel("—")
        self.service_distribution = QLabel("No requests")
        layout.addRow("Scheduling policy", self.policy)
        layout.addRow("Waiting time", self.waiting_time)
        layout.addRow("Service distribution", self.service_distribution)
