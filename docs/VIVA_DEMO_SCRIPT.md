# Complete Viva Demo Script & GUI / Dashboard Reference Guide

This document is your single reference for demonstrating the **Reliable Market-Data Distribution for a Networked Electronic Exchange** (Group 11, CSC-303) live during the viva. It covers:

1. **System Verification Matrix** — what is working, how it was verified with real processes and sockets, and exact measured numbers.
2. **Complete GUI & Dashboard Reference** — how the GUI works under the hood, what every tab, panel, table, and button does, and how live telemetry flows from the C++ processes into the charts.
3. **10-Minute Step-by-Step Viva Demo Script** — exact commands to copy-paste and exact GUI buttons to click to prove all six engineering claims live.

---

## 1. System Verification Matrix ("Is Everything Working?")

Every component below has been built and executed on this machine (`macOS`, AppleClang 17 + Homebrew GCC 16, real UDP multicast on `239.1.1.1`/`239.1.1.2` over `lo0` and real TCP sockets on `127.0.0.1`).

| Component / Feature | Status | Verified Proof & Measured Numbers |
| :--- | :--- | :--- |
| **C++23 Build (Clang & GCC)** | Working | Clean compilation (`0` warnings/errors) on both `/usr/bin/clang++` and `/opt/homebrew/bin/g++-16` with `Tests`, `exchange`, `reliable_subscriber`, `workload_client`, `order_client`, `recovery_client`, `reliability_demo`. |
| **GoogleTest Unit Suite (51 tests)** | Working | `51/51` unit tests pass in `0.35 s` (`OrderBookTests`, `MatchingEngineTests`, `SequenceTrackerTests`, `DualFeedSubscriberTests`, `RecoveryHistoryTests`, `SnapshotManagerTests`, `RecoverySchedulerTests`, `FaultInjectorTests`, `RecoveryWireV2Tests`, `ImpairmentStageTests`). |
| **Fast Multicast Receiver Stop (< 200 ms)** | Working | `MulticastReceiver::stop()` wakes the stats thread immediately via `stats_cv_` (`stop()` completes in `< 15 ms` instead of blocking for `5000 ms`). |
| **Normative End-of-Run Reconciliation** | Working | Subscriber stops UDP receivers first, polls `STATUS` until two consecutive responses return identical `(latest_sequence, history_minimum_sequence, latest_book_digest)`, feeds `on_heartbeat(L)`, drains TCP recovery, verifies bitset accounting and SHA-256 book digest, and sends `BYE`. Zero cases of `delivered > exchange_latest_sequence`. |
| **Bitset Sequence Accounting & Book Digest** | Working | Every sequence `1..L` is tracked in `arrived_a_`, `arrived_b_`, `delivered_source_`, and `snapshot_covered_`. `BookBuilder::digest()` computes a canonical SHA-256 over all non-zero price levels and matches `exchange_book_digest` (`book_matches_exchange=1`). |
| **30-Run Lossy Flake Gate** | Working | `30/30` consecutive 8-subscriber lossy runs passed in `280.56 s` (`0` integrity failures, `0` book mismatches). |
| **32-Subscriber Scalability** | Working | `32/32` concurrent `reliable_subscriber` processes against one `exchange` completed with `permanently_lost=0` and `book_matches_exchange=1` in `10.75 s`. |
| **Dual-Feed Redundancy (A + B)** | Working | At 10% independent loss (`seed=42`), single-feed triggers `88` gap detections (`100` missing events), whereas dual-feed (`A+B`) reduces TCP recovery ranges to `8` (`90.1%` of single-feed gaps avoided immediately by the second feed with `0.15 ms` p50 latency vs `3.15 ms` TCP recovery latency). |
| **FCFS vs Round-Robin Scheduler Contention** | Working | Under genuine contention (`1` noisy subscriber at 50% loss + `--recovery-concurrency 8` vs `6` quiet subscribers at 2% loss, `1` worker, `300 us/req + 20 us/ev` service delay): quiet-subscriber `p99` recovery latency drops from **`15.46 ms` (FCFS)** to **`5.07 ms` (Round-Robin)** — a **`3.05x` tail-latency improvement**, while saturated-window Jain fairness improves from `0.625` to `0.946`. |
| **On-Demand Snapshot Fallback** | Working | When `--history 30` is smaller than a burst gap, `TOO_OLD` triggers `SNAPSHOT_REQUEST`; with `--snapshot-on-demand` enabled on the exchange, `20` snapshot recoveries succeed with `0` failures (`recovery_failures=0`, `book_matches_exchange=1`). |
| **Cryptographic Snapshot Tamper Detection** | Working | `--tamper-snapshot book_value\|sequence\|previous_hash\|hash` mutates the outbound snapshot; the subscriber's `SnapshotHasher::verify()` rejects it (`snapshot_rejected=1`, `book_matches_exchange=0`, `run_status=failed_integrity`, exit code `2`). |
| **PyQt6 Control-Room GUI & Offscreen Smoke Test** | Working | `tests/gui_smoke_test.py` (`QT_QPA_PLATFORM=offscreen`) launches `MainWindow`, verifies all 3 top-level tabs and all 8 `ReliabilityDashboard` sub-tabs, loads both single-run JSON and preset suite directories, and verifies non-empty `pyqtgraph` plots (`PASSED` in `5.02 s`). |

