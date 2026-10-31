# Complete Viva Demo Script & Visual GUI / Dashboard Guide

This document is your single reference for demonstrating the **Reliable Market-Data Distribution for a Networked Electronic Exchange** (Group 11, CSC-303). It covers:

1. **System Verification Matrix** — what is working, how it was verified with real processes and sockets, and exact measured numbers.
2. **Visual GUI & Dashboard Guide** — what every tab, button, graph, and table does across the redesigned visual-first GUI.
3. **10-Minute Step-by-Step Viva Demo Script** — exact commands and button clicks to demonstrate every engineering claim live.

---

## 1. System Verification Matrix ("Is Everything Working?")

| Component / Feature | Status | Verified Proof & Measured Numbers |
| :--- | :--- | :--- |
| **C++23 Build (`clang++` & `g++-16`)** | Working | Clean compilation (`0` errors) on AppleClang 17 and Homebrew GCC 16 (`Tests`, `exchange`, `reliable_subscriber`, `workload_client`, `order_client`, `recovery_client`, `reliability_demo`). |
| **GoogleTest Unit Suite (`51` tests)** | Working | `51/51` unit tests pass in `0.35 s`. |
| **Fast Multicast Receiver Stop (< 200 ms)** | Working | `MulticastReceiver::stop()` wakes `stats_thread_` immediately via `stats_cv_` (`< 15 ms` instead of blocking `5000 ms`). |
| **Normative End-of-Run Reconciliation** | Working | Subscriber stops UDP receivers first, polls `STATUS` until two consecutive responses match `(latest_sequence, history_minimum_sequence, latest_book_digest)`, feeds `on_heartbeat(L)`, drains recovery, verifies bitset accounting and SHA-256 book digest, and sends `BYE`. |
| **30-Run Lossy Flake Gate** | Working | `30/30` consecutive 8-subscriber lossy runs passed in `280.56 s` (`0` integrity failures, `0` book mismatches). |
| **32-Subscriber Scalability** | Working | `32/32` concurrent `reliable_subscriber` processes against one `exchange` completed with `permanently_lost=0` and `book_matches_exchange=1` in `10.75 s`. |
| **Dual-Feed Redundancy (`A + B`)** | Working | At 10% independent loss (`seed=42`), Feed B avoids **`90.1%`** of single-feed gaps (`91/101` gaps repaired immediately via Feed B at `~0.15 ms` p50 vs `~3.15 ms` TCP recovery latency, reducing TCP recovery ranges from `88` to `8`). |
| **FCFS vs Round-Robin Scheduler Contention** | Working | Under contention (`1` noisy subscriber at 50% loss + `--recovery-concurrency 8` vs `6` quiet subscribers at 2% loss, `1` worker, `300 us/req + 20 us/ev` service delay): quiet-subscriber `p99` latency drops from **`15.46 ms` (FCFS)** to **`5.07 ms` (Round-Robin)** (**`3.05x` improvement**), while saturated-window Jain fairness improves from `0.625` to `0.946`. |
| **On-Demand Snapshot Fallback** | Working | With `--history 30` and 30% single-feed loss, `--snapshot-on-demand` completes **`20` snapshot recoveries with `0` failures** and `book_matches_exchange=1`. |
| **Cryptographic Snapshot Tamper Detection** | Working | `--tamper-snapshot book_value\|sequence\|previous_hash\|hash` mutates the outbound snapshot; subscriber's `SnapshotHasher::verify()` rejects it (`snapshot_rejected > 0`, `book_matches_exchange=0`, `run_status=failed_integrity`, exit code `2`). |

---

## 2. Visual GUI & Dashboard Guide (What Every Tab, Button, Graph, and Table Does)

Launch the GUI from the repository root:

```sh
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 src/simulation_python/main.py
```

The main window (`1680 x 1020`) has **three visual-first top-level tabs**:

---

### Top-Level Tab 1: `1. Market Visualisation (Live Order Book)`

