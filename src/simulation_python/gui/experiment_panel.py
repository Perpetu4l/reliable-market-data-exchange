"""Controls for the compiled deterministic reliability experiment."""

from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel, QPushButton, QSpinBox


class ExperimentPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Experiment control", parent)
        layout = QFormLayout(self)
        self.mode = QComboBox()
        self.mode.addItem("Single feed", "single")
        self.mode.addItem("Dual feed - independent loss", "independent")
        self.mode.addItem("Dual feed - correlated loss", "correlated")
        self.events = QSpinBox()
        self.events.setRange(1, 1_000_000)
        self.events.setValue(100)
        self.loss_rate = QDoubleSpinBox()
        self.loss_rate.setRange(0.0, 1.0)
        self.loss_rate.setSingleStep(0.01)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        self.seed.setValue(42)
        self.history = QSpinBox()
        self.history.setRange(0, 1_000_000)
        self.history.setValue(128)
        self.start_button = QPushButton("Run reliability experiment")
        self.stop_button = QPushButton("Stop experiment")
        self.status = QLabel("Idle")
        layout.addRow("Feed mode", self.mode)
        layout.addRow("Events", self.events)
        layout.addRow("Packet loss rate", self.loss_rate)
        layout.addRow("Random seed", self.seed)
        layout.addRow("Event history", self.history)
        layout.addRow(self.start_button)
        layout.addRow(self.stop_button)
        layout.addRow("Experiment status", self.status)