---

## 2. Complete GUI & Dashboard Guide (How Everything Works & What Every Button Does)

### Launching the GUI

From the repository root, launch the GUI using the framework Python 3.14 interpreter (which has `PyQt6`, `pyqtgraph`, and the compiled `orderbook` C++ extension):

```sh
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 src/simulation_python/main.py
```

The main window (`MainWindow` in `src/simulation_python/gui/main_window.py`) has **three top-level tabs**:

---

### Top-Level Tab 1: `Market Visualisation (local simulator)`

* **How it works under the hood:** Runs an in-process Python thread (`MarketSimulator`) against the pybind11 C++ `orderbook.OrderBook` (`AAPL`, Security ID 1) at 60 FPS. It does **not** open UDP or TCP sockets, and its status box explicitly states: `Transport: none — not connected to the TCP exchange or UDP feed`.
* **Charts shown:**
  1. **Price (USD):** Live Best Bid (green) and Best Ask (red) over a rolling 8-second window.
  2. **Order Book Depth:** Horizontal bar chart of the top 15 bid and ask price levels.
  3. **Volume:** Rolling 1-second matched trade volume bars.
* **Buttons:**
  * **`Add Buy Pressure`**: Injects a burst of aggressive buy orders into the local in-process AAPL order book so you can watch the spread tighten and best bid/ask step upward.
  * **`Add Sell Pressure`**: Injects a burst of aggressive sell orders so you can watch the price levels move downward.

---

### Top-Level Tab 2: `Exchange Control` (`src/simulation_python/gui/exchange_control_panel.py`)

This tab controls real child processes (`exchange`, `workload_client`, and `reliable_subscriber`) via `ExchangeController` (`src/simulation_python/gui/exchange_controller.py`). It has a persistent top status bar (`Exchange status`, `Endpoint`, `Recovery`, `Market-Data Feeds`, `Workload status`, `Connected clients`, `Active subscribers`) and **four sub-tabs**:

#### Sub-Tab 2.1: `Local Simulation`
* **Controls & Spinboxes:**
  * **`Order-entry port`** (`1024–65535`, default `8080`): TCP port for `exchange --mode client --oe-port`.
  * **`Feed mode`**: `Dual feed (A + B)` or `Single feed (A)`.
  * **`Recovery TCP port`** (default `9090`): TCP port for the recovery server (`--recovery-port`).
  * **`Recovery policy`**: `round-robin` or `fcfs`.
  * **`History capacity`** (default `65536`): Circular buffer size (`--history`) for retained UDP events.
  * **`Snapshot interval`** (default `1000`): Events between periodic SHA-256 checkpoints (`--snapshot-interval`).
  * **`Feed A loss`** / **`Feed B loss`**: Publisher-side deterministic application drop rates (`0.00–1.00`).
  * **`Client processes`** (`1–8`, default `2`): Number of `workload_client` processes to spawn (when `>= 2`, automatically pairs buy and sell clients so orders cross and emit market data).
  * **`Order rate / client`** (`1–5000 orders/s`, default `500`).
  * **`Duration`** (`1–300 s`, default `10 s`).
  * **`Live subscribers`** (`1–16`, default `2`): Number of `reliable_subscriber` processes to attach.
  * **`Sub receive loss`** (`0.00–1.00`, default `0.05`): Receive-side per-subscriber drop rate.
