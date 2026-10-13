"""A/B feed comparison placeholder."""

from PyQt6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QLabel


class RedundancyPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Feed redundancy", parent)
        layout = QFormLayout(self)
        self.mode = QComboBox()
        self.mode.addItems(["Single feed", "Dual feed / independent loss", "Dual feed / correlated loss"])
        self.feed_a = QLabel("Unknown")
        self.feed_b = QLabel("Unknown")
        self.avoided_gaps = QLabel("0")
        layout.addRow("Mode", self.mode)
        layout.addRow("Feed A", self.feed_a)
        layout.addRow("Feed B", self.feed_b)
        layout.addRow("Gaps avoided by second feed", self.avoided_gaps)
