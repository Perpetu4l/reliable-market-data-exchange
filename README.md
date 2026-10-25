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
- TCP order-entry server and interactive TCP order-entry client with complete
  stream framing, partial-write handling, and acknowledgement/reject reads.
- Explicit `CLIENT_HELLO` / `SERVER_WELCOME` connection handshake for the
  automatic workload client.
- A separate-process `workload_client` that sends reproducible orders over
  real TCP sockets and reports client-measured acknowledgement RTT percentiles.
- UDP multicast publishing of binary price-level, trade, and book-clear
  events, with heartbeats. `--dual-feed` publishes the same bytes and sequence
  numbers on independently configured Feed A and Feed B multicast endpoints.
- UDP multicast receiver with message validation, sequence-gap statistics, and
  logging controls.
- Lock-free SPSC market-data ring buffer.
- A bounded publisher-side recovery history and a separate framed TCP recovery
  endpoint. It uses the shared FCFS or Round-Robin scheduler and serves exact
  retained binary datagrams; a request outside the window returns `TOO_OLD`.
- C++ benchmarks and GoogleTest unit tests, including order-entry frame and
  queue-boundary checks.
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
- FCFS and Round Robin recovery scheduling in the deterministic demo, with
  per-subscriber service counters.

The demonstration intentionally keeps its own recovery in one process so that loss,
seed, history, snapshot, and scheduling experiments are repeatable. The GUI
runs this compiled demo and displays its emitted metrics; it is not labelled or
presented as a live production multicast/recovery deployment.

The normal `exchange` now exposes a separate live recovery TCP endpoint, and
`reliable_subscriber` joins Feed A (and optionally Feed B), performs
first-copy-wins sequencing, and requests retained gaps over that endpoint.
Use `recovery_client` to verify a retained range or a checkpoint manually.
When history is too old, the subscriber requests the first retained composite
snapshot at or after the missing sequence, verifies its SHA-256 checkpoint
hash, applies its absolute market-data levels, and resynchronises.

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

### C++ applications and the reliability demo

```sh
cmake -S . -B build-local \
  -DBUILD_PYTHON_BINDINGS=OFF \
  -DBUILD_PROJECT_TESTS=OFF \
  -DBUILD_BENCHMARKS=OFF
cmake --build build-local --parallel
```

`build-local` is the directory searched by the GUI for `exchange`,
`workload_client`, and `reliability_demo`.

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

### 1. Build and run all project GoogleTests

This runs the order-book/matching-engine tests, reliability tests, and the
order-entry protocol/queue tests.

```sh
cmake -S . -B build-test \
  -DBUILD_PYTHON_BINDINGS=OFF \
  -DBUILD_BENCHMARKS=OFF \
  -DBUILD_PROJECT_TESTS=ON
cmake --build build-test --target Tests --parallel
ctest --test-dir build-test --output-on-failure
```

To run only named project suites after building:

```sh
ctest --test-dir build-test --output-on-failure -R \
  '^(OrderBookTests|MatchingEngineTests|OrderBookEntryTests|SequenceTrackerTests|DualFeedSubscriberTests|RecoveryHistoryTests|SnapshotManagerTests|RecoverySchedulerTests|FaultInjectorTests|RedundancyExperimentTests|OrderEntryProtocolTests|OrderEntryQueueTests)\.'
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
--retention N
--scheduler fcfs|round-robin
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

`order_client` waits for and prints the actual acknowledgement, rejection, or
execution-report frame returned by the exchange. The exchange owns the shared
order books; clients do not modify them directly.

### Live dual-feed publishing and TCP recovery

Start the exchange with two real multicast destinations and a bounded replay
window. Feed A and Feed B carry the same original market-data datagrams.

```sh
./build-local/src/exchange --mode simulation --dual-feed \
  --md-ip 239.1.1.1 --md-port 9999 \
  --feed-b-ip 239.1.1.2 --feed-b-port 10000 \
  --history 65536 --recovery-port 9090 \
  --snapshot-interval 1000 --snapshot-retention 8
```

The recovery listener binds to all IPv4 interfaces on the configured port. In
another terminal, request a range that has already been published:

```sh
./build-local/src/recovery_client \
  --server 127.0.0.1 --port 9090 --subscriber demo-client --start 1 --end 5
