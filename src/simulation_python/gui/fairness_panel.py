"""Spacious Scheduler Policy & Server Capacity controls."""

from PyQt6.QtWidgets import QComboBox, QGridLayout, QGroupBox, QLabel, QSizePolicy, QSpinBox


class FairnessPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Scheduler & Server Capacity Controls", parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(14, 14, 14, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self.policy = QComboBox()
        self.policy.addItems(["FCFS", "Round Robin"])
        self.policy.setMinimumHeight(32)
        self.policy.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.workers = QSpinBox()
        self.workers.setRange(1, 16)
        self.workers.setValue(1)
        self.workers.setMinimumHeight(32)
        self.workers.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.delay_per_req_us = QSpinBox()
        self.delay_per_req_us.setRange(0, 1_000_000)
        self.delay_per_req_us.setValue(0)
        self.delay_per_req_us.setSuffix(" µs")
        self.delay_per_req_us.setMinimumHeight(32)
        self.delay_per_req_us.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.delay_per_event_us = QSpinBox()
        self.delay_per_event_us.setRange(0, 100_000)
        self.delay_per_event_us.setValue(0)
        self.delay_per_event_us.setSuffix(" µs")
        self.delay_per_event_us.setMinimumHeight(32)
        self.delay_per_event_us.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.waiting_time = QLabel("—")
        self.noisy_quiet_ratio = QLabel("n/a")
        self.noisy_quiet_ratio.setMinimumHeight(28)
        self.service_distribution = QLabel("—")
        self.jain_label = QLabel("n/a")

        grid.addWidget(QLabel("<b>Policy:</b>"), 0, 0)
        grid.addWidget(self.policy, 0, 1)
        grid.addWidget(QLabel("<b>Workers:</b>"), 0, 2)
        grid.addWidget(self.workers, 0, 3)
        grid.addWidget(QLabel("<b>Delay/Req:</b>"), 1, 0)
        grid.addWidget(self.delay_per_req_us, 1, 1)
        grid.addWidget(QLabel("<b>Delay/Ev:</b>"), 1, 2)
        grid.addWidget(self.delay_per_event_us, 1, 3)
        grid.addWidget(QLabel("<b>RR Advantage:</b>"), 2, 0)
        grid.addWidget(self.noisy_quiet_ratio, 2, 1, 1, 3)
