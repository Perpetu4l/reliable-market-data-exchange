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


def parse_result(lines: list[str]) -> dict[str, str] | None:
    for line in reversed(lines):
        match = RESULT_RE.search(line)
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
    parser.add_argument("--fault-seed", type=int, default=42)
    parser.add_argument("--recovery-policy", choices=("fcfs", "round-robin"), default="fcfs")
    parser.add_argument("--history", type=int, default=65536)
    parser.add_argument("--snapshot-interval", type=int, default=0)
    parser.add_argument("--snapshot-retention", type=int, default=8)
    parser.add_argument("--recovery-port", type=int, default=9090)
    parser.add_argument("--results-dir", default="results")
    args = parser.parse_args()

    if args.duration <= 0 or args.history < 0 or args.snapshot_interval < 0 or args.snapshot_retention < 0:
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
                       args.interface)
    exchange_command = [str(exchange), "--mode", "simulation", "--md-interface", args.interface,
                        "--history", str(args.history), "--snapshot-interval", str(args.snapshot_interval),
                        "--snapshot-retention", str(args.snapshot_retention), "--recovery-port",
                        str(args.recovery_port), "--recovery-policy", args.recovery_policy,
                        "--feed-a-loss", str(args.feed_a_loss), "--feed-b-loss", str(args.feed_b_loss),
                        "--correlated-loss", str(args.correlated_loss), "--fault-seed", str(args.fault_seed)]
    subscriber_command = [str(subscriber), "--interface", args.interface, "--recovery-server", "127.0.0.1",
                          "--recovery-port", str(args.recovery_port), "--run-seconds", str(args.duration)]
    if args.dual_feed:
        exchange_command.append("--dual-feed")
        subscriber_command.append("--dual-feed")

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
                   "config": asdict(config), "exchange_log_tail": exchange_lines}
        result_path.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"success": False, "result_path": str(result_path),
                          "error": outcome["error"]}, indent=2))
        return 1

    subscriber_process = subprocess.Popen(subscriber_command, cwd=root, text=True,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    subscriber_reader = threading.Thread(target=reader, args=(subscriber_process, subscriber_lines, threading.Event()), daemon=True)
    subscriber_reader.start()
    try:
        subscriber_exit = subscriber_process.wait(timeout=args.duration + 8)
    except subprocess.TimeoutExpired:
        stop(subscriber_process)
        subscriber_exit = subscriber_process.returncode
    stop(exchange_process)
    exchange_reader.join(timeout=1)
    subscriber_reader.join(timeout=1)

    parsed = parse_result(subscriber_lines)
    delivered = int(parsed.get("delivered", "0")) if parsed else 0
    success = subscriber_exit == 0 and parsed is not None and delivered > 0
    outcome = {
        "schema": "reliable-market-data-live-run/v1",
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "config": asdict(config),
        "subscriber_exit_code": subscriber_exit,
        "subscriber_metrics": parsed,
        "exchange_log_tail": exchange_lines,
        "subscriber_log_tail": subscriber_lines,
        "measurement_boundary": "Live local processes with application-layer loss injection; not physical-network packet loss.",
    }
    result_path.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": success, "result_path": str(result_path),
                      "subscriber_metrics": parsed}, indent=2))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
