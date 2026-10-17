# Viva Codebase Guide: Reliable Market-Data Distribution for a Networked Electronic Exchange

**Purpose:** This is an honest explanation of what the current source code does.
It is based on source inspection, not on file names, the design document, or
README claims. Paths below are relative to the repository root.

## 1. One-minute honest project description

This project contains three related parts:

1. A **centralised electronic exchange**: a C++ limit order book, matching
   engine, TCP order entry, and one UDP multicast market-data publisher.
2. A **local reliability demonstration**: an in-process simulation of two feeds,
   packet loss/duplication, sequence tracking, duplicate suppression, bounded
   replay recovery, snapshot checkpoint fallback, and CSV metrics.
3. A **real TCP workload path**: the exchange and each `workload_client` run
   in separate processes, use TCP sockets, complete an identity handshake, and
   report client-measured acknowledgement RTTs.

The important honest sentence is:

> The reliability logic works in `reliability_demo` as an in-process,
> deterministic demonstration. It is not yet connected to two real UDP feeds,
> a real TCP recovery socket, the live exchange publisher, or the GUI.

The exchange has one authoritative order book per symbol. It does **not** have
distributed consensus, replica order books, or distributed state replication.

## 2. Architecture and execution flow

### 2.1 Existing exchange path

```text
Interactive or workload order-entry client
        |
        | TCP binary order message
        v
OrderEntryServer -> bounded multi-producer order queue -> Exchange::client_order_loop
                                                   |
                                                   v
                                            OrderBook::AddOrder
                                                   |
                                                   v
                                           MDAdapter notification
                                                   |
                                                   v
                                      MarketDataPublisher ring buffer
                                                   |
                                                   v
                                    MulticastPublisherThread -> UDP multicast
```

The normal exchange publisher is one multicast endpoint. It does not instantiate
`DualFeedPublisher`.

### 2.2 Implemented reliability-demo path

```text
reliability_demo creates synthetic events 1..N
        |
        +-> RecoveryHistory (bounded in-memory deque)
        +-> SnapshotManager (empty-book checkpoint metadata)
        |
        v
DualFeedPublisher
   |             |
FaultInjectingFeed A   FaultInjectingFeed B
   |             |
   +-------> DualFeedSubscriber
                    |
                    +-> SequenceTracker: accept / duplicate / gap
                    +-> pending later-event map
                    +-> RecoveryClient -> direct C++ call -> RecoveryServer
                                                       |
                                                       +-> replay events
                                                       +-> or snapshot checkpoint
```

There are no UDP or TCP sockets on this demo path. The recovery client directly
calls `RecoveryServer::request_missing_data()` in the same process.

## 3. Implemented features, with code evidence

### 3.1 Central limit order book and matching engine — **implemented**

| What it does | Code evidence |
|---|---|
| Stores bid price levels in descending order and ask price levels in ascending order. | `lib/OrderBook/include/core/OrderBook.h`: `bidLimits_` uses `std::greater<>`; `askLimits_` uses `std::less<>`. |
| Adds a limit order, first attempting to cross against the opposite side; rests remaining quantity. | `OrderBook::AddOrder()` and private `OrderBook::AddOrder(Order, ...)` in `lib/OrderBook/src/core/OrderBook.cpp`. |
| Matches at the best opposing price until quantity is exhausted or the limit price no longer crosses. | `OrderBook::TryMatch()` in `lib/OrderBook/src/core/OrderBook.cpp`. |
| Places market buy/sell orders using extreme prices so they cross available levels. | `OrderBook::PlaceMarketBuyOrder()` and `PlaceMarketSellOrder()`. |
| Cancels an order by order ID. | `OrderBook::RemoveOrder()`. |
| Amends an order by removing it then re-adding it. | `OrderBook::AmendOrder()`. This loses its old queue position, which is normal when price/size is changed. |
| Returns best bid, best ask, spread, order count, and aggregate quantities for visualisation. | `GetBestBidPrice`, `GetBestAskPrice`, `GetSpread`, `Count`, `GetBidQuantities`, `GetAskQuantities`. |

Prices are stored internally as integer **cents**. For example, `15050` means
`$150.50`. Display code divides by `100.0`.

### 3.2 Multiple securities — **implemented**

`src/exchange/SymbolManager.cpp` creates order books for `AAPL`, `GOOGL`,
`AMZN`, `NFLX`, and `META` in `SymbolManager::initialize_symbols()`. Each one
has a `Security`, a market-data adapter, and an `OrderBook`.

