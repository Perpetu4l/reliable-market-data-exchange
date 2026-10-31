"""Spacious Tamper & Book Integrity controls."""

from PyQt6.QtWidgets import QComboBox, QGridLayout, QGroupBox, QLabel, QSizePolicy


class HistoryIntegrityPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Tamper & Book Digest Controls", parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(14, 14, 14, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self.tamper_mode = QComboBox()
        self.tamper_mode.addItems(["none", "book_value", "sequence", "previous_hash", "hash"])
        self.tamper_mode.setMinimumHeight(32)
        self.tamper_mode.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.latest_hash = QLabel("—")
        self.latest_hash.setWordWrap(True)
        self.chain_status = QLabel("Ready")
        self.chain_status.setMinimumHeight(28)
        self.book_match_status = QLabel("No run yet")
        self.book_match_status.setWordWrap(True)
        self.book_match_status.setMinimumHeight(32)
        self.tamper_result = QLabel("0 rejected")
        self.tamper_result.setMinimumHeight(28)

        grid.addWidget(QLabel("<b>Tamper Mode:</b>"), 0, 0)
        grid.addWidget(self.tamper_mode, 0, 1)
        grid.addWidget(QLabel("<b>Rejected:</b>"), 0, 2)
        grid.addWidget(self.tamper_result, 0, 3)
        grid.addWidget(QLabel("<b>Book Match:</b>"), 1, 0)
        grid.addWidget(self.book_match_status, 1, 1, 1, 3)
        grid.addWidget(QLabel("<b>Chain State:</b>"), 2, 0)
        grid.addWidget(self.chain_status, 2, 1, 1, 3)