* **3 Real-Time 60 FPS Graphs:**
  1. **Best Bid & Ask Price (USD):** Rolling 8-second green bid line and red ask line.
  2. **Order Book Depth by Price Level:** Horizontal depth bars across the top 15 bid/ask price levels.
  3. **Matched Trade Volume:** Rolling 1-second trade volume bar chart.
* **Buttons & Table (Bottom-Right):**
  * **`▲ Add Buy Pressure` (Green Button):** Injects aggressive buy orders into the AAPL order book and shifts the price/depth graphs upward.
  * **`▼ Add Sell Pressure` (Red Button):** Injects aggressive sell orders and shifts the price/depth graphs downward.
  * **Live Book Telemetry Table (`4x4`):** Displays live `Best Bid`, `Best Ask`, `Best Bid Depth`, `Best Ask Depth`, `Bid-Ask Spread`, `Resting Orders`, `Matched Units`, and `Transport`.

---

### Top-Level Tab 2: `2. Exchange Control (Live Processes & Graphs)`

Contains **four sub-tabs** and a compact bottom event log (`self.event_log`):

#### Sub-Tab 2.1: `1. Local Simulation (Live Graphs & Tables)`
* **Top Control Row:**
  * **Left (`Exchange & Recovery Server Parameters`):** Compact grid for `OE Port`, `Rec Port`, `Interface`, `History`, `Snap Intv`, `Snap Ret`, `Policy` (`fcfs` / `round-robin`), `Workers`, `Delay/Req (µs)`, `Delay/Ev (µs)`, `Tamper`, `Dual Feed (A+B)`, and `On-Demand Snapshot`.
  * **Right (`Workload Parameters & Process Controls`):** `Clients`, `Orders/s`, `Duration (s)`, `Symbol` (`AAPL`, `GOOGL`, `AMZN`, `NFLX`, `META`), plus 5 color-coded action buttons:
    * **`1. Start Exchange` (Green):** Starts `exchange --mode client` and waits for `Ready`.
    * **`2. Start Subscribers` (Blue):** Spawns the configured `reliable_subscriber` processes (1–32).
    * **`3. Start Workload` (Purple):** Spawns crossing buy/sell `workload_client` processes.
    * **`Reconcile & Stop Subs` (Amber):** Sends `SIGINT` to subscribers so they run final `STATUS` + SHA-256 book digest reconciliation.
    * **`Stop All` (Red):** Stops all child processes.
* **Middle Visual Charts Row (2 Live `pyqtgraph` Charts):**
  1. **`Live Subscriber Delivered vs Recovered Events`:** Grouped green (`Delivered`) and blue (`Recovered`) bars per subscriber that update live every 500 ms as `SUBSCRIBER_STATS` and `RELIABLE_SUBSCRIBER_RESULT` stream in.
  2. **`Exchange Sequence Progress / Workload ACK RTT (µs)`:** Plots live `latest_seq` progression at 1 Hz while running, and switches to grouped `p50 / p95 / p99 (µs)` ACK RTT bars when `workload_client` processes finish.
* **Bottom Structured Tables:**
  * **`Market-Data Subscribers Configuration Table`:** Editable per-subscriber rows (`ID`, `Feeds (a/ab)`, `Loss Model`, `Drop A`, `Drop B`, `Corr Loss`, `Seed`, `Concurrency`, `Role`).
  * **`Live Subscriber Status & Reconciliation Table`:** Live per-subscriber counters and final `Book Match` (`MATCH (1)` in green or `MISMATCH` in red).
  * **`Order-Entry Workload Clients Table`:** Live client connection state and measured ACK RTT percentiles.

