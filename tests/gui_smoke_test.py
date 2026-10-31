#!/usr/bin/env python3
"""Offscreen PyQt6 smoke test for ExchangeControlPanel and ReliabilityDashboard.

Runs with QT_QPA_PLATFORM=offscreen:
1. Instantiates ReliabilityDashboard and ExchangeControlPanel and verifies
   honest empty states when no data are loaded.
2. Runs a tiny real live experiment via live_reliability_runner.py (plus a
   failed negative-control run), loads the resulting run directories into the
   GUI, and asserts that tables/charts contain real data, provenance text is
   present, and failed runs render red.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication  # noqa: E402
except ModuleNotFoundError:
    if os.environ.get("GUI_SMOKE_REEXEC") != "1":
        for candidate in (
            "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
        ):
            if Path(candidate).is_file() and candidate != sys.executable:
                env = dict(os.environ)
                env["GUI_SMOKE_REEXEC"] = "1"
                res = subprocess.run([candidate, __file__, *sys.argv[1:]], env=env)
                raise SystemExit(res.returncode)
    raise

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src" / "simulation_python"))
sys.path.insert(0, str(ROOT / "src" / "reliability_runner"))

from gui.exchange_control_panel import ExchangeControlPanel  # noqa: E402
from gui.reliability_dashboard import ReliabilityDashboard  # noqa: E402
from live_reliability_runner import build_parser, run_experiment  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build-dir", default="build-local")
    args = ap.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)

    dashboard = ReliabilityDashboard()
    exchange_ctrl = ExchangeControlPanel()

    # 1. Verify empty state when no data are loaded
    assert "Empty state" in dashboard.summary.text(), f"Unexpected initial summary: {dashboard.summary.text()}"
    assert "No data" in dashboard.recovery.gap_label.text()
    assert exchange_ctrl.sub_cfg_table.rowCount() >= 1

    temp_results = Path(tempfile.mkdtemp(prefix="gui-smoke-"))
    try:
        # 2. Run a tiny real passing live run
        parser = build_parser()
        run_args = parser.parse_args([
            "--build-dir", args.build_dir,
            "--results-dir", str(temp_results),
            "--run-id", "gui-smoke-pass",
            "--duration", "1.2",
            "--clients", "2",
            "--rate", "400",
            "--dual-feed",
            "--subscriber", "id=smoke-sub-1,feeds=ab,model=independent,drop_a=0.15,drop_b=0.15,seed=11",
        ])
        code, outcome = run_experiment(run_args)
        if code != 0:
            tail = "\n".join(outcome.get("exchange_log_tail", [])).lower()
            err = str(outcome.get("error", "")).lower()
            if "bind" in (tail + err) and ("operation not permitted" in (tail + err) or "permission denied" in (tail + err)):
                print("SKIP: socket binding forbidden in this environment")
                return 77
            print("FAIL: smoke test live runner failed:", outcome, file=sys.stderr)
            return 1

        loaded = dashboard.load_path(outcome["run_dir"])
        assert loaded, "Failed to load passing run_dir into ReliabilityDashboard"
        assert "LIVE RUN PASSED" in dashboard.summary.text(), dashboard.summary.text()
        assert "udp_loopback" in dashboard.provenance_banner.text(), dashboard.provenance_banner.text()
        assert "#197b30" in dashboard.summary.styleSheet(), "Passing run did not render green"
        assert "1 (" in dashboard.integrity.book_match_status.text()

        # 3. Run a tiny negative-control failing run and verify red failure rendering
        fail_args = parser.parse_args([
            "--build-dir", args.build_dir,
            "--results-dir", str(temp_results),
            "--run-id", "gui-smoke-fail",
            "--duration", "1.0",
            "--clients", "2",
            "--rate", "400",
            "--expect-failure",
            "--subscriber", "id=smoke-neg,feeds=a,model=independent,drop_a=0.30,seed=9,no_recovery=1",
        ])
        f_code, f_outcome = run_experiment(fail_args)
        assert f_code == 0 and f_outcome.get("expected_failure_observed"), "Negative control run failed unexpectedly"

        dashboard.load_path(f_outcome["run_dir"])
        assert "#b42318" in dashboard.summary.styleSheet(), "Failed run did not render red"
        assert "0 (" in dashboard.integrity.book_match_status.text()

        dashboard.shutdown()
        exchange_ctrl.shutdown()
        app.quit()
        print("PASS: GUI offscreen smoke test verified empty state, real data loading, provenance, and red failure state")
        return 0
    finally:
        shutil.rmtree(temp_results, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
