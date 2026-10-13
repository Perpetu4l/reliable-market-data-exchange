"""Structural dashboard extension for the existing market-simulation GUI.

No network or pybind11 reliability bindings are connected at this stage.
"""

from PyQt6.QtWidgets import QScrollArea, QTabWidget, QVBoxLayout, QWidget

from .experiment_panel import ExperimentPanel
from .fairness_panel import FairnessPanel
from .history_integrity_panel import HistoryIntegrityPanel
from .metrics_panel import MetricsPanel
from .recovery_panel import RecoveryPanel
from .redundancy_panel import RedundancyPanel
from .snapshot_panel import SnapshotPanel


class ReliabilityDashboard(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.addTab(self._scroll([RecoveryPanel(), RedundancyPanel(), MetricsPanel()]), "Live system")
        self.addTab(self._scroll([ExperimentPanel(), SnapshotPanel(), FairnessPanel()]), "Experiments")
        self.addTab(self._scroll([HistoryIntegrityPanel()]), "History integrity")

    @staticmethod
    def _scroll(panels):
        content = QWidget()
        layout = QVBoxLayout(content)
        for panel in panels:
            layout.addWidget(panel)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll
