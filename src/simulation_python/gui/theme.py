"""Professional Financial Trading Terminal Theme, Chart Helpers, KPI Cards, and Collapsible Console."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Semantic Colour System (Consistent across the entire application)
# ---------------------------------------------------------------------------
BG_MAIN = "#0b0f19"          # Deep navy-charcoal window background
BG_PANEL = "#111827"         # Primary panel surface
BG_CARD = "#161f33"          # Elevated card / metric box surface
BG_ELEVATED = BG_CARD
BG_INPUT = "#0f172a"         # Input / spinbox / combo / table background
BG_CHART = "#0d1424"         # Dark financial chart canvas
BORDER_SUBTLE = "#1e293b"    # Standard subtle divider
BORDER_ACCENT = "#283548"    # Card / group border

TEXT_PRIMARY = "#f8fafc"     # High-contrast value & heading text
TEXT_SECONDARY = "#cbd5e1"   # Standard body / table text
TEXT_MUTED = "#94a3b8"       # Axis labels, captions, units

# Semantic status & data colours
COLOR_GREEN = "#10b981"      # Healthy, bid side, Feed A / Feed B avoided, book match
GREEN_OK = COLOR_GREEN
GREEN_BRIGHT = "#34d399"
COLOR_GREEN_DARK = "#197b30" # Smoke-test compatible green border/badge token
COLOR_RED = "#ef4444"        # Error, ask side, drops, permanently lost, tamper reject
RED_ERR = COLOR_RED
RED_BRIGHT = "#f87171"
COLOR_RED_DARK = "#b42318"   # Smoke-test compatible red border/badge token
COLOR_AMBER = "#f59e0b"      # Warning, delay, snapshot fallback, TOO_OLD
AMBER_WARN = COLOR_AMBER
COLOR_BLUE = "#3b82f6"       # Neutral operational info, TCP recovery, primary action
BLUE_INFO = COLOR_BLUE
COLOR_CYAN = "#06b6d4"       # Secondary feed / Feed B share / RTT
CYAN_ACCENT = COLOR_CYAN
COLOR_PURPLE = "#8b5cf6"     # Analytical percentiles (p95/p99), preset sweeps
PURPLE_ANALYTIC = COLOR_PURPLE

TERMINAL_STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {BG_MAIN};
    color: {TEXT_PRIMARY};
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", Roboto, sans-serif;
    font-size: 12px;
}}
QScrollArea {{
    border: none;
    background-color: {BG_MAIN};
}}
QGroupBox {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER_ACCENT};
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 8px;
    font-weight: 700;
    font-size: 12px;
    color: {TEXT_PRIMARY};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: #e2e8f0;
    background-color: {BG_PANEL};
}}
QLabel {{
    background: transparent;
    color: {TEXT_SECONDARY};
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid #334155;
    border-radius: 4px;
    padding: 3px 8px;
    min-height: 24px;
    selection-background-color: {COLOR_BLUE};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {COLOR_BLUE};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
    selection-background-color: #1d4ed8;
    selection-color: #ffffff;
    border: 1px solid #334155;
}}
QCheckBox {{
    color: {TEXT_SECONDARY};
    spacing: 6px;
    font-weight: 500;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 1px solid #475569;
    background-color: {BG_INPUT};
}}
QCheckBox::indicator:checked {{
    background-color: {COLOR_BLUE};
    border: 1px solid {COLOR_BLUE};
}}
QPushButton {{
    background-color: #1e293b;
    color: {TEXT_PRIMARY};
    border: 1px solid #334155;
    border-radius: 5px;
    padding: 5px 12px;
    font-weight: 600;
    font-size: 12px;
}}
QPushButton:hover {{
    background-color: #334155;
    border-color: #475569;
}}
QPushButton:disabled {{
    background-color: #0f172a;
    color: #475569;
    border-color: #1e293b;
}}
QTableWidget, QListWidget {{
    background-color: {BG_INPUT};
    alternate-background-color: #131c2e;
    color: {TEXT_PRIMARY};
    gridline-color: #1e293b;
    border: 1px solid {BORDER_ACCENT};
    border-radius: 5px;
    selection-background-color: #1e3a8a;
    selection-color: #ffffff;
}}
QHeaderView::section {{
    background-color: #162032;
    color: #94a3b8;
    font-weight: 700;
    font-size: 11px;
    padding: 5px 8px;
    border: none;
    border-bottom: 1px solid #334155;
    border-right: 1px solid #1e293b;
}}
QPlainTextEdit {{
    background-color: #090d16;
    color: #cbd5e1;
    border: 1px solid {BORDER_ACCENT};
    border-radius: 5px;
    font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
    font-size: 11px;
    padding: 6px;
}}
QProgressBar {{
    background-color: {BG_INPUT};
    border: 1px solid #334155;
    border-radius: 4px;
    text-align: center;
    color: {TEXT_PRIMARY};
    font-weight: 700;
}}
QProgressBar::chunk {{
    background-color: {COLOR_BLUE};
    border-radius: 3px;
}}
QTabWidget::pane {{
    border: 1px solid {BORDER_ACCENT};
    background-color: {BG_MAIN};
    border-radius: 4px;
}}
QTabBar::tab {{
    background-color: #111827;
    color: #94a3b8;
    border: 1px solid #1e293b;
    border-bottom: none;
    padding: 6px 16px;
    margin-right: 4px;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    font-weight: 600;
    font-size: 12px;
}}
QTabBar::tab:selected {{
    background-color: #1e3a8a;
    color: #ffffff;
    border-color: {COLOR_BLUE};
}}
QTabBar::tab:hover:!selected {{
    background-color: #1e293b;
    color: #e2e8f0;
}}
"""


