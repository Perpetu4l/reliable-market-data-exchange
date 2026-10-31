#!/usr/bin/env python3
"""Reproduction and verification runner for Section 4 audit scenarios (F1-F12)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "reliability_runner"))

from live_reliability_runner import build_parser, run_experiment  # noqa: E402


def run_all_repros(build_dir: str, results_dir: str) -> int:
    parser = build_parser()

    # 1. F1/F3 simulation-mode 5s check (verify runtime_ms ~ 5000ms and delivered == events_expected)
    args_sim = parser.parse_args([
        "--build-dir", build_dir,
        "--results-dir", results_dir,
        "--run-id", "repro-f1-sim",
        "--exchange-mode", "simulation",
        "--duration", "3",
        "--dual-feed",
        "--subscriber", "id=t1,feeds=ab,model=none",
    ])
    c_sim, out_sim = run_experiment(args_sim)
    print("F1 simulation check:", json.dumps(out_sim["subscriber_metrics"], indent=2))

    # 2. Client mode: dual-indep, dual-corr, single, plus negative control
    args_client = parser.parse_args([
        "--build-dir", build_dir,
        "--results-dir", results_dir,
        "--run-id", "repro-client-modes",
        "--duration", "3",
        "--clients", "2",
        "--rate", "600",
        "--dual-feed",
        "--subscriber", "id=dual-indep,feeds=ab,model=independent,drop_a=0.2,drop_b=0.2,seed=1",
        "--subscriber", "id=dual-corr,feeds=ab,model=correlated,corr_loss=0.2,seed=2",
        "--subscriber", "id=single,feeds=a,model=independent,drop_a=0.2,seed=3",
    ])
    c_cli, out_cli = run_experiment(args_client)
    print("Client mode check:", json.dumps(out_cli["subscriber_metrics"], indent=2))

    # Negative control (--no-recovery)
    args_neg = parser.parse_args([
        "--build-dir", build_dir,
        "--results-dir", results_dir,
        "--run-id", "repro-negctl",
        "--duration", "2",
        "--clients", "2",
        "--rate", "500",
        "--dual-feed",
        "--expect-failure",
        "--subscriber", "id=negctl,feeds=ab,model=independent,drop_a=0.2,drop_b=0.2,seed=4,no_recovery=1",
    ])
    c_neg, out_neg = run_experiment(args_neg)
    print("Negative control check:", json.dumps(out_neg["subscriber_metrics"], indent=2))

    # 3. F6 snapshot fallback with tiny history=30 and snapshot-interval=200
    args_snap = parser.parse_args([
        "--build-dir", build_dir,
        "--results-dir", results_dir,
        "--run-id", "repro-f6-snapshot",
        "--duration", "3",
        "--clients", "2",
        "--rate", "800",
        "--history", "30",
        "--snapshot-interval", "200",
        "--snapshot-retention", "8",
        "--subscriber", "id=snap-sub,feeds=a,model=independent,drop_a=0.3,seed=5",
    ])
    c_snap, out_snap = run_experiment(args_snap)
    print("F6 snapshot check:", json.dumps(out_snap["subscriber_metrics"], indent=2))

    return 0 if (c_sim == 0 and c_cli == 0 and c_neg == 0 and c_snap == 0) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build-dir", default="build-local")
    ap.add_argument("--results-dir", default="results/repro")
    args = ap.parse_args()
    return run_all_repros(args.build_dir, args.results_dir)


if __name__ == "__main__":
    raise SystemExit(main())
