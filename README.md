# Reliable Market-Data Distribution for a Networked Electronic Exchange

**Computer Networks Project — Group 11**

---

## Project Information

### Team Members

| Name                     | Roll No. | Email                     |
|--------------------------|----------|---------------------------|
| Anurag Kishor Patil      | 24114015 | anurag_kp@cs.iitr.ac.in   |
| Prabhat Chandra Tiwari   | 24114066 | prabhat_ct@cs.iitr.ac.in  |
| Vaibhav Kumar            | 24114103 | vaibhav_k@cs.iitr.ac.in   |
| Samarth Maheshwari       | 24114084 | samarth_m@cs.iitr.ac.in   |
| Vishal Kumar Shaw        | 24114105 | vishal_ks@cs.iitr.ac.in   |
| Rishabh Gupta            | 24114077 | rishabh_g@cs.iitr.ac.in   |

---

## Table of Contents
1. [Prerequisites & Dependencies](#prerequisites--dependencies)
2. [Installation & Setup](#installation--setup)
3. [Build Instructions](#build-instructions)
4. [Usage Guide](#usage-guide)
5. [Configuration Options](#configuration-options)
6. [Overview](#overview)
7. [Project Motivation](#project-motivation)
8. [Features](#features)
9. [System Architecture](#system-architecture)
10. [Dashboard Interface](#dashboard-interface)
11. [Export Formats](#export-formats)
12. [Technical Implementation](#technical-implementation)
13. [Project Structure](#project-structure)
14. [Known Limitations](#known-limitations)
15. [Future Enhancements](#future-enhancements)

---

## Prerequisites & Dependencies

### System Requirements

| Requirement | Minimum Version | Purpose |
|-------------|-----------------|---------|
| **Operating System** | Linux (Ubuntu 22.04+) or macOS 13+ | POSIX UDP multicast (`SO_REUSEADDR`/`SO_REUSEPORT`) & TCP sockets |
| **C++ Compiler** | GCC ≥ 13 or Clang/AppleClang ≥ 16 | Full C++23 support (`std::format`, `<ranges>`, `<chrono>`) |
| **CMake** | ≥ 3.24 | Multi-target C++ build, GoogleTest & CTest orchestration |
| **Python** | ≥ 3.11 | Multi-process experiment runner, plotting tools, PyQt6 GUI |

### Software Dependencies

| Package | Purpose | Installation (Ubuntu/Debian) | Installation (macOS Homebrew) |
|---------|---------|------------------------------|-------------------------------|
| **cmake** | Build system generator | `sudo apt install cmake` | `brew install cmake` |
| **clang / g++** | C++23 compiler | `sudo apt install g++-13 clang` | `xcode-select --install` / `brew install gcc` |
| **libboost-all-dev** | Networking utilities & headers | `sudo apt install libboost-all-dev` | `brew install boost` |
| **libspdlog-dev** | High-speed structured C++ logging | `sudo apt install libspdlog-dev` | `brew install spdlog` |
| **pybind11** | C++ OrderBook Python bindings | `pip install pybind11` | `pip install pybind11` |
| **PyQt6 & pyqtgraph** | Visual Control-Room GUI & 60 FPS charts | `pip install PyQt6 pyqtgraph` | `pip install PyQt6 pyqtgraph` |
| **matplotlib & numpy** | Automated preset plot generation | `pip install matplotlib numpy` | `pip install matplotlib numpy` |

### Verification Commands

```bash
# Check CMake version (should be >= 3.24)
cmake --version

# Check C++ compiler version
c++ --version

# Check Python & GUI packages
python3 -c "import PyQt6, pyqtgraph, numpy, matplotlib; print('Python dependencies OK')"

# Verify loopback multicast route
netstat -rn | grep 239 || true
```

---

## Installation & Setup

### Step 1: Install Dependencies

**On Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install -y build-essential cmake g++ clang libboost-all-dev \
    libspdlog-dev libfmt-dev python3 python3-pip python3-venv
pip3 install PyQt6 pyqtgraph numpy matplotlib pybind11
```

**On macOS (Homebrew):**
```bash
brew install cmake boost spdlog fmt python
pip3 install PyQt6 pyqtgraph numpy matplotlib pybind11
```

**On Fedora/RHEL:**
```bash
sudo dnf install -y gcc-c++ clang cmake boost-devel spdlog-devel fmt-devel python3-pip
pip3 install PyQt6 pyqtgraph numpy matplotlib pybind11
```

### Step 2: Clone or Extract Project

```bash
# If from repository
git clone https://github.com/Perpetu4l/reliable-market-data-exchange.git
cd reliable-market-data-exchange

# If from archive
unzip reliable-market-data-exchange.zip
cd reliable-market-data-exchange
```

### Step 3: Verify Documentation References

- **[`docs/RECOVERY_PROTOCOL.md`](docs/RECOVERY_PROTOCOL.md)** — Framed TCP Recovery Protocol v2 wire specification
- **[`docs/METRICS.md`](docs/METRICS.md)** — Sequence-accounting invariants, repair latency classes, and fairness formulas
- **[`docs/LIVE_TESTING.md`](docs/LIVE_TESTING.md)** — Single-host loopback, two-laptop LAN (`udp_lan`), and `tc netem` / `pfctl` setup

---

## Build Instructions

### Quick Build (Recommended)

```bash
cmake -S . -B build-local \
  -DBUILD_BENCHMARKS=OFF \
  -DBUILD_PYTHON_BINDINGS=ON \
  -DBUILD_PROJECT_TESTS=ON \
  -DCMAKE_BUILD_TYPE=Release

cmake --build build-local -j8
```

The CMake build compiles:
- **Core Libraries:** `OrderBook`, `MDFeed`, `OrderEntry`
- **Python Bindings (`orderbook/`):** `_orderbook`, `_mdfeed_receiver`, `_orderentry_client` (with an automatic pure-Python fallback in `orderbook/__init__.py`)
- **Exchange & Server Binaries:** `exchange`, `order_server`
- **Client & Subscriber Binaries:** `reliable_subscriber`, `workload_client`, `order_client`, `recovery_client`, `client`
- **Deterministic Harness & Unit Tests:** `reliability_demo`, `tests/Tests` (51 GoogleTests)

### Build with Python Bindings (Optional Rebuild of `orderbook` Module)

```bash
cmake -S . -B build-local \
  -DBUILD_BENCHMARKS=OFF \
  -DBUILD_PYTHON_BINDINGS=ON \
  -DBUILD_PROJECT_TESTS=ON \
  -DCMAKE_BUILD_TYPE=Release

cmake --build build-local -j8
```

### Build Verification & Running Tests

```bash
# 1. Check compiled executables
ls -lh build-local/src/exchange build-local/src/reliable_subscriber build-local/src/workload_client

# 2. Run all 51 C++ GoogleTest unit tests
./build-local/tests/Tests

# 3. Run fast live end-to-end UDP/TCP integration tests
python3 tests/live_reliability_e2e.py --build-dir build-local

# 4. Run slow scheduler contention test (proves RR quiet p99 < FCFS quiet p99 by >= 2x)
python3 tests/live_reliability_e2e.py --build-dir build-local --slow

# 5. Run offscreen PyQt6 GUI smoke test
QT_QPA_PLATFORM=offscreen python3 tests/gui_smoke_test.py --build-dir build-local
```

### Cleaning Build Artifacts

```bash
rm -rf build-local
```

---

## Usage Guide

### 1. Launching the Visual Control-Room GUI

```bash
PYTHONPATH=.:src/simulation_python python3 src/simulation_python/main.py
```

### 2. Running Live Multi-Process Experiments (CLI Runner)

#### Basic Dual-Feed Live Run (4 Subscribers, 10% Independent Drop Rate)
```bash
python3 src/reliability_runner/live_reliability_runner.py \
  --build-dir build-local \
  --duration 4 \
  --clients 2 \
  --rate 600 \
  --dual-feed \
  --subscribers 4 \
  --feed-a-loss 0.10 \
  --feed-b-loss 0.10 \
  --fault-seed 42
```

#### Custom Per-Subscriber Impairments (Loss, Duplication, Reordering, Delay, Concurrency)
```bash
python3 src/reliability_runner/live_reliability_runner.py \
  --build-dir build-local \
  --duration 4 \
  --clients 2 \
  --rate 800 \
  --dual-feed \
  --recovery-policy round-robin \
  --recovery-workers 1 \
  --service-delay-us-per-request 300 \
  --service-delay-us-per-event 20 \
  --subscriber "id=noisy-1,feeds=a,model=independent,drop_a=0.50,seed=10,concurrency=8,role=noisy" \
  --subscriber "id=quiet-1,feeds=ab,model=independent,drop_a=0.02,drop_b=0.02,dup=0.01,reorder=0.02,delay_ms=1,seed=11,concurrency=1,role=quiet"
```

#### Snapshot Fallback & Cryptographic Tamper Verification
```bash
# Force TOO_OLD -> On-Demand Snapshot Recovery (small history=30)
python3 src/reliability_runner/live_reliability_runner.py \
  --build-dir build-local \
  --duration 3 --clients 2 --rate 800 \
  --history 30 --snapshot-interval 200 --snapshot-on-demand \
  --subscriber "id=snap-sub,feeds=a,model=independent,drop_a=0.30,seed=5"

# Verify tamper detection rejects corrupted snapshot (exits with status=failed_integrity)
python3 src/reliability_runner/live_reliability_runner.py \
  --build-dir build-local \
  --duration 3 --clients 2 --rate 800 \
  --history 20 --snapshot-interval 100 \
  --tamper-snapshot book_value --expect-failure \
  --subscriber "id=tamper-sub,feeds=a,model=independent,drop_a=0.35,seed=7"
```

### 3. Running All 5 Experiment Presets & Generating Plots

```bash
# Execute all 5 presets (redundancy_sweep, scheduler_contention, history_snapshot_sweep, snapshot_tamper, scalability)
python3 src/reliability_runner/experiments.py \
  --preset all --size quick --build-dir build-local --results-dir results/live

# Generate publication PNG plots in results/plots/
python3 tools/plot_results.py \
  --results-dir results/live --out-dir results/plots
```

### 4. Running Individual Binaries Manually

```bash
# Terminal 1: Start Exchange with Dual Feed + TCP Recovery Server
./build-local/src/exchange --mode client \
  --oe-port 8080 --dual-feed \
  --md-ip 239.1.1.1 --md-port 9999 \
  --feed-b-ip 239.1.1.2 --feed-b-port 10000 \
  --md-interface 127.0.0.1 \
  --recovery-port 9090 --recovery-policy round-robin \
  --history 65536 --snapshot-interval 1000 --snapshot-on-demand

# Terminal 2: Start Reliable Subscriber
./build-local/src/reliable_subscriber --dual-feed \
  --feed-a-ip 239.1.1.1 --feed-a-port 9999 \
  --feed-b-ip 239.1.1.2 --feed-b-port 10000 \
  --interface 127.0.0.1 \
  --recovery-server 127.0.0.1 --recovery-port 9090 \
  --subscriber sub-1 \
  --loss-model independent --drop-a 0.10 --drop-b 0.10 --drop-seed 42 \
  --stop-when-idle-ms 500 \
  --csv results/sub-1.csv --latency-csv results/sub-1.latency.csv

# Terminal 3: Run Crossing Buy/Sell Order Workload Clients
./build-local/src/workload_client --server 127.0.0.1 --port 8080 \
  --client-id 1 --duration 5 --rate 600 --symbol AAPL --side buy --seed 101 &
./build-local/src/workload_client --server 127.0.0.1 --port 8080 \
  --client-id 2 --duration 5 --rate 600 --symbol AAPL --side sell --seed 102 &
wait
```

---

## Configuration Options

### 1. `exchange` Command-Line Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--mode MODE` | `simulation` \| `client` | `simulation` | Synthetic internal order flow or live TCP client order entry |
| `--oe-port PORT` | Integer | `8080` | Framed TCP order-entry listener port |
| `--dual-feed` | Flag | Off | Publish identical market-data stream on both Feed A and Feed B |
| `--md-ip IP` / `--md-port PORT` | String / Int | `239.1.1.1` / `9999` | Feed A UDP multicast group and port |
| `--feed-b-ip IP` / `--feed-b-port PORT` | String / Int | `239.1.1.2` / `10000` | Feed B UDP multicast group and port |
| `--md-interface IP` | IPv4 String | `127.0.0.1` | Outbound multicast interface IPv4 address |
| `--recovery-port PORT` | Integer | `9090` | Framed TCP recovery & snapshot server port |
| `--recovery-policy POLICY` | `fcfs` \| `round-robin` | `fcfs` | Scheduler policy for queued TCP recovery requests |
| `--recovery-workers N` | Integer | `1` | Server worker thread pool size for recovery requests |
| `--service-delay-us-per-request US` | Integer | `0` | Modelled per-request service cost in microseconds |
| `--service-delay-us-per-event US` | Integer | `0` | Modelled per-replayed-event service cost in microseconds |
| `--history N` | Integer | `65536` | Bounded publisher history capacity (events retained in memory) |
| `--snapshot-interval N` | Integer | `1000` | Periodic composite checkpoint interval in sequence numbers (`0` disables) |
| `--snapshot-retention N` | Integer | `8` | Maximum periodic snapshots retained in `SnapshotStore` |
| `--snapshot-on-demand` | Flag | Enabled | Construct a fresh composite snapshot under `state_mutex_` on `TOO_OLD` |
| `--tamper-snapshot MODE` | `none`\|`book_value`\|`sequence`\|`previous_hash`\|`hash` | `none` | Inject snapshot corruption to test subscriber SHA-256 verification |
| `--requests-csv FILE` | Path | None | Export per-request scheduler queue wait & service times to CSV |

### 2. `reliable_subscriber` Command-Line Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--subscriber ID` | String | `sub-1` | Unique subscriber identifier registered with recovery server |
| `--dual-feed` | Flag | Off | Subscribe to both Feed A and Feed B (`feeds=ab`) |
| `--interface IP` | IPv4 String | `127.0.0.1` | Local interface IP (`127.0.0.1` -> `udp_loopback`, LAN IP -> `udp_lan`) |
| `--recovery-server HOST` / `--recovery-port PORT` | String / Int | `127.0.0.1` / `9090` | TCP recovery server address and port |
| `--loss-model MODEL` | `none`\|`independent`\|`correlated` | `none` | Receive-side deterministic fault injection mode |
| `--drop-a P` / `--drop-b P` | Float `[0, 1]` | `0.0` | Independent packet drop probability on Feed A / Feed B |
| `--corr-loss P` | Float `[0, 1]` | `0.0` | Simultaneous drop probability across both feeds (`correlated` mode) |
| `--dup P` | Float `[0, 1]` | `0.0` | Receive-side packet duplication probability |
| `--reorder P` / `--reorder-depth D` | Float / Int | `0.0` / `4` | Receive-side packet reordering probability and holdback depth |
| `--delay-ms MS` | Integer | `0` | Receive-side fixed delay injection in milliseconds |
| `--drop-seed SEED` | Integer | `1` | Deterministic `std::mt19937_64` seed for reproducible faults |
| `--gap-grace-ms MS` | Integer | `5` | Grace window before escalating an unresolved gap to TCP recovery |
| `--recovery-concurrency K` | Integer | `1` | Number of parallel TCP recovery worker threads per subscriber |
| `--recovery-retries N` | Integer | `5` | Exponential backoff retry count on transient TCP recovery errors |
| `--no-recovery` | Flag | Off | Negative control: disable TCP recovery and measure `permanently_lost` |
| `--run-seconds S` / `--stop-when-idle-ms MS` | Int | `5` / `0` | Run duration limit or automatic quiescence detection window |
| `--csv FILE` / `--latency-csv FILE` | Path | None | Output paths for summary metrics CSV and raw per-event latency CSV |

---

## Overview

**Reliable Market-Data Distribution for a Networked Electronic Exchange** is a high-performance C++23 electronic exchange and fault-tolerant market-data distribution system accompanied by a visual PyQt6/pyqtgraph Control-Room GUI.

The exchange remains **centralised**: there is one authoritative price-time-priority matching engine and limit order book per instrument (`AAPL`, `GOOGL`, `AMZN`, `NFLX`, `META`). Order flow enters over framed TCP (`OrderEntryServer`), matches against resting liquidity, and publishes sequence-numbered binary market-data datagrams over UDP multicast.

To achieve **100% ordered, gap-free state reconstruction** over lossy UDP multicast without slowing down the matching engine, the system combines four layered reliability mechanisms:
1. **Active-Active Dual-Feed UDP Multicast (`Feed A` + `Feed B`)** with first-copy-wins duplicate suppression.
2. **Gap-Safe Out-of-Order Buffering (`SequenceTracker` + `DualFeedSubscriber`)** with configurable grace timers.
3. **Bounded Publisher History & Framed TCP Replay (`RecoveryProtocol v2`)** scheduled via **FCFS** or **Round-Robin** worker pools.
4. **Periodic & On-Demand Composite SHA-256 Checkpoints (`SnapshotManager`)** with end-to-end order-book digest verification (`BookBuilder::digest()`).

---

## Project Motivation

Electronic financial exchanges must distribute market-data updates (price-level changes and executions) to dozens or hundreds of subscribers with microsecond-to-millisecond latency:
- **Unicast TCP market data** scales poorly because the exchange must duplicate packets and maintain congestion/retransmission state per subscriber, causing head-of-line blocking across clients.
- **Raw UDP multicast** scales to arbitrary subscribers with minimal publisher overhead, but suffers from silent packet loss, duplication, and reordering under bursty traffic or network contention.
- **Naive retransmission servers** suffer from **noisy-neighbour starvation** (a single high-loss subscriber flooding the recovery queue delays quiet subscribers) and **history eviction failures** when a subscriber's gap falls behind the bounded in-memory replay buffer.

Our system solves these challenges by:
1. **Repairing ~90% of independent packet drops in < 200 µs** using redundant UDP multicast feeds (`Feed A` + `Feed B`) before TCP recovery is ever needed.
2. **Isolating quiet subscribers from noisy neighbours** using a multi-worker **Round-Robin** TCP recovery scheduler (reducing quiet-subscriber `p99` queue wait by **> 3×** compared to FCFS under contention).
3. **Guaranteeing recovery from arbitrary lag** via on-demand composite snapshots chained with SHA-256 hashes.
4. **Proving state equivalence at run end** by reconciling the subscriber's reconstructed order-book digest against the exchange's authoritative digest at the exact cut sequence $L$.

---

## Features

### Core Capabilities

- **Central Price-Time-Priority Matching Engine (`lib/OrderBook`)**
  - Supports limit orders, market buy/sell orders, order cancellations, and order modifications across 5 instruments (`AAPL`, `GOOGL`, `AMZN`, `NFLX`, `META`).
  - Integer-cent pricing (`15050` = `$150.50`) to eliminate floating-point rounding defects.

- **Framed TCP Order Entry & Reproducible Workload Generation (`lib/OrderEntry`, `workload_client`)**
  - Binary length-framed TCP protocol with `CLIENT_HELLO` / `SERVER_WELCOME` handshake and thread-safe multi-client queue (`OrderRingBuffer`).
  - Independent `workload_client` processes generate crossing buy/sell order flow and measure client-side order ACK RTT percentiles (`p50`, `p95`, `p99`).

- **Dual-Feed UDP Multicast & First-Copy-Wins Deduplication (`lib/MDFeed`)**
  - Simultaneous multicast publication on Feed A (`239.1.1.1:9999`) and Feed B (`239.1.1.2:10000`) plus periodic heartbeats.
  - Per-sequence arrival bitsets (`arrived_a_`, `arrived_b_`, `delivered_source_`, `snapshot_covered_`) verify exact sequence accounting:
    $$\text{events\_expected} = \text{delivered\_via\_feed\_a} + \text{delivered\_via\_feed\_b} + \text{delivered\_via\_recovery} + \text{events\_covered\_by\_snapshot} + \text{permanently\_lost}$$

- **Per-Missing-Event Latency Classification**
  - Every missing sequence is timestamped at the exact instant gap detection occurs ($t_{\text{gap}}$) and classified upon delivery ($t_{\text{deliv}} - t_{\text{gap}}$) into:
    - `other_feed` (repaired by the second UDP multicast feed)
    - `tcp_recovery` (repaired via TCP range replay)
    - `snapshot` (covered by composite checkpoint fallback)
    - `tcp_request_rtt` (network + server round-trip time of TCP recovery requests)

- **FCFS & Round-Robin TCP Recovery Server (`RecoveryTcpServer`, Protocol v2)**
  - Supports chunked streaming (`RECOVERY_CHUNK` + `RECOVERY_COMPLETE`) for large gap replays (`> 1 MiB`).
  - Configurable worker threads (`--recovery-workers`), modelled service delays, per-request CSV logging (`recovery-requests.csv`), per-subscriber queue wait percentiles, and saturated-window Jain fairness index (`queue_depth >= 2`).

- **Tamper-Evident SHA-256 Snapshots & Final Book Digest Reconciliation**
  - Periodic and on-demand (`--snapshot-on-demand`) composite checkpoints protected by a SHA-256 hash chain (`SnapshotHasher::verify()`).
  - Protocol v2 `STATUS_RESPONSE` carries the exchange's authoritative `latest_book_digest`; every subscriber verifies `book_matches_exchange=1` at run completion.

- **Visual-First PyQt6 / pyqtgraph Control-Room GUI**
  - Three top-level tabs (**Market Visualisation**, **Exchange Control**, and **Reliability Experiments**) featuring **12 interactive live & preset graphs**, structured telemetry tables, and one-click experiment execution.

---

## System Architecture

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                   ORDER ENTRY & WORKLOAD CLIENTS (TCP :8080)                  │
│   ┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────┐  │
│   │ workload_client (Buy)│   │workload_client (Sell)│   │order_client / GUI│  │
│   └──────────┬───────────┘   └──────────┬───────────┘   └────────┬─────────┘  │
└──────────────┼──────────────────────────┼────────────────────────┼────────────┘
               │                          │                        │
               └──────────────────────────┼────────────────────────┘
                                          │ Framed Binary TCP (CLIENT_HELLO / NEW_ORDER)
                                          ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                         EXCHANGE SERVER PROCESS (C++23)                       │
├───────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │  OrderEntryServer (:8080) ──► OrderRingBuffer ──► Matching Engine       │  │
│  │  • Price-Time-Priority OrderBook per Symbol (AAPL, GOOGL, AMZN, NFLX...)│  │
│  │  • MDAdapter converts price-level updates & trades into binary messages │  │
│  └──────────────────────────────┬──────────────────────────┬───────────────┘  │
│                                 │                          │                  │
│                                 ▼                          ▼                  │
│  ┌────────────────────────────────────────┐  ┌─────────────────────────────┐  │
│  │ MulticastPublisherThread (UDP)         │  │ RecoveryServer (:9090 TCP)  │  │
│  │ • Global monotonic sequence 1..L       │  │ • Unified state_mutex_      │  │
│  │ • Feed A: 239.1.1.1:9999               │  │ • Bounded RecoveryHistory   │  │
│  │ • Feed B: 239.1.1.2:10000 (--dual-feed)│  │ • SnapshotManager (SHA-256) │  │
│  │ • Periodic Heartbeat datagrams         │  │ • FCFS / Round-Robin Queues │  │
│  └───────┬────────────────────────┬───────┘  └──────────────▲──────────────┘  │
└──────────┼────────────────────────┼─────────────────────────┼─────────────────┘
           │ UDP Feed A             │ UDP Feed B              │ Framed TCP
           ▼                        ▼                         │ Recovery v2
┌─────────────────────────────────────────────────────────────┴─────────────────┐
│                    RELIABLE SUBSCRIBER PROCESSES (1 .. 32)                    │
├───────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │ 1. Receive-Side ImpairmentStage (Feed A & Feed B)                       │  │
│  │    • Seeded drop_a, drop_b, corr_loss, dup, reorder, delay_ms           │  │
│  └──────────────────────────────┬──────────────────────────────────────────┘  │
│                                 ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │ 2. DualFeedSubscriber & SequenceTracker                                 │  │
│  │    • First-copy-wins deduplication + out-of-order pending_events_ map   │  │
│  │    • Per-missing-event latency clock (other_feed, tcp_recovery, snap)   │  │
│  │    • Gap Grace Timer ──► Concurrent Recovery Worker Pool (K threads)    │  │
│  └──────────────────────────────┬──────────────────────────────────────────┘  │
│                                 ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │ 3. Local BookBuilder & End-of-Run Quiescence Protocol                   │  │
│  │    • Applies in-order PriceLevelUpdateMessages or verified Snapshots    │  │
│  │    • Stops UDP -> Polls STATUS convergence -> Drains tail gaps          │  │
│  │    • Compares local BookBuilder::digest() == exchange_book_digest       │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────┘
```

### Normative Subscriber Lifecycle & Quiescence Protocol

1. **Reachability Handshake (`HELLO`):** Subscriber connects to the exchange's TCP recovery port (`9090`) and sends `STATUS_REQUEST(subscriber_id)`. Exchange registers the subscriber and emits `SUBSCRIBER_CONNECTED`.
2. **Ready Barrier (`SUBSCRIBER_READY`):** Once UDP multicast sockets are bound on Feed A (and Feed B), the subscriber prints `SUBSCRIBER_READY id=<id> feeds=<a|ab>`. The experiment runner starts `workload_client` traffic only after all subscribers are ready.
3. **Live Reception & Gap Repair:** Incoming packets pass through `ImpairmentStage` into `DualFeedSubscriber`. In-order packets apply immediately to `BookBuilder`; gaps older than `--gap-grace-ms` are sliced across `--recovery-concurrency K` TCP workers (`RECOVERY_REQUEST`), falling back to `SNAPSHOT_REQUEST` on `TOO_OLD`.
4. **Stop & `STATUS` Convergence:** When the run duration or `--stop-when-idle-ms` expires, the subscriber stops its UDP receivers first (`< 15 ms`), then polls `STATUS_REQUEST` until two consecutive responses return identical `(latest_sequence, history_minimum_sequence, latest_book_digest)`.
5. **Tail Drain & Book Verification:** The subscriber invokes `on_heartbeat(latest_sequence)` to expose any unreceived tail `[expected_sequence, latest_sequence]`, drains all remaining recovery ranges, verifies the exact sequence bitset identity (`accounting_valid=1`) and `BookBuilder::digest() == latest_book_digest` (`book_matches_exchange=1`), sends `BYE`, and exits (`0` = completed, `2` = failed_integrity, `3` = failed_accounting).

---

## Dashboard Interface

The PyQt6 Control-Room GUI (`src/simulation_python/main.py`) is organized into **3 visual-first top-level tabs** containing **12 real-time and preset `pyqtgraph` charts** and structured telemetry tables:

### Top-Level Tab 1: `1. Market Visualisation (Live Order Book)`

```
┌─────────────────────────────────────────┬───────────────────────────────────┐
│ 1. Best Bid & Ask Price (USD, 60 FPS)   │ 2. Order Book Depth by Price Level│
│    Green: Best Bid | Red: Best Ask      │    Horizontal Bid/Ask Depth Bars  │
├─────────────────────────────────────────┼───────────────────────────────────┤
│ 3. Matched Trade Volume (Rolling 1s)    │ Interactive Controls & Book Table │
│    Blue Volume Bars                     │ [▲ Add Buy Pressure] [▼ Sell]     │
│                                         │ 4x4 Live Telemetry Table          │
└─────────────────────────────────────────┴───────────────────────────────────┘
```

- **Interactive Pressure Buttons:** **`▲ Add Buy Pressure`** and **`▼ Add Sell Pressure`** inject burst market orders into the C++ `OrderBook` and shift the price, depth, and volume charts in real time.
- **Live Book Telemetry Table (`4x4`):** Displays `Best Bid`, `Best Ask`, `Best Bid Depth`, `Best Ask Depth`, `Bid-Ask Spread`, `Resting Orders`, `Matched Units`, and `Transport`.

### Top-Level Tab 2: `2. Exchange Control (Live Processes & Graphs)`

Divided into **4 operational sub-tabs** with a live process event log at the bottom:

1. **`1. Local Simulation (Live Graphs & Tables)`**
   - **Top Controls:** Exchange & Recovery Server configuration (`OE Port`, `Rec Port`, `History`, `Snap Intv`, `Policy`, `Workers`, `Delay/Req`, `Tamper`, `Dual Feed`, `On-Demand Snapshot`) and one-click process lifecycle buttons (**`1. Start Exchange`**, **`2. Start Subscribers`**, **`3. Start Workload`**, **`Reconcile & Stop Subs`**, **`Stop All`**, **`📊 Open Latest in 6-Graph Dashboard`**).
   - **2 Live Charts:**
     - **Chart A (`Live Subscriber Delivered vs Recovered Events`):** Grouped green (`Delivered`) and blue (`Recovered`) bars per subscriber updated every 500 ms.
     - **Chart B (`Exchange Sequence Progress / Workload ACK RTT`):** Real-time 1 Hz sequence curve during execution; switches to grouped `p50 / p95 / p99 (µs)` ACK RTT bars upon workload completion.
   - **3 Structured Tables:** Editable `Subscribers Configuration Table`, live `Subscriber Status & Reconciliation Table`, and `Workload Clients Table`.
2. **`2. Manual Order Testing (RTT Graph & Table)`**
   - Submit individual binary TCP limit orders (`AAPL`, `GOOGL`, `AMZN`, `NFLX`, `META`) via **`▶ Send Order Over TCP`** and inspect the live **`Manual Order TCP Round-Trip Time (ms)` Bar Chart** and **`Submission History Table`**.
3. **`3. Distributed LAN Setup`**
   - Auto-detects local IPv4 interfaces, tests TCP reachability (`🔌 Test TCP Reachability`), starts a LAN-bound exchange, generates full-width copyable CLI commands for remote laptops, and tracks remote connections in the **`Live Connected Remote Clients & Subscribers Table`**.
4. **`4. Saved Results Explorer (Table & Graph)`**
   - Browse any saved `.json` or `.csv` result in `results/`, render an immediate visual bar chart preview and structured data table, or click **`📊 Open Selected in 6-Graph Reliability Dashboard`**.

### Top-Level Tab 3: `3. Reliability Experiments (6-Graph Dashboard)`

A unified single-screen visual dashboard combining experiment controls, **6 pre-loaded and dynamically updated `pyqtgraph` charts**, and **2 structured telemetry tables**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TOP INTERACTIVE CONTROLS & ONE-CLICK ACTIONS                                │
│ • Mode, Loss, Dup, Reorder, Delay, Subscribers (1-32), History, Conc(K)     │
│ • Snapshot Interval/On-Demand, Scheduler (FCFS/RR, Workers, Service Delay)  │
│ [▶ Run Live Experiment] [⚡ Run In-Process] [📊 Run Preset] [🔄 Load Sweeps]│
├────────────────────────┬─────────────────────────┬──────────────────────────┤
│ Graph 1:               │ Graph 2:                │ Graph 3:                 │
│ Events Needing Rec (%) │ Repair Latency by Class │ FCFS vs RR Queue Wait    │
│ vs Loss / Breakdown    │ (p50/p95/p99 µs) vs N   │ p99 (3.05x RR speedup)   │
├────────────────────────┼─────────────────────────┼──────────────────────────┤
│ Graph 4:               │ Graph 5:                │ Graph 6:                 │
│ Delivery Sources &     │ Redundancy Effectiveness│ Delivered Events per Sub │
│ Snapshot Fallback      │ & Feed Share (%)        │ (Green=Book Match, Red=X)│
├────────────────────────┴─────────────────────────┴──────────────────────────┤
│ Table 1: Per-Subscriber Accounting, Latency & Book Match (12 Columns)       │
│ Table 2: System, Redundancy, Scheduler & Snapshot KPIs (6x4 Grid)           │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Export Formats

### 1. Structured Run JSON (`results/live/<run_id>/run.json`)

Every live run persists a complete machine-readable snapshot containing provenance, configuration, exchange metrics, workload client RTTs, and per-subscriber accounting:

```json
{
  "run_id": "20261031T092331852436Z",
  "transport": "udp_loopback",
  "loss_injection": "app_level_receive_side",
  "overall_status": "completed",
  "exchange_metrics": {
    "recovery_policy": "round-robin",
    "recovery_workers": "1",
    "recovery_requests_served": "8",
    "recovery_fairness_saturated_jain_ppm": "945946",
    "snapshots_retained": "3",
    "on_demand_snapshots": "0",
    "latest_book_digest": "8c5d99e2e14a7c2b"
  },
  "subscriber_metrics": {
    "sub-1": {
      "subscriber_id": "sub-1",
      "run_status": "completed",
      "events_expected": "2548",
      "delivered": "2548",
      "delivered_via_feed_a": "2297",
      "delivered_via_feed_b": "228",
      "delivered_via_recovery": "23",
      "events_covered_by_snapshot": "0",
      "permanently_lost": "0",
      "accounting_valid": "1",
      "potential_single_feed_gaps": "251",
      "gaps_avoided_by_second_feed": "228",
      "redundancy_effectiveness": "0.908367",
      "recovery_p50_ns": "3145000",
      "recovery_p99_ns": "6420000",
      "other_feed_p50_ns": "148000",
      "book_matches_exchange": "1"
    }
  }
}
```

### 2. Per-Subscriber Summary CSV (`subscriber-<id>.csv` & `run-summary.csv`)

Flat tabular format compatible with `pandas`, R, Excel, and `tools/plot_results.py`:

```csv
schema_version,subscriber_id,transport,loss_injection,feed_mode,run_status,events_expected,delivered,delivered_via_feed_a,delivered_via_feed_b,delivered_via_recovery,events_covered_by_snapshot,permanently_lost,accounting_valid,redundancy_effectiveness,recovery_p50_ns,recovery_p95_ns,recovery_p99_ns,book_matches_exchange
2,sub-1,udp_loopback,app_level_receive_side,dual,completed,2548,2548,2297,228,23,0,0,1,0.908367,3145000,5120000,6420000,1
```

### 3. Raw Per-Missing-Event Latency CSV (`subscriber-<id>.latency.csv`)

Records every individual missing sequence $s$, its repair class (`other_feed`, `tcp_recovery`, `snapshot`, or `tcp_request_rtt`), and exact nanosecond repair duration $\Delta t(s) = t_{\text{deliv}}(s) - t_{\text{gap}}(s)$:

```csv
subscriber_id,sequence,latency_class,latency_ns
sub-1,14,other_feed,142000
sub-1,89,other_feed,165000
sub-1,203,tcp_recovery,3184000
sub-1,0,tcp_request_rtt,1420000
```

### 4. Server Scheduler Request Log (`recovery-requests.csv`)

Generated by `exchange --requests-csv` to audit FCFS vs Round-Robin queue wait under contention:

```csv
request_id,subscriber_id,start_seq,end_seq,status,event_count,enqueue_ns,dequeue_ns,complete_ns,queue_wait_us,service_time_us,queue_depth_at_enqueue
1,noisy-1,12,28,OK,17,1045200,1048900,1688900,3.7,640.0,4
2,quiet-1,45,46,OK,2,1090100,1690200,2030200,600.1,340.0,5
```

---

## Technical Implementation

### 1. Exact Sequence Accounting & First-Copy-Wins Deduplication

Every sequence $s \in \{1, \dots, L\}$ is tracked in four bitsets inside `DualFeedSubscriber` (`lib/MDFeed/src/reliability/DualFeed.cpp`):

```cpp
// Record arrival on Feed A or Feed B before deduplication
if (event.feed_id == FeedId::FEED_A) {
    ensure_capacity(seq);
    arrived_a_[seq] = 1;
} else if (event.feed_id == FeedId::FEED_B) {
    ensure_capacity(seq);
    arrived_b_[seq] = 1;
}

// SequenceTracker enforces expected_sequence_ starting strictly at 1
auto decision = tracker_.on_sequence(seq, now_ns);
if (decision.duplicate) {
    ++duplicates_suppressed_;
    return; // First-copy-wins: second copy is silently suppressed
}
```

At run completion (`reconcile_until(L)`), the subscriber verifies that `delivered_source_[s] != 0` and `snapshot_covered_[s] != 0` are mutually exclusive for every $s \in \{1, \dots, L\}$, guaranteeing:

$$\text{events\_expected} = \text{delivered} + \text{events\_covered\_by\_snapshot} + \text{permanently\_lost}$$

### 2. Per-Missing-Event Latency Clock

Instead of timing only the TCP socket call, `DualFeedSubscriber` records $t_{\text{gap}}(s)$ the instant a sequence discontinuity is detected:

```cpp
// When sequence `seq > expected_sequence_` arrives:
for (std::uint64_t missing = expected_before; missing < seq; ++missing) {
    if (gap_First_seen_ns_[missing] == 0) {
        gap_first_seen_ns_[missing] = now_ns; // Start timer at gap detection
    }
}

// When sequence `s` is finally delivered in order (via Feed B, TCP replay, or Snapshot):
if (gap_first_seen_ns_[s] > 0 && now_ns >= gap_first_seen_ns_[s]) {
    record_latency_sample(missing_class, now_ns - gap_first_seen_ns_[s]);
    gap_first_seen_ns_[s] = 0;
}
```

### 3. FCFS vs Round-Robin Scheduler & Saturated-Window Fairness

`RecoveryScheduler` (`lib/MDFeed/src/reliability/RecoveryScheduler.cpp`) maintains both a global FIFO queue for `FCFS` and per-subscriber FIFO queues cycled by a cursor for `ROUND_ROBIN`:

```cpp
if (policy_ == RecoveryPolicy::FCFS) {
    next = fcfs_queue_.front();
    fcfs_queue_.pop_front();
} else {
    // Round-Robin: step cursor to the next subscriber with a non-empty queue
    for (std::size_t i = 0; i < subscriber_order_.size(); ++i) {
        const auto idx = (rr_cursor_ + i) % subscriber_order_.size();
        const auto& sub_id = subscriber_order_[idx];
        auto& q = rr_queues_[sub_id];
        if (!q.empty()) {
            next = q.front();
            q.pop_front();
            rr_cursor_ = (idx + 1) % subscriber_order_.size();
            break;
        }
    }
}
```

Under contention (`1` noisy subscriber at 50% drop rate + `--recovery-concurrency 8` vs `6` quiet subscribers at 2% drop rate), **Round-Robin reduces quiet-subscriber `p99` queue wait from `15.46 ms` to `5.07 ms` (`3.05×` improvement)** and raises saturated-window Jain fairness (`queue_depth_at_enqueue >= 2`) from `0.625` to `0.946`.

### 4. Composite SHA-256 Checkpoints & End-to-End Book Digest Verification

Because the exchange uses a single global sequence number across all 5 instruments, `SnapshotManager` constructs **composite multi-instrument snapshots** (`instrument_id = 0` in the header, per-level `instrument_id` inside each `SnapshotPriceLevel`) protected by a 256-bit SHA-256 hash chain (`SnapshotHasher`).

In addition, `RecoveryServer::record_published_event` updates `history_`, `published_book_`, and `snapshots_` atomically under a single `state_mutex_`, ensuring that `STATUS_RESPONSE` (`Protocol v2`) always pairs `latest_sequence` with the exact canonical book digest `latest_book_digest`.

---

## Project Structure

```
reliable-market-data-exchange/
│
├── CMakeLists.txt                   # Top-level CMake configuration (C++23, tests, options)
├── README.md                        # Project documentation (this file)
├── pyproject.toml / setup.py        # Python package metadata for C++ bindings
│
├── lib/
│   ├── OrderBook/                   # Price-time-priority matching engine library
│   │   ├── include/core/OrderBook.h # Bid/Ask limit maps, AddOrder, TryMatch, Amend, Cancel
│   │   ├── src/core/OrderBook.cpp   # Matching engine implementation
│   │   └── python/bindings.cpp      # pybind11 C++ -> Python OrderBook bridge
│   │
│   ├── OrderEntry/                  # Framed binary TCP order-entry library
│   │   ├── include/messages/        # Packed ClientHello, ServerWelcome, NewOrder, OrderAck
│   │   ├── src/server/              # Multi-client TCP listener & OrderRingBuffer queue
│   │   └── src/client/              # TCP order-entry client transport
│   │
│   └── MDFeed/                      # Market-data multicast & reliability subsystem
│       ├── include/messages/        # Binary MessageHeader, PriceLevelUpdate, Trade, Heartbeat
│       ├── include/reliability/     # SequenceTracker, DualFeed, RecoveryWire, Snapshot, Metrics
│       ├── src/publisher/           # MarketDataPublisher & UDP MulticastPublisherThread
│       ├── src/receiver/            # UDP MulticastReceiver (fast CV wakeup on stop)
│       └── src/reliability/         # DualFeedSubscriber, RecoveryTcp, Scheduler, SnapshotHasher
│
├── src/
│   ├── exchange/                    # Main exchange server executable (matching + UDP + TCP recovery)
│   ├── reliable_subscriber/         # Live dual-feed UDP + TCP recovery subscriber executable
│   ├── workload_client/             # Reproducible crossing TCP order generator & RTT profiler
│   ├── order_client/                # Interactive CLI TCP order-entry client
│   ├── recovery_client/             # CLI utility for querying TCP Recovery Protocol v2
│   ├── reliability_demo/            # Deterministic single-process baseline harness
│   ├── reliability_runner/          # Multi-process Python orchestrator & 5 experiment presets
│   │   ├── live_reliability_runner.py
│   │   ├── experiments.py
│   │   └── aggregate.py
│   └── simulation_python/           # PyQt6 + pyqtgraph Visual Control-Room GUI
│       ├── main.py                  # GUI application entry point
│       ├── market_simulator.py      # Background order-flow simulator for Tab 1
│       └── gui/                     # MainWindow, ExchangeControlPanel, ReliabilityDashboard
│
├── tests/
│   ├── OrderBookTests.cpp           # Order book unit tests
│   ├── MatchingEngineTests.cpp      # Matching engine execution tests
│   ├── OrderEntryNetworkingTests.cpp# TCP order-entry framing & queue tests
│   ├── ReliabilitySkeletonTests.cpp # 51 unit tests (tracker, latency, wire v2, digest, RR vs FCFS)
│   ├── live_reliability_e2e.py      # Multi-process live UDP/TCP end-to-end test suite
│   └── gui_smoke_test.py            # Offscreen PyQt6 GUI verification test
│
├── tools/
│   └── plot_results.py              # Generates 6 publication PNG charts from preset CSVs
│
└── docs/
    
```

### File Responsibilities

| Component | Primary Files | Primary Responsibility |
|-----------|---------------|------------------------|
| **Matching Engine** | `lib/OrderBook/src/core/OrderBook.cpp`, `src/exchange/Exchange.cpp` | Price-time-priority matching, book maintenance, market-data publication |
| **UDP Multicast Feed** | `MulticastPublisher.cpp`, `MulticastReceiver.cpp` | Dual-feed (`Feed A` / `Feed B`) UDP multicast socket transmission & reception |
| **Reliability Core** | `SequenceTracker.cpp`, `DualFeed.cpp`, `FaultInjector.cpp` | First-copy-wins deduplication, bitset accounting, per-event latency, receive-side impairments |
| **TCP Recovery v2 & Scheduler** | `RecoveryTcp.cpp`, `RecoveryScheduler.cpp`, `RecoveryProtocol.cpp` | Length-prefixed TCP framing, chunked replay, FCFS / Round-Robin worker scheduling |
| **Snapshots & Book Digest** | `Snapshot.cpp`, `BookBuilder` | Composite multi-instrument checkpoints, SHA-256 hash chain, final book digest reconciliation |
| **Live Subscriber Binary** | `src/reliable_subscriber/main.cpp` | End-to-end subscriber lifecycle, concurrent recovery worker pool, CSV/JSON telemetry export |
| **Experiment Orchestrator** | `live_reliability_runner.py`, `experiments.py`, `aggregate.py` | Multi-process orchestration (`exchange` + `workload_client` + `1..32` subscribers) & 5 presets |
| **Control-Room GUI** | `main_window.py`, `exchange_control_panel.py`, `reliability_dashboard.py` | Visual PyQt6 interface with 12 live/preset `pyqtgraph` charts and interactive controls |

---

## Known Limitations

1. **Centralised Matching Engine (Single Authoritative Exchange)**
   - The system provides fault-tolerant **market-data distribution** from a single central exchange; it does not implement Raft/Paxos order-book replication across multiple exchange nodes.
2. **Campus Wi-Fi Multicast Filtering**
   - Many enterprise/university Wi-Fi access points drop or rate-limit IPv4 multicast (`239.0.0.0/8`). Two-laptop `udp_lan` experiments perform best over an Ethernet switch, USB-C LAN cable, or dedicated local hotspot.
3. **In-Memory History & Snapshot Store**
   - `RecoveryHistory` and `SnapshotStore` reside in RAM for microsecond access and are bounded by `--history` and `--snapshot-retention`; they are not persisted to disk across exchange restarts.
4. **Unauthenticated SHA-256 Integrity Checkpoints**
   - Snapshot hash chains (`SnapshotHasher`) detect accidental corruption and tampering of checkpoint payloads (`--tamper-snapshot`), but do not use asymmetric digital signatures (e.g., Ed25519) or TLS client authentication.
5. **High-Percentile (`p99.9`) Sample Sufficiency**
   - Nearest-rank `p99.9` requires at least $N \ge 1000$ recovery events to be statistically distinct from `max`. The subscriber explicitly flags `insufficient_p999=1` when $N < 1000$ and `insufficient_samples=1` when $N < 30$.
6. **Tab 1 (`Market Visualisation`) Uses In-Process `OrderBook` Simulation**
   - Tab 1 (`Market Visualisation`) drives the compiled C++ `OrderBook` directly via `pybind11` at 60 FPS for responsive visual depth exploration, whereas Tabs 2 and 3 (`Exchange Control` and `Reliability Experiments`) spawn and monitor real multi-process UDP/TCP binaries.

---

## Future Enhancements

1. **Kernel-Bypass Networking (`io_uring` / DPDK / Solarflare `onload`)**
   - Replace standard POSIX `sendto`/`recvfrom` loops with batched `recvmmsg`/`sendmmsg` or `io_uring` zero-copy rings to push multicast throughput beyond 500,000 events/s.
2. **Forward Error Correction (FEC / Reed-Solomon Parity Packets)**
   - Emit periodic XOR or Reed-Solomon parity datagrams on UDP multicast so subscribers can reconstruct single-packet drops in single-feed mode without a TCP round-trip.
3. **Weighted Deficit Round-Robin (DRR) Recovery Scheduling**
   - Account for variable replay byte sizes (`event_count`) using Deficit Round-Robin quantum credits so a subscriber requesting a 500-event range cannot consume more wire time than a subscriber requesting a 2-event range.
4. **Incremental Snapshot Deltas & Disk-Backed Ring Buffer (`mmap`)**
   - Persist publisher history to a memory-mapped circular file so the recovery server can serve multi-million-event historical replays without increasing heap footprint.
5. **Authenticated Multicast & TLS Recovery Channel**
   - Add HMAC-SHA256 / Ed25519 signatures on periodic snapshots and optional TLS 1.3 encryption on the TCP order-entry and recovery ports.
6. **Live UDP Multicast Binding for Tab 1 Depth Renderer**
   - Connect the Tab 1 60 FPS depth chart directly to a background `reliable_subscriber` shared-memory book view so live external workload order flow can be watched tick-by-tick alongside the reliability charts.