def button_style(bg: str, hover: str, fg: str = "#ffffff") -> str:
    return (
        f"QPushButton {{ background-color: {bg}; color: {fg}; border: 1px solid {bg}; "
        f"border-radius: 5px; padding: 5px 14px; font-weight: 700; font-size: 12px; }}"
        f"QPushButton:hover {{ background-color: {hover}; border-color: {hover}; }}"
        f"QPushButton:disabled {{ background-color: #1e293b; color: #64748b; border-color: #334155; }}"
    )


class MetricCard(QFrame):
    """Compact financial terminal KPI card with title, prominent value, and sub-label/status."""

    def __init__(
        self,
        title: str,
        initial_value: str = "—",
        subtext: str = "",
        accent_color: str = COLOR_BLUE,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._accent = accent_color
        self.setObjectName("MetricCard")
        self._apply_card_border(accent_color)
        self.setMinimumHeight(68)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(2)

        self.title_label = QLabel(title.upper())
        self.title_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.6px;"
        )
        self.value_label = QLabel(initial_value)
        self.value_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 17px; font-weight: 800; font-family: 'SF Mono', Menlo, monospace;"
        )
        self.sub_label = QLabel(subtext)
        self.sub_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; font-weight: 500;")

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.sub_label)

    def _apply_card_border(self, accent_color: str) -> None:
        self.setStyleSheet(
            f"QFrame#MetricCard {{ background-color: {BG_CARD}; border: 1px solid {BORDER_ACCENT}; "
            f"border-left: 3px solid {accent_color}; border-radius: 5px; }}"
        )

    def set_metric(self, value: str, subtext: str | None = None, color: str | None = None) -> None:
        self.value_label.setText(value)
        if color:
            self.value_label.setStyleSheet(
                f"color: {color}; font-size: 17px; font-weight: 800; font-family: 'SF Mono', Menlo, monospace;"
            )
            self._apply_card_border(color)
        if subtext is not None:
            self.sub_label.setText(subtext)

    def set_value(self, value: str, subtext: str | None = None, color: str | None = None) -> None:
        self.set_metric(value, subtext=subtext, color=color)