### 3.3 Simulated exchange order flow — **implemented**

`src/exchange/Exchange.cpp` provides simulation mode:

- `Exchange::initialize_simulation_books()` seeds each order book with initial
  bid and ask liquidity.
- `Exchange::simulation_loop()` chooses a symbol repeatedly.
- `Exchange::simulate_trading_activity()` chooses between a market order and a
  limit order.
- `place_simulation_market_order()` sends buy or sell market orders.
- `add_simulation_limit_order()` chooses prices and quantities using normal
  distributions.

This is a synthetic workload. It is not an external market-data source.

### 3.4 Binary market-data messages and one UDP multicast feed — **implemented**

| Feature | Code evidence |
|---|---|
| Packed binary message definitions: header, price-level update/delete, trade, snapshot message types, book clear. | `lib/MDFeed/include/messages/Messages.h`. |
| Global publisher sequence number starts at `1` and increments per published message. | `MarketDataPublisher::sequence_number_` and publish methods in `lib/MDFeed/src/publisher/MarketDataPublisher.cpp`. |
| Book changes become market-data messages. | `lib/MDFeed/include/publisher/MDAdapter.h`, especially `notify_price_level_change()`. |
| Queue between order book and publisher thread. | `MDRingBuffer` in `lib/MDFeed/include/utils/RingBuffer.h`, used by `MarketDataPublisher`. |
| Background publisher sends ring-buffer messages by UDP multicast. | `MulticastPublisherThread::publisher_loop()` in `lib/MDFeed/src/publisher/MulticastPublisherThread.cpp`. |
| UDP socket uses `sendto`. | `MulticastPublisher::send()` and its private socket implementation in `lib/MDFeed/src/publisher/MulticastPublisher.cpp`. |
| Receiver joins a multicast group, validates message sizes, logs messages, and counts sequence discontinuities. | `MulticastReceiver` in `lib/MDFeed/src/receiver/MulticastReceiver.cpp`. |

Important limitation: the base receiver's sequence check only counts a
discontinuity. It does not distinguish old packets from duplicates and does not
recover anything.

### 3.5 TCP order entry and automatic workload — **implemented, with real sockets**

| Feature | Evidence |
|---|---|
| TCP listener reassembles a byte stream into complete, size-validated frames and accepts multiple connections. | `OrderEntryServer::start`, `server_loop`, `handle_client` in `lib/OrderEntry/src/server/OrderEntryServer.cpp`. |
| Connection identity handshake. | Packed `ClientHelloMessage` / `ServerWelcomeMessage` in `OrderMessages.h`; server `send_welcome`; client `send_client_hello`. |
| Incoming messages are passed through a bounded mutex-protected queue. | `OrderRingBuffer` in `lib/OrderEntry/include/utils/OrderBuffer.h`. It replaces the unsafe old SPSC use at this multi-producer boundary. |
| Interactive client sends new and cancel orders and prints a real response. | `src/order_client/main.cpp`; transport functions in `lib/OrderEntry/src/client/OrderEntryClient.cpp`. |
| Automatic client runs independently and sends reproducible TCP orders. | `src/workload_client/main.cpp`, target `workload_client`. |
| Exchange consumes queued orders and calls the matching engine. | `Exchange::client_order_loop`, `process_client_order`, `handle_new_order`, `handle_cancel_order`, `handle_modify_order` in `src/exchange/Exchange.cpp`. |
| Exchange sends acknowledgement, reject, and an approximate execution report. | `send_order_ack`, `send_order_reject`, `send_execution_report`. |

Important limitations:

- The public interactive client exposes `NEW_ORDER` and `CANCEL_ORDER`; the
  exchange additionally dispatches packed `MODIFY_ORDER` for compatible
  clients, but the command-line UI does not yet offer a modify command.
- The standalone `src/order_server/main.cpp` only prints received orders; it
  does not match them or send exchange responses.
- The exchange computes execution price after matching from the current best
  opposite-side price. That may differ from the actual resting execution price,
  so this is not a complete execution-report implementation.

### 3.6 pybind11 Python bridge — **implemented**

The Python GUI calls the C++ order book through the compiled `_orderbook`
extension.

- Bindings: `lib/OrderBook/python/bindings.cpp`.
- Build rule/output package location: `lib/OrderBook/python/CMakeLists.txt`.
- Python package imports extensions: `orderbook/__init__.py`.
- The C++ Python binding intentionally uses `NullMarketDataPublisher`, so GUI
  simulator order-book actions do **not** publish UDP market data.

