"""Controls for reliability experiments and preset sweeps."""

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
)


class ExperimentPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Experiment Control", parent)
        layout = QFormLayout(self)

        self.execution_mode = QComboBox()
        self.execution_mode.addItem("In-process simulation (deterministic baseline)", "inprocess")
        self.execution_mode.addItem("Live loopback", "udp_loopback")
        self.execution_mode.addItem("Live LAN", "udp_lan")
        self.execution_mode.setCurrentIndex(1)

        self.lan_interface = QLineEdit("127.0.0.1")
        self.lan_interface.setToolTip("Local IPv4 interface for UDP multicast (127.0.0.1 for loopback)")

        self.mode = QComboBox()
        self.mode.addItem("Single feed", "single")
        self.mode.addItem("Dual feed - independent loss", "independent")
        self.mode.addItem("Dual feed - correlated loss", "correlated")
        self.mode.setCurrentIndex(1)

        self.events = QSpinBox()
        self.events.setRange(1, 1_000_000)
        self.events.setValue(500)

        self.loss_rate = QDoubleSpinBox()
        self.loss_rate.setRange(0.0, 1.0)
        self.loss_rate.setDecimals(3)
        self.loss_rate.setSingleStep(0.01)
        self.loss_rate.setValue(0.10)

        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        self.seed.setValue(42)

        self.history = QSpinBox()
        self.history.setRange(0, 1_000_000)
        self.history.setValue(65536)

        self.live_duration = QSpinBox()
        self.live_duration.setRange(1, 3_600)
        self.live_duration.setValue(3)
        self.live_duration.setSuffix(" s")

        self.live_subscribers = QSpinBox()
        self.live_subscribers.setRange(1, 32)
        self.live_subscribers.setValue(2)

        self.start_button = QPushButton("Run in-process baseline")
        self.live_start_button = QPushButton("Run live experiment")
        self.stop_button = QPushButton("Stop / Cancel")

        # Preset runner controls
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Redundancy Sweep (loss x feed mode)", "redundancy_sweep")
        self.preset_combo.addItem("Scheduler Contention (FCFS vs RR)", "scheduler_contention")
        self.preset_combo.addItem("History & Snapshot Sweep", "history_snapshot_sweep")
        self.preset_combo.addItem("Snapshot Tamper Verification", "snapshot_tamper")
        self.preset_combo.addItem("Scalability (N=1..32 subscribers)", "scalability")

        self.preset_size = QComboBox()
        self.preset_size.addItem("Quick", "quick")
        self.preset_size.addItem("Full", "full")

        self.run_preset_button = QPushButton("Run Preset Sweep")
        self.show_theory_checkbox = QCheckBox("Show theory curves (p and p²)")
        self.show_theory_checkbox.setChecked(True)
        self.log_y_checkbox = QCheckBox("Log-Y scale on recovery rate chart")

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.status = QLabel("Idle — no experiment run loaded")
        self.status.setWordWrap(True)

        layout.addRow("Execution mode", self.execution_mode)
        layout.addRow("Multicast interface IPv4", self.lan_interface)
        layout.addRow("Feed mode", self.mode)
        layout.addRow("In-process events", self.events)
        layout.addRow("Application loss rate", self.loss_rate)
        layout.addRow("Random seed", self.seed)
        layout.addRow("Retained event history", self.history)
        layout.addRow("Live workload duration", self.live_duration)
        layout.addRow("Live subscribers (1-32)", self.live_subscribers)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.start_button)
        btn_row.addWidget(self.live_start_button)
        btn_row.addWidget(self.stop_button)
        layout.addRow(btn_row)

        preset_row = QHBoxLayout()
        preset_row.addWidget(self.preset_combo, 2)
        preset_row.addWidget(self.preset_size, 1)
        preset_row.addWidget(self.run_preset_button, 1)
        layout.addRow("Preset suite", preset_row)

        opt_row = QHBoxLayout()
        opt_row.addWidget(self.show_theory_checkbox)
        opt_row.addWidget(self.log_y_checkbox)
        layout.addRow("Chart options", opt_row)

        layout.addRow("Progress", self.progress)
        layout.addRow("Experiment status", self.status)