#### Sub-Tab 2.2: `2. Manual Order Testing (RTT Graph & Table)`
* **Left:** Form to submit a single binary TCP limit order (`Server`, `TCP port`, `Instrument`, `Side`, `Limit price`, `Quantity`) via **`▶ Send Order Over TCP`**.
* **Right:**
  * **`Manual Order TCP Round-Trip Time (ms)` Graph:** Adds a new bar for every manual order submitted so you can visually compare RTTs across orders.
  * **`Submission History Table`:** Logs every manual order (`Order #`, `Symbol`, `Side`, `Price`, `Qty`, `Response & RTT`).

#### Sub-Tab 2.3: `3. Distributed LAN Setup`
* Auto-detects local IPv4 interfaces, provides **`▶ Start Exchange on LAN Interface`** and **`🔌 Test TCP Reachability`**, generates copyable `exchange`, `workload_client`, and `reliable_subscriber` commands, and displays the **`Live Connected Remote Clients & Subscribers Table`**.

#### Sub-Tab 2.4: `4. Saved Results Explorer (Table & Graph)`
* **`🔄 Refresh Saved Results`** and **`📊 Open Selected in 6-Graph Reliability Dashboard`**.
* Selecting any saved run directory, CSV, or JSON immediately populates:
  1. **`Selected Result Visual Preview` Bar Graph** (top-right).
  2. **`Full Structured CSV/JSON Data Table`** (middle).
  3. Compact raw file preview (bottom).

---

### Top-Level Tab 3: `3. Reliability Experiments (6-Graph Dashboard)`

Everything is on a **single unified visual page** (no tab-switching needed to see controls, graphs, and tables together):

1. **Top Interactive Control & Action Bar:**
   * **Row 1 (`Experiment Parameters & One-Click Actions`):** `Execution` (`In-process`, `Live UDP/TCP loopback`, `Live UDP/TCP LAN`), `Feed Mode` (`Single`, `Dual — independent`, `Dual — correlated`), `Drop / Loss Rate`, `Subscribers (1–32)`, `Live Duration`, `History Window`, `Fault Seed`, `In-Process Events`, `Interface IPv4`.
   * **Row 2 (`Snapshot, Scheduler & Tamper Controls`):** Compact 3-box strip for `Periodic Checkpoints`, `On-Demand Fallback`, `Interval`, `Retention`, `Policy` (`FCFS` / `Round Robin`), `Workers`, `Delay/Req (µs)`, `Delay/Ev (µs)`, and `Tamper Mode` (`none`, `book_value`, `sequence`, `previous_hash`, `hash`).
   * **Action Buttons:**
     * **`▶ Run Live Experiment (Updates All 6 Graphs)` (Green):** Runs `live_reliability_runner.py` and updates all 6 graphs and both tables from the live run.
     * **`⚡ Run In-Process Baseline` (Blue):** Runs `reliability_demo` (< 0.5 s) and updates all 6 graphs and both tables.
     * **`📊 Run Preset Sweep` (Purple):** Runs `experiments.py` for the selected preset (`Redundancy Sweep`, `Scheduler Contention`, `History & Snapshot Sweep`, `Snapshot Tamper`, `Scalability`) and updates all 6 sweep graphs.
     * **`🔄 Load Preset Sweep Graphs (All 6)`:** Instantly reloads the 5 aggregated preset sweep CSVs from `results/` into all 6 graphs.
     * **`📂 Open Run JSON / CSV…`**, **`🖼 Export 6 Graphs PNG`**, **`💾 Export Active CSV`**, **`■ Stop`**.
