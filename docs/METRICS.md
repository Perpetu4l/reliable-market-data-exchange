# Metrics & Measurement Specification (`docs/METRICS.md`)

Every metric emitted by `reliable_subscriber`, `exchange`, `live_reliability_runner.py`, and the GUI is formally defined below. All outputs carry explicit provenance (`transport` and `loss_injection`) and adhere to strict sequence-accounting invariants.

---

## 1. Provenance Labels

| Field | Allowed Values | Definition |
| :--- | :--- | :--- |
| `transport` | `inprocess` \| `udp_loopback` \| `udp_lan` | `inprocess` for `reliability_demo`; `udp_loopback` when the multicast interface is `127.0.0.1`/`localhost`; `udp_lan` when bound to a non-loopback NIC IPv4 address. |
| `loss_injection` | `none` \| `app_level_receive_side` \| `app_level_publisher_side` \| `os_netem` | Identifies where packet drops/impairments occurred. Application-level withholding is never labelled as OS or physical network packet loss. |

---

## 2. Sequence Accounting Invariants

Let $L$ be the converged `exchange_latest_sequence` returned by two consecutive identical `STATUS_RESPONSE` frames after the subscriber stops its UDP receivers. Every sequence number $s \in \{1, \dots, L\}$ is tracked in per-sequence bitsets inside `DualFeedSubscriber`:

- `arrived_a_[s]`: Sequence $s$ arrived on UDP Feed A (after receive-side impairment).
- `arrived_b_[s]`: Sequence $s$ arrived on UDP Feed B (after receive-side impairment).
- `delivered_source_[s]`: Which source first delivered sequence $s$ in order to `BookBuilder`:
  - `1` (`delivered_via_feed_a`): Delivered from Feed A (live or buffered).
  - `2` (`delivered_via_feed_b`): Delivered from Feed B (live or buffered).
  - `3` (`delivered_via_recovery`): Delivered from a TCP `RECOVERY_RESPONSE` / `RECOVERY_CHUNK` replay.
- `snapshot_covered_[s]`: Sequence $s$ was not individually delivered ($delivered\_source\_[s] = 0$), and was bypassed by a verified `SNAPSHOT_RESPONSE` checkpoint at sequence $S \ge s$.

### Exact Decomposition Identity

For every run with a converged $L$:

$$\text{events\_expected} = L$$

$$\text{delivered} = \text{delivered\_via\_feed\_a} + \text{delivered\_via\_feed\_b} + \text{delivered\_via\_recovery}$$

$$\text{events\_expected} = \text{delivered} + \text{events\_covered\_by\_snapshot} + \text{permanently\_lost}$$

The boolean flag `accounting_valid` checks that these equations hold with zero overlap (`delivered_source_[s] != 0` and `snapshot_covered_[s]` are mutually exclusive for every $s \in \{1, \dots, L\}$) and that no sequence $> L$ was delivered. Any violation sets `run_status = failed_accounting` and exits with code `3`.

---

## 3. Dual-Feed Redundancy Metrics

Computed from the arrival bitsets `arrived_a_[1..L]` and `arrived_b_[1..L]`:

- **`potential_single_feed_gaps`**:
  $$\left|\{s \in \{1, \dots, L\} : \neg \text{arrived\_a}[s]\}\right|$$
  The exact count of sequences missed on Feed A that would have required TCP recovery in a single-feed deployment.
- **`gaps_avoided_by_second_feed`**:
  $$\left|\{s \in \{1, \dots, L\} : \neg \text{arrived\_a}[s] \land \text{arrived\_b}[s]\}\right|$$
  Sequences missed on Feed A that arrived on Feed B and therefore avoided a TCP recovery request.
- **`redundancy_effectiveness`**:
  - `n/a` (`std::nullopt`) in single-feed mode or when `potential_single_feed_gaps == 0`.
  - Otherwise:
    $$\frac{\text{gaps\_avoided\_by\_second\_feed}}{\text{potential\_single\_feed\_gaps}} \in [0.0, 1.0]$$