Bindings also exist for the multicast receiver and order-entry client, but the
current GUI does not use them.

### 3.7 Reliability: dual-feed model — **implemented only in-process**

The following works inside the demo and tests:

- `MarketDataEvent` wraps the existing `MessageHeader` and stores `FeedId`.
  File: `lib/MDFeed/include/reliability/ReliabilityTypes.h`.
- `DualFeedPublisher::publish()` sends one event to two `MarketDataFeed`
  objects. File: `lib/MDFeed/src/reliability/DualFeed.cpp`.
- `FaultInjectingFeed` invokes `FaultInjector`, then forwards zero, one, or two
  copies to a callback. This is an in-memory callback, not UDP.
- `FeedA` and `FeedB` are lightweight callback wrappers. They are never
  instantiated by the normal `exchange` executable.

### 3.8 Reliability: deterministic loss and duplication — **implemented**

`FaultInjector` in `lib/MDFeed/src/reliability/FaultInjector.cpp` uses
`std::mt19937_64` seeded from `FaultConfig.random_seed`.

- `packet_loss_rate` works: `apply()` returns an empty vector when dropped.
- `duplication_rate` works: `apply()` returns two copies when duplicated.
- Same configuration and seed give the same random decisions for a single run.
- `reorder_rate` and `delay` do **not** work yet. `should_reorder()` exists,
  but `apply()` never uses it, and no delay queue exists.

### 3.9 Reliability: duplicate detection, gaps, buffering, ordered replay — **implemented in demo/tests**

`SequenceTracker::on_event()` in
`lib/MDFeed/src/reliability/SequenceTracker.cpp` does this:

- First event sets `expected_sequence_` to its sequence plus one.
- An event below the expected sequence is a duplicate.
- An event above the expected sequence creates a missing range
  `[expected, received - 1]` and records the later sequence as out-of-order.
- An event equal to expected is accepted and increments expected.

`DualFeedSubscriber` in `lib/MDFeed/src/reliability/DualFeed.cpp` then:

1. drops duplicate copies (first-copy-wins);
2. stores a later event in `pending_events_` when there is a gap;
3. calls its gap callback;
4. accepts recovered events via `on_recovered_event()`;
5. calls `drain_pending_events()` to release buffered events only when sequence
   continuity has been restored.

The test `DualFeedSubscriberTests.ReplaysGapBeforeBufferedLaterEvent` in
`tests/ReliabilitySkeletonTests.cpp` proves the small case `1, 3` plus replay
of `2` delivers `1, 2, 3` in order.

### 3.10 Reliability: bounded replay recovery — **implemented in-process**

- `RecoveryHistory` stores a bounded `std::deque` of events and evicts oldest
  events when full. File: `lib/MDFeed/src/reliability/RecoveryHistory.cpp`.
- `RecoveryServer::request_missing_data()` returns `OK`, `TOO_OLD`,
  `BAD_REQUEST`, or `ERROR`. File:
  `lib/MDFeed/src/reliability/RecoveryProtocol.cpp`.
- `RecoveryClient::recover()` directly calls the server object in memory.
  It has an explicit TODO to replace that call with TCP transport.

So bounded-range decision logic is real; the TCP recovery network protocol is
not implemented.

### 3.11 Reliability: snapshots and hash-chain checks — **partially implemented**

Implemented:

- `SnapshotManager::should_checkpoint()` uses configured interval, enabled flag,
  and retention count.
- `SnapshotStore` retains a bounded deque of snapshots.
- `SnapshotHasher` hashes a string representation of snapshot metadata,
  price levels, and previous hash.
- `verify_chain()` checks each retained hash and each link within the retained
  segment.
- `DualFeedSubscriber::resynchronise_after_snapshot()` moves expected sequence
  to checkpoint plus one and releases later buffered packets.

Critical limitation:

- `reliability_demo` creates snapshots with an empty `book_state`:
  `create_snapshot(sequence, 1, {}, timestamp)`. It does not capture a live
  `OrderBook`.
- Snapshot resynchronisation changes only sequence tracking. It does not apply
  a snapshot to any local order book.
- The hash is 64-bit FNV-1a in `Snapshot.cpp`, not SHA-256. It is a deterministic
  integrity-demo checksum, not production cryptography or tamper-proof storage.

### 3.12 Reliability: metrics and CSV — **implemented only for demo inputs**

