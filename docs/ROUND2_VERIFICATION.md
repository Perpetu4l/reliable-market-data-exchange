# Round 2 verification ledger

This ledger distinguishes findings reproduced by source inspection in the
restricted development environment from behaviours that require real multicast
and TCP socket execution on a normal host. It must not be read as a record of
successful LAN testing.

## Baseline audit and plan

| Finding | Baseline status | Evidence in the pre-Round-2 tree | Planned phase |
| --- | --- | --- | --- |
| F0 portability | Partly present | Several requested direct includes were absent or transitive. | R0 |
| F1 shutdown/cut sequence | Present | Statistics thread slept for its full interval; STATUS preceded receiver stop. | R1 |
| F2 tail race | Not executable here | Socket binding is forbidden in this sandbox; source had no stable STATUS cut. | R1 |
| F3 low-rate runner | Present | `live_reliability_runner.py` starts `exchange --mode simulation`. | R4 |
| F4 request-level latency | Present | Subscriber starts timing after gap grace and records only request RTT. | R2 |
| F5 fairness contention | Present | One recovery worker and one request worker per subscriber. | R3/R4 |
| F6 checkpoint availability | Present | Recovery only requests a periodic checkpoint at-or-after the gap. | R3 |
| F7 book verification | Missing | STATUS contains sequence/history boundary only. | R2 |
| F8 counters/CSV/stats | Partial | Final line has a subset; no live CSV or periodic stats. | R2 |
| F9 negative-control cut | Present | `--no-recovery` previously skipped STATUS. | R1 |
| F10 hot-path logging | Present | Every delivery wrote `DELIVERED` to stdout. | R1 |
| F11 out-of-order replay | Present | Later recovered events were discarded instead of buffered. | R1 |
| F12 retries | Missing | Failed recovery request was abandoned. | R1 |
| F13 GUI controls | Partial | Dashboard starts a basic local runner; Exchange Control has no subscriber table. | R6 |
| F14/R7 experiments/docs | Partial | Several planned impairments, presets, and live docs are missing. | R2--R7 |

## Executed checks in this environment

| Date | Command/result | Outcome |
| --- | --- | --- |
| 2026-10-27 | `cmake --build /private/tmp/reliable-phase1 -j2` | Passed before Round 2 changes. |
| 2026-10-27 | `python3 tests/live_reliability_e2e.py --build-dir /private/tmp/reliable-phase1` | Explicit SKIP: sandbox forbids socket binding. |
| 2026-10-27 | R1 target build (`MDFeed`, `reliable_subscriber`, `exchange`, `workload_client`) | Passed after the initial R1 code changes. |

## Remaining phase order

1. R0/R1: direct portability includes, interruptible receiver shutdown,
   stable final cut, exact sequence accounting, out-of-order replay, retries,
   configurable recovery concurrency, and quiet default output.
2. R2: canonical book digest/status verification, subscriber counters,
   latency classes, periodic stats, CSV outputs, and deterministic impairments.
3. R3: on-demand snapshots, tamper experiment, scheduler worker/service-model
   controls and exchange structured status.
4. R4/R5: client-mode runner, repeatable configurations/presets/aggregation,
   and tests that run where sockets are permitted.
5. R6/R7: control-room GUI wiring and documentation updates based only on
   executed checks.