* **Buttons & What They Do:**
  * **`Start exchange`**: Launches `./build-local/src/exchange --mode client` with `--dual-feed`, `--snapshot-on-demand`, and the configured ports/history. Waits for `ORDER_ENTRY_READY` and `RECOVERY_SERVER_READY` before turning the status indicator green (**Ready**).
  * **`Start workload`**: Spawns the configured `workload_client` processes. When each client completes its `CLIENT_HELLO` / `SERVER_WELCOME` handshake, the exchange emits `CLIENT_CONNECTED` and the **Connected Workload Clients** table updates to `Connected — workload active`, then `Completed — result recorded` with measured `ack_p50_us` and `ack_p99_us`.
  * **`Start live subscribers`**: Spawns `reliable_subscriber` processes via `live_reliability_runner.py` against the running exchange (or as a self-contained live run) and streams periodic `SUBSCRIBER_STATS` lines into the **Connected Market-Data Subscribers** table (`Phase`, `Last Seq`, `Delivered`, `Duplicates`, `Gap Ranges`, `Recovered`, `Snapshots`, `Lost`, `Book Match`, `Feed A/B health`).
  * **`Stop / clean up`**: Sends `SIGTERM` to all running child processes (`workload_client`, `reliable_subscriber`, `exchange`) and resets status safely.
  * **`Open latest in Reliability Dashboard`**: Switches the main window directly to Tab 3 (`Reliability Experiments`) and loads the just-completed `live-reliability-summary.json` into all 7 panels and 6 charts.
* **Live Telemetry Boxes on Sub-Tab 2.1:**
  * **Exchange Live Stats (`1 Hz EXCHANGE_STATS`)**: Shows live `Seq`, `Pub A`, `Pub B`, `History [min..max]`, `Snap seq + hash prefix`, `Clients`, `Subs`, `Rec Reqs`, `Queue Depth`.
  * **Connected Workload Clients Table**: Shows each `workload_client` PID, side, sent/accepted/rejected counts, throughput, and ACK RTT (`p50`/`p99`).
  * **Connected Market-Data Subscribers Table**: Live per-subscriber row showing `Running`/`Reconciling`/`completed`, `Last Seq`, `Delivered`, `Duplicates`, `Gap Ranges`, `Recovered`, `Snapshots`, `Lost`, `Book Match` (`MATCH` in green or `MISMATCH` in red), and `Feed A/B` status (`ok` vs `stale`).

#### Sub-Tab 2.2: `Manual Testing`
* **How it works:** Opens a real TCP socket from the GUI (`gui/network_protocol.py`), performs the binary `CLIENT_HELLO` / `SERVER_WELCOME` handshake, sends a single packed `NEW_ORDER`, `MODIFY_ORDER`, or `CANCEL_ORDER` frame, and measures the round-trip time in microseconds.
* **Buttons:**
  * **`Submit new limit order`**: Sends a binary limit order (`Symbol`, `Side`, `Price $`, `Quantity`, `Order ID`) and displays the returned `ORDER_ACK` or `ORDER_REJECT` frame and RTT.
  * **`Send modify order`**: Modifies the price/quantity of an existing resting order ID.
  * **`Send cancel order`**: Cancels a resting order ID on the exchange.

#### Sub-Tab 2.3: `Distributed Network`
* **How it works:** Generates ready-to-copy command lines (`exchange`, `workload_client`, and `reliable_subscriber`) pre-filled with your LAN IPv4 address and ports for running across two laptops on a Wi-Fi/Ethernet LAN.

