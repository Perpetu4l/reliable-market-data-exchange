# Reliable Market-Data Distribution for a Networked Electronic Exchange

**Computer Networks (CSC-303) — Group 11**

A C++23 electronic exchange and reliable market-data distribution system featuring a price-time-priority limit order book, framed TCP order entry, dual-feed UDP multicast market data (`Feed A` + `Feed B`), bounded publisher history, a framed TCP recovery service with **FCFS** and **Round-Robin** scheduling, on-demand and periodic composite SHA-256 checkpoints, end-to-end order-book digest verification, automated multi-process experiments, and a PyQt6/pyqtgraph Control-Room GUI.

The exchange remains centralised: there is one authoritative matching engine and order book per instrument (`AAPL`, `GOOGL`, `AMZN`, `NFLX`, `META`). The reliability subsystem guarantees ordered, gap-free market-data state reconstruction at subscribers over lossy UDP multicast without distributed consensus.

---

## Quick Links to Documentation

- **[`docs/VIVA_DEMO_SCRIPT.md`](docs/VIVA_DEMO_SCRIPT.md)** — **Start here for the Viva Demo:** complete 10-minute live demo script, verification matrix, and a button-by-button guide to every GUI tab, panel, table, and chart.
- **[`docs/METRICS.md`](docs/METRICS.md)** — Formal definitions of sequence-accounting invariants, per-missing-event latency classes (`other_feed`, `tcp_recovery`, `snapshot`, `tcp_request_rtt`), nearest-rank percentiles, redundancy effectiveness, and saturated-window Jain fairness.
- **[`docs/RECOVERY_PROTOCOL.md`](docs/RECOVERY_PROTOCOL.md)** — Wire format specification for Recovery TCP Protocol v2 (`RECOVERY_REQUEST`, `RECOVERY_RESPONSE`, `RECOVERY_CHUNK`, `RECOVERY_COMPLETE`, `SNAPSHOT_REQUEST`, `SNAPSHOT_RESPONSE`, `STATUS_REQUEST`, `STATUS_RESPONSE` with SHA-256 book digest, and `BYE`).
- **[`docs/LIVE_TESTING.md`](docs/LIVE_TESTING.md)** — Single-host loopback testing, two-laptop LAN (`udp_lan`) setup, and OS-level `tc netem` / `pfctl` impairment guide.
- **[`docs/DESIGN_DOC_ERRATA.md`](docs/DESIGN_DOC_ERRATA.md)** — Six documented corrections to the October 2026 v0.1 Design Document.
- **[`docs/ROUND2_VERIFICATION.md`](docs/ROUND2_VERIFICATION.md)** — Before/after verification ledger for all Round 2 audit findings (`F0–F15`).
- **[`docs/IMPLEMENTATION_CHECKLIST.md`](docs/IMPLEMENTATION_CHECKLIST.md)** — Requirement-to-code traceability checklist.

---

## Architecture & Live Reliability Pipeline

```text
  workload_client (Buy)      workload_client (Sell)      order_client / GUI
           \                          |                         /
            +-------------------------+------------------------+
                                      | Framed TCP Order Entry (port 8080)
                                      v
                 +-----------------------------------------+
                 |       exchange (Matching Engine)        |
                 |  Authoritative Books + state_mutex_     |
                 |  RecoveryHistory + SnapshotManager      |
                 +----+------------------+------------+----+
                      |                  |            ^
       UDP Multicast  |                  |            | Framed TCP Recovery v2
       Feed A (:9999) |   Feed B (:10000)|            | (port 9090, FCFS / RR,
                      v                  v            |  Worker Pool, Snapshots)
                 +------------------------------------+----+
                 |         reliable_subscriber (1..32)     |
                 |  Receive-Side ImpairmentStage (A & B)   |
                 |  First-Copy-Wins DualFeedSubscriber     |
                 |  Gap Grace Timer -> Worker Pool (K)     |
                 |  Local BookBuilder + SHA-256 Digest     |
                 +-----------------------------------------+
```

### Key Capabilities

