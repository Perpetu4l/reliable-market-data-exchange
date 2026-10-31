"""Compact, multi-column visual control panel for reliability experiments and preset sweeps."""

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)


class ExperimentPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Experiment Parameters & One-Click Actions", parent)
        outer = QVBoxLayout(self)
        outer.setSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)

        self.execution_mode = QComboBox()
        self.execution_mode.addItem("In-process simulation (fast baseline)", "inprocess")
        self.execution_mode.addItem("Live UDP/TCP loopback", "udp_loopback")
        self.execution_mode.addItem("Live UDP/TCP LAN", "udp_lan")
        self.execution_mode.setCurrentIndex(1)

        self.lan_interface = QLineEdit("127.0.0.1")
        self.lan_interface.setToolTip("Local IPv4 interface for UDP multicast (127.0.0.1 for loopback)")

        self.mode = QComboBox()
        self.mode.addItem("Single feed (A)", "single")
        self.mode.addItem("Dual feed — independent loss (A+B)", "independent")
        self.mode.addItem("Dual feed — correlated loss (A+B)", "correlated")
        self.mode.setCurrentIndex(1)

        self.events = QSpinBox()
        self.events.setRange(1, 1_000_000)
        self.events.setValue(500)

        self.loss_rate = QDoubleSpinBox()
        self.loss_rate.setRange(0.0, 1.0)
        self.loss_rate.setDecimals(3)
        self.loss_rate.setSingleStep(0.02)
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

        # Row 0: Mode, Feed Mode, Loss Rate
        grid.addWidget(QLabel("<b>Execution:</b>"), 0, 0)
        grid.addWidget(self.execution_mode, 0, 1)
        grid.addWidget(QLabel("<b>Feed Mode:</b>"), 0, 2)
        grid.addWidget(self.mode, 0, 3)
        grid.addWidget(QLabel("<b>Drop / Loss Rate:</b>"), 0, 4)
        grid.addWidget(self.loss_rate, 0, 5)

        # Row 1: Subscribers, Duration, History
        grid.addWidget(QLabel("<b>Subscribers (1–32):</b>"), 1, 0)
        grid.addWidget(self.live_subscribers, 1, 1)
        grid.addWidget(QLabel("<b>Live Duration:</b>"), 1, 2)
        grid.addWidget(self.live_duration, 1, 3)
        grid.addWidget(QLabel("<b>History Window:</b>"), 1, 4)
        grid.addWidget(self.history, 1, 5)

        # Row 2: Seed, In-Process Events, Interface IP
        grid.addWidget(QLabel("<b>Fault Seed:</b>"), 2, 0)
        grid.addWidget(self.seed, 2, 1)
        grid.addWidget(QLabel("<b>In-Process Events:</b>"), 2, 2)
        grid.addWidget(self.events, 2, 3)
        grid.addWidget(QLabel("<b>Interface IPv4:</b>"), 2, 4)
        grid.addWidget(self.lan_interface, 2, 5)

        outer.addLayout(grid)

        # Preset & Options Row
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Redundancy Sweep (Loss x Feed Mode)", "redundancy_sweep")
        self.preset_combo.addItem("Scheduler Contention (FCFS vs Round-Robin)", "scheduler_contention")
        self.preset_combo.addItem("History & Snapshot Fallback Sweep", "history_snapshot_sweep")
        self.preset_combo.addItem("Snapshot Tamper Verification", "snapshot_tamper")
        self.preset_combo.addItem("Scalability Sweep (N=1..16 Subscribers)", "scalability")

        self.preset_size = QComboBox()
        self.preset_size.addItem("Quick", "quick")
        self.preset_size.addItem("Full", "full")

        self.show_theory_checkbox = QCheckBox("Show theory curves (p, p²)")
        self.show_theory_checkbox.setChecked(True)
        self.log_y_checkbox = QCheckBox("Log-Y scale on Chart 1")

        # Main Action Buttons Row
        self.live_start_button = QPushButton("▶ Run Live Experiment (Updates All 6 Graphs)")
        self.live_start_button.setMinimumHeight(36)
        self.live_start_button.setStyleSheet(
            "QPushButton { background: #16a34a; color: white; font-weight: bold; padding: 6px 14px; border-radius: 5px; }"
            "QPushButton:hover { background: #15803d; }"
        )

        self.start_button = QPushButton("⚡ Run In-Process Baseline")
        self.start_button.setMinimumHeight(36)
        self.start_button.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; font-weight: bold; padding: 6px 14px; border-radius: 5px; }"
            "QPushButton:hover { background: #1d4ed8; }"
        )

        self.run_preset_button = QPushButton("📊 Run Preset Sweep")
        self.run_preset_button.setMinimumHeight(36)
        self.run_preset_button.setStyleSheet(
            "QPushButton { background: #7c3aed; color: white; font-weight: bold; padding: 6px 14px; border-radius: 5px; }"
            "QPushButton:hover { background: #6d28d9; }"
        )

        self.stop_button = QPushButton("■ Stop")
        self.stop_button.setMinimumHeight(36)
        self.stop_button.setStyleSheet(
            "QPushButton { background: #dc2626; color: white; font-weight: bold; padding: 6px 12px; border-radius: 5px; }"
            "QPushButton:hover { background: #b91c1c; }"
        )

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.live_start_button, 2)
        btn_row.addWidget(self.start_button, 1)
        btn_row.addWidget(QLabel("<b>Preset:</b>"))
        btn_row.addWidget(self.preset_combo, 2)
        btn_row.addWidget(self.preset_size, 0)
        btn_row.addWidget(self.run_preset_button, 1)
        btn_row.addWidget(self.stop_button, 0)
        outer.addLayout(btn_row)

        status_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setMaximumWidth(220)
        self.progress.setMaximumHeight(18)

        self.status = QLabel("Idle — ready")
        self.status.setStyleSheet("font-weight: 600; color: #334155;")

        status_row.addWidget(QLabel("<b>Progress:</b>"))
        status_row.addWidget(self.progress)
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.show_theory_checkbox)
        status_row.addWidget(self.log_y_checkbox)
        outer.addLayout(status_row)
