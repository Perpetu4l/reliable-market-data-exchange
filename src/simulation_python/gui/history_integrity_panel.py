"""Tamper-evident checkpoint results from the reliability experiment."""

from PyQt6.QtWidgets import QFormLayout, QGroupBox, QLabel


class HistoryIntegrityPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Checkpoint integrity", parent)
        layout = QFormLayout(self)
        self.latest_hash = QLabel("—")
        self.chain_status = QLabel("Not verified")
        layout.addRow("Latest hash", self.latest_hash)
        layout.addRow("Chain status", self.chain_status)