#### Sub-Tab 2.4: `Previous Results`
* **Buttons:**
  * **`Refresh files`**: Scans `results/` for saved `workload-*.csv`, `reliability-*.csv`, and `live-reliability-summary.json` files.
  * **`Load selected file`**: Displays the selected CSV/JSON in the preview table without fabricating any numbers.

---

### Top-Level Tab 3: `Reliability Experiments` (`src/simulation_python/gui/reliability_dashboard.py`)

This is the main **Reliability Control Room**. It is driven by `ReliabilityExperimentController` (`src/simulation_python/gui/reliability_controller.py`) and contains **8 sub-tabs**:

#### Sub-Tab 3.1: `Configure & Run` (`gui/experiment_panel.py`)
* **Controls:**
  * **`Execution mode`**:
    * `Live UDP/TCP (multi-process)` — runs real `exchange` + `workload_client` + `reliable_subscriber` over UDP multicast and TCP recovery via `live_reliability_runner.py`.
    * `In-process demo (deterministic)` — runs the single-process `reliability_demo` binary.
  * **`Preset`**:
    * `Custom configuration` — unlocks all spinboxes for manual tuning.
    * `Redundancy sweep (single vs dual A+B across loss rates)` — auto-fills parameters and runs `experiments.py --preset redundancy_sweep --size quick`.
    * `Scheduler contention (FCFS vs Round-Robin, 1 noisy + 6 quiet)` — runs `experiments.py --preset scheduler_contention --size quick`.
    * `History & snapshot fallback sweep` — runs `experiments.py --preset history_snapshot_sweep --size quick`.
    * `Snapshot tamper detection (5 tamper modes)` — runs `experiments.py --preset snapshot_tamper --size quick`.
    * `Subscriber scalability (1, 2, 4, 8, 16 subscribers)` — runs `experiments.py --preset scalability --size quick`.
  * **Feed & Receive-Side Fault Injection Controls:** `Feed mode` (`dual`/`single`), `Loss model` (`independent`/`correlated`/`none`), `Feed A drop rate`, `Feed B drop rate`, `Correlated loss`, `Duplication rate (--dup)`, `Reorder rate (--reorder)`, `Reorder depth (--reorder-depth)`, `Hold-back delay (--delay-ms)`, `Fault seed`, `Gap grace (--gap-grace-ms)`.
  * **Workload & Recovery Server Controls:** `Subscribers` (`1–32`), `Workload clients` (`1–8`), `Order rate / client`, `Duration (s)`, `History capacity`, `Snapshot interval`, `Snapshot retention`, `On-demand snapshot` checkbox, `Snapshot tamper` (`none`, `book_value`, `sequence`, `previous_hash`, `hash`), `Recovery policy` (`round-robin` / `fcfs`), `Recovery workers`, `Subscriber concurrency (--recovery-concurrency)`, `Service delay / req (us)`, `Service delay / event (us)`.
* **Buttons:**
  * **`Run Live Experiment`**: Launches `live_reliability_runner.py` with the configured parameters, streams real-time `EXCHANGE_STATS` and `SUBSCRIBER_STATS` into the **Live Subscriber Telemetry** table at the bottom of the panel, and automatically switches to **Results & Provenance** when the run finishes.
  * **`Run Selected Preset Suite`**: Launches `src/reliability_runner/experiments.py --preset <name> --size quick`, runs all points in the preset sweep, aggregates the CSVs, and populates the multi-run comparison charts (`Chart 1`, `Chart 4`, `Chart 5`) automatically.
  * **`Stop Active Run`**: Terminates any in-flight live run or preset suite cleanly.
  * **`Load Saved Run JSON…`**: Opens a file/folder picker so you can load either a single `live-reliability-summary.json` file or an entire preset directory (`results/live/redundancy_sweep`, `results/live/scheduler_contention`, etc.).
  * **`Run In-Process Demo`**: Runs `./build-local/src/reliability_demo` for a fast (< 1 s) deterministic check.

