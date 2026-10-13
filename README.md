# Reliable Market-Data Distribution for a Networked Electronic Exchange

A C++23 electronic-exchange project with a price-time-priority limit order
book, matching engine, TCP order entry, UDP multicast market data, Python
bindings, a PyQt6 market simulator, and a runnable reliability demonstration.

The exchange remains centralised: there is one authoritative order book per
instrument. The reliability work improves distribution of market-data events;
it does **not** provide distributed consensus or replicated order books.

## Current capabilities

### Exchange and market-data system

- Limit and market orders, cancellation, modification, and price-time priority.
- Multiple instruments: `AAPL`, `GOOGL`, `AMZN`, `NFLX`, and `META`.
- TCP order-entry server and interactive TCP order-entry client.
- UDP multicast publishing of binary price-level, trade, and book-clear
  events, with heartbeat processing. The protocol also defines snapshot message
  types for the reliability layer.
- UDP multicast receiver with message validation, sequence-gap statistics, and
  logging controls.
- Lock-free SPSC market-data ring buffer.
- C++ benchmarks and GoogleTest unit tests.
- Optional pybind11 bindings and PyQt6/pyqtgraph market simulator.

### Reliability demonstration

The reliability subsystem is implemented as a deterministic, in-process demo
target named `reliability_demo`.

- Feed A and Feed B publish the same sequence-numbered event stream.
- Configurable deterministic packet loss and duplication fault injection.
- Single-feed, dual-feed independent-loss, and dual-feed correlated-loss modes.
- First-copy-wins duplicate suppression.
- Gap detection and buffering of later events.
- Recovery from bounded event history through a recovery client/server API.
- Ordered replay: recovered events are applied before buffered later events.
- Snapshot checkpoint fallback and sequence resynchronisation.
- Bounded snapshot store with retained hash-chain verification.
- CSV metrics export: loss, duplicates, gaps, recovery counts, recovery
  latency percentiles, snapshot recoveries, and redundancy effectiveness.
- Structural interfaces for FCFS/Round Robin recovery scheduling and experiment
  configuration.

The demonstration intentionally uses local function calls for recovery. It is
not yet a live TCP recovery server or a second live UDP multicast feed.

## Not implemented yet

These boundaries are important when presenting the project:

- Separate live UDP multicast sockets/endpoints for Feed A and Feed B.
- Framed TCP recovery networking, authentication, and multi-client service.
- Recovery scheduler integration into a running TCP server.
- Real order-book-state extraction and application during snapshot recovery.
- SHA-256 checkpoint hashing. The current hash-chain implementation uses a
  deterministic FNV-1a placeholder for demonstration only.
- Reordering and delay execution in the fault injector; packet loss and
  duplication are implemented.
- Live C++ telemetry bindings to the reliability GUI panels.
- Persistent event history/snapshots and production concurrency tuning.

## Prerequisites

Required for the C++ build:

- CMake 3.24 or later.
- A C++23 compiler.
- Boost 1.82 or later.
- `spdlog`.

The first CMake configuration also obtains GoogleTest and Google Benchmark
through CMake `FetchContent` unless local copies are already available.

Optional Python GUI requirements:

```sh
python3 -m pip install -r src/simulation_python/requirements.txt pybind11
```

## Build

Use a new build directory. This is especially useful when working from an
archive that already contains a build cache created on another machine.

### C++ applications, tests, benchmarks, and reliability demo

```sh
cmake -S . -B build-local -DBUILD_PYTHON_BINDINGS=OFF
cmake --build build-local --parallel
```

### Optional Python bindings

Install `pybind11` into the Python environment used by CMake, then configure:

```sh
cmake -S . -B build-python \
  -DBUILD_PYTHON_BINDINGS=ON \
  -DBUILD_PROJECT_TESTS=OFF \
  -DBUILD_BENCHMARKS=OFF \
  -Dpybind11_DIR="$(python3 -m pybind11 --cmakedir)"
cmake --build build-python --parallel
```

The generated Python extension modules are placed in `orderbook/`.

### GUI troubleshooting

Before running the simulator, ensure that the order-book binding was built:

```sh
cmake --build build-python --target _orderbook
```

When using an existing build directory configured with Python bindings, the
equivalent command is:

```sh
cmake --build build --target _orderbook
```

`src/simulation_python/main.py` and the GUI module add the repository root to
Python's import path automatically. If Python still reports that `orderbook`
cannot be imported, the `_orderbook` extension has not been built for the
Python interpreter currently running the GUI.

## Test the current implementation

### 1. Run all project GoogleTests

This runs the order-book/matching-engine tests and all reliability tests.

```sh
./build-local/tests/Tests
```

Or use CTest for the project test suites only:

```sh
ctest --test-dir build-local --output-on-failure -R \
  '^(OrderBookTests|MatchingEngineTests|OrderBookEntryTests|SequenceTrackerTests|DualFeedSubscriberTests|RecoveryHistoryTests|SnapshotManagerTests|RecoverySchedulerTests|FaultInjectorTests|RedundancyExperimentTests)\.'
```

The reliability tests verify:

- gap and duplicate recognition;
- bounded-history rejection of old requests;
- replay before release of a buffered later event;
- snapshot sequence resynchronisation;
- snapshot hash-chain verification after retention removes older checkpoints;
- FCFS and Round Robin scheduler construction;
- deterministic fault configuration; and
- redundancy experiment metrics.

