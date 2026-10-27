#!/usr/bin/env python3
"""Exercise the live UDP/TCP reliability path on a host that permits sockets.

This is deliberately an integration *script*, not a unit test with fabricated
traffic.  It calls the same multi-process runner used by the GUI and accepts a
SKIP only when the operating system explicitly refuses socket binding.  Other
startup failures are failures that need investigation.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def socket_policy_blocked(output: str) -> bool:
    text = output.lower()
    return ("operation not permitted" in text or "permission denied" in text) and "bind" in text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", default="build-local")
    parser.add_argument("--recovery-port", type=int, default=19090)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    runner = root / "src" / "reliability_runner" / "live_reliability_runner.py"
    temporary_results = Path(tempfile.mkdtemp(prefix="reliable-e2e-"))
    command = [
        sys.executable, str(runner), "--build-dir", args.build_dir,
        "--duration", "4", "--dual-feed", "--subscribers", "1",
        "--history", "65536", "--snapshot-interval", "0",
        "--recovery-port", str(args.recovery_port),
        "--results-dir", str(temporary_results),
    ]
    try:
        completed = subprocess.run(command, cwd=root, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   timeout=30, check=False)
        output = completed.stdout
        print(output, end="")
        files = list(temporary_results.glob("live-reliability-*.json"))
        result_output = output
        if files:
            # The runner intentionally prints a compact summary. Its saved
            # process tail carries the operating-system reason for a failed
            # listener, which is what determines a legitimate skip.
            try:
                failure_result = json.loads(files[0].read_text(encoding="utf-8"))
                result_output += "\n" + "\n".join(failure_result.get("exchange_log_tail", []))
            except json.JSONDecodeError:
                pass
        if completed.returncode == 0:
            if len(files) != 1:
                print("FAIL: runner did not write exactly one result file", file=sys.stderr)
                return 1
            result = json.loads(files[0].read_text(encoding="utf-8"))
            metrics = result.get("subscriber_metrics", {}).get("subscriber-1", {})
            if not result.get("success") or metrics.get("run_status") != "completed":
                print("FAIL: live runner returned success without a completed subscriber", file=sys.stderr)
                return 1
            print("PASS: live UDP/TCP baseline completed with a reconciled subscriber result")
            return 0
        if socket_policy_blocked(result_output):
            print("SKIP: this environment forbids socket binding; run on a normal host for live proof")
            return 77
        print("FAIL: live reliability baseline did not complete", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("FAIL: live reliability baseline timed out", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(temporary_results, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