```

`Recovery status: OK` followed by original sequence numbers proves the TCP
request/response path and publisher-retained history. `TOO_OLD` is the correct
result for a range evicted from `--history`; it is not a successful recovery.
The client accepts numeric IPv4 addresses, so replace `127.0.0.1` with the
server machine's reachable LAN address for a remote test. This project does
not yet provide TLS/authentication, persistent checkpoints, or replay of
events after a snapshot checkpoint.

Request a retained checkpoint explicitly:

```sh
./build-local/src/recovery_client \
  --server 127.0.0.1 --port 9090 --snapshot-min-sequence 1000
```

The result is `OK` only when a retained checkpoint at or after sequence 1000
exists. A snapshot is built from published price-level state, not by reading
or serialising the matching engine directly.

To exercise the full live subscriber path on a normal host, start this after
the exchange:

```sh
./build-local/src/reliable_subscriber --dual-feed \
  --feed-a-ip 239.1.1.1 --feed-a-port 9999 \
  --feed-b-ip 239.1.1.2 --feed-b-port 10000 \
  --interface 127.0.0.1 --recovery-server 127.0.0.1 --recovery-port 9090
```

It prints `DELIVERED`, `RECOVERY OK`, and a final count of duplicates, gaps,
recovery requests, and failures. The subscriber does not inject loss itself;
use an operating-system/network test mechanism or a purpose-built publisher
fault stage when testing loss. This sandbox blocks socket binding, so this
live path was compiled and its startup-error handling was checked here, but it
must be run on a normal macOS/Linux host for multicast/TCP end-to-end proof.

### Automatic local TCP workload

Start the exchange in one terminal:

```sh
./build-local/src/exchange --mode client --oe-port 8080
```

Then start independent client processes in other terminals (or use the
Exchange Control GUI page, which launches equivalent child processes):

```sh
./build-local/src/workload_client \
  --server 127.0.0.1 --port 8080 --client-id 1 \
  --duration 10 --rate 50 --symbol AAPL --side buy --seed 11

./build-local/src/workload_client \
  --server 127.0.0.1 --port 8080 --client-id 2 \
  --duration 10 --rate 50 --symbol AAPL --side sell --seed 12
```

Each client prints `WORKLOAD_READY` only after the TCP handshake succeeds and
ends with `WORKLOAD_RESULT`. `ack_p50_us`, `ack_p95_us`, and `ack_p99_us` are
client-measured request-to-acknowledgement round-trip times in microseconds.
They are loopback TCP RTTs in local mode, not one-way or physical-network
latencies.

### Distributed client

Run the exchange on the server machine and replace `127.0.0.1` with that
machine's reachable IPv4 address when launching `workload_client` elsewhere:

```sh
./workload_client --server 192.168.1.10 --port 8080 \
  --client-id 101 --duration 10 --rate 20 --symbol AAPL --side buy --seed 101
