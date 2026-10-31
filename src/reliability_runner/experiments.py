#!/usr/bin/env python3
"""Run live reliability experiment presets and write aggregated CSVs.

Presets:
  - redundancy_sweep
  - scheduler_contention
  - history_snapshot_sweep
  - snapshot_tamper
  - scalability
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aggregate import aggregate_rows
from live_reliability_runner import build_parser, run_experiment


def _read_summary_rows(summary_csv: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    path = Path(summary_csv)
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        rows = [dict(r) for r in csv.DictReader(f)]
    if extra:
        for r in rows:
            r.update(extra)
    return rows


def run_preset(
    preset: str,
    size: str,
    build_dir: str = "build-local",
    results_dir: str = "results",
    interface: str = "127.0.0.1",
) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    res_root = (root / results_dir).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    all_rows: list[dict[str, Any]] = []
    run_records: list[dict[str, Any]] = []

    if preset == "redundancy_sweep":
        # Paired execution: one exchange run per seed carries all (mode, loss)
        # subscribers so they observe the exact same event stream.
        losses = [0.01, 0.05, 0.10, 0.20] if size == "quick" else [0.0, 0.001, 0.01, 0.05, 0.10, 0.20, 0.30]
        seeds = list(range(1, 6)) if size == "quick" else list(range(1, 11))
        duration = 2.0 if size == "quick" else 3.5
        rate = 900.0 if size == "quick" else 1200.0

        for seed in seeds:
            specs: list[str] = []
            for loss in losses:
                loss_tag = f"{loss:g}".replace(".", "p")
                specs.append(f"id=single_{loss_tag},feeds=a,model=independent,drop_a={loss},seed={seed*100+1},role=single_{loss}")
                specs.append(f"id=indep_{loss_tag},feeds=ab,model=independent,drop_a={loss},drop_b={loss},seed={seed*100+2},role=dual_independent_{loss}")
                specs.append(f"id=corr_{loss_tag},feeds=ab,model=correlated,corr_loss={loss},seed={seed*100+3},role=dual_correlated_{loss}")

            parser = build_parser()
            args = parser.parse_args([
                "--build-dir", build_dir,
                "--results-dir", results_dir,
                "--interface", interface,
                "--run-id", f"redundancy-{size}-seed{seed}-{stamp}",
                "--duration", str(duration),
                "--clients", "2",
                "--rate", str(rate),
                "--workload-seed", str(10 + seed),
                "--dual-feed",
            ] + [ item for s in specs for item in ("--subscriber", s) ])
            code, outcome = run_experiment(args)
            run_records.append({"seed": seed, "exit_code": code, "run_id": outcome["run_id"], "success": outcome["success"]})
            for r in _read_summary_rows(outcome["summary_csv"], {"seed": seed, "paired_note": "paired_counting_only_shared_server"}):
                role = str(r.get("role", ""))
                if role.startswith("single_"):
                    r["mode_group"] = "single"
                    r["configured_loss"] = role.split("_", 1)[1]
                elif role.startswith("dual_independent_"):
                    r["mode_group"] = "dual_independent"
                    r["configured_loss"] = role.split("dual_independent_", 1)[1]
                elif role.startswith("dual_correlated_"):
                    r["mode_group"] = "dual_correlated"
                    r["configured_loss"] = role.split("dual_correlated_", 1)[1]
                all_rows.append(r)

        agg_path = res_root / f"aggregate-redundancy_sweep.csv"
        agg_rows = aggregate_rows(all_rows, ("mode_group", "configured_loss"), agg_path)

    elif preset == "scheduler_contention":
        # 1 noisy (single feed, loss 0.5, concurrency=8) + 5 quiet (loss 0.02, concurrency=1)
        # Exchange with --recovery-workers 1 and modelled service delay so server saturates.
        seeds = list(range(1, 6)) if size == "quick" else list(range(1, 11))
        duration = 2.0 if size == "quick" else 3.5
        rate = 750.0 if size == "quick" else 1000.0
        delay_per_req_us = 1500
        delay_per_event_us = 20

        for seed in seeds:
            for policy in ("fcfs", "round-robin"):
                specs = [
                    f"id=noisy-1,feeds=a,model=independent,drop_a=0.50,seed={seed*100+1},role=noisy,concurrency=8",
                ]
                for q in range(1, 6):
                    specs.append(
                        f"id=quiet-{q},feeds=a,model=independent,drop_a=0.02,seed={seed*100+10+q},role=quiet,concurrency=1"
                    )
                parser = build_parser()
                args = parser.parse_args([
                    "--build-dir", build_dir,
                    "--results-dir", results_dir,
                    "--interface", interface,
                    "--run-id", f"scheduler-{policy}-seed{seed}-{stamp}",
                    "--duration", str(duration),
                    "--clients", "2",
                    "--rate", str(rate),
                    "--workload-seed", str(50 + seed),
                    "--recovery-policy", policy,
                    "--recovery-workers", "1",
                    "--service-delay-us-per-request", str(delay_per_req_us),
                    "--service-delay-us-per-event", str(delay_per_event_us),
                ] + [ item for s in specs for item in ("--subscriber", s) ])
                code, outcome = run_experiment(args)
                run_records.append({
                    "seed": seed, "policy": policy, "exit_code": code,
                    "run_id": outcome["run_id"], "success": outcome["success"],
                })
                all_rows.extend(_read_summary_rows(outcome["summary_csv"], {"seed": seed}))

        agg_path = res_root / f"aggregate-scheduler_contention.csv"
        agg_rows = aggregate_rows(all_rows, ("recovery_policy", "role"), agg_path)

    elif preset == "history_snapshot_sweep":
        histories = [0, 16, 128, 4096]
        snap_intervals = [0, 100, 1000]
        seeds = [1, 2] if size == "quick" else list(range(1, 6))
        duration = 1.5 if size == "quick" else 2.5
        rate = 800.0

        for seed in seeds:
            for hist in histories:
                for snap_int in snap_intervals:
                    parser = build_parser()
                    args = parser.parse_args([
                        "--build-dir", build_dir,
                        "--results-dir", results_dir,
                        "--interface", interface,
                        "--run-id", f"histsnap-h{hist}-s{snap_int}-seed{seed}-{stamp}",
                        "--duration", str(duration),
                        "--clients", "2",
                        "--rate", str(rate),
                        "--workload-seed", str(100 + seed),
                        "--history", str(hist),
                        "--snapshot-interval", str(snap_int),
                        "--snapshot-retention", "8",
                        "--subscriber", f"id=sub-1,feeds=a,model=independent,drop_a=0.15,seed={seed*10+1},role=lossy",
                    ])
                    code, outcome = run_experiment(args)
                    run_records.append({
                        "seed": seed, "history": hist, "snapshot_interval": snap_int,
                        "exit_code": code, "run_id": outcome["run_id"], "success": outcome["success"],
                    })
                    all_rows.extend(_read_summary_rows(outcome["summary_csv"], {"seed": seed}))

        agg_path = res_root / f"aggregate-history_snapshot_sweep.csv"
        agg_rows = aggregate_rows(all_rows, ("history_events", "snapshot_interval"), agg_path)

    elif preset == "snapshot_tamper":
        tamper_modes = ["book_value", "sequence", "previous_hash", "hash"]
        for mode in tamper_modes:
            parser = build_parser()
            args = parser.parse_args([
                "--build-dir", build_dir,
                "--results-dir", results_dir,
                "--interface", interface,
                "--run-id", f"tamper-{mode}-{stamp}",
                "--duration", "1.5",
                "--clients", "2",
                "--rate", "600",
                "--history", "0",
                "--snapshot-interval", "50",
                "--tamper-snapshot", mode,
                "--expect-failure",
                "--subscriber", "id=tamper-sub,feeds=a,model=independent,drop_a=0.25,seed=7,role=tamper_target,retries=1",
            ])
            code, outcome = run_experiment(args)
            run_records.append({
                "tamper_mode": mode,
                "exit_code": code,
                "run_id": outcome["run_id"],
                "expected_failure_observed": outcome.get("expected_failure_observed", False),
                "success": outcome["success"],
            })
            all_rows.extend(_read_summary_rows(outcome["summary_csv"], {
                "expected_failure_observed": int(outcome.get("expected_failure_observed", False)),
            }))

        agg_path = res_root / f"aggregate-snapshot_tamper.csv"
        agg_rows = aggregate_rows(all_rows, ("tamper_snapshot", "run_status", "failure_reason"), agg_path)

    elif preset == "scalability":
        counts = [1, 2, 4, 8, 16, 32] if size == "full" else [1, 2, 4, 8, 16]
        seeds = [1, 2] if size == "quick" else list(range(1, 6))
        duration = 1.8 if size == "quick" else 2.5
        rate = 700.0

        for seed in seeds:
            for n_subs in counts:
                specs = [
                    f"id=sub-{i+1},feeds=ab,model=independent,drop_a=0.10,drop_b=0.10,seed={seed*100+i+1},role=scale_{n_subs}"
                    for i in range(n_subs)
                ]
                parser = build_parser()
                args = parser.parse_args([
                    "--build-dir", build_dir,
                    "--results-dir", results_dir,
                    "--interface", interface,
                    "--run-id", f"scale-n{n_subs}-seed{seed}-{stamp}",
                    "--duration", str(duration),
                    "--clients", "2",
                    "--rate", str(rate),
                    "--workload-seed", str(200 + seed),
                    "--dual-feed",
                ] + [ item for s in specs for item in ("--subscriber", s) ])
                code, outcome = run_experiment(args)
                run_records.append({
                    "seed": seed, "n_subscribers": n_subs, "exit_code": code,
                    "run_id": outcome["run_id"], "success": outcome["success"],
                })
                all_rows.extend(_read_summary_rows(outcome["summary_csv"], {"seed": seed, "n_subscribers": str(n_subs)}))

        agg_path = res_root / f"aggregate-scalability.csv"
        agg_rows = aggregate_rows(all_rows, ("n_subscribers",), agg_path)

    else:
        raise ValueError(f"Unknown preset: {preset}")

    raw_out_path = res_root / f"raw-{preset}.csv"
    if all_rows:
        keys = list(all_rows[0].keys())
        for r in all_rows[1:]:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
        with raw_out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(all_rows)

    all_passed = all(r.get("success", False) for r in run_records)
    return {
        "preset": preset,
        "size": size,
        "success": all_passed,
        "aggregate_csv": str(agg_path),
        "raw_csv": str(raw_out_path),
        "runs": run_records,
        "aggregate_rows": agg_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preset",
        required=True,
        choices=("redundancy_sweep", "scheduler_contention", "history_snapshot_sweep", "snapshot_tamper", "scalability", "all"),
    )
    parser.add_argument("--size", choices=("quick", "full"), default="quick")
    parser.add_argument("--build-dir", default="build-local")
    parser.add_argument("--results-dir", default="results")
    parser.add_argument("--interface", default="127.0.0.1")
    args = parser.parse_args()

    presets = (
        ["redundancy_sweep", "scheduler_contention", "history_snapshot_sweep", "snapshot_tamper", "scalability"]
        if args.preset == "all"
        else [args.preset]
    )
    summaries = []
    overall_ok = True
    for p in presets:
        res = run_preset(p, args.size, args.build_dir, args.results_dir, args.interface)
        summaries.append(res)
        if not res["success"]:
            overall_ok = False
    print(json.dumps({"success": overall_ok, "presets": summaries}, indent=2))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
