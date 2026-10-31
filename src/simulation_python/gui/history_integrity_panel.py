"""Compact Tamper & Book Integrity controls."""

from PyQt6.QtWidgets import QComboBox, QGridLayout, QGroupBox, QLabel


class HistoryIntegrityPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Tamper & Book Digest Controls", parent)
        grid = QGridLayout(self)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        self.tamper_mode = QComboBox()
        self.tamper_mode.addItems(["none", "book_value", "sequence", "previous_hash", "hash"])
        self.latest_hash = QLabel("—")
        self.latest_hash.setWordWrap(True)
        self.chain_status = QLabel("Ready")
        self.book_match_status = QLabel("No run yet")
        self.book_match_status.setWordWrap(True)
        self.tamper_result = QLabel("0 rejected")

        grid.addWidget(QLabel("<b>Tamper Mode:</b>"), 0, 0)
        grid.addWidget(self.tamper_mode, 0, 1)
        grid.addWidget(QLabel("<b>Rejected:</b>"), 0, 2)
        grid.addWidget(self.tamper_result, 0, 3)
        grid.addWidget(QLabel("<b>Book Match:</b>"), 1, 0)
        grid.addWidget(self.book_match_status, 1, 1, 1, 3)
        grid.addWidget(QLabel("<b>Chain State:</b>"), 2, 0)
        grid.addWidget(self.chain_status, 2, 1, 1, 3)