```

The server uses an IPv4 wildcard bind. Allow the selected TCP port through the
server firewall and use a trusted private network. This project does not add
authentication or transport encryption.

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

The GUI has three clear views:

- **Market Visualisation (local simulator):** the existing in-process price,
  depth, and volume charts with buy/sell pressure controls. It is not presented
  as TCP or UDP live data.
- **Exchange Control:** launches the C++ exchange and independent
  `workload_client` processes, displays actual process output and workload
  results, saves completed client rows as CSV under `results/`, previews saved
  result files in its Previous Results section, provides a manual TCP
  limit-order form, and documents a remote-client command.
- **Reliability Experiments:** runs the compiled `reliability_demo` with the
  selected feed mode, loss rate, seed, event-history capacity, checkpoint
  interval/retention, and FCFS or Round Robin setting. The **Results** tab
  shows the actual structured output: loss, duplicate suppression, gaps,
  replay recovery, feed drops, checkpoint hash, and latency percentiles. Each
  run writes `results/reliability-*.csv`; its output is also kept in the
  dashboard log.

For a quick visible demo, open **Reliability Experiments → Configure & Run**,
choose **Dual feed - independent loss**, set packet loss to `0.20`, leave the
seed as `42`, then press **Run reliability experiment**. The dashboard changes
to **Results** automatically when the run completes.

### GUI workflows and what they prove

**Market Visualisation (local simulator)** is an in-process Python/pybind11
simulation of one AAPL book. Its header explicitly says that it has no TCP
clients and is not connected to the external exchange or UDP feed. The bid/ask
chart, depth chart, and volume chart therefore describe local simulated order
flow only.

**Exchange Control → Local Simulation** is the real local socket workflow:

1. Choose port, client count, rate, duration, symbol, and seed.
2. Press **Start exchange**, then wait for the green **Ready** state.
3. Press **Start workload**. Every configured client must become
   **Connected — workload active** in the status table.
4. Wait for each client to become **Completed — result recorded**. Only then
   is a workload CSV written under `results/`.
5. Use **Stop / clean up** to terminate child processes early. A stopped or
   failed/incomplete workload is not recorded as a successful result.

**Manual Testing** sends one actual packed `CLIENT_HELLO`, receives a
`SERVER_WELCOME`, sends `NEW_ORDER`, and displays the returned ACK or reject
plus client-measured round-trip time. ACK means the exchange accepted the
order. It does not by itself mean that the order traded; an execution report
requires a crossing order.

**Distributed Network** is for a workload executable started independently on
another computer. The GUI does not pretend it can start a remote process.

1. On the exchange machine, run `./build-local/src/exchange --mode client
   --oe-port 8080`.
2. Find its reachable private-LAN IPv4 address, for example `ipconfig getifaddr
   en0` on macOS or `hostname -I` on Linux. Enter that address in the GUI;
   `192.168.1.10` is not a universal value.
3. Copy the displayed `workload_client` command and run it from the other
   machine after building the same project there.
4. Server output must show the client identity and received orders;
   `WORKLOAD_READY` proves the handshake and `WORKLOAD_RESULT` proves the
   client observed responses.

The TCP listener binds to `0.0.0.0` (all IPv4 interfaces). If the client
cannot connect, check the address, port, both machines' private-network reachability,
and firewall rules. A process start is not treated as a client connection.

**Previous Results** reads only persisted `workload-*.csv` and
`reliability-*.csv` files from `results/`; it creates no sample runs. Reliability
CSVs store the measured metrics plus the run time, seed, mode, loss rate,
history/snapshot settings, and scheduler for reproducible comparisons.

### Reliability experiment boundaries

The **Reliability Experiments** GUI launches the compiled, deterministic,
single-process `reliability_demo`. It is a controlled application-level fault
experiment: Feed A/B delivery is invoked in-process, and configured loss means
an event is deliberately withheld by the fault injector before subscriber
delivery. It is not a claim of operating-system or physical-network packet
loss. The test records actual events produced by that run, including duplicate
suppression, gaps, replay recovery, snapshot fallback, and latency samples.

The Results page contains two measured charts for the current run:

- **Observed delivery outcome**: source events, per-feed application-level
  drops, duplicates suppressed, and replayed events.
- **Observed recovery latency percentiles**: p50/p95/p99 in microseconds. The
  bars are zero when no recovery occurred; they are not invented samples.

For a meaningful comparison, run a zero-loss baseline and a fault-injected run
with the same event count, seed, history, snapshot settings, and scheduler.
Compare the persisted CSVs; deterministic fault decisions are reproducible for
the same configuration, although the wall-clock throughput measurement can
vary between runs.

## Main source layout

```text
lib/OrderBook/                    Limit order book and matching components
lib/MDFeed/                       UDP market-data messages, publisher, receiver
lib/MDFeed/include/reliability/   Reliability interfaces and data structures
lib/MDFeed/src/reliability/       Reliability implementation
lib/OrderEntry/                   TCP order-entry server and client
src/exchange/                     Exchange executable
src/workload_client/              Independent real-TCP automatic workload client
src/reliability_demo/             Runnable reliability demonstration
src/simulation_python/            PyQt6 simulator and dashboard structure
tests/                            GoogleTest test suites
benchmarks/                       Google Benchmark benchmarks
docs/RELIABILITY_DEMO.md          Short viva demonstration guide
docs/IMPLEMENTATION_CHECKLIST.md  Requirement-to-code verification status
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
