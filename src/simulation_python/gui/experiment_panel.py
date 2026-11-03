"""Grouped configuration and execution panel for Reliability Experiments."""

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
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .theme import COLOR_BLUE, COLOR_GREEN, COLOR_PURPLE, COLOR_RED, button_style


class ExperimentPanel(QWidget):
    """Organises Transport/Network, Packet Impairment, Subscriber Configuration, and Execution Controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # Top row of 3 distinct configuration group boxes (Section 8.1)
        groups_row = QHBoxLayout()
        groups_row.setSpacing(10)

        # 1. Transport & Network Group
        net_group = QGroupBox("1. Transport && Network")
        net_grid = QGridLayout(net_group)
        net_grid.setContentsMargins(10, 14, 10, 10)
        net_grid.setHorizontalSpacing(10)
        net_grid.setVerticalSpacing(6)
        net_grid.setColumnStretch(1, 1)

        self.execution_mode = QComboBox()
        self.execution_mode.addItem("In-process simulation (fast baseline)", "inprocess")
        self.execution_mode.addItem("Live UDP/TCP loopback", "udp_loopback")
        self.execution_mode.addItem("Live UDP/TCP LAN", "udp_lan")
        self.execution_mode.setCurrentIndex(1)
        self.execution_mode.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.mode = QComboBox()
        self.mode.addItem("Single feed (A)", "single")
        self.mode.addItem("Dual feed — independent loss (A+B)", "independent")
        self.mode.addItem("Dual feed — correlated loss (A+B)", "correlated")
        self.mode.setCurrentIndex(1)
        self.mode.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.lan_interface = QLineEdit("127.0.0.1")
        self.lan_interface.setToolTip("Local IPv4 interface for UDP multicast (127.0.0.1 for loopback)")

        net_grid.addWidget(QLabel("<b>Execution Mode:</b>"), 0, 0)
        net_grid.addWidget(self.execution_mode, 0, 1)
        net_grid.addWidget(QLabel("<b>Feed Mode:</b>"), 1, 0)
        net_grid.addWidget(self.mode, 1, 1)
        net_grid.addWidget(QLabel("<b>Interface IPv4:</b>"), 2, 0)
        net_grid.addWidget(self.lan_interface, 2, 1)

        # 2. Packet Impairment Group
        imp_group = QGroupBox("2. Packet Impairment && Fault Seed")
        imp_grid = QGridLayout(imp_group)
        imp_grid.setContentsMargins(10, 14, 10, 10)
        imp_grid.setHorizontalSpacing(10)
        imp_grid.setVerticalSpacing(6)
        imp_grid.setColumnStretch(1, 1)
        imp_grid.setColumnStretch(3, 1)

        self.loss_rate = QDoubleSpinBox()
        self.loss_rate.setRange(0.0, 1.0)
        self.loss_rate.setDecimals(3)
        self.loss_rate.setSingleStep(0.02)
        self.loss_rate.setValue(0.10)

        self.dup_rate = QDoubleSpinBox()
        self.dup_rate.setRange(0.0, 1.0)
        self.dup_rate.setDecimals(2)
        self.dup_rate.setSingleStep(0.05)
        self.dup_rate.setValue(0.0)

        self.reorder_rate = QDoubleSpinBox()
        self.reorder_rate.setRange(0.0, 1.0)
        self.reorder_rate.setDecimals(2)
        self.reorder_rate.setSingleStep(0.05)
        self.reorder_rate.setValue(0.0)

        self.delay_ms = QSpinBox()
        self.delay_ms.setRange(0, 5000)
        self.delay_ms.setValue(0)
        self.delay_ms.setSuffix(" ms")

        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        self.seed.setValue(42)

        self.no_recovery_checkbox = QCheckBox("Negative Control (--no-recovery)")

        imp_grid.addWidget(QLabel("<b>Loss Rate:</b>"), 0, 0)
        imp_grid.addWidget(self.loss_rate, 0, 1)
        imp_grid.addWidget(QLabel("<b>Dup Rate:</b>"), 0, 2)
        imp_grid.addWidget(self.dup_rate, 0, 3)
        imp_grid.addWidget(QLabel("<b>Reorder Rate:</b>"), 1, 0)
        imp_grid.addWidget(self.reorder_rate, 1, 1)
        imp_grid.addWidget(QLabel("<b>Hold Delay:</b>"), 1, 2)
        imp_grid.addWidget(self.delay_ms, 1, 3)
        imp_grid.addWidget(QLabel("<b>Fault Seed:</b>"), 2, 0)
        imp_grid.addWidget(self.seed, 2, 1)
        imp_grid.addWidget(self.no_recovery_checkbox, 2, 2, 1, 2)

        # 3. Subscriber & History Configuration Group
        sub_group = QGroupBox("3. Subscribers, Workload && History")
        sub_grid = QGridLayout(sub_group)
        sub_grid.setContentsMargins(10, 14, 10, 10)
        sub_grid.setHorizontalSpacing(10)
        sub_grid.setVerticalSpacing(6)
        sub_grid.setColumnStretch(1, 1)
        sub_grid.setColumnStretch(3, 1)

        self.live_subscribers = QSpinBox()
        self.live_subscribers.setRange(1, 32)
        self.live_subscribers.setValue(2)

        self.live_duration = QSpinBox()
        self.live_duration.setRange(1, 3_600)
        self.live_duration.setValue(3)
        self.live_duration.setSuffix(" s")

        self.recovery_concurrency = QSpinBox()
        self.recovery_concurrency.setRange(1, 16)
        self.recovery_concurrency.setValue(1)

        self.history = QSpinBox()
        self.history.setRange(0, 1_000_000)
        self.history.setValue(65536)

        self.events = QSpinBox()
        self.events.setRange(1, 1_000_000)
        self.events.setValue(500)

        sub_grid.addWidget(QLabel("<b>Subscribers:</b>"), 0, 0)
        sub_grid.addWidget(self.live_subscribers, 0, 1)
        sub_grid.addWidget(QLabel("<b>Duration:</b>"), 0, 2)
        sub_grid.addWidget(self.live_duration, 0, 3)
        sub_grid.addWidget(QLabel("<b>Concurrency (K):</b>"), 1, 0)
        sub_grid.addWidget(self.recovery_concurrency, 1, 1)
        sub_grid.addWidget(QLabel("<b>History Cap:</b>"), 1, 2)
        sub_grid.addWidget(self.history, 1, 3)
        sub_grid.addWidget(QLabel("<b>In-Proc Events:</b>"), 2, 0)
        sub_grid.addWidget(self.events, 2, 1, 1, 3)

        groups_row.addWidget(net_group, 3)
        groups_row.addWidget(imp_group, 4)
        groups_row.addWidget(sub_group, 4)
        outer.addLayout(groups_row)

        # Execution & Preset Sweep Control Bar (Section 8.2)
        exec_box = QGroupBox("Experiment Execution && Preset Sweeps")
        exec_layout = QHBoxLayout(exec_box)
        exec_layout.setContentsMargins(10, 12, 10, 8)
        exec_layout.setSpacing(10)

        self.live_start_button = QPushButton("▶ Run Experiment (Live UDP/TCP)")
        self.live_start_button.setMinimumHeight(36)
        self.live_start_button.setStyleSheet(button_style("#059669", "#047857"))

        self.start_button = QPushButton("⚡ Run In-Process Baseline")
        self.start_button.setMinimumHeight(36)
        self.start_button.setStyleSheet(button_style("#2563eb", "#1d4ed8"))

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Redundancy Sweep (Loss x Feed Mode)", "redundancy_sweep")
        self.preset_combo.addItem("Scheduler Contention (FCFS vs Round-Robin)", "scheduler_contention")
        self.preset_combo.addItem("History & Snapshot Fallback Sweep", "history_snapshot_sweep")
        self.preset_combo.addItem("Snapshot Tamper Verification", "snapshot_tamper")
        self.preset_combo.addItem("Scalability Sweep (N=1..16 Subscribers)", "scalability")
        self.preset_combo.setMinimumHeight(34)

        self.preset_size = QComboBox()
        self.preset_size.addItem("Quick", "quick")
        self.preset_size.addItem("Full", "full")
        self.preset_size.setMinimumHeight(34)

        self.run_preset_button = QPushButton("📊 Run Preset Sweep")
        self.run_preset_button.setMinimumHeight(36)
        self.run_preset_button.setStyleSheet(button_style("#7c3aed", "#6d28d9"))

        self.stop_button = QPushButton("■ Stop Experiment")
        self.stop_button.setMinimumHeight(36)
        self.stop_button.setStyleSheet(button_style("#dc2626", "#b91c1c"))

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setMinimumWidth(140)
        self.progress.setMaximumWidth(190)
        self.progress.setMinimumHeight(24)

        self.status = QLabel("Idle — ready")
        self.status.setStyleSheet(
            f"color: {COLOR_GREEN}; background: rgba(16, 185, 129, 0.12); "
            f"border: 1px solid {COLOR_GREEN}; border-radius: 4px; padding: 4px 10px; font-weight: 700;"
        )

        self.show_theory_checkbox = QCheckBox("Theory (p, p²)")
        self.show_theory_checkbox.setChecked(True)
        self.log_y_checkbox = QCheckBox("Log-Y")

        exec_layout.addWidget(self.live_start_button)
        exec_layout.addWidget(self.start_button)
        exec_layout.addWidget(QLabel("<b>Preset:</b>"))
        exec_layout.addWidget(self.preset_combo, 1)
        exec_layout.addWidget(self.preset_size)
        exec_layout.addWidget(self.run_preset_button)
        exec_layout.addWidget(self.stop_button)
        exec_layout.addWidget(self.progress)
        exec_layout.addWidget(self.status)
        exec_layout.addWidget(self.show_theory_checkbox)
        exec_layout.addWidget(self.log_y_checkbox)
        outer.addWidget(exec_box)