1. **Dual-Feed Redundancy (`--dual-feed`)**: Identical sequence-numbered market-data datagrams are published on Feed A (`239.1.1.1:9999`) and Feed B (`239.1.1.2:10000`). `DualFeedSubscriber` suppresses duplicates on a first-copy-wins basis and tracks exact per-sequence arrival bitsets (`potential_single_feed_gaps`, `gaps_avoided_by_second_feed`, `redundancy_effectiveness`).
2. **Per-Missing-Event Latency by Class**: Every missing sequence is timestamped at gap detection ($t_{\text{gap}}$) and classified on delivery ($t_{\text{deliv}} - t_{\text{gap}}$) into `other_feed`, `tcp_recovery`, or `snapshot`, alongside a separate `tcp_request_rtt` timer.
3. **Bounded History & Chunked TCP Replay**: `exchange` retains the last `--history N` datagrams. Gaps within history are replayed over TCP (`RECOVERY_RESPONSE` or multi-frame `RECOVERY_CHUNK` + `RECOVERY_COMPLETE`).
4. **On-Demand & Periodic Composite SHA-256 Snapshots**: When a gap falls behind `--history` (`TOO_OLD`), the subscriber requests a composite snapshot (`SNAPSHOT_REQUEST`). With `--snapshot-on-demand` (default on), the exchange creates a fresh SHA-256 hash-chained checkpoint on demand if no newer periodic checkpoint exists. The subscriber verifies `SnapshotHasher::verify()` before resynchronising its local `BookBuilder`.
5. **Cryptographic End-to-End Order Book Verification**: At run end, the subscriber stops its UDP receivers, polls `STATUS_REQUEST` until the exchange's `(latest_sequence, history_minimum_sequence, latest_book_digest)` converges, drains any remaining tail gaps, and verifies that its local `BookBuilder::digest()` matches `exchange_book_digest` (`book_matches_exchange=1`).
6. **FCFS vs Round-Robin Scheduler under Contention**: `RecoveryScheduler` supports `fcfs` and `round-robin` policies across configurable worker threads (`--recovery-workers N`) and modelled service costs (`--service-delay-us-per-request`, `--service-delay-us-per-event`), while subscribers support concurrent recovery workers (`--recovery-concurrency K`).

---

## Build Instructions

### Prerequisites
- CMake `>= 3.24`
- C++23 compiler (`clang++` 16+ or `g++` 13+)
- Boost (`>= 1.82`) and `spdlog`
- Python `3.11+` with `PyQt6`, `pyqtgraph`, `matplotlib`, `pybind11` (for the GUI and plotting tools)

### Configure and Build (C++ Binaries & Tests)

```sh
cmake -S . -B build-local \
  -DBUILD_BENCHMARKS=OFF \
  -DBUILD_PYTHON_BINDINGS=OFF \
  -DBUILD_PROJECT_TESTS=ON \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build-local -j8
```

---

## Running the Test Suites

### 1. Unit Tests (`51` GoogleTests)
```sh
./build-local/tests/Tests
```

### 2. Live End-to-End Integration Tests
```sh
# Fast baseline (deterministic drop hash, 0% loss, 8-subscriber lossy, negative control, snapshot tamper)
python3 tests/live_reliability_e2e.py --build-dir build-local

# Slow scheduler contention test (proves Round-Robin quiet p99 < FCFS quiet p99 by >= 2x)
python3 tests/live_reliability_e2e.py --build-dir build-local --slow

# 30-run consecutive lossy flake gate
python3 tests/live_reliability_e2e.py --build-dir build-local --repeat 30
```

### 3. Offscreen GUI Smoke Test
```sh
QT_QPA_PLATFORM=offscreen python3 tests/gui_smoke_test.py
```

---

## Running Live Experiments & Generating Plots

### Single Multi-Subscriber Live Run
```sh
python3 src/reliability_runner/live_reliability_runner.py \
  --build-dir build-local --duration 4 --clients 2 --rate 600 \
  --dual-feed --subscribers 4 --feed-a-loss 0.10 --feed-b-loss 0.10 --fault-seed 42
```

### All Five Experiment Presets (`redundancy_sweep`, `scheduler_contention`, `history_snapshot_sweep`, `snapshot_tamper`, `scalability`)
```sh
python3 src/reliability_runner/experiments.py \
  --preset all --size quick --build-dir build-local --results-dir results/live

python3 tools/plot_results.py \
  --results-dir results/live --out-dir results/plots
```

---

## Launching the Control-Room GUI

```sh
python3 src/simulation_python/main.py
```

See **[`docs/VIVA_DEMO_SCRIPT.md`](docs/VIVA_DEMO_SCRIPT.md)** for a complete walkthrough of all three top-level tabs (**Market Visualisation**, **Exchange Control**, and **Reliability Experiments**), every sub-panel, button, table, and chart.