#### Sub-Tab 3.2: `Results & Provenance` (`gui/metrics_panel.py`)
* **Provenance Banner (top):** Always states the exact `transport` (`udp_loopback` | `udp_lan` | `inprocess`), `loss_injection` (`app_level_receive_side` | `app_level_publisher_side` | `none`), `recovery_policy`, worker count, and modelled service delay (`us/req`, `us/event`).
* **Integrity Status Banner:** Turns **GREEN** when all subscribers report `run_status=completed`, `permanently_lost=0`, `accounting_valid=1`, and `book_matches_exchange=1`. Turns **RED** immediately if any gap is unresolved, any snapshot hash is rejected, or any book digest mismatches.
* **16 Summary Cards:** `Expected Events`, `Delivered Events`, `Via Feed A`, `Via Feed B`, `Via TCP Recovery`, `Snapshot Covered`, `Permanently Lost`, `Duplicates Suppressed`, `Gap Detections`, `TCP Recovery Requests`, `Snapshot Recoveries`, `Snapshots Rejected`, `Redundancy Effectiveness`, `Book Matches Exchange`, `TCP Recovery p99`, `Other-Feed p99`.
* **Per-Subscriber Accounting & Book Digest Table:** One row per subscriber showing exact sequence decomposition and the 16-hex-char prefix of the final SHA-256 order-book digest.
* **Per-Missing-Event Latency Breakdown by Class Table:** Shows `Sample Count (n)`, `Insufficient (<30)`, `p50`, `p95`, `p99`, `p99.9`, `Max`, and `Mean` (in milliseconds) for all four latency classes (`other_feed`, `tcp_recovery`, `snapshot`, `tcp_request_rtt`).

#### Sub-Tab 3.3: `Redundancy` (`gui/redundancy_panel.py`)
* **Top Counter Box:** `Potential single-feed gaps (missing on A)`, `Gaps avoided by Feed B (arrived on B)`, `Redundancy effectiveness`, and `Remaining TCP recovery requests`.
* **Chart 2 (Left): `Dual-Feed Redundancy Breakdown`:** Bar chart comparing `Missing on Feed A`, `Avoided by Feed B`, `TCP Recovered`, `Snapshot Covered`, and `Permanently Lost`.
* **Chart 1 (Right): `Sweep: TCP Gap Ranges & Avoided Gaps vs Loss Rate`:** Populated when you run or load the `redundancy_sweep` preset; plots remaining TCP recovery ranges vs loss rate for Single Feed, Dual Feed (Independent), and Dual Feed (Correlated).

#### Sub-Tab 3.4: `Recovery & Latency` (`gui/recovery_panel.py`)
* **Chart 3 (Left): `Per-Missing-Event Latency Percentiles by Class (ms)`:** Grouped bar chart of `p50`, `p95`, `p99`, `p99.9`, and `max` across `other_feed`, `tcp_recovery`, `snapshot`, and `tcp_request_rtt`.
* **Chart 6 (Right): `Empirical CDF of Per-Missing-Event Repair Latency`:** Plots the exact empirical cumulative distribution function $F(x)$ from the raw `subscriber-*.latency.csv` samples, separated by class (`other_feed` in green, `tcp_recovery` in blue, `snapshot` in orange), showing the 1–2 order-of-magnitude gap between immediate Feed B repair (~`0.15 ms`) and TCP replay (~`3–5 ms`).

#### Sub-Tab 3.5: `Snapshot Fallback` (`gui/snapshot_panel.py`)
* **Top Counter Box:** `TOO_OLD responses`, `Snapshot recoveries`, `Snapshots rejected (hash mismatch)`, `Events covered by snapshot`, `Exchange snapshots created`, `On-demand snapshots created`, `Retained checkpoints`, and `Latest checkpoint hash`.
* **Chart 5: `Sequence Accounting & Snapshot Fallback Breakdown`:** Stacked bar chart showing `Delivered via UDP (A+B)`, `Recovered via TCP Replay`, `Covered by Snapshot`, and `Permanently Lost` across history sizes (for `history_snapshot_sweep`) or per subscriber (for a single run).