---

## 4. Per-Missing-Event Repair Latency & Request RTT

### Why Per-Missing-Event Latency?
Measuring only the TCP request/response round-trip time after `sleep_for(gap_grace_ms)` undercounts the true delay experienced by the subscriber's order book and ignores events repaired by the second multicast feed.

### Clock & Start/End Timestamps
- **Start timestamp ($t_{\text{gap}}(s)$):** Recorded at the exact instant `SequenceTracker::on_sequence` or `on_heartbeat` detects that sequence $s$ is missing (`seq > expected_sequence()`), **before** `gap_grace_ms` and before recovery worker queueing.
- **End timestamp ($t_{\text{deliv}}(s)$):** Recorded at the exact instant sequence $s$ is delivered in-order to `BookBuilder::apply` (or covered by `BookBuilder::apply_snapshot`).
- **Sample value:** $\Delta t(s) = t_{\text{deliv}}(s) - t_{\text{gap}}(s)$ in nanoseconds.

### Four Latency Classes
1. **`other_feed` (`MissingEventClass::OTHER_FEED`):** Sequence $s$ was missing when a higher sequence arrived on one feed, and was subsequently filled by the second UDP feed without a TCP recovery replay.
2. **`tcp_recovery` (`MissingEventClass::TCP_RECOVERY`):** Sequence $s$ was delivered via TCP `RECOVERY_RESPONSE` / `RECOVERY_CHUNK`.
3. **`snapshot` (`MissingEventClass::SNAPSHOT`):** Sequence $s$ was covered by a verified `SNAPSHOT_RESPONSE` checkpoint after `TOO_OLD`.
4. **`tcp_request_rtt`:** Separate network/server round-trip timer around `RecoveryTcpClient::request_missing_data` (excludes `gap_grace_ms` and local worker wait before dequeue).

### Nearest-Rank Percentiles & Sample Sufficiency Flags
Given $N$ sorted samples $x_1 \le x_2 \le \dots \le x_N$:
- Nearest-rank index for percentile $p \in (0, 100]$:
  $$k = \max\left(1, \left\lceil \frac{p}{100} \cdot N \right\rceil\right), \quad \text{percentile}(p) = x_k$$
- **`insufficient_samples`:** `1` when $N < 30$ (`0` when $N \ge 30$).
- **`insufficient_p999`:** `1` when $N < 1000$ (`0` when $N \ge 1000$).

---

## 5. Scheduler Contention & Fairness Metrics (`EXCHANGE_RESULT`)

Let $c_i$ be the number of requests served for subscriber $i \in \{1, \dots, M\}$.

1. **`recovery_fairness_jain_ppm` (Overall Served-Count Jain Index):**
   $$J_{\text{overall}} = \frac{\left(\sum_{i=1}^M c_i\right)^2}{M \sum_{i=1}^M c_i^2} \times 10^6$$
   *Note:* When one subscriber has 50% loss and others have 2% loss, total served counts $c_i$ reflect **demand asymmetry**, so $J_{\text{overall}}$ is low (~`0.30`) under both FCFS and RR once all requests complete.
2. **`recovery_saturated_fairness_jain_ppm` (Saturated-Window Jain Index):**
   Computed over requests dequeued while the scheduler had at least 2 active subscriber queues (`queue_depth_at_enqueue >= 2`), measuring how fairly the scheduler allocates service turns during active contention bursts.
3. **Per-Subscriber Queue Wait Percentiles (`recovery-requests.csv` & `EXCHANGE_RESULT`):**
   Every request logs `enqueue_ns`, `dequeue_ns`, `complete_ns`, `queue_wait_us`, `service_time_us`, and `queue_depth_at_enqueue`. The decisive fairness metric is the quiet subscribers' `p95` and `p99` queue wait and end-to-end recovery latency under contention.
