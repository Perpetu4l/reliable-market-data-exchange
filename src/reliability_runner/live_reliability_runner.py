#!/usr/bin/env python3
"""Run one provenance-labelled live reliability experiment.

This script starts the project's real exchange and reliable_subscriber
executables as independent processes. It does not synthesize events, results,
or connection success. A result is successful only when the exchange reports
its recovery listener ready and the subscriber exits with a parsed result that
contains at least one actually delivered event.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


RESULT_RE = re.compile(r"RELIABLE_SUBSCRIBER_RESULT\s+(.*)$")
EXCHANGE_RESULT_RE = re.compile(r"EXCHANGE_RESULT\s+(.*)$")
READY_TEXT = "Recovery TCP server listening"


@dataclass
class RunConfig:
    duration_seconds: int
    dual_feed: bool
    feed_a_loss: float
    feed_b_loss: float
    correlated_loss: float
    fault_seed: int
    recovery_policy: str
    history_events: int
    snapshot_interval: int
    snapshot_retention: int
    recovery_port: int
    multicast_interface: str
    loss_injection: str
    subscriber_count: int


def reader(process: subprocess.Popen[str], target: list[str], ready: threading.Event) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip("\n")
        target.append(line)
        if len(target) > 300:
            del target[:-300]
        if READY_TEXT in line:
            ready.set()


def stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def parse_fields(lines: list[str], pattern: re.Pattern[str]) -> dict[str, str] | None:
    for line in reversed(lines):
        match = pattern.search(line)
        if not match:
            continue
        fields: dict[str, str] = {}
        for part in match.group(1).split():
            if "=" in part:
                key, value = part.split("=", 1)
                fields[key] = value
        return fields
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", default="build-local", help="CMake build directory")
    parser.add_argument("--duration", type=int, default=10, help="Subscriber runtime in seconds")
    parser.add_argument("--interface", default="127.0.0.1", help="Multicast interface IPv4")
    parser.add_argument("--dual-feed", action="store_true", help="Use Feed A and Feed B")
    parser.add_argument("--feed-a-loss", type=float, default=0.0)
    parser.add_argument("--feed-b-loss", type=float, default=0.0)
    parser.add_argument("--correlated-loss", type=float, default=0.0)
    parser.add_argument("--loss-placement", choices=("subscriber", "publisher"), default="subscriber",
                        help="Where app-level loss is injected; subscriber is the recommended live experiment path")
    parser.add_argument("--fault-seed", type=int, default=42)
    parser.add_argument("--recovery-policy", choices=("fcfs", "round-robin"), default="fcfs")
    parser.add_argument("--history", type=int, default=65536)
    parser.add_argument("--snapshot-interval", type=int, default=0)
    parser.add_argument("--snapshot-retention", type=int, default=8)
    parser.add_argument("--recovery-port", type=int, default=9090)
    parser.add_argument("--subscribers", type=int, default=1, help="Independent market-data subscriber processes")
    parser.add_argument("--results-dir", default="results")
    args = parser.parse_args()

    if args.duration <= 0 or args.subscribers < 1 or args.subscribers > 16 or args.history < 0 or args.snapshot_interval < 0 or args.snapshot_retention < 0:
        parser.error("duration and retention values must be non-negative; duration must be positive")
    if any(not 0.0 <= value <= 1.0 for value in
           (args.feed_a_loss, args.feed_b_loss, args.correlated_loss)):
        parser.error("loss rates must be in [0, 1]")

    root = Path(__file__).resolve().parents[2]
    build_dir = (root / args.build_dir).resolve()
    exchange = build_dir / "src" / "exchange"
    subscriber = build_dir / "src" / "reliable_subscriber"
    if not exchange.is_file() or not subscriber.is_file():
        parser.error("build the exchange and reliable_subscriber targets before running this script")

    config = RunConfig(args.duration, args.dual_feed, args.feed_a_loss, args.feed_b_loss,
                       args.correlated_loss, args.fault_seed, args.recovery_policy, args.history,
                       args.snapshot_interval, args.snapshot_retention, args.recovery_port,
                       args.interface, args.loss_placement, args.subscribers)
    exchange_command = [str(exchange), "--mode", "simulation", "--md-interface", args.interface,
                        "--history", str(args.history), "--snapshot-interval", str(args.snapshot_interval),
                        "--snapshot-retention", str(args.snapshot_retention), "--recovery-port",
                        str(args.recovery_port), "--recovery-policy", args.recovery_policy,
                        "--fault-seed", str(args.fault_seed)]
    subscriber_command = [str(subscriber), "--interface", args.interface, "--recovery-server", "127.0.0.1",
                          "--recovery-port", str(args.recovery_port), "--run-seconds", str(args.duration)]
    if args.dual_feed:
        exchange_command.append("--dual-feed")
        subscriber_command.append("--dual-feed")
    if args.loss_placement == "publisher":
        exchange_command.extend(["--feed-a-loss", str(args.feed_a_loss), "--feed-b-loss", str(args.feed_b_loss),
                                 "--correlated-loss", str(args.correlated_loss)])
    elif args.correlated_loss:
        subscriber_command.extend(["--loss-model", "correlated", "--corr-loss", str(args.correlated_loss),
                                   "--drop-seed", str(args.fault_seed)])
    elif args.feed_a_loss or args.feed_b_loss:
        subscriber_command.extend(["--loss-model", "independent", "--drop-a", str(args.feed_a_loss),
                                   "--drop-b", str(args.feed_b_loss), "--drop-seed", str(args.fault_seed)])

    exchange_lines: list[str] = []
    subscriber_lines: list[str] = []
    ready = threading.Event()
    started_at = datetime.now(timezone.utc)
    results_dir = (root / args.results_dir).resolve()
    results_dir.mkdir(parents=True, exist_ok=True)
    result_path = results_dir / f"live-reliability-{started_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    exchange_process = subprocess.Popen(exchange_command, cwd=root, text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    exchange_reader = threading.Thread(target=reader, args=(exchange_process, exchange_lines, ready), daemon=True)
    exchange_reader.start()
    if not ready.wait(timeout=8):
        stop(exchange_process)
        outcome = {"success": False, "error": "exchange recovery listener did not become ready",
                   "config": asdict(config), "exchange_metrics": parse_fields(exchange_lines, EXCHANGE_RESULT_RE),
                   "exchange_log_tail": exchange_lines}
        result_path.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"success": False, "result_path": str(result_path),
                          "error": outcome["error"]}, indent=2))
        return 1

    subscriber_processes: list[subprocess.Popen[str]] = []
    subscriber_lines_by_id: dict[str, list[str]] = {}
    subscriber_readers: list[threading.Thread] = []
    for index in range(args.subscribers):
        subscriber_id = f"subscriber-{index + 1}"
        command = [*subscriber_command, "--subscriber", subscriber_id]
        # Independent receive-side streams must use distinct seeds; correlated
        # remains correlated between feeds within each subscriber.
        if args.loss_placement == "subscriber":
            command.extend(["--drop-seed", str(args.fault_seed + index)])
        process = subprocess.Popen(command, cwd=root, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        lines: list[str] = []
        subscriber_lines_by_id[subscriber_id] = lines
        subscriber_processes.append(process)
        thread = threading.Thread(target=reader, args=(process, lines, threading.Event()), daemon=True)
        thread.start()
        subscriber_readers.append(thread)
    subscriber_exits: dict[str, int | None] = {}
    for index, process in enumerate(subscriber_processes):
        subscriber_id = f"subscriber-{index + 1}"
        try:
            subscriber_exits[subscriber_id] = process.wait(timeout=args.duration + 8)
        except subprocess.TimeoutExpired:
            stop(process)
            subscriber_exits[subscriber_id] = process.returncode
    stop(exchange_process)
    exchange_reader.join(timeout=1)
    for thread in subscriber_readers:
        thread.join(timeout=1)

    parsed_by_id = {subscriber_id: parse_fields(lines, RESULT_RE)
                    for subscriber_id, lines in subscriber_lines_by_id.items()}
    exchange_metrics = parse_fields(exchange_lines, EXCHANGE_RESULT_RE)
    # A parsed line alone is not proof of a valid experiment.  The subscriber
    # performs final status reconciliation and reports whether the exchange
    # sequence was fully accounted for; preserve that boundary in saved runs.
    success = all(
        subscriber_exits[subscriber_id] == 0
        and metrics is not None
        and metrics.get("run_status") == "completed"
        and metrics.get("permanently_lost", "0") == "0"
        and int(metrics.get("delivered", "0")) > 0
        for subscriber_id, metrics in parsed_by_id.items()
    )
    outcome = {
        "schema": "reliable-market-data-live-run/v1",
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "config": asdict(config),
        "subscriber_exit_codes": subscriber_exits,
        "subscriber_metrics": parsed_by_id,
        "exchange_metrics": exchange_metrics,
        "exchange_log_tail": exchange_lines,
        "subscriber_log_tails": subscriber_lines_by_id,
        "measurement_boundary": f"Live local processes with {args.loss_placement}-side application-layer loss injection; not physical-network packet loss.",
    }
    result_path.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": success, "result_path": str(result_path),
                      "subscriber_metrics": parsed_by_id}, indent=2))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
