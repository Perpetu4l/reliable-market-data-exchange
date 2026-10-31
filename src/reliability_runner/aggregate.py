#!/usr/bin/env python3
"""Aggregate multi-seed preset runs into mean, sd, and 95% confidence intervals."""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


NUMERIC_METRICS = (
    "events_expected",
    "delivered",
    "delivered_via_feed_a",
    "delivered_via_feed_b",
    "delivered_via_recovery",
    "events_covered_by_snapshot",
    "permanently_lost",
    "duplicates_suppressed",
    "gap_detections",
    "gap_ranges_requested",
    "events_in_gaps",
    "recovery_requests",
    "recovered_events",
    "too_old_responses",
    "snapshot_recoveries",
    "snapshot_rejected",
    "recovery_failures",
    "potential_single_feed_gaps",
    "gaps_avoided_by_second_feed",
    "redundancy_effectiveness",
    "delivered_events_per_second",
    "tcp_recovery_n",
    "tcp_recovery_p50_ns",
    "tcp_recovery_p95_ns",
    "tcp_recovery_p99_ns",
    "tcp_recovery_p999_ns",
    "tcp_recovery_max_ns",
    "tcp_recovery_mean_ns",
    "other_feed_n",
    "other_feed_p50_ns",
    "other_feed_p99_ns",
    "snapshot_lat_n",
    "snapshot_lat_p50_ns",
    "snapshot_lat_p99_ns",
    "tcp_request_rtt_p50_ns",
    "tcp_request_rtt_p99_ns",
    "server_wait_p50_ns",
    "server_wait_p99_ns",
    "server_wait_max_ns",
    "server_requests_served",
    "exchange_snapshot_bytes_sent",
    "exchange_fairness_jain_ppm",
    "exchange_fairness_saturated_jain_ppm",
    "events_needing_recovery_pct",
)


def _stats(values: list[float]) -> tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0, 0.0
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    sd = math.sqrt(var)
    ci95 = 1.96 * sd / math.sqrt(n)
    return mean, sd, ci95


def aggregate_rows(
    rows: list[dict[str, Any]],
    group_keys: tuple[str, ...],
    output_csv: Path,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        expected = float(row.get("events_expected", 0) or 0)
        recovered = float(row.get("delivered_via_recovery", 0) or 0) + float(row.get("events_covered_by_snapshot", 0) or 0)
        row["events_needing_recovery_pct"] = (100.0 * recovered / expected) if expected > 0 else 0.0
        key = tuple(str(row.get(k, "")) for k in group_keys)
        grouped[key].append(row)

    out_rows: list[dict[str, Any]] = []
    for key, cell_rows in sorted(grouped.items()):
        agg: dict[str, Any] = {k: v for k, v in zip(group_keys, key)}
        agg["n_runs"] = len(cell_rows)
        agg["n_failed"] = sum(
            1 for r in cell_rows
            if str(r.get("run_status", "")) != "completed" or str(r.get("book_matches_exchange", "0")) != "1"
        )
        # Preserve provenance columns from first row
        for prov_key in (
            "schema_version", "transport", "loss_injection", "git_commit",
            "service_delay_us_per_request", "service_delay_us_per_event", "recovery_workers",
        ):
            if prov_key not in agg and cell_rows:
                agg[prov_key] = cell_rows[0].get(prov_key, "")

        for metric in NUMERIC_METRICS:
            vals: list[float] = []
            for r in cell_rows:
                raw = r.get(metric)
                if raw is None or raw == "" or raw == "n/a":
                    continue
                try:
                    vals.append(float(raw))
                except ValueError:
                    pass
            mean, sd, ci95 = _stats(vals)
            agg[f"{metric}_mean"] = round(mean, 6)
            agg[f"{metric}_sd"] = round(sd, 6)
            agg[f"{metric}_ci95"] = round(ci95, 6)
        out_rows.append(agg)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    if out_rows:
        with output_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
            writer.writeheader()
            writer.writerows(out_rows)
    return out_rows