def create_terminal_chart(
    title: str,
    y_label: str,
    x_label: str,
    min_height: int = 240,
) -> pg.PlotWidget:
    """Create a dark-themed financial terminal PlotWidget with SI-prefix disabled and clean axes."""
    chart = pg.PlotWidget()
    chart.setMinimumHeight(min_height)
    chart.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    chart.setBackground(BG_CHART)

    plot_item = chart.getPlotItem()
    plot_item.setContentsMargins(10, 10, 14, 8)
    plot_item.setTitle(
        f"<span style='color: #e2e8f0; font-size: 10.5pt; font-weight: 700;'>{title}</span>"
    )
    plot_item.showGrid(x=True, y=True, alpha=0.18)

    label_style = {"color": TEXT_MUTED, "font-size": "9.5pt", "font-weight": "600"}
    plot_item.setLabel("left", y_label, **label_style)
    plot_item.setLabel("bottom", x_label, **label_style)

    tick_font = QFont("Menlo", 9)
    for axis_name in ("left", "bottom"):
        ax = plot_item.getAxis(axis_name)
        ax.enableAutoSIPrefix(False)
        ax.setPen(pg.mkPen(color="#334155", width=1))
        ax.setTextPen(pg.mkPen(color="#cbd5e1"))
        ax.setStyle(tickFont=tick_font, tickTextOffset=6)

    chart.setStyleSheet(f"border: 1px solid {BORDER_ACCENT}; border-radius: 5px;")
    return chart


def set_chart_title(chart: pg.PlotWidget, title: str) -> None:
    chart.getPlotItem().setTitle(
        f"<span style='color: #e2e8f0; font-size: 10.5pt; font-weight: 700;'>{title}</span>"
    )


def render_empty_chart(
    chart: pg.PlotWidget,
    message: str = "No data available — start an experiment to view results.",
    title: str | None = None,
) -> None:
    chart.clear()
    if title:
        set_chart_title(chart, title)
    plot_item = chart.getPlotItem()
    plot_item.getAxis("bottom").setTicks(None)
    chart.setXRange(0.0, 10.0, padding=0)
    chart.setYRange(0.0, 10.0, padding=0)
    txt = pg.TextItem(
        html=(
            f"<div style='background-color: rgba(22, 31, 51, 220); border: 1px solid #334155; "
            f"padding: 8px 14px; border-radius: 4px; color: #94a3b8; font-size: 10pt; font-weight: 600;'>"
            f"{message}</div>"
        ),
        anchor=(0.5, 0.5),
    )
    txt.setPos(5.0, 5.0)
    chart.addItem(txt)


def _format_num(val: float, decimals: int | None = None) -> str:
    if decimals is not None:
        return f"{val:,.{decimals}f}"
    if abs(val) >= 100 and abs(val - round(val)) < 1e-3:
        return f"{int(round(val)):,}"
    if abs(val - round(val)) < 1e-4:
        return f"{int(round(val)):,}"
    if abs(val) < 1.0 and val != 0.0:
        return f"{val:.3f}"
    return f"{val:.2f}"


