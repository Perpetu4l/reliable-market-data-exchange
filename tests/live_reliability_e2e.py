#!/usr/bin/env python3
"""End-to-end integration tests for the live UDP/TCP reliability pipeline.

Exercises:
  1. Zero-loss baseline (book_matches_exchange=1, recovery_requests=0)
  2. Lossy run with recovery (single, dual-independent, dual-correlated)
  3. Negative control (--no-recovery -> exit != 0, permanently_lost > 0, book_matches_exchange=0)
  4. Determinism of dropped_seq_hash_a/b across two identical seeded runs
  5. Tiny history -> snapshot fallback (snapshot_recoveries > 0, recovery_failures=0, book match)
  6. Snapshot tamper rejection (book_value, sequence, previous_hash, hash)
  7. 8 concurrent subscribers on one machine
  8. Optional --slow scheduler contention check (RR quiet p99 wait < FCFS quiet p99 wait)

Returns 77 (CTest SKIP) ONLY when the OS explicitly forbids socket binding.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "reliability_runner"))

from live_reliability_runner import build_parser, parse_sub_map, run_experiment  # noqa: E402


def socket_policy_blocked(outcome: dict) -> bool:
    tail = "\n".join(outcome.get("exchange_log_tail", [])).lower()
    err = str(outcome.get("error", "")).lower()
    combined = tail + "\n" + err
    return ("operation not permitted" in combined or "permission denied" in combined) and "bind" in combined


def run_one(cli_args: list[str]) -> tuple[int, dict]:
    parser = build_parser()
    args = parser.parse_args(cli_args)
    return run_experiment(args)


def run_standard_suite(build_dir: str, temp_dir: Path) -> int:
    # 1. Zero-loss baseline
    code_z, out_z = run_one([
        "--build-dir", build_dir,
        "--results-dir", str(temp_dir),
        "--run-id", "e2e-zero-loss",
        "--duration", "1.5",
        "--clients", "2",
        "--rate", "300",
        "--workload-seed", "11",
        "--dual-feed",
        "--subscriber", "id=zero-loss,feeds=ab,model=none,seed=1,role=zero",
    ])
    if code_z != 0:
        if socket_policy_blocked(out_z):
            print("SKIP: this environment forbids socket binding; run on a normal host for live proof")
            return 77
        print("FAIL: zero-loss baseline run failed:", json.dumps(out_z, indent=2), file=sys.stderr)
        return 1
    z = out_z["subscriber_metrics"]["zero-loss"]
    if (
        z.get("gap_ranges_requested") != "0"
        or z.get("recovery_requests") != "0"
        or z.get("permanently_lost") != "0"
        or z.get("book_matches_exchange") != "1"
    ):
        print("FAIL: zero-loss subscriber had unexpected gaps/loss:", z, file=sys.stderr)
        return 1

    # 2. Lossy recovery + 7. 8 concurrent subscribers on one machine
    code, out = run_one([
        "--build-dir", build_dir,
        "--results-dir", str(temp_dir),
        "--run-id", "e2e-multi-8subs",
        "--duration", "1.5",
        "--clients", "2",
        "--rate", "400",
        "--workload-seed", "21",
        "--dual-feed",
        "--subscriber", "id=indep-1,feeds=ab,model=independent,drop_a=0.10,drop_b=0.10,seed=1,role=indep",
        "--subscriber", "id=indep-2,feeds=ab,model=independent,drop_a=0.15,drop_b=0.15,seed=2,role=indep",
        "--subscriber", "id=indep-3,feeds=ab,model=independent,drop_a=0.15,drop_b=0.15,seed=3,role=indep",
        "--subscriber", "id=indep-4,feeds=ab,model=independent,drop_a=0.20,drop_b=0.20,seed=4,role=indep",
        "--subscriber", "id=corr-1,feeds=ab,model=correlated,corr_loss=0.15,seed=5,role=corr",
        "--subscriber", "id=corr-2,feeds=ab,model=correlated,corr_loss=0.20,seed=6,role=corr",
        "--subscriber", "id=single-1,feeds=a,model=independent,drop_a=0.15,seed=7,role=single",
        "--subscriber", "id=single-2,feeds=a,model=independent,drop_a=0.20,seed=8,role=single",
    ])
    if code != 0:
        print("FAIL: 8-subscriber lossy recovery run failed:", json.dumps(out, indent=2), file=sys.stderr)
        return 1

    subs = out["subscriber_metrics"]
    for sid, m in subs.items():
        if m.get("permanently_lost") != "0" or m.get("book_matches_exchange") != "1" or m.get("accounting_valid") != "1":
            print(f"FAIL: subscriber {sid} did not reconcile cleanly:", m, file=sys.stderr)
            return 1

    # 3. Negative control (--no-recovery)
    code_neg, out_neg = run_one([
        "--build-dir", build_dir,
        "--results-dir", str(temp_dir),
        "--run-id", "e2e-negctl",
        "--duration", "1.2",
        "--clients", "2",
        "--rate", "500",
        "--expect-failure",
        "--subscriber", "id=neg-sub,feeds=a,model=independent,drop_a=0.25,seed=9,no_recovery=1",
    ])
    if code_neg != 0 or not out_neg.get("expected_failure_observed"):
        print("FAIL: negative control did not fail as expected:", json.dumps(out_neg, indent=2), file=sys.stderr)
        return 1
    neg_m = out_neg["subscriber_metrics"]["neg-sub"]
    if int(neg_m.get("permanently_lost", "0")) <= 0 or neg_m.get("book_matches_exchange") != "0" or int(neg_m.get("events_expected", "0")) <= 0:
        print("FAIL: negative control metrics invalid:", neg_m, file=sys.stderr)
        return 1

    # 4. Determinism of dropped_seq_hash across two runs with until_seq=150
    hashes = []
    for run_idx in (1, 2):
        c_det, out_det = run_one([
            "--build-dir", build_dir,
            "--results-dir", str(temp_dir),
            "--run-id", f"e2e-det-{run_idx}",
            "--duration", "1.5",
            "--clients", "2",
            "--rate", "500",
            "--until-seq", "150",
            "--workload-seed", "77",
            "--dual-feed",
            "--subscriber", "id=det-sub,feeds=ab,model=independent,drop_a=0.20,drop_b=0.20,seed=12345",
        ])
        if c_det != 0:
            print("FAIL: determinism run failed:", json.dumps(out_det, indent=2), file=sys.stderr)
            return 1
        hashes.append(out_det["subscriber_metrics"]["det-sub"])

    if (
        hashes[0]["dropped_seq_hash_a"] != hashes[1]["dropped_seq_hash_a"]
        or hashes[0]["dropped_seq_hash_b"] != hashes[1]["dropped_seq_hash_b"]
    ):
        print("FAIL: dropped_seq_hash differed across identical seeded runs", hashes, file=sys.stderr)
        return 1

    # 5. Tiny history -> snapshot fallback (on-demand + periodic)
    c_snap, out_snap = run_one([
        "--build-dir", build_dir,
        "--results-dir", str(temp_dir),
        "--run-id", "e2e-snapshot-fallback",
        "--duration", "1.5",
        "--clients", "2",
        "--rate", "800",
        "--history", "4",
        "--snapshot-interval", "100",
        "--subscriber", "id=snap-sub,feeds=a,model=independent,drop_a=0.30,seed=55",
    ])
    if c_snap != 0:
        print("FAIL: tiny history snapshot fallback run failed:", json.dumps(out_snap, indent=2), file=sys.stderr)
        return 1
    snap_m = out_snap["subscriber_metrics"]["snap-sub"]
    if int(snap_m.get("snapshot_recoveries", "0")) <= 0 or snap_m.get("recovery_failures") != "0" or snap_m.get("book_matches_exchange") != "1":
        print("FAIL: snapshot fallback did not recover cleanly:", snap_m, file=sys.stderr)
        return 1

    # 6. Each tamper mode rejected
    for mode in ("book_value", "sequence", "previous_hash", "hash"):
        c_tamper, out_tamper = run_one([
            "--build-dir", build_dir,
            "--results-dir", str(temp_dir),
            "--run-id", f"e2e-tamper-{mode}",
            "--duration", "1.2",
            "--clients", "2",
            "--rate", "500",
            "--history", "0",
            "--snapshot-interval", "50",
            "--tamper-snapshot", mode,
            "--expect-failure",
            "--subscriber", "id=t-sub,feeds=a,model=independent,drop_a=0.30,seed=7,retries=1",
        ])
        if c_tamper != 0 or not out_tamper.get("expected_failure_observed"):
            print(f"FAIL: tamper mode {mode} was not rejected:", json.dumps(out_tamper, indent=2), file=sys.stderr)
            return 1
        tm = out_tamper["subscriber_metrics"]["t-sub"]
        if int(tm.get("snapshot_rejected", "0")) <= 0 or tm.get("reason") != "snapshot_rejected":
            print(f"FAIL: tamper mode {mode} did not record snapshot_rejected:", tm, file=sys.stderr)
            return 1

    print("PASS: all standard live UDP/TCP e2e checks succeeded")
    return 0


def run_slow_scheduler_test(build_dir: str, temp_dir: Path) -> int:
    fcfs_quiet_p99s: list[float] = []
    rr_quiet_p99s: list[float] = []

    for seed in range(1, 6):
        specs = [f"id=noisy-1,feeds=a,model=independent,drop_a=0.50,seed={seed*100+1},role=noisy,concurrency=8"]
        for q in range(1, 6):
            specs.append(f"id=quiet-{q},feeds=a,model=independent,drop_a=0.02,seed={seed*100+10+q},role=quiet,concurrency=1")

        for policy, target_list in (("fcfs", fcfs_quiet_p99s), ("round-robin", rr_quiet_p99s)):
            code, out = run_one([
                "--build-dir", build_dir,
                "--results-dir", str(temp_dir),
                "--run-id", f"slow-sched-{policy}-s{seed}",
                "--duration", "1.8",
                "--clients", "2",
                "--rate", "700",
                "--workload-seed", str(50 + seed),
                "--recovery-policy", policy,
                "--recovery-workers", "1",
                "--service-delay-us-per-request", "1500",
                "--service-delay-us-per-event", "20",
            ] + [item for s in specs for item in ("--subscriber", s)])
            if code != 0:
                if socket_policy_blocked(out):
                    print("SKIP: this environment forbids socket binding")
                    return 77
                print(f"FAIL: scheduler contention run ({policy}, seed={seed}) failed:", json.dumps(out, indent=2), file=sys.stderr)
                return 1
            wait_p99_map = parse_sub_map(out["exchange_metrics"].get("per_subscriber_wait_p99_ns", "none"))
            quiet_vals = [v for k, v in wait_p99_map.items() if k.startswith("quiet-") and v > 0]
            if quiet_vals:
                target_list.append(sum(quiet_vals) / len(quiet_vals))

    if not fcfs_quiet_p99s or not rr_quiet_p99s:
        print("FAIL: no quiet subscriber queue wait samples collected", file=sys.stderr)
        return 1

    mean_fcfs_ms = (sum(fcfs_quiet_p99s) / len(fcfs_quiet_p99s)) / 1e6
    mean_rr_ms = (sum(rr_quiet_p99s) / len(rr_quiet_p99s)) / 1e6
    factor = mean_fcfs_ms / mean_rr_ms if mean_rr_ms > 0 else float("inf")
    print(
        f"SCHEDULER_CONTENTION_RESULT fcfs_quiet_p99_ms={mean_fcfs_ms:.3f} "
        f"rr_quiet_p99_ms={mean_rr_ms:.3f} improvement_factor={factor:.2f}x"
    )
    if mean_rr_ms >= mean_fcfs_ms:
        print("FAIL: RR quiet p99 wait was not lower than FCFS quiet p99 wait", file=sys.stderr)
        return 1
    print("PASS: Round-Robin reduced quiet-subscriber p99 queue wait vs FCFS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", default="build-local")
    parser.add_argument("--recovery-port", type=int, default=19090)
    parser.add_argument("--slow", action="store_true", help="Run the 5-seed scheduler contention comparison")
    args = parser.parse_args()

    temporary_results = Path(tempfile.mkdtemp(prefix="reliable-e2e-"))
    try:
        if args.slow:
            return run_slow_scheduler_test(args.build_dir, temporary_results)
        return run_standard_suite(args.build_dir, temporary_results)
    finally:
        shutil.rmtree(temporary_results, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