2. **Center `2x3` Visual Graphs Grid (All 6 Graphs Pre-Loaded on Startup & Updated on Every Run):**
   * **Graph 1:** `1. Events Needing Recovery (%) vs Loss` (Sweep curves + $p, p^2$ theory) OR `1. Loss & Gap Repair Breakdown` (Single Run bars: `Feed A Drops`, `Feed B Drops`, `Avoided by Feed B`, `TCP Recovered`, `Permanently Lost`).
   * **Graph 2:** `2. Repair Latency by Class (µs)` (`tcp_rec p50/p95/p99`, `other_feed p50`, `snapshot p50`, `tcp_rtt p50`) OR `TCP Recovery Latency p99 vs N Subscribers` (Scalability Sweep).
   * **Graph 3:** `3. FCFS vs RR Queue Wait p99` (Contention Sweep showing `3.05x` lower quiet wait under RR, or Single Run per-subscriber Queue Wait / Requests Served).
   * **Graph 4:** `4. Sequence Delivery Sources & Snapshot Fallback` (Single Run `Via Feed A`, `Via Feed B`, `Via TCP Replay`, `Snapshot Covered`, `TOO_OLD`, `Snapshots Rejected`, or Sweep `Snapshot Recoveries vs History/Interval`).
   * **Graph 5:** `5. Redundancy Effectiveness & Share (%)` (Single Run `Feed A/B/Recovery Share %` + `Redundancy Eff. %`, or Sweep `Effectiveness vs Loss Rate`).
   * **Graph 6:** `6. Delivered Events per Subscriber` (Bars colored **green** when `book_matches_exchange=1` and **red** on mismatch/tamper).
3. **Bottom Structured Tables + Compact Event Log:**
   * **Left Table (`Per-Subscriber Accounting, Latency & Book Match`):** 12-column table showing every subscriber's exact sequence decomposition (`Expected`, `Delivered`, `Via A`, `Via B`, `Via TCP`, `Snap Cov`, `Lost`, `Redund. Eff`, `TCP p50/p99`, `Book Match`).
   * **Right Table (`System & Scheduler KPIs`):** `Transport`, `Loss Injection`, `Throughput`, `Duplicates`, `Gaps / TCP Ranges`, `Other-Feed Latency`, `Scheduler Policy`, `Queue Wait`, `Saturated Jain Fairness`, `Snapshots Retained / On-Demand`, `TOO_OLD / Applied`, `Authoritative Book Digest Match`.
   * **Bottom Log (`self.log`):** Compact 100px process log.

---

## 3. Quick 5-Minute Viva Demo Flow in the GUI

1. **Open the GUI:**
   ```sh
   /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 src/simulation_python/main.py
   ```
2. **Tab 1 (`1. Market Visualisation`):** Click **`▲ Add Buy Pressure`** and **`▼ Add Sell Pressure`** to show the 3 live 60 FPS order-book graphs and the `4x4` telemetry table updating in real time.
3. **Tab 3 (`3. Reliability Experiments`):**
   * Point out that **all 6 preset sweep graphs are already loaded on screen** (`Recovery vs Loss`, `Latency vs N Subscribers`, `FCFS vs RR Queue Wait`, `Snapshot Fallback`, `Redundancy Effectiveness`, `Delivered Events`).
   * Change `Drop / Loss Rate` to `0.15`, set `Feed Mode` to `Dual feed — independent loss (A+B)`, and click **`▶ Run Live Experiment (Updates All 6 Graphs)`** (or **`⚡ Run In-Process Baseline`** for an instant `< 0.5 s` update). Watch all 6 graphs and the 2 tables below update immediately with a green `PASSED` banner.
   * Change `Tamper Mode` to `book_value`, set `History Window` to `10`, and click **`▶ Run Live Experiment`**: watch the banner and Graph 6 turn **red** (`EXPECTED FAILURE OBSERVED`, `snapshot_rejected > 0`, `Book Match = FAIL (0)`).
   * Click **`🔄 Load Preset Sweep Graphs (All 6)`** to restore the 6 multi-run sweep charts at any time.
4. **Tab 2 (`2. Exchange Control`):**
   * On `1. Local Simulation`, click **`1. Start Exchange`**, **`2. Start Subscribers`**, and **`3. Start Workload`**: watch the 2 live graphs and the subscriber/workload tables populate in real time.
   * Switch to `2. Manual Order Testing` and click **`▶ Send Order Over TCP`** 3 times to watch the Manual Order RTT Bar Graph and History Table grow live.