`MetricsCollector` and `MetricsExporter` are implemented in
`lib/MDFeed/src/reliability/Metrics.cpp`.

- Latency samples are sorted to compute p50, p95, p99, p99.9, and max.
- `MetricsExporter::export_csv()` writes one header line and one metric row.
- `src/reliability_demo/main.cpp` increments several counters and can write a
  CSV file through `--csv FILE`.

Limitations:

- `throughput_events_per_second` is wall-clock throughput for the deterministic
  in-process run. It is useful for comparing demo configurations on one
  machine, not a guarantee of network throughput.
- The demo's `packets_lost` mixes per-feed drops with correlated-loss events,
  so it is a demo metric, not a precise network-loss accounting definition.
- `ReliabilityExperimentController` now launches the compiled demo and parses
  its final `RELIABILITY_RESULT` line into the Reliability Experiments GUI.
  The separate Exchange Control GUI reads TCP workload-client results instead;
  its RTTs are not reliability-demo metrics.

### 3.13 Recovery fairness — **integrated in the deterministic demo**

`RecoveryScheduler` supports FCFS and Round Robin queues in
`lib/MDFeed/src/reliability/RecoveryScheduler.cpp`. The queue selection logic
works when called directly. `src/reliability_demo/main.cpp` now enqueues each
detected-gap request and asks the scheduler for the next request before making
the in-process recovery call. The GUI chooses FCFS or Round Robin for this
demo. It is not wired into a networked recovery server.

`RecoverySchedulerMetrics` does not calculate waiting time or recovery latency.
It counts request count, recovered message count, and services per subscriber.

## 4. GUI explanation

## 4.1 Where it is implemented

- Entry point: `src/simulation_python/main.py`.
- Main window: `src/simulation_python/gui/main_window.py`, class `MainWindow`.
- Simulation workload: `src/simulation_python/market_simulator.py`, class
  `MarketSimulator`.
- Reliability dashboard container: `src/simulation_python/gui/reliability_dashboard.py`.
- Reliability panel widgets: the remaining files under
  `src/simulation_python/gui/` ending in `_panel.py`.

The local visualisation tab calls the C++ `OrderBook` via pybind11.
`MainWindow.__init__()` creates one `Security`, one `orderbook.OrderBook`, and
one `MarketSimulator`. That local tab is not connected to the C++ `Exchange`
executable, real UDP market data, or the reliability demo.

The same window also has an **Exchange Control** tab. It uses
`exchange_controller.py` to launch the compiled C++ exchange and each compiled
`workload_client` as separate `QProcess` children. It reads their actual stdout
events, requires the exchange to announce readiness before a workload can
start, and saves actual workload client rows to `results/*.csv`.

## 4.2 The three actual graphs

### A. Price chart

Implemented by `MainWindow.setup_price_chart()` and updated in `update_data()`.

- **Green line:** best bid price, the highest current buy price.
- **Red line:** best ask price, the lowest current sell price.
- **X axis:** elapsed wall-clock seconds since GUI startup.
- **Y axis:** USD price, converted from integer cents by division by 100.
- **Data window:** deques retain at most `MAX_AGE_SECONDS * FRAME_RATE` = 600
  samples (nominally ten seconds at 60 updates/s).
- **Visible X range:** last eight seconds.
- **Y auto-scaling:** uses the last five seconds of bid/ask samples, then adds
  `$0.10` padding above and below.

It is a chart of the GUI's local simulated order book, not a received UDP feed.

### B. Order-book depth chart

Implemented by `setup_depth_chart()` and `update_depth_chart()`.

- **Vertical axis:** price level in USD.
- **Horizontal axis:** aggregate contracts/quantity at that level.
- **Green horizontal bars:** bid-side price levels.
- **Red horizontal bars:** ask-side price levels.
- It takes at most `MAX_RENDERED_DEPTHS` = 15 levels from each side.
- It calls C++-bound `get_bid_quantities()` and `get_ask_quantities()`.

The chart is cleared and rebuilt on every GUI update. It is a simple visual
snapshot, not an incremental market-depth renderer.

### C. Volume chart

Implemented by `setup_volume_chart()` and `update_data()`.

- **X axis:** elapsed seconds.
- **Y axis:** total quantity classified by the Python simulator as recently
  matched during the last one second.
- **Blue bars:** `MarketSimulator.get_recent_volume(1.0)` values sampled on
  GUI updates.