#### Sub-Tab 3.6: `Scheduler Fairness` (`gui/fairness_panel.py`)
* **Top Counter Box:** `Scheduler policy`, `Recovery workers`, `Modelled service delay`, `Requests scheduled`, `Completed responses`, `Avg / Max queue wait (us)`, `Saturated-window Jain fairness (queue depth >= 2)`, and `Overall served-count Jain (demand-skewed)`.
* **Per-Subscriber Queue Wait & Service Distribution Table:** Lists every subscriber (`noisy` vs `quiet-1..6`), its served count, queue wait `p50`/`p95`/`p99`/`max`, and subscriber-side TCP recovery `p95`/`p99`.
* **Chart 4: `Scheduler Queue-Wait / Recovery Tail Latency (p95 / p99)`:** Compares Quiet-subscriber `p99` and `p95` vs Noisy-subscriber `p99` under **FCFS** vs **Round-Robin**.

#### Sub-Tab 3.7: `History & Integrity` (`gui/history_integrity_panel.py`)
* **Sequence Accounting Invariant Table:** Verifies `events_expected == delivered_via_feed_a + delivered_via_feed_b + delivered_via_recovery + events_covered_by_snapshot + permanently_lost` for every subscriber (`accounting_valid=1`).
* **Cryptographic Book Digest & Deterministic Drop Verification Table:** Displays the full 64-hex-character SHA-256 `Local Book Digest`, `Exchange Book Digest`, `Match` (`MATCH` / `MISMATCH`), and `dropped_seq_hash_a` / `dropped_seq_hash_b` (proving identical drop patterns when comparing policies with the same `--drop-seed`).

#### Sub-Tab 3.8: `Process Log`
* Live scrolling console showing exact command lines, `EXCHANGE_STATS`, `SUBSCRIBER_STATS`, `RELIABLE_SUBSCRIBER_RESULT`, and `EXCHANGE_RESULT`.

---

## 3. The 10-Minute Viva Demo Script (Step-by-Step)

### Step 0: Build & Run the Automated Test Suite (1 minute)

Show the examiner that all 51 unit tests and end-to-end tests pass:

```sh
./build-local/tests/Tests
```

Expected output: `[  PASSED  ] 51 tests.` in `< 0.5 s`.

---

### Step 1: Prove Live End-to-End Accounting, Dual-Feed Redundancy & Book Digest Verification (2 minutes)

Run the reproduction script that compares single-feed loss, dual-feed independent loss, dual-feed correlated loss, and the `--no-recovery` negative control with the exact same seed (`42`):

```sh
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/repro/run_repro.py --build-dir build-local
```

**What to point out in the terminal table:**
1. **`single` (`drop_a=0.10`)**: `gap_detections=88`, `gap_ranges_requested=88`, `via_rec=100`, `permanently_lost=0`, `book_match=1`.
2. **`indep` (`drop_a=0.10, drop_b=0.10`)**: `potential_single_feed_gaps=101`, `gaps_avoided_by_second_feed=91` (**`90.1%` redundancy effectiveness**), `gap_ranges_requested=8` (91% fewer TCP recovery requests!), `permanently_lost=0`, `book_match=1`.
3. **`corr` (`corr_loss=0.10`)**: `gaps_avoided_by_second_feed=0` (`redundancy_effectiveness=0.0`), because correlated loss drops the same sequence on both feeds, proving why independent paths matter.
4. **`no-rec` (`--no-recovery` negative control)**: `permanently_lost=100`, `book_match=0`, `status=failed_integrity`, exit code `2` — proving that without recovery, gaps cause a loud failure and a mismatched order book digest.

---

### Step 2: Prove Round-Robin Beats FCFS Under Contention (2 minutes)

Run the scheduler contention test (`1` noisy subscriber with 50% loss and `--recovery-concurrency 8` + `6` quiet subscribers with 2% loss, `1` recovery worker, `300 us/req + 20 us/ev` service delay):

```sh
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/live_reliability_e2e.py --build-dir build-local --slow
```