def render_bar_chart(
    chart: pg.PlotWidget,
    labels: Sequence[str],
    values: Sequence[float],
    colors: str | Sequence[str],
    title: str = "",
    y_label: str | None = None,
    y_ceiling: float | None = None,
    value_suffix: str = "",
    unit_suffix: str = "",
    decimals: int | None = None,
    badge_overrides: Sequence[str] | None = None,
) -> None:
    """Render an auto-scaled bar chart with zero-clipping margins and crisp value badges."""
    chart.clear()
    if title:
        set_chart_title(chart, title)
    if y_label:
        chart.getPlotItem().setLabel(
            "left", y_label, **{"color": TEXT_MUTED, "font-size": "9.5pt", "font-weight": "600"}
        )
    if not values or not labels:
        render_empty_chart(chart)
        return

    suffix = value_suffix or unit_suffix
    n = len(values)
    positions = list(range(n))
    max_val = max((float(v) for v in values), default=0.0)
    top_y = y_ceiling if y_ceiling is not None else max(1.0, max_val * 1.32 if max_val > 0 else 10.0)
    min_stub = top_y * 0.018

    color_list = [colors] * n if isinstance(colors, str) else list(colors)
    bar_width = min(0.56, max(0.32, 2.4 / max(3, n)))

    for pos, raw_val, c in zip(positions, values, color_list):
        val = float(raw_val)
        draw_h = val if val > 0 else min_stub
        fill_color = c if val > 0 else "#334155"
        bar = pg.BarGraphItem(
            x=[pos],
            height=[draw_h],
            width=bar_width,
            brush=pg.mkBrush(fill_color),
            pen=pg.mkPen(fill_color, width=1),
        )
        chart.addItem(bar)

        badge_txt = (
            badge_overrides[pos]
            if badge_overrides and pos < len(badge_overrides)
            else f"{_format_num(val, decimals=decimals)}{suffix}"
        )
        lbl = pg.TextItem(
            html=(
                f"<div style='color: #f8fafc; font-size: 8.5pt; font-weight: 700; "
                f"text-align: center;'>{badge_txt}</div>"
            ),
            anchor=(0.5, 1.05),
        )
        lbl.setPos(pos, draw_h + top_y * 0.015)
        chart.addItem(lbl)

    ax_bottom = chart.getPlotItem().getAxis("bottom")
    ax_bottom.setTicks([list(zip(positions, labels))])

    chart.setXRange(-0.75, n - 0.25, padding=0)
    chart.setYRange(0.0, top_y, padding=0)


def render_grouped_bar_chart(
    chart: pg.PlotWidget,
    labels: Sequence[str],
    series_a: Sequence[float],
    series_b: Sequence[float],
    name_a: str,
    name_b: str,
    color_a: str | Sequence[str],
    color_b: str | Sequence[str],
    title: str = "",
    y_label: str | None = None,
    badge_b_overrides: Sequence[str] | None = None,
    status_badges: Sequence[str] | None = None,
) -> None:
    """Render a side-by-side grouped bar chart (e.g. Expected vs Delivered, or Delivered vs Recovered)."""
    chart.clear()
    if title:
        set_chart_title(chart, title)
    if y_label:
        chart.getPlotItem().setLabel(
            "left", y_label, **{"color": TEXT_MUTED, "font-size": "9.5pt", "font-weight": "600"}
        )
    if not labels:
        render_empty_chart(chart)
        return

    n = len(labels)
    positions = list(range(n))
    max_val = max(
        max((float(v) for v in series_a), default=0.0),
        max((float(v) for v in series_b), default=0.0),
    )
    top_y = max(5.0, max_val * 1.38 if max_val > 0 else 10.0)
    min_stub = top_y * 0.015
    width = min(0.32, max(0.18, 1.6 / max(2, n)))

    cols_a = [color_a] * n if isinstance(color_a, str) else list(color_a)
    cols_b = [color_b] * n if isinstance(color_b, str) else list(color_b)
    badges = status_badges or badge_b_overrides

    for idx, pos in enumerate(positions):
        va = float(series_a[idx]) if idx < len(series_a) else 0.0
        vb = float(series_b[idx]) if idx < len(series_b) else 0.0
        ca = cols_a[idx] if va > 0 else "#334155"
        cb = cols_b[idx] if vb > 0 else "#334155"
        ha = va if va > 0 else min_stub
        hb = vb if vb > 0 else min_stub

        chart.addItem(
            pg.BarGraphItem(
                x=[pos - width * 0.55],
                height=[ha],
                width=width,
                brush=pg.mkBrush(ca),
                pen=pg.mkPen(ca, width=1),
                name=name_a if idx == 0 else None,
            )
        )
        chart.addItem(
            pg.BarGraphItem(
                x=[pos + width * 0.55],
                height=[hb],
                width=width,
                brush=pg.mkBrush(cb),
                pen=pg.mkPen(cb, width=1),
                name=name_b if idx == 0 else None,
            )
        )

        if badges and idx < len(badges):
            badge_str = badges[idx]
            badge_col = GREEN_BRIGHT if "MATCH" in badge_str and "MISMATCH" not in badge_str else (RED_BRIGHT if "MISMATCH" in badge_str else "#f8fafc")
            html_txt = (
                f"<div style='color: #f8fafc; font-size: 8.5pt; font-weight: 700; text-align: center;'>"
                f"{_format_num(vb)} <span style='color: {badge_col};'>{badge_str}</span></div>"
            )
            lbl = pg.TextItem(html=html_txt, anchor=(0.5, 1.05))
            lbl.setPos(pos, max(ha, hb) + top_y * 0.015)
            chart.addItem(lbl)
        else:
            lbl_a = pg.TextItem(
                html=f"<div style='color: #a7f3d0; font-size: 8pt; font-weight: 700; text-align: center;'>{_format_num(va)}</div>",
                anchor=(0.5, 1.05),
            )
            lbl_a.setPos(pos - width * 0.55, ha + top_y * 0.012)
            chart.addItem(lbl_a)

            lbl_b = pg.TextItem(
                html=f"<div style='color: #93c5fd; font-size: 8pt; font-weight: 700; text-align: center;'>{_format_num(vb)}</div>",
                anchor=(0.5, 1.05),
            )
            lbl_b.setPos(pos + width * 0.55, hb + top_y * 0.012)
            chart.addItem(lbl_b)

    ax_bottom = chart.getPlotItem().getAxis("bottom")
    ax_bottom.setTicks([list(zip(positions, labels))])
    chart.setXRange(-0.8, n - 0.2, padding=0)
    chart.setYRange(0.0, top_y, padding=0)