Important honesty point: `MarketSimulator` infers a trade by comparing
`get_orders_matched()` before and after a simulated action, then records the
action's generated `price` and matched quantity. It does not receive actual
trade messages from the exchange. The bars are a local simulation indicator.

## 4.3 Main controls and statistics

Defined in `MainWindow.setup_controls()`:

| Control/display | What it actually does |
|---|---|
| **Add Buy Pressure** | Calls `MainWindow.on_buy_pressure()`, then `MarketSimulator.add_buy_pressure()`. A daemon Python thread places up to 500 market-buy orders, sleeping 2 ms between attempts. |
| **Add Sell Pressure** | Starts a daemon thread in `MarketSimulator._apply_sell_pressure()`. It places market sells and sometimes inserts new bid/ask limits. |
| **Order Count** | `self.order_book.count()`; active orders in local GUI book. |
| **Spread** | Best ask minus best bid from `get_spread()`, shown in USD. |
| **Best Bid / Best Ask** | Current local book prices, shown in USD. |
| **Best Bid Depth / Best Ask Depth** | Aggregate quantity at the current best price from quantity maps. |

## 4.4 Reliability dashboard: compiled experiment controller

The dashboard is the **Reliability Experiments** main-window tab.
`ReliabilityDashboard` creates three tabs and uses
`ReliabilityExperimentController` to start `reliability_demo` as a `QProcess`.

| Tab / panel | File | Widgets shown | Actual connection |
|---|---|---|---|
| Results / Recovery status | `recovery_panel.py` | detected gaps, replay count, latency | Populated from `RELIABILITY_RESULT`. |
| Results / Feed redundancy | `redundancy_panel.py` | mode, Feed A/B drop count, avoided gaps | Populated from the compiled demo. |
| Results / Metrics | `metrics_panel.py` | throughput, duplicates, gaps, latency percentiles | Populated from the compiled demo. |
| Configure & Run / Experiment control | `experiment_panel.py` | mode, events, loss, seed, history, Run/Stop | Arguments passed to the compiled demo. |
| Configure & Run / Snapshots | `snapshot_panel.py` | enabled, interval, retention, checkpoint | Arguments passed to demo; checkpoint updated from result. |
| Configure & Run / Recovery fairness | `fairness_panel.py` | FCFS/Round Robin, service result | Policy passed to demo; service count displayed. |
| Checkpoint Integrity | `history_integrity_panel.py` | latest hash, chain status | Populated from demo's final checkpoint result. |

This is a real controller for a deterministic, in-process experiment. It does
not turn the exchange's existing UDP market-data path into two live feeds or
provide a networked recovery service.

## 4.5 Exchange Control GUI: real TCP path

| Area | File | What it actually does |
|---|---|---|
| Local Simulation | `exchange_control_panel.py` | Starts `exchange --mode client`, then starts the requested number of separate `workload_client` processes with count, rate, duration, symbol, and seed. |
| Lifecycle / status | `exchange_controller.py` | Parses the exchange readiness line; refuses to start a workload before it is ready; reports child process failures and bounded logs. |
| Manual Testing | `network_protocol.py` and `ManualOrderThread` | Opens a real Python TCP socket, sends `CLIENT_HELLO`, waits for `SERVER_WELCOME`, then sends a packed `NEW_ORDER`; displays the actual ACK or rejection and client RTT. |
| Distributed Network | `exchange_control_panel.py` | Shows a command for independently running `workload_client` on a remote machine. It does not pretend to remotely launch a machine. |
| Previous Results | `exchange_control_panel.py`, `exchange_controller.py` | Lists and previews real per-client CSV result files. It does not manufacture a comparison graph. |

The control tab is connected to order-entry TCP only. Its metrics do not claim
to be UDP market-data, dual-feed, recovery, or one-way network measurements.

## 4.6 GUI concurrency warning

`MarketSimulator` changes the C++ order book from a background Python thread,
while the PyQt timer reads it from the GUI thread. The code contains no mutex or
other synchronisation around those accesses. It works as a demonstration but
should not be described as thread-safe production GUI design.

## 5. Claimed novelty: exact, honest wording

Use this wording in the viva:

| Claimed idea | What is novel compared with a plain single-feed example | Actual implementation status |
|---|---|---|
| **A/B feed redundancy** | The subscriber can accept the first arriving copy of the same sequence event and suppress the later copy. | Implemented in process through `DualFeedPublisher`, `FaultInjectingFeed`, `DualFeedSubscriber`, and `SequenceTracker`. Not connected to real two-UDP-feed sockets. |
| **Gap-safe ordered delivery** | A later event is held instead of immediately delivered while missing sequence numbers are recovered. | Implemented in `DualFeedSubscriber::pending_events_`, `on_recovered_event()`, and `drain_pending_events()`. Unit tested. |
| **Bounded-history versus snapshot fallback** | The design explicitly models the choice: replay retained events if possible; otherwise use a checkpoint. | Range lookup and decision are implemented. Snapshot data is empty in the demo and does not rebuild an order book. |
| **Deterministic fault experiments** | Fixed seed allows the same packet-loss and duplication decisions to be reproduced in a viva/test. | Implemented in `FaultInjector` with `std::mt19937_64`. Reordering/delay are configuration-only. |
| **Recovery fairness seam** | FCFS and Round Robin are modelled as alternatives at a shared recovery service, instead of artificial network delays. | Queue class is implemented, but it is not integrated into recovery execution or GUI. |
| **Tamper-evident checkpoint-chain model** | Snapshots include previous hash so retained checkpoints can be checked for modification/link mismatch. | FNV-1a chain and verification work. It is not SHA-256, persistent storage, or a tamper-proof database. |

Do **not** claim these as completed features:

- real networked dual-feed redundancy;
- TCP-based recovery;
- distributed order-book replication;
- consensus;
- production cryptography;
- live reliability dashboard;
- implemented reordering or delay impairment;
- fairness experiment results.

## 6. What is incomplete or missing

### 6.1 Missing connections

1. The normal `exchange` creates `MarketDataPublisher` and one
   `MulticastPublisherThread`; it never creates `DualFeedPublisher`.
2. The normal UDP receiver creates `MulticastReceiver`; it never creates
   `DualFeedSubscriber`, `SequenceTracker`, `RecoveryClient`, or
   `RecoveryServer`.
3. `RecoveryClient::recover()` is a local function call, not TCP.
4. The GUI reliability widgets have no signals, callbacks, bindings, or timers
   that read `SystemMetrics`. This limitation does not apply to the separate
   Exchange Control tab, which reads actual C++ process output.
5. `RecoveryScheduler` is not passed into `RecoveryServer`.

### 6.2 Incomplete functionality

1. `FaultInjector::apply()` handles loss and duplication only. It does not
   reorder or delay packets.
2. Snapshot `book_state` is not generated from `OrderBook`, persisted, sent, or
   applied.
3. Snapshot integrity uses FNV-1a rather than a cryptographic hash.
4. `MetricsCollector` does not calculate throughput automatically.
5. `RedundancyExperiment` only stores counters when manually called. The demo
   does not use this class.
6. The `client` executable's `process_market_data()` is empty; the underlying
   receiver logs messages, but the application does not build a book or present
   a custom client view.
7. Existing binary snapshot message structs are defined but no publisher method
   creates snapshot begin/entry/end messages.
8. The command-line interactive client does not have a `modify` command even
   though the exchange TCP dispatcher supports packed `MODIFY_ORDER`.

### 6.3 Important correctness limitations to acknowledge if asked

- `MulticastReceiver::process_message()` calculates an unsigned subtraction
  when a received sequence is lower than the previous one; an old packet could
  create an incorrect large gap statistic.
- The normal exchange only provides best-price-based execution reporting after
  matching, not exact per-fill reports.
- The GUI simulation uses a `NullMarketDataPublisher`; it does not exercise the
  exchange's UDP market-data path.
- Default CMake tests/benchmarks fetch third-party dependencies. A GUI-only
  offline build must disable them with `BUILD_PROJECT_TESTS=OFF` and
  `BUILD_BENCHMARKS=OFF`.

## 7. Viva questions and accurate answers

### Q1. What problem are you solving?

UDP multicast is fast but unreliable: packets can be lost or duplicated. The
project demonstrates how sequence tracking, redundant copies, bounded replay,
and snapshots can help a subscriber restore ordered market-data delivery.

### Q2. Is your exchange distributed?

No. It is a centralised exchange with one authoritative order book per
instrument. The reliability work is about distributing market data reliably,
not synchronising replicas of the order book.

### Q3. What does “first-copy-wins” mean here?

Feed A and Feed B carry the same sequence number. The first accepted copy is
delivered. If the same sequence arrives again, `SequenceTracker::on_event()`
classifies it as a duplicate and `DualFeedSubscriber::on_packet()` drops it.

### Q4. What happens when sequence 10 arrives after sequence 8?