**What to point out in the output:**
* Under **FCFS**, the noisy subscriber queues 8 concurrent requests at a time and blocks the 6 quiet subscribers behind bursts: `fcfs_quiet_p99_ms = 15.46 ms`, saturated-window Jain index `= 0.625`.
* Under **Round-Robin**, the scheduler alternates per subscriber queue: `rr_quiet_p99_ms = 5.07 ms` (**`3.05x` lower p99 latency for quiet subscribers!**), saturated-window Jain index `= 0.946`.
* Both runs finish with `permanently_lost=0` and `book_matches_exchange=1` across all 7 subscribers.

---

### Step 3: Prove Snapshot Fallback & Cryptographic Tamper Rejection (2 minutes)

Run a high-loss subscriber against an exchange with tiny history (`--history 30`) and `--snapshot-on-demand`:

```sh
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 src/reliability_runner/live_reliability_runner.py \
  --build-dir build-local --duration 4 --clients 2 --rate 800 \
  --history 30 --snapshot-interval 100 --snapshot-on-demand \
  --subscriber id=sub-snap,feeds=a,loss_model=independent,drop_a=0.30,seed=9
```

* Point out `too_old_responses > 0`, `snapshot_recoveries > 0`, `recovery_failures = 0`, `book_matches_exchange = 1`, `run_status = completed`.

Now run the exact same command with `--tamper-snapshot book_value`:

```sh
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 src/reliability_runner/live_reliability_runner.py \
  --build-dir build-local --duration 4 --clients 2 --rate 800 \
  --history 30 --snapshot-interval 100 --snapshot-on-demand \
  --tamper-snapshot book_value --expect-failure \
  --subscriber id=sub-tamper,feeds=a,loss_model=independent,drop_a=0.30,seed=9
```

* Point out `SNAPSHOT REJECTED (hash mismatch)`, `snapshot_rejected = 1`, `book_matches_exchange = 0`, `run_status = failed_integrity`, and `EXPECTED_FAILURE_OBSERVED`.

---

### Step 4: Live GUI Walkthrough (3 minutes)

Launch the GUI:

```sh
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 src/simulation_python/main.py
```

1. **Tab 1 (`Market Visualisation`):** Click **`Add Buy Pressure`** and **`Add Sell Pressure`** to show the 60 FPS in-process order book and depth chart. Point out the explicit provenance box (`Transport: none`).
2. **Tab 2 (`Exchange Control`):**
   * Click **`Start exchange`** -> wait 0.5 s for **Ready** (green).
   * Click **`Start workload`** -> watch the **Connected Workload Clients** table populate live (`Connected — workload active` -> `Completed — result recorded`) and watch the **Exchange Live Stats** box tick at 1 Hz.
   * Switch to **`Manual Testing`** -> click **`Submit new limit order`** -> show the real TCP `ORDER_ACK` and microsecond RTT.
3. **Tab 3 (`Reliability Experiments`):**
   * On **`Configure & Run`**, keep `Execution mode = Live UDP/TCP (multi-process)` and click **`Run Live Experiment`**.
   * Watch the **Live Subscriber Telemetry** table stream `SUBSCRIBER_STATS` every 500 ms (`running` -> `reconciling` -> `done`).
   * When it auto-switches to **`Results & Provenance`**, point out the green `INTEGRITY PASSED` banner, the 16 KPI cards, the `Per-Subscriber Accounting & Book Digest` table, and the `Latency Breakdown by Class` table.
   * Click through **`Redundancy`** (Chart 2), **`Recovery & Latency`** (Chart 3 percentiles + Chart 6 empirical CDF), **`Snapshot Fallback`** (Chart 5), **`Scheduler Fairness`** (Chart 4), and **`History & Integrity`** (full 64-hex SHA-256 book digests and drop-sequence hashes).
   * Finally, click **`Load Saved Run JSON…`** and select `/tmp/reliable-round2-presets/scheduler_contention` (or run **`Run Selected Preset Suite`** with `Scheduler contention`) to show the side-by-side FCFS vs Round-Robin chart in **`Scheduler Fairness`**.