def style_ReadOnly_table(
    table: QTableWidget,
    min_height: int = 185,
    row_height: int = 32,
    stretch_all: bool = True,
) -> None:
    """Apply consistent financial terminal styling and column sizing to a QTableWidget."""
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.setAlternatingRowColors(True)
    table.setMinimumHeight(min_height)
    table.setWordWrap(False)
    table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
    header = table.horizontalHeader()
    header.setMinimumSectionSize(72)
    if stretch_all:
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    else:
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)


style_readonly_table = style_ReadOnly_table


class CollapsibleConsole(QFrame):
    """Collapsible debugging & process log console with subsystem filtering, clear, and export."""

    def __init__(
        self,
        title: str = "System & Process Console",
        collapsed_by_default: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("CollapsibleConsole")
        self.setStyleSheet(
            f"QFrame#CollapsibleConsole {{ background-color: {BG_PANEL}; "
            f"border: 1px solid {BORDER_ACCENT}; border-radius: 6px; }}"
        )
        self._entries: list[tuple[str, str, str]] = []
        self._collapsed = collapsed_by_default

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(10)

        self.toggle_btn = QPushButton()
        self.toggle_btn.setMinimumHeight(28)
        self.toggle_btn.setStyleSheet(
            "QPushButton { background: #1e293b; color: #f8fafc; font-weight: 700; padding: 4px 10px; }"
        )
        self.toggle_btn.clicked.connect(self.toggle_collapsed)

        self.title_lbl = QLabel(f"<b>{title}</b>")
        self.alert_badge = QLabel("No errors or warnings")
        self.alert_badge.setStyleSheet(
            f"color: {COLOR_GREEN}; background: #064e3b; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 11px;"
        )

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All Subsystems", "Exchange", "Subscriber", "Workload", "Manual", "Errors & Warnings"])
        self.filter_combo.setMinimumWidth(145)
        self.filter_combo.currentIndexChanged.connect(self._rebuild_filtered_view)

        self.clear_btn = QPushButton("Clear Log")
        self.clear_btn.setMinimumHeight(26)
        self.clear_btn.clicked.connect(self.clear)

        self.export_btn = QPushButton("Export Log…")
        self.export_btn.setMinimumHeight(26)
        self.export_btn.clicked.connect(self._export_log)

        header.addWidget(self.toggle_btn)
        header.addWidget(self.title_lbl)
        header.addWidget(self.alert_badge, 1)
        header.addWidget(QLabel("Filter:"))
        header.addWidget(self.filter_combo)
        header.addWidget(self.clear_btn)
        header.addWidget(self.export_btn)
        outer.addLayout(header)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(600)
        self.text_edit.setMinimumHeight(130)
        self.text_edit.setMaximumHeight(180)
        outer.addWidget(self.text_edit)

        self._sync_collapsed_state()

    def _sync_collapsed_state(self) -> None:
        self.text_edit.setVisible(not self._collapsed)
        arrow = "▶ Expand Console" if self._collapsed else "▼ Collapse Console"
        self.toggle_btn.setText(f"{arrow} ({len(self._entries)})")

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self._sync_collapsed_state()

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._sync_collapsed_state()

    @staticmethod
    def _classify_line(line: str) -> tuple[str, str]:
        low = line.lower()
        if any(tok in low for tok in ("error", "failed", "rejected", "mismatch", "exception", "tamper")):
            severity = "error"
        elif any(tok in low for tok in ("warn", "timeout", "retry", "too_old")):
            severity = "warn"
        else:
            severity = "info"

        if "subscriber" in low or "sub-" in low:
            subsys = "Subscriber"
        elif "client" in low or "workload" in low:
            subsys = "Workload"
        elif "manual" in low:
            subsys = "Manual"
        else:
            subsys = "Exchange"
        return subsys, severity

    def appendPlainText(self, text: str) -> None:
        subsys, severity = self._classify_line(text)
        self._entries.append((text, subsys, severity))
        if len(self._entries) > 600:
            self._entries = self._entries[-600:]

        err_count = sum(1 for _, _, s in self._entries if s == "error")
        warn_count = sum(1 for _, _, s in self._entries if s == "warn")
        if severity == "error":
            self.alert_badge.setText(f"Latest Error ({err_count} total): {text[-95:]}")
            self.alert_badge.setStyleSheet(
                f"color: #fecaca; background: #7f1d1d; border: 1px solid {COLOR_RED}; "
                "padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px;"
            )
        elif err_count == 0 and severity == "warn":
            self.alert_badge.setText(f"Warning ({warn_count}): {text[-95:]}")
            self.alert_badge.setStyleSheet(
                f"color: #fde68a; background: #78350f; border: 1px solid {COLOR_AMBER}; "
                "padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 11px;"
            )
        elif err_count == 0 and warn_count == 0:
            self.alert_badge.setText(f"Latest: {text[-90:]}")
            self.alert_badge.setStyleSheet(
                f"color: #a7f3d0; background: #064e3b; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 11px;"
            )

        if self._matches_filter(subsys, severity):
            self.text_edit.appendPlainText(text)
        self._sync_collapsed_state()

    def _matches_filter(self, subsys: str, severity: str) -> bool:
        mode = self.filter_combo.currentText()
        if mode == "All Subsystems":
            return True
        if mode == "Errors & Warnings":
            return severity in ("error", "warn")
        return subsys == mode

    def _rebuild_filtered_view(self) -> None:
        self.text_edit.clear()
        for line, subsys, severity in self._entries:
            if self._matches_filter(subsys, severity):
                self.text_edit.appendPlainText(line)

    def clear(self) -> None:
        self._entries.clear()
        self.text_edit.clear()
        self.alert_badge.setText("Console cleared — no errors")
        self.alert_badge.setStyleSheet(
            f"color: {COLOR_GREEN}; background: #064e3b; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 11px;"
        )
        self._sync_collapsed_state()

    def toPlainText(self) -> str:
        return self.text_edit.toPlainText()

    def setMaximumBlockCount(self, count: int) -> None:
        self.text_edit.setMaximumBlockCount(count)

    def setPlaceholderText(self, text: str) -> None:
        self.text_edit.setPlaceholderText(text)

    def _export_log(self) -> None:
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Export Console Log",
            f"console-log-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log",
            "Log Files (*.log *.txt)",
        )
        if dest:
            Path(dest).write_text(self.text_edit.toPlainText(), encoding="utf-8")