The tracker expects 9, therefore it reports missing range `[9, 9]`. The
subscriber stores event 10 in `pending_events_`, requests recovery for 9, and
only delivers 10 after 9 has been replayed.

### Q5. Is recovery really TCP now?

No. The interface is designed like a recovery client/server protocol, but
`RecoveryClient::recover()` currently calls the server object directly in the
same process. Real TCP framing is planned work.

### Q6. What does bounded history do?

It retains only the latest configurable number of events in a deque. If a
requested missing range is no longer retained, recovery returns `TOO_OLD` and
the demo tries snapshot fallback.

### Q7. Does snapshot fallback rebuild the order book?

Not yet. The current demo uses empty snapshots and only advances sequence
continuity after receiving a checkpoint. Real order-book serialisation and
application are future work.

### Q8. What exactly does the GUI show?

The Market Visualisation tab shows a local simulated order book: best bid/ask
over time, depth by price level, and recent simulated matched volume. It does
not show live UDP exchange data. The reliability dashboard is currently visual
structure only. The separate Exchange Control tab does launch real TCP
processes, show their actual logs/results, and submit real manual orders.

### Q9. How do you simulate loss reproducibly?

`FaultInjector` has a random seed and uses `std::mt19937_64`. Reusing the same
seed and configuration gives the same loss/duplication decision sequence.

### Q10. What does correlated loss demonstrate?

In correlated mode the demo decides loss once before publishing to either feed,
so both copies are lost together. It shows dual feeds help only when failures
are at least partly independent.

### Q11. What is the role of FCFS versus Round Robin?

`RecoveryScheduler` models how a shared recovery service could choose pending
requests. FCFS serves arrival order; Round Robin rotates among subscribers.
The class works alone but is not yet wired to the recovery server.

### Q12. Is your hash chain cryptographically secure?

No. The implementation uses FNV-1a as a deterministic placeholder. It can
demonstrate linked checkpoint verification, but a real implementation should
use SHA-256 or another cryptographic hash plus durable storage.

### Q13. What is the most convincing live demonstration today?

For real networking, use Exchange Control to launch one exchange and two
workload clients; show `WORKLOAD_READY`, `WORKLOAD_RESULT`, server order logs,
and the saved CSV. For reliability, run `reliability_demo` in `single`, `independent`, and `correlated` modes with
the same seed. Point out delivered sequences, duplicate count, detected gaps,
recovery ranges, snapshot fallback when history is zero, CSV export, and the
valid checkpoint-chain line.

## 8. File-by-file navigation guide

### Build and entry points

| File | Why it matters |
|---|---|
| `CMakeLists.txt` | Top-level options; builds core libraries, executables, optional tests, benchmarks, and pybind11 modules. |
| `src/CMakeLists.txt` | Defines `exchange`, `client`, `order_server`, `order_client`, `workload_client`, and `reliability_demo`. |
| `src/exchange/main.cpp` | Parses exchange mode and multicast/order-entry configuration. |
| `src/reliability_demo/main.cpp` | Best file for demonstrating implemented reliability behaviour. |

### Core matching engine

| File | Why it matters |
|---|---|
| `lib/OrderBook/include/core/OrderBook.h` | Main order-book API and data structures. |
| `lib/OrderBook/src/core/OrderBook.cpp` | Matching, market orders, add/amend/cancel, book queries. |
| `lib/OrderBook/include/entries/OrderBookEntry.h` and `src/entries/OrderBookEntry.cpp` | Linked-list entries and price-level support. |
| `lib/OrderBook/include/orders/Order.h`, `OrderCore.h` | Order data and identifier/user/security information. |
| `src/exchange/SymbolManager.cpp` | Creates five symbols and their books. |
| `src/exchange/Exchange.cpp` | Coordinates simulation/client orders, matching, UDP publishing, and basic responses. |

### Network market data

| File | Why it matters |
|---|---|
| `lib/MDFeed/include/messages/Messages.h` | Wire-message structures and existing sequence header. |
| `lib/MDFeed/include/publisher/MDAdapter.h` | Converts order-book changes into market-data publisher calls. |
| `lib/MDFeed/src/publisher/MarketDataPublisher.cpp` | Adds sequence numbers and pushes binary messages to ring buffer. |
| `lib/MDFeed/src/publisher/MulticastPublisherThread.cpp` | Pops queue entries and sends them through UDP multicast. |
| `lib/MDFeed/src/receiver/MulticastReceiver.cpp` | Joins multicast, validates/logs messages, counts discontinuities. |
| `src/client/main.cpp` | Command-line receiver wrapper; payload callback is currently empty. |

