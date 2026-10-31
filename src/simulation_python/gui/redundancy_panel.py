"""Feed redundancy metrics panel."""

from PyQt6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QLabel


class RedundancyPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Feed Redundancy", parent)
        layout = QFormLayout(self)
        self.mode = QComboBox()
        self.mode.addItems(["Single feed", "Dual feed / independent loss", "Dual feed / correlated loss"])
        self.feed_a = QLabel("No run yet")
        self.feed_b = QLabel("No run yet")
        self.potential_gaps = QLabel("—")
        self.avoided_gaps = QLabel("—")
        self.effectiveness = QLabel("n/a")
        layout.addRow("Mode", self.mode)
        layout.addRow("Feed A", self.feed_a)
        layout.addRow("Feed B", self.feed_b)
        layout.addRow("Potential single-feed gaps", self.potential_gaps)
        layout.addRow("Gaps avoided by Feed B", self.avoided_gaps)
        layout.addRow("Redundancy effectiveness", self.effectiveness)
