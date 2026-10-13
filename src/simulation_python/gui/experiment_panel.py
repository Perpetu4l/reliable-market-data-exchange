"""Controls placeholder for reliability experiment configuration."""

from PyQt6.QtWidgets import QDoubleSpinBox, QFormLayout, QGroupBox, QPushButton, QSpinBox


class ExperimentPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Experiment control", parent)
        layout = QFormLayout(self)
        self.loss_rate = QDoubleSpinBox()
        self.loss_rate.setRange(0.0, 1.0)
        self.loss_rate.setSingleStep(0.01)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        self.start_button = QPushButton("Start experiment (not connected)")
        self.stop_button = QPushButton("Stop experiment (not connected)")
        layout.addRow("Packet loss rate", self.loss_rate)
        layout.addRow("Random seed", self.seed)
        layout.addRow(self.start_button)
        layout.addRow(self.stop_button)
