"""Tamper-evident checkpoint and book digest verification panel."""

from PyQt6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QLabel


class HistoryIntegrityPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("History & Book Integrity", parent)
        layout = QFormLayout(self)
        self.tamper_mode = QComboBox()
        self.tamper_mode.addItems(["none", "book_value", "sequence", "previous_hash", "hash"])
        self.latest_hash = QLabel("—")
        self.latest_hash.setWordWrap(True)
        self.chain_status = QLabel("Not verified")
        self.book_match_status = QLabel("No run yet")
        self.book_match_status.setWordWrap(True)
        self.tamper_result = QLabel("None rejected")
        layout.addRow("Experiment tamper mode", self.tamper_mode)
        layout.addRow("Latest book / checkpoint digest", self.latest_hash)
        layout.addRow("Checkpoint chain status", self.chain_status)
        layout.addRow("Authoritative book match (book_matches_exchange)", self.book_match_status)
        layout.addRow("Snapshots rejected (tamper check)", self.tamper_result)
