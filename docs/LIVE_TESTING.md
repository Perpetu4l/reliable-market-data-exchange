# Live Testing & Two-Laptop LAN Guide (`docs/LIVE_TESTING.md`)

This guide explains how to run the live UDP multicast + TCP recovery pipeline:
1. On a **single machine** over loopback (`udp_loopback`), including OS-level `pfctl`/`dnctl` (macOS) or `tc netem` (Linux) impairment.
2. Across **two laptops on a LAN** (`udp_lan`).

---

## 1. Single-Machine Loopback Testing (`127.0.0.1`)

### Automated End-to-End Integration Suite

```sh
# Fast baseline suite (< 20 s): deterministic seed check, 0% loss, 8-subscriber lossy run, negative control, snapshot tamper
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/live_reliability_e2e.py --build-dir build-local

# Slow scheduler contention suite (~15 s): proves RR quiet p99 < FCFS quiet p99 by >= 2x
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/live_reliability_e2e.py --build-dir build-local --slow

# 30-run consecutive lossy flake gate
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/live_reliability_e2e.py --build-dir build-local --repeat 30
```

### Running All Five Experiment Presets & Generating Plots

```sh
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 src/reliability_runner/experiments.py \
  --preset all --size quick --build-dir build-local --results-dir results/live

/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tools/plot_results.py \
  --results-dir results/live --out-dir results/plots
```

This produces:
- `results/live/redundancy_sweep/aggregate-redundancy_sweep.csv`
- `results/live/scheduler_contention/aggregate-scheduler_contention.csv`
- `results/live/history_snapshot_sweep/aggregate-history_snapshot_sweep.csv`
- `results/live/snapshot_tamper/aggregate-snapshot_tamper.csv`
- `results/live/scalability/aggregate-scalability.csv`
- `results/plots/chart1_redundancy_gap_rate.png` .. `chart6_latency_cdf.png`

---

## 2. Two-Laptop LAN Testing (`udp_lan`)

Assume:
- **Laptop A (Exchange Server):** IPv4 address `192.168.1.10` on interface `en0` (macOS) or `eth0`/`wlan0` (Linux).
- **Laptop B (Workload & Subscribers):** IPv4 address `192.168.1.20` on the same subnet.

### Step 1: Check Multicast Routing & Firewall on Both Laptops

On **macOS**:
```sh
# Find your active LAN IPv4 address
ipconfig getifaddr en0

# Verify multicast route for 239.1.1.0/24 points to en0
netstat -rn | grep 239
# If needed, bind multicast route to en0:
sudo route add -net 239.1.1.0/24 -interface en0
```

On **Linux**:
```sh
ip -4 addr show
sudo ip route add 239.1.1.0/24 dev wlan0
```

> **Note on Wi-Fi Multicast:** Many campus/enterprise Wi-Fi APs drop or rate-limit IP multicast (`239.0.0.0/8`). Prefer a direct Ethernet cable, USB-C LAN adapter, or a local hotspot router with IGMP snooping disabled.

### Step 2: Start the Exchange on Laptop A (`192.168.1.10`)

```sh
./build-local/src/exchange --mode client \
  --oe-port 8080 \
  --dual-feed \
  --md-ip 239.1.1.1 --md-port 9999 \
  --feed-b-ip 239.1.1.2 --feed-b-port 10000 \
  --md-interface 192.168.1.10 \
  --recovery-port 9090 \
  --recovery-policy round-robin \
  --recovery-workers 2 \
  --history 65536 \
  --snapshot-interval 1000 --snapshot-retention 8 --snapshot-on-demand
```

Wait for `ORDER_ENTRY_READY port=8080` and `RECOVERY_SERVER_READY port=9090`.

### Step 3: Start Subscribers on Laptop B (`192.168.1.20`)

Because `reliable_subscriber` passes `--interface 192.168.1.20` (non-loopback), it automatically records `transport=udp_lan` in its output CSV and JSON:

```sh
./build-local/src/reliable_subscriber --dual-feed \
  --feed-a-ip 239.1.1.1 --feed-a-port 9999 \
  --feed-b-ip 239.1.1.2 --feed-b-port 10000 \
  --interface 192.168.1.20 \
  --recovery-server 192.168.1.10 --recovery-port 9090 \
  --subscriber lan-sub-1 \
  --loss-model independent --drop-a 0.10 --drop-b 0.10 --drop-seed 42 \
  --stop-when-idle-ms 500 \
  --csv results/lan-sub-1.csv \
  --latency-csv results/lan-sub-1.latency.csv
```

Wait for `SUBSCRIBER_READY id=lan-sub-1 feeds=ab`. Notice on Laptop A that the exchange logs:
`SUBSCRIBER_CONNECTED id=lan-sub-1 addr=192.168.1.20:...`

### Step 4: Run Crossing Order Flow from Laptop B (or Laptop A)

```sh
./build-local/src/workload_client --server 192.168.1.10 --port 8080 \
  --client-id 1 --duration 10 --rate 500 --symbol AAPL --side buy --seed 101 &
./build-local/src/workload_client --server 192.168.1.10 --port 8080 \
  --client-id 2 --duration 10 --rate 500 --symbol AAPL --side sell --seed 102 &
wait
```

500 ms after the workload clients finish, `lan-sub-1` automatically detects quiescence (`--stop-when-idle-ms 500`), reconciles `STATUS` with Laptop A, verifies its SHA-256 order book digest (`book_matches_exchange=1`), sends `BYE`, and writes `results/lan-sub-1.csv`.

---

## 3. Optional OS-Level Network Impairment (`os_netem`)

To test real kernel-level packet drops/delays instead of application-level `--drop-a`/`--drop-b`:

### Linux (`tc netem` on loopback or LAN NIC)
```sh
# Add 5% packet loss and 2ms +/- 0.5ms jitter to UDP Feed A port 9999
sudo tc qdisc add dev lo root handle 1: prio
sudo tc qdisc add dev lo parent 1:3 handle 30: netem loss 5% delay 2ms 0.5ms
sudo tc filter add dev lo protocol ip parent 1:0 prio 3 u32 match ip dport 9999 0xffff flowid 1:3

# Run subscriber with --loss-model none, then clean up:
sudo tc qdisc del dev lo root
```

### macOS (`dnctl` + `pfctl` dummynet pipe)
```sh
# Configure pipe 1 with 5% packet loss on UDP port 9999
sudo dnctl pipe 1 config plr 0.05
echo "dummynet out proto udp from any to 239.1.1.1 port 9999 pipe 1" | sudo pfctl -f -
sudo pfctl -e

# Clean up after experiment:
sudo pfctl -d
sudo dnctl -q flush
```