### Order entry

| File | Why it matters |
|---|---|
| `lib/OrderEntry/include/messages/OrderMessages.h` | TCP order-entry binary protocol structs. |
| `lib/OrderEntry/src/server/OrderEntryServer.cpp` | TCP listener and incoming order buffering. |
| `lib/OrderEntry/src/client/OrderEntryClient.cpp` | TCP connection and message sending. |
| `src/order_client/main.cpp` | Interactive terminal order client. |
| `src/workload_client/main.cpp` | Separate-process automatic TCP workload generator and acknowledgement-RTT measurement. |
| `src/order_server/main.cpp` | Standalone protocol-printing server. |

### Reliability code

| File | Why it matters |
|---|---|
| `ReliabilityTypes.h` | `FeedId`, `MarketDataEvent`, `MissingRange`. |
| `SequenceTracker.*` | Expected sequence, duplicate and gap decisions. |
| `DualFeed.*` | Feed abstraction, faultable feed, dual publisher, subscriber buffering/replay. |
| `FaultInjector.*` | Seeded loss/duplication decisions. |
| `RecoveryHistory.*` | Bounded event retention and range lookup. |
| `RecoveryProtocol.*` | In-process recovery request/response API. |
| `Snapshot.*` | Checkpoints, bounded store, FNV-1a chain validation. |
| `RecoveryScheduler.*` | FCFS / Round Robin queue class, used by the deterministic demo before each in-process recovery request. |
| `Metrics.*` | Metric aggregation and one-row CSV writer. |
| `Experiments.*` | Configuration/metric holder classes, not a running experiment framework. |

### GUI and Python bridge

| File | Why it matters |
|---|---|
| `lib/OrderBook/python/bindings.cpp` | Exposes C++ order book to Python; uses `NullMarketDataPublisher`. |
| `orderbook/__init__.py` | Imports compiled Python extension modules. |
| `src/simulation_python/main.py` | PyQt application startup and repository import path setup. |
| `src/simulation_python/gui/main_window.py` | The three real graphs, controls, stats, and Reliability Experiments main tab. |
| `src/simulation_python/market_simulator.py` | Background random order-flow generator and pressure actions. |
| `src/simulation_python/gui/reliability_dashboard.py` | Starts the deterministic compiled demo and renders its structured result. |
| `src/simulation_python/gui/reliability_controller.py` | QProcess controller, result-line parser, and reliability CSV output path. |
| `src/simulation_python/gui/*_panel.py` | Inputs and measured-result widgets used by the reliability dashboard. |
| `src/simulation_python/gui/exchange_controller.py` | QProcess orchestration, stdout parsing, readiness gate, and CSV result writing. |
| `src/simulation_python/gui/exchange_control_panel.py` | Local/distributed/manual/results GUI controls. |
| `src/simulation_python/gui/network_protocol.py` | Manual GUI's packed TCP handshake and order submitter. |

### Tests and benchmarks

| File | Why it matters |
|---|---|
| `tests/OrderBookTests.cpp`, `MatchingEngineTests.cpp`, `OrderBookEntryTests.cpp` | Existing book/matching behaviour tests. |
| `tests/ReliabilitySkeletonTests.cpp` | Small focused tests for tracker, replay, snapshot resync, history, scheduler construction, fault config, and experiment counter. |
| `benchmarks/OrderBookBenchmarks.cpp` | Microbenchmark definitions; results depend on the machine/build and are not guaranteed workload performance. |

## 9. Recommended viva demo order

1. Start with `reliability_demo` because it demonstrates the new work without
   multicast network setup.
2. Run independent loss, explain duplicate suppression and replay.
3. Run correlated loss, explain why redundancy does not solve shared failure.
4. Run history zero plus snapshot interval one, explain the *sequence resync*
   limitation honestly.
5. Open Exchange Control, run two clients, and point out it uses separate TCP
   processes and client-measured acknowledgement RTT.
6. Open the local GUI visualisation and explain its three working local-simulation graphs.
7. Show the reliability dashboard, clearly saying it is a planned integration
   UI scaffold.
8. If asked about the original exchange, show `OrderBook::TryMatch`,
   `MDAdapter`, and `MarketDataPublisher`.

Useful commands are in `README.md` and `docs/RELIABILITY_DEMO.md`.
