"""Scheduler fairness and queue-wait panel."""

from PyQt6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QLabel, QSpinBox


class FairnessPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Recovery Fairness & Server Capacity Model", parent)
        layout = QFormLayout(self)
        self.policy = QComboBox()
        self.policy.addItems(["FCFS", "Round Robin"])
        self.workers = QSpinBox()
        self.workers.setRange(1, 16)
        self.workers.setValue(1)
        self.delay_per_req_us = QSpinBox()
        self.delay_per_req_us.setRange(0, 1_000_000)
        self.delay_per_req_us.setValue(0)
        self.delay_per_req_us.setSuffix(" µs")
        self.delay_per_event_us = QSpinBox()
        self.delay_per_event_us.setRange(0, 100_000)
        self.delay_per_event_us.setValue(0)
        self.delay_per_event_us.setSuffix(" µs")

        self.waiting_time = QLabel("—")
        self.waiting_time.setWordWrap(True)
        self.noisy_quiet_ratio = QLabel("n/a")
        self.service_distribution = QLabel("No run yet")
        self.service_distribution.setWordWrap(True)
        self.jain_label = QLabel("n/a")
        self.jain_label.setWordWrap(True)
        self.jain_label.setToolTip(
            "Jain's index J = (sum x_i)^2 / (N * sum x_i^2) on served request counts "
            "within the saturated window (queue_depth >= 2). Limitation: raw served counts "
            "reflect demand imbalance rather than scheduling latency fairness; primary "
            "fairness evidence is per-subscriber queue wait p50/p99/max."
        )

        layout.addRow("Scheduling policy", self.policy)
        layout.addRow("Recovery workers", self.workers)
        layout.addRow("Modelled server capacity delay / request", self.delay_per_req_us)
        layout.addRow("Modelled server capacity delay / event", self.delay_per_event_us)
        layout.addRow("Per-subscriber queue wait (p50/p99/max)", self.waiting_time)
        layout.addRow("Noisy-to-quiet p99 wait ratio", self.noisy_quiet_ratio)
        layout.addRow("Service distribution", self.service_distribution)
        layout.addRow("Jain index (saturated window, hover for limitation)", self.jain_label)
