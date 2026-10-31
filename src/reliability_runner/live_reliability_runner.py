#!/usr/bin/env python3
"""Run one provenance-labelled live reliability experiment.

Orchestrates the compiled C++ exchange (--mode client or simulation), up to 32
independent reliable_subscriber processes, and workload_client processes.
Writes the full run artifact tree under results/live/<run_id>/.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RESULT_RE = re.compile(r"RELIABLE_SUBSCRIBER_RESULT\s+(.*)$")
EXCHANGE_RESULT_RE = re.compile(r"EXCHANGE_RESULT\s+(.*)$")
SUBSCRIBER_READY_RE = re.compile(r"^SUBSCRIBER_READY\s+id=(\S+)\s+feeds=(\S+)")
READY_TEXT = "Recovery TCP server listening"
OE_READY_TEXT = "OrderEntry server started"


@dataclass
class SubscriberSpec:
    id: str = "sub-1"
    feeds: str = "ab"  # "a" or "ab"
    model: str = "none"  # "none", "independent", "correlated"
    drop_a: float = 0.0
    drop_b: float = 0.0
    corr_loss: float = 0.0
    dup: float = 0.0
    reorder: float = 0.0
    reorder_depth: int = 1
    delay_ms: int = 0
    seed: int = 42
    role: str = "default"
    concurrency: int = 1
    retries: int = 5
    grace_ms: int = 2
    no_recovery: bool = False


@dataclass
class RunConfig:
    run_id: str
    exchange_mode: str
    duration_seconds: float
    clients: int
    rate_per_client: float
    workload_seed: int
    symbol: str
    stop_when_idle_ms: int
    until_seq: int
    dual_feed: bool
    feed_a_loss: float
    feed_b_loss: float
    correlated_loss: float
    fault_seed: int
    recovery_policy: str
    recovery_workers: int
    service_delay_us_per_request: int
    service_delay_us_per_event: int
    history_events: int
    snapshot_interval: int
    snapshot_retention: int
    snapshot_on_demand: bool
    tamper_snapshot: str
    heartbeat_ms: int
    oe_port: int
    recovery_port: int
    feed_a_port: int
    feed_b_port: int
    multicast_interface: str
    transport: str
    loss_injection: str
    git_commit: str
    subscribers: list[SubscriberSpec] = field(default_factory=list)


def detect_transport(interface_ip: str) -> str:
    if interface_ip in ("127.0.0.1", "localhost") or interface_ip.startswith("127."):
        return "udp_loopback"
    return "udp_lan"


def detect_git_commit(root: Path) -> str:
    head = root / ".git" / "HEAD"
    try:
        if head.is_file():
            content = head.read_text(encoding="utf-8").strip()
            if content.startswith("ref: "):
                ref_path = root / ".git" / content[5:]
                if ref_path.is_file():
                    return ref_path.read_text(encoding="utf-8").strip()[:12]
            return content[:12]
    except OSError:
        pass
    return "unknown"


def pick_free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])


def pick_free_udp_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", preferred))
            return preferred
        except OSError:
            s.bind(("0.0.0.0", 0))
            return int(s.getsockname()[1])


def parse_subscriber_spec(raw: str, index: int) -> SubscriberSpec:
    spec = SubscriberSpec(id=f"S{index + 1}")
    for token in raw.split(","):
        token = token.strip()
        if not token or "=" not in token:
            continue
        k, v = token.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k == "id":
            spec.id = v
        elif k == "feeds":
            spec.feeds = v
        elif k in ("model", "loss_model"):
            spec.model = v
        elif k == "drop_a":
            spec.drop_a = float(v)
        elif k == "drop_b":
            spec.drop_b = float(v)
        elif k in ("corr_loss", "correlated_loss"):
            spec.corr_loss = float(v)
        elif k == "dup":
            spec.dup = float(v)
        elif k == "reorder":
            spec.reorder = float(v)
        elif k == "reorder_depth":
            spec.reorder_depth = int(v)
        elif k == "delay_ms":
            spec.delay_ms = int(v)
        elif k == "seed":
            spec.seed = int(v)
        elif k == "role":
            spec.role = v
        elif k == "concurrency":
            spec.concurrency = int(v)
        elif k == "retries":
            spec.retries = int(v)
        elif k == "grace_ms":
            spec.grace_ms = int(v)
        elif k == "no_recovery":
            spec.no_recovery = v.lower() in ("1", "true", "yes", "on")
    return spec


def stop_process(process: subprocess.Popen[str]) -> None:
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


def parse_sub_map(encoded: str) -> dict[str, int]:
    result: dict[str, int] = {}
    if not encoded or encoded == "none":
        return result
    for item in encoded.split(","):
        if ":" in item:
            k, v = item.split(":", 1)
            try:
                result[k] = int(v)
            except ValueError:
                pass
    return result


def run_experiment(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    build_dir = (root / args.build_dir).resolve()
    exchange_bin = build_dir / "src" / "exchange"
    subscriber_bin = build_dir / "src" / "reliable_subscriber"
    workload_bin = build_dir / "src" / "workload_client"
    if not exchange_bin.is_file() or not subscriber_bin.is_file() or not workload_bin.is_file():
        raise RuntimeError("Build exchange, reliable_subscriber, and workload_client targets first.")

    started_at = datetime.now(timezone.utc)
    run_id = args.run_id or f"run-{started_at.strftime('%Y%m%dT%H%M%S%fZ')}"

    if args.config:
        cfg_data = json.loads(Path(args.config).read_text(encoding="utf-8"))
        for k, v in cfg_data.items():
            if k == "subscribers":
                continue
            if hasattr(args, k):
                setattr(args, k, v)
        sub_specs = [SubscriberSpec(**s) for s in cfg_data.get("subscribers", [])]
    elif args.subscriber:
        sub_specs = [parse_subscriber_spec(s, idx) for idx, s in enumerate(args.subscriber)]
    else:
        sub_specs = []
        for idx in range(args.subscribers):
            feeds = "ab" if args.dual_feed else "a"
            if args.loss_placement == "subscriber":
                if args.correlated_loss > 0.0:
                    model = "correlated"
                elif args.feed_a_loss > 0.0 or args.feed_b_loss > 0.0:
                    model = "independent"
                else:
                    model = "none"
                drop_a = args.feed_a_loss
                drop_b = args.feed_b_loss
                corr = args.correlated_loss
            else:
                model = "none"
                drop_a = 0.0
                drop_b = 0.0
                corr = 0.0
            sub_specs.append(SubscriberSpec(
                id=f"subscriber-{idx + 1}",
                feeds=feeds,
                model=model,
                drop_a=drop_a,
                drop_b=drop_b,
                corr_loss=corr,
                dup=args.dup,
                reorder=args.reorder,
                reorder_depth=args.reorder_depth,
                delay_ms=args.delay_ms,
                seed=args.fault_seed + idx * 10,
                role="default",
                concurrency=args.recovery_concurrency,
                retries=args.recovery_retries,
                grace_ms=args.gap_grace_ms,
                no_recovery=args.no_recovery,
            ))

    if not (1 <= len(sub_specs) <= 32):
        raise ValueError("Subscriber count must be in [1, 32]")

    any_dual = args.dual_feed or any(s.feeds == "ab" for s in sub_specs)
    transport = detect_transport(args.interface)
    if args.loss_placement == "publisher" and (args.feed_a_loss > 0 or args.feed_b_loss > 0 or args.correlated_loss > 0):
        loss_injection = "app_level_publisher_side"
    elif any(s.model != "none" and (s.drop_a > 0 or s.drop_b > 0 or s.corr_loss > 0) or s.dup > 0 or s.reorder > 0 or s.delay_ms > 0 for s in sub_specs):
        loss_injection = "app_level_receive_side"
    else:
        loss_injection = "none"

    git_commit = detect_git_commit(root)
    oe_port = pick_free_port(args.oe_port)
    recovery_port = pick_free_port(args.recovery_port if args.recovery_port != oe_port else args.recovery_port + 1)
    feed_a_port = pick_free_udp_port(args.feed_a_port)
    feed_b_port = pick_free_udp_port(args.feed_b_port if args.feed_b_port != feed_a_port else args.feed_b_port + 1)

    results_root = (root / args.results_dir).resolve()
    live_run_dir = results_root / "live" / run_id
    live_run_dir.mkdir(parents=True, exist_ok=True)

    config = RunConfig(
        run_id=run_id,
        exchange_mode=args.exchange_mode,
        duration_seconds=args.duration,
        clients=args.clients,
        rate_per_client=args.rate,
        workload_seed=args.workload_seed,
        symbol=args.symbol,
        stop_when_idle_ms=args.stop_when_idle_ms,
        until_seq=args.until_seq,
        dual_feed=any_dual,
        feed_a_loss=args.feed_a_loss if args.loss_placement == "publisher" else 0.0,
        feed_b_loss=args.feed_b_loss if args.loss_placement == "publisher" else 0.0,
        correlated_loss=args.correlated_loss if args.loss_placement == "publisher" else 0.0,
        fault_seed=args.fault_seed,
        recovery_policy=args.recovery_policy,
        recovery_workers=args.recovery_workers,
        service_delay_us_per_request=args.service_delay_us_per_request,
        service_delay_us_per_event=args.service_delay_us_per_event,
        history_events=args.history,
        snapshot_interval=args.snapshot_interval,
        snapshot_retention=args.snapshot_retention,
        snapshot_on_demand=not args.no_snapshot_on_demand,
        tamper_snapshot=args.tamper_snapshot,
        heartbeat_ms=args.heartbeat_ms,
        oe_port=oe_port,
        recovery_port=recovery_port,
        feed_a_port=feed_a_port,
        feed_b_port=feed_b_port,
        multicast_interface=args.interface,
        transport=transport,
        loss_injection=loss_injection,
        git_commit=git_commit,
        subscribers=sub_specs,
    )

    (live_run_dir / "config.json").write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")

    exchange_cmd = [
        str(exchange_bin),
        "--mode", args.exchange_mode,
        "--oe-port", str(oe_port),
        "--md-interface", args.interface,
        "--md-port", str(feed_a_port),
        "--feed-b-port", str(feed_b_port),
        "--heartbeat-ms", str(args.heartbeat_ms),
        "--history", str(args.history),
        "--snapshot-interval", str(args.snapshot_interval),
        "--snapshot-retention", str(args.snapshot_retention),
        "--snapshot-on-demand", "1" if not args.no_snapshot_on_demand else "0",
        "--tamper-snapshot", args.tamper_snapshot,
        "--recovery-port", str(recovery_port),
        "--recovery-policy", args.recovery_policy,
        "--recovery-workers", str(args.recovery_workers),
        "--service-delay-us-per-request", str(args.service_delay_us_per_request),
        "--service-delay-us-per-event", str(args.service_delay_us_per_event),
        "--results-dir", str(live_run_dir),
        "--run-id", run_id,
        "--fault-seed", str(args.fault_seed),
    ]
    if any_dual:
        exchange_cmd.append("--dual-feed")
    if args.loss_placement == "publisher":
        exchange_cmd.extend([
            "--feed-a-loss", str(args.feed_a_loss),
            "--feed-b-loss", str(args.feed_b_loss),
            "--correlated-loss", str(args.correlated_loss),
        ])

    exchange_lines: list[str] = []
    recovery_ready = threading.Event()
    oe_ready = threading.Event()

    def ex_reader(proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            exchange_lines.append(line)
            if READY_TEXT in line:
                recovery_ready.set()
            if OE_READY_TEXT in line or "Exchange started in SIMULATION mode" in line:
                oe_ready.set()

    exchange_proc = subprocess.Popen(
        exchange_cmd, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    ex_thread = threading.Thread(target=ex_reader, args=(exchange_proc,), daemon=True)
    ex_thread.start()

    if not (recovery_ready.wait(timeout=8) and oe_ready.wait(timeout=8)):
        stop_process(exchange_proc)
        ex_thread.join(timeout=2)
        (live_run_dir / "exchange.log").write_text("\n".join(exchange_lines) + "\n", encoding="utf-8")
        outcome = {
            "schema": "reliable-market-data-live-run/v2",
            "run_id": run_id,
            "success": False,
            "error": "exchange listener did not become ready",
            "config": asdict(config),
            "exchange_metrics": parse_fields(exchange_lines, EXCHANGE_RESULT_RE),
            "exchange_log_tail": exchange_lines[-300:],
            "result_path": str(live_run_dir / "run.json"),
            "run_dir": str(live_run_dir),
        }
        (live_run_dir / "run.json").write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
        legacy_path = results_root / f"live-reliability-{started_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        legacy_path.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
        return 1, outcome

    sub_procs: dict[str, subprocess.Popen[str]] = {}
    sub_lines: dict[str, list[str]] = {}
    sub_ready_events: dict[str, threading.Event] = {}
    sub_threads: list[threading.Thread] = []

    for spec in sub_specs:
        sub_csv = live_run_dir / f"subscriber-{spec.id}.csv"
        lat_csv = live_run_dir / f"subscriber-{spec.id}-latency.csv"
        cmd = [
            str(subscriber_bin),
            "--interface", args.interface,
            "--feed-a-port", str(feed_a_port),
            "--feed-b-port", str(feed_b_port),
            "--recovery-server", "127.0.0.1",
            "--recovery-port", str(recovery_port),
            "--subscriber", spec.id,
            "--run-id", run_id,
            "--gap-grace-ms", str(spec.grace_ms),
            "--recovery-concurrency", str(spec.concurrency),
            "--recovery-retries", str(spec.retries),
            "--loss-model", spec.model,
            "--drop-a", str(spec.drop_a),
            "--drop-b", str(spec.drop_b),
            "--corr-loss", str(spec.corr_loss),
            "--dup", str(spec.dup),
            "--reorder", str(spec.reorder),
            "--reorder-depth", str(spec.reorder_depth),
            "--delay-ms", str(spec.delay_ms),
            "--drop-seed", str(spec.seed),
            "--csv", str(sub_csv),
            "--latency-csv", str(lat_csv),
        ]
        if spec.feeds == "ab":
            cmd.append("--dual-feed")
        if spec.no_recovery:
            cmd.append("--no-recovery")
        if args.until_seq > 0:
            cmd.extend(["--until-seq", str(args.until_seq)])
        elif args.exchange_mode == "client" and args.stop_when_idle_ms > 0:
            cmd.extend(["--stop-when-idle-ms", str(args.stop_when_idle_ms),
                        "--run-seconds", str(int(args.duration) + 15)])
        else:
            cmd.extend(["--run-seconds", str(max(1, int(args.duration)))])

        proc = subprocess.Popen(cmd, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        lines: list[str] = []
        r_ev = threading.Event()
        sub_procs[spec.id] = proc
        sub_lines[spec.id] = lines
        sub_ready_events[spec.id] = r_ev

        def sub_reader(p: subprocess.Popen[str], target: list[str], ev: threading.Event) -> None:
            assert p.stdout is not None
            for raw in p.stdout:
                ln = raw.rstrip("\n")
                target.append(ln)
                if SUBSCRIBER_READY_RE.match(ln):
                    ev.set()

        t = threading.Thread(target=sub_reader, args=(proc, lines, r_ev), daemon=True)
        t.start()
        sub_threads.append(t)

    # Wait for every subscriber to report SUBSCRIBER_READY before starting workload.
    all_subs_ready = all(ev.wait(timeout=8) for ev in sub_ready_events.values())

    workload_procs: list[subprocess.Popen[str]] = []
    workload_outputs: list[str] = []
    if all_subs_ready and args.exchange_mode == "client":
        for cid in range(1, args.clients + 1):
            side = "buy" if cid % 2 == 1 else "sell"
            w_cmd = [
                str(workload_bin),
                "--server", "127.0.0.1",
                "--port", str(oe_port),
                "--client-id", str(cid),
                "--duration", str(args.duration),
                "--rate", str(args.rate),
                "--symbol", args.symbol,
                "--side", side,
                "--seed", str(args.workload_seed + cid - 1),
            ]
            workload_procs.append(
                subprocess.Popen(w_cmd, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            )
        for wp in workload_procs:
            try:
                out, _ = wp.communicate(timeout=args.duration + 10)
                workload_outputs.append(out or "")
            except subprocess.TimeoutExpired:
                stop_process(wp)

    sub_exits: dict[str, int | None] = {}
    wait_timeout = args.duration + 20
    for sid, proc in sub_procs.items():
        try:
            sub_exits[sid] = proc.wait(timeout=wait_timeout)
        except subprocess.TimeoutExpired:
            stop_process(proc)
            sub_exits[sid] = proc.returncode

    for t in sub_threads:
        t.join(timeout=2)

    stop_process(exchange_proc)
    ex_thread.join(timeout=2)

    (live_run_dir / "exchange.log").write_text("\n".join(exchange_lines) + "\n", encoding="utf-8")
    exchange_metrics = parse_fields(exchange_lines, EXCHANGE_RESULT_RE) or {}
    (live_run_dir / "exchange-result.json").write_text(
        json.dumps(exchange_metrics, indent=2) + "\n", encoding="utf-8"
    )

    parsed_by_id = {sid: parse_fields(lines, RESULT_RE) for sid, lines in sub_lines.items()}

    wait_p50_map = parse_sub_map(exchange_metrics.get("per_subscriber_wait_p50_ns", "none"))
    wait_p99_map = parse_sub_map(exchange_metrics.get("per_subscriber_wait_p99_ns", "none"))
    wait_max_map = parse_sub_map(exchange_metrics.get("per_subscriber_wait_max_ns", "none"))
    served_map = parse_sub_map(exchange_metrics.get("recovery_service_distribution", "none"))

    # Write combined run-summary.csv
    summary_rows: list[dict[str, Any]] = []
    for spec in sub_specs:
        sub_csv = live_run_dir / f"subscriber-{spec.id}.csv"
        row: dict[str, Any] = {}
        if sub_csv.is_file():
            with sub_csv.open(newline="", encoding="utf-8") as f:
                reader_csv = list(csv.DictReader(f))
                if reader_csv:
                    row = dict(reader_csv[0])
        elif parsed_by_id.get(spec.id):
            row = dict(parsed_by_id[spec.id] or {})
        row.update({
            "run_id": run_id,
            "subscriber_id": spec.id,
            "role": spec.role,
            "git_commit": git_commit,
            "transport": transport,
            "loss_injection": loss_injection,
            "recovery_policy": args.recovery_policy,
            "recovery_workers": args.recovery_workers,
            "service_delay_us_per_request": args.service_delay_us_per_request,
            "service_delay_us_per_event": args.service_delay_us_per_event,
            "history_events": args.history,
            "snapshot_interval": args.snapshot_interval,
            "snapshot_retention": args.snapshot_retention,
            "snapshot_on_demand": int(not args.no_snapshot_on_demand),
            "tamper_snapshot": args.tamper_snapshot,
            "server_wait_p50_ns": wait_p50_map.get(spec.id, 0),
            "server_wait_p99_ns": wait_p99_map.get(spec.id, 0),
            "server_wait_max_ns": wait_max_map.get(spec.id, 0),
            "server_requests_served": served_map.get(spec.id, 0),
            "exchange_snapshot_bytes_sent": exchange_metrics.get("snapshot_bytes_sent", "0"),
            "exchange_fairness_jain_ppm": exchange_metrics.get("recovery_fairness_jain_ppm", "0"),
            "exchange_fairness_saturated_jain_ppm": exchange_metrics.get("recovery_fairness_saturated_jain_ppm", "0"),
            "exit_code": sub_exits.get(spec.id, -1),
        })
        summary_rows.append(row)

    summary_csv_path = live_run_dir / "run-summary.csv"
    if summary_rows:
        fieldnames = list(summary_rows[0].keys())
        for r in summary_rows[1:]:
            for k in r.keys():
                if k not in fieldnames:
                    fieldnames.append(k)
        with summary_csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(summary_rows)

    all_completed = all(
        sub_exits.get(sid) == 0
        and metrics is not None
        and metrics.get("run_status") == "completed"
        and metrics.get("accounting_valid") == "1"
        and metrics.get("permanently_lost") == "0"
        and metrics.get("book_matches_exchange") == "1"
        and int(metrics.get("events_expected", "0")) > 0
        for sid, metrics in parsed_by_id.items()
    )
    expected_failure_observed = False
    if args.expect_failure:
        expected_failure_observed = all(
            sub_exits.get(sid, 0) != 0
            and metrics is not None
            and metrics.get("run_status", "").startswith("failed_")
            and metrics.get("accounting_valid") == "1"
            for sid, metrics in parsed_by_id.items()
        )

    success = expected_failure_observed if args.expect_failure else all_completed
    outcome: dict[str, Any] = {
        "schema": "reliable-market-data-live-run/v2",
        "run_id": run_id,
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "all_completed": all_completed,
        "expected_failure_observed": expected_failure_observed,
        "config": asdict(config),
        "subscriber_exit_codes": sub_exits,
        "subscriber_metrics": parsed_by_id,
        "exchange_metrics": exchange_metrics,
        "exchange_log_tail": exchange_lines[-300:],
        "subscriber_log_tails": {sid: lns[-100:] for sid, lns in sub_lines.items()},
        "run_dir": str(live_run_dir),
        "summary_csv": str(summary_csv_path),
        "result_path": str(live_run_dir / "run.json"),
        "measurement_boundary": (
            f"Transport={transport}, loss_injection={loss_injection}, "
            f"service_delay_us_per_req={args.service_delay_us_per_request}, "
            f"service_delay_us_per_event={args.service_delay_us_per_event}. "
            "Application-level loss is not physical-network loss."
        ),
    }
    (live_run_dir / "run.json").write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    legacy_path = results_root / f"live-reliability-{started_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    legacy_path.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    return (0 if success else 1), outcome


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", default="build-local", help="CMake build directory")
    parser.add_argument("--run-id", default="", help="Explicit run identifier")
    parser.add_argument("--config", default="", help="JSON config file path")
    parser.add_argument("--exchange-mode", choices=("client", "simulation"), default="client")
    parser.add_argument("--duration", type=float, default=4.0, help="Workload or simulation duration in seconds")
    parser.add_argument("--clients", type=int, default=2, help="Number of workload_client processes in client mode")
    parser.add_argument("--rate", type=float, default=800.0, help="Orders/s per workload_client")
    parser.add_argument("--workload-seed", type=int, default=11)
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--stop-when-idle-ms", type=int, default=600)
    parser.add_argument("--until-seq", type=int, default=0)
    parser.add_argument("--interface", default="127.0.0.1", help="Multicast interface IPv4")
    parser.add_argument("--dual-feed", action="store_true", help="Use Feed A and Feed B")
    parser.add_argument("--feed-a-loss", type=float, default=0.0)
    parser.add_argument("--feed-b-loss", type=float, default=0.0)
    parser.add_argument("--correlated-loss", type=float, default=0.0)
    parser.add_argument("--dup", type=float, default=0.0)
    parser.add_argument("--reorder", type=float, default=0.0)
    parser.add_argument("--reorder-depth", type=int, default=1)
    parser.add_argument("--delay-ms", type=int, default=0)
    parser.add_argument("--loss-placement", choices=("subscriber", "publisher"), default="subscriber")
    parser.add_argument("--fault-seed", type=int, default=42)
    parser.add_argument("--gap-grace-ms", type=int, default=2)
    parser.add_argument("--recovery-concurrency", type=int, default=1)
    parser.add_argument("--recovery-retries", type=int, default=5)
    parser.add_argument("--no-recovery", action="store_true")
    parser.add_argument("--expect-failure", action="store_true")
    parser.add_argument("--recovery-policy", choices=("fcfs", "round-robin"), default="fcfs")
    parser.add_argument("--recovery-workers", type=int, default=1)
    parser.add_argument("--service-delay-us-per-request", type=int, default=0)
    parser.add_argument("--service-delay-us-per-event", type=int, default=0)
    parser.add_argument("--history", type=int, default=65536)
    parser.add_argument("--snapshot-interval", type=int, default=0)
    parser.add_argument("--snapshot-retention", type=int, default=8)
    parser.add_argument("--no-snapshot-on-demand", action="store_true")
    parser.add_argument("--tamper-snapshot", default="none")
    parser.add_argument("--heartbeat-ms", type=int, default=500)
    parser.add_argument("--oe-port", type=int, default=18080)
    parser.add_argument("--recovery-port", type=int, default=19090)
    parser.add_argument("--feed-a-port", type=int, default=19999)
    parser.add_argument("--feed-b-port", type=int, default=20000)
    parser.add_argument("--subscribers", type=int, default=1)
    parser.add_argument("--subscriber", action="append", default=[])
    parser.add_argument("--results-dir", default="results")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        code, outcome = run_experiment(args)
        print(json.dumps({
            "success": outcome["success"],
            "run_id": outcome["run_id"],
            "run_dir": outcome["run_dir"],
            "result_path": outcome["result_path"],
            "expected_failure_observed": outcome.get("expected_failure_observed", False),
            "subscriber_metrics": outcome["subscriber_metrics"],
            "exchange_metrics": outcome["exchange_metrics"],
        }, indent=2))
        return code
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