To run every CTest registered in the build, including third-party benchmark
tests, use the following command. It takes longer than the project test suite.

```sh
ctest --test-dir build-local --output-on-failure
```

### 2. Demonstrate independent dual-feed loss and recovery

```sh
./build-local/src/reliability_demo \
  --events 20 --loss 0.25 --mode independent --seed 7 \
  --csv reliability-metrics.csv
```

Expected terminal evidence includes:

- `DELIVERED seq=... feed=A|B`;
- `RECOVERY OK range=...` after a gap;
- delivered events in sequence order after replay;
- a final summary with duplicates, gaps, recovered events, and hash-chain
  status; and
- a CSV file named `reliability-metrics.csv`.

### 3. Compare a single feed with independent dual feeds

```sh
./build-local/src/reliability_demo \
  --events 20 --loss 0.25 --mode single --seed 7

./build-local/src/reliability_demo \
  --events 20 --loss 0.25 --mode independent --seed 7
```

Use the same seed in both runs. Compare gap count, recovery requests, recovered
events, and redundancy effectiveness in the summaries.

### 4. Demonstrate correlated loss

```sh
./build-local/src/reliability_demo \
  --events 20 --loss 0.25 --mode correlated --seed 7
```

In correlated mode, a selected event is lost on both feeds. This shows why two
feeds only help when their loss behaviour is not fully correlated.

### 5. Demonstrate snapshot fallback

```sh
./build-local/src/reliability_demo \
  --events 12 --loss 0.30 --mode single --seed 9 \
  --history 0 --snapshot-interval 1
```

This intentionally disables retained event history and creates a checkpoint for
each event. The output should contain `RECOVERY SNAPSHOT RESYNC` and report a
successful snapshot recovery. The demo advances sequence continuity after a
validated checkpoint; it does not yet rebuild a real order book from snapshot
price levels.

### 6. View all reliability-demo options

```sh
./build-local/src/reliability_demo --help
```

Available options:

```text
--events N
--loss RATE
--mode single|independent|correlated
--seed N
--history N
--snapshot-interval N
--csv FILE
```

Detailed viva examples are also available in
[`docs/RELIABILITY_DEMO.md`](docs/RELIABILITY_DEMO.md).

## Run the exchange applications

All executable paths below assume the `build-local` directory created above.

### Exchange simulation and market-data receiver

Terminal 1:

```sh
./build-local/src/exchange \
  --mode simulation \
  --md-ip 239.1.1.1 \
  --md-port 9999 \
  --md-interface 127.0.0.1
```

Terminal 2:

```sh
./build-local/src/client \
  --ip 239.1.1.1 \
  --port 9999 \
  --interface 127.0.0.1
```

Press `Ctrl+C` to stop either program. Multicast loopback behaviour depends on
the host and network configuration.

### Exchange client mode and interactive order entry

Terminal 1:

```sh
./build-local/src/exchange --mode client --oe-port 8080
```

Terminal 2:

```sh
./build-local/src/order_client --server 127.0.0.1 --port 8080
```

Example interactive commands:

```text
new AAPL buy 150.50 100
new AAPL sell 151.00 50
cancel 1 AAPL
quit
```

### Standalone order-entry protocol test

Terminal 1:

```sh
./build-local/src/order_server --port 8080
```

Terminal 2:

```sh
./build-local/src/order_client --server 127.0.0.1 --port 8080
```

The standalone server logs received new-order and cancel-order messages. It is
useful for exercising the TCP order-entry protocol independently of matching.

### Benchmarks

```sh
./build-local/benchmarks/Benchmarks
```

Benchmark results depend on the machine, compiler, build mode, and background
load. They are measurements, not guaranteed system throughput.

### Python market simulator

After building with `BUILD_PYTHON_BINDINGS=ON` and installing the Python
requirements:

```sh
cd src/simulation_python
python3 main.py
```

The GUI shows price, depth, and volume charts with buy/sell pressure controls.
It also contains structural reliability dashboard panels. Those panels are not
yet connected to live reliability-demo metrics.

## Main source layout

```text
lib/OrderBook/                    Limit order book and matching components
lib/MDFeed/                       UDP market-data messages, publisher, receiver
lib/MDFeed/include/reliability/   Reliability interfaces and data structures
lib/MDFeed/src/reliability/       Reliability implementation
lib/OrderEntry/                   TCP order-entry server and client
src/exchange/                     Exchange executable
src/reliability_demo/             Runnable reliability demonstration
src/simulation_python/            PyQt6 simulator and dashboard structure
tests/                            GoogleTest test suites
benchmarks/                       Google Benchmark benchmarks
docs/RELIABILITY_DEMO.md          Short viva demonstration guide
```

## Reliability architecture at a glance

```text
Matching engine / central order book
                |
                v
      sequence-numbered market-data event
                |
         +------+------+
         |             |
         v             v
       Feed A        Feed B
         |             |
         +------+------+
                |
                v
      dual-feed subscriber
      duplicate suppression + gap detection
                |
                v
      recovery history -> replay response
                |
                v
      snapshot fallback when history is too old
```

## License

No license file is currently included in this source tree.
