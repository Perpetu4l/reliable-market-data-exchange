#!/usr/bin/env python3
"""Generate publication-ready PNG charts from aggregate-<preset>.csv files.

Produces the same charts shown in the PyQt6 Reliability Dashboard directly
from the persisted CSV outputs (no synthetic data).
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def plot_all(results_dir: Path, output_dir: Path) -> list[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed; skipping PNG chart generation.", file=sys.stderr)
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []

    # 1. Redundancy sweep: Events needing recovery (%) vs loss rate + theory p and p^2
    #    and Redundancy effectiveness vs loss rate.
    red_rows = _read_csv(results_dir / "aggregate-redundancy_sweep.csv")
    if red_rows:
        prov = red_rows[0]
        subtitle = (
            f"transport={prov.get('transport', 'udp_loopback')}, "
            f"loss_injection={prov.get('loss_injection', 'app_level_receive_side')}, "
            f"n_runs/cell={prov.get('n_runs', '?')} (paired counting run)"
        )
        fig, ax = plt.subplots(figsize=(8, 5))
        by_mode: dict[str, list[tuple[float, float, float]]] = {}
        for r in red_rows:
            mode = r.get("mode_group", "")
            loss = float(r.get("configured_loss", "0") or 0)
            mean = float(r.get("events_needing_recovery_pct_mean", "0") or 0)
            sd = float(r.get("events_needing_recovery_pct_sd", "0") or 0)
            by_mode.setdefault(mode, []).append((loss, mean, sd))
        all_losses = sorted({pt[0] for pts in by_mode.values() for pt in pts})
        for mode, pts in sorted(by_mode.items()):
            pts.sort(key=lambda x: x[0])
            xs = [p[0] * 100.0 for p in pts]
            ys = [p[1] for p in pts]
            errs = [p[2] for p in pts]
            ax.errorbar(xs, ys, yerr=errs, marker="o", capsize=4, label=mode)
        if all_losses:
            xs_t = [p * 100.0 for p in all_losses]
            ax.plot(xs_t, [p * 100.0 for p in all_losses], "--", color="gray", label="theory: p (single/correlated)")
            ax.plot(xs_t, [(p * p) * 100.0 for p in all_losses], ":", color="black", label="theory: p^2 (dual independent)")
        ax.set_xlabel("Application Loss Rate p (%)")
        ax.set_ylabel("Events Needing TCP Recovery (%)")
        ax.set_title(f"Events Needing Recovery (%) vs Loss Rate\n{subtitle}", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.legend()
        out1 = output_dir / "chart1_recovery_rate_vs_loss.png"
        fig.tight_layout()
        fig.savefig(out1, dpi=150)
        plt.close(fig)
        generated.append(str(out1))

        # Chart 5: Redundancy effectiveness vs loss
        fig, ax = plt.subplots(figsize=(8, 5))
        for mode, _ in sorted(by_mode.items()):
            pts_eff = []
            for r in red_rows:
                if r.get("mode_group") == mode:
                    loss = float(r.get("configured_loss", "0") or 0)
                    if loss <= 0:
                        continue
                    eff_m = float(r.get("redundancy_effectiveness_mean", "0") or 0)
                    eff_sd = float(r.get("redundancy_effectiveness_sd", "0") or 0)
                    pts_eff.append((loss * 100.0, eff_m, eff_sd))
            if pts_eff:
                pts_eff.sort(key=lambda x: x[0])
                ax.errorbar([x[0] for x in pts_eff], [x[1] for x in pts_eff],
                            yerr=[x[2] for x in pts_eff], marker="s", capsize=4, label=mode)
        ax.set_xlabel("Application Loss Rate p (%)")
        ax.set_ylabel("Redundancy Effectiveness (avoided / potential)")
        ax.set_ylim(-0.05, 1.05)
        ax.set_title(f"Redundancy Effectiveness vs Loss Rate\n{subtitle}", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.legend()
        out5 = output_dir / "chart5_redundancy_effectiveness.png"
        fig.tight_layout()
        fig.savefig(out5, dpi=150)
        plt.close(fig)
        generated.append(str(out5))

    # 3. Scheduler contention: FCFS vs RR grouped bars
    sched_rows = _read_csv(results_dir / "aggregate-scheduler_contention.csv")
    if sched_rows:
        prov = sched_rows[0]
        subtitle = (
            f"transport={prov.get('transport', 'udp_loopback')}, "
            f"modelled_delay_us(req/event)={prov.get('service_delay_us_per_request','0')}/"
            f"{prov.get('service_delay_us_per_event','0')}, n_runs={prov.get('n_runs','?')}"
        )
        fig, ax = plt.subplots(figsize=(8, 5))
        labels = []
        p50_ms = []
        p99_ms = []
        p99_sd_ms = []
        for r in sched_rows:
            pol = r.get("recovery_policy", "")
            role = r.get("role", "")
            labels.append(f"{pol}\n({role})")
            p50_ms.append(float(r.get("server_wait_p50_ns_mean", "0") or 0) / 1e6)
            p99_ms.append(float(r.get("server_wait_p99_ns_mean", "0") or 0) / 1e6)
            p99_sd_ms.append(float(r.get("server_wait_p99_ns_sd", "0") or 0) / 1e6)
        xs = list(range(len(labels)))
        width = 0.35
        ax.bar([x - width / 2 for x in xs], p50_ms, width=width, label="Queue Wait p50 (ms)")
        ax.bar([x + width / 2 for x in xs], p99_ms, width=width, yerr=p99_sd_ms, capsize=4, label="Queue Wait p99 (ms)")
        ax.set_xticks(xs)
        ax.set_xticklabels(labels)
        ax.set_ylabel("Server Queue Wait (ms)")
        ax.set_title(f"Scheduler Contention: FCFS vs Round-Robin Queue Wait\n{subtitle}", fontsize=10)
        ax.grid(True, axis="y", alpha=0.3)
        ax.legend()
        out3 = output_dir / "chart3_fcfs_vs_rr_queue_wait.png"
        fig.tight_layout()
        fig.savefig(out3, dpi=150)
        plt.close(fig)
        generated.append(str(out3))

    # 4. History & snapshot sweep
    hs_rows = _read_csv(results_dir / "aggregate-history_snapshot_sweep.csv")
    if hs_rows:
        fig, ax = plt.subplots(figsize=(8, 5))
        labels = [f"H={r.get('history_events')}\nS={r.get('snapshot_interval')}" for r in hs_rows]
        too_old = [float(r.get("too_old_responses_mean", "0") or 0) for r in hs_rows]
        snaps = [float(r.get("snapshot_recoveries_mean", "0") or 0) for r in hs_rows]
        xs = list(range(len(labels)))
        ax.bar(xs, too_old, width=0.5, label="TOO_OLD responses / Snapshot recoveries")
        ax.plot(xs, snaps, "ro-", label="Verified Snapshots Applied")
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, fontsize=8)
        ax.set_ylabel("Count")
        ax.set_title("History Capacity & Snapshot Interval vs Snapshot Fallback", fontsize=10)
        ax.grid(True, axis="y", alpha=0.3)
        ax.legend()
        out4 = output_dir / "chart4_history_snapshot_sweep.png"
        fig.tight_layout()
        fig.savefig(out4, dpi=150)
        plt.close(fig)
        generated.append(str(out4))

    # 2. Scalability: Latency vs N subscribers
    scale_rows = _read_csv(results_dir / "aggregate-scalability.csv")
    if scale_rows:
        scale_rows.sort(key=lambda r: int(r.get("n_subscribers", "0") or 0))
        ns = [int(r.get("n_subscribers", "0") or 0) for r in scale_rows]
        p50_us = [float(r.get("tcp_recovery_p50_ns_mean", "0") or 0) / 1e3 for r in scale_rows]
        p99_us = [float(r.get("tcp_recovery_p99_ns_mean", "0") or 0) / 1e3 for r in scale_rows]
        wait_p99_us = [float(r.get("server_wait_p99_ns_mean", "0") or 0) / 1e3 for r in scale_rows]
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(ns, p50_us, "bo-", label="Subscriber TCP Recovery Latency p50 (µs)")
        ax.plot(ns, p99_us, "rs-", label="Subscriber TCP Recovery Latency p99 (µs)")
        ax.plot(ns, wait_p99_us, "g^--", label="Server Queue Wait p99 (µs)")
        ax.set_xlabel("Concurrent Subscribers (N)")
        ax.set_ylabel("Latency / Wait (µs)")
        ax.set_title("Scalability: Recovery Latency and Queue Wait vs N Subscribers", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.legend()
        out2 = output_dir / "chart2_scalability_latency.png"
        fig.tight_layout()
        fig.savefig(out2, dpi=150)
        plt.close(fig)
        generated.append(str(out2))

    return generated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default="results")
    parser.add_argument("--output-dir", "--out-dir", dest="output_dir", default="results/charts")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    charts = plot_all((root / args.results_dir).resolve(), (root / args.output_dir).resolve())
    for c in charts:
        print(f"Generated: {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
