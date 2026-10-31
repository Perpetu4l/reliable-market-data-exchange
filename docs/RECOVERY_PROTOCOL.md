# Recovery TCP Protocol Specification (Version 2)

Status: **Implemented and Verified (`PROTOCOL_VERSION = 2`)**. The exchange hosts this TCP listener when `--recovery-port` is non-zero (default `9090`). `recovery_client` provides a CLI verification utility, and `reliable_subscriber` uses this protocol for initial `STATUS_REQUEST` reachability (`HELLO`), gap replay (`RECOVERY_REQUEST`), checkpoint fallback (`SNAPSHOT_REQUEST`), end-of-run quiescence and SHA-256 book-digest reconciliation (`STATUS_REQUEST`), and clean session termination (`BYE`).

Recovery traffic runs over TCP independently of the TCP order-entry port (`8080`) and UDP multicast feeds (`239.1.1.1:9999` and `239.1.1.2:10000`). Every TCP stream carries length-prefixed binary frames:

```text
u32 big-endian frame_payload_length
u8  message_type
... type-specific payload
```

`frame_payload_length` excludes the 4-byte length prefix, includes the 1-byte `message_type`, and is bounded by `MAX_FRAME_PAYLOAD_LENGTH = 1048576` (1 MiB). Partial reads are buffered until a complete frame arrives; oversized or unknown frame types are rejected immediately without unbounded memory allocation.

## Frame Type Summary

| Type | Value | Direction | Status |
| :--- | ---: | :--- | :--- |
| `RECOVERY_REQUEST` | `1` | Subscriber -> Exchange | Implemented (`subscriber_id`, `start_sequence`, `end_sequence`) |
| `RECOVERY_RESPONSE` | `2` | Exchange -> Subscriber | Implemented (`OK`, `TOO_OLD`, `BAD_REQUEST`, `ERROR` + raw events) |
| `SNAPSHOT_REQUEST` | `3` | Subscriber -> Exchange | Implemented (`minimum_sequence`) |
| `SNAPSHOT_RESPONSE` | `4` | Exchange -> Subscriber | Implemented (composite SHA-256 checkpoint + on-demand snapshot creation) |
| `STATUS_REQUEST` | `5` | Subscriber -> Exchange | Implemented (carries optional `subscriber_id` for session registration) |
| `STATUS_RESPONSE` | `6` | Exchange -> Subscriber | Implemented (Protocol v2: `protocol_version`, `latest_sequence`, `history_minimum_sequence`, `latest_book_digest`) |
| `RECOVERY_CHUNK` | `7` | Exchange -> Subscriber | Implemented (streamed chunk when replay exceeds 1 MiB) |
| `RECOVERY_COMPLETE` | `8` | Exchange -> Subscriber | Implemented (terminator frame after `RECOVERY_CHUNK` stream) |
| `BYE` | `9` | Subscriber -> Exchange | Implemented (Protocol v2: explicit subscriber session disconnect) |

---

## 1. `RECOVERY_REQUEST` (`type = 1`)

```text
u16 subscriber_id_length
bytes subscriber_id (UTF-8)
u64 start_sequence
u64 end_sequence
```

Sequences are 1-based, global across all instruments, and inclusive (`1 <= start_sequence <= end_sequence`). Empty `subscriber_id`, zero `start_sequence`, or inverted ranges (`start_sequence > end_sequence`) return `BAD_REQUEST`.

## 2. `RECOVERY_RESPONSE` (`type = 2`), `RECOVERY_CHUNK` (`type = 7`), `RECOVERY_COMPLETE` (`type = 8`)

```text
u8  status                 # 0=OK, 1=TOO_OLD, 2=NOT_AVAILABLE, 3=BAD_REQUEST, 4=ERROR
u32 event_count
repeat event_count:
  u32 original_event_length
  bytes original_market_data_datagram
u16 detail_length
bytes detail (UTF-8)
```

Each event payload is the exact binary market-data datagram (`MessageHeader` + body) originally published on UDP multicast. When a replay exceeds `1 MiB`, the server splits the events across multiple `RECOVERY_CHUNK` (`type = 7`) frames (each formatted identically to `RECOVERY_RESPONSE`), followed by an empty `RECOVERY_COMPLETE` (`type = 8`) frame.

## 3. `SNAPSHOT_REQUEST` (`type = 3`) and `SNAPSHOT_RESPONSE` (`type = 4`)

### Request (`type = 3`)
```text
u64 minimum_sequence
```

### Response (`type = 4`)
```text
u8  status
u64 checkpoint_sequence
u32 snapshot_instrument_id       # 0 for composite multi-instrument snapshot
u64 timestamp_ns
u32 level_count
repeat level_count:
  u32 instrument_id
  u64 price_in_cents
  u64 aggregate_quantity
  u8  side                       # BUY=1, SELL=2
u16 previous_hash_length; bytes previous_hash (hex SHA-256)
u16 hash_length;          bytes hash (hex SHA-256)
u16 detail_length;        bytes detail (UTF-8)
```

With `--snapshot-on-demand` enabled (default on in `exchange`), if no periodic checkpoint has `checkpoint_sequence >= minimum_sequence` and the exchange has already published at or past `minimum_sequence`, `RecoveryServer::send_snapshot` atomically constructs and chains a new composite checkpoint from `published_book_` under `state_mutex_`. `reliable_subscriber` verifies `SnapshotHasher::verify(snapshot)` before applying levels to its local `BookBuilder`.

## 4. `STATUS_REQUEST` (`type = 5`) and `STATUS_RESPONSE` (`type = 6`) — Protocol v2

### Request (`type = 5`)
```text
# Legacy empty payload (0 bytes) OR Protocol v2 payload:
u16 subscriber_id_length
bytes subscriber_id (UTF-8)
```
When `subscriber_id` is non-empty, the exchange records or refreshes the subscriber session and prints `SUBSCRIBER_CONNECTED id=<id> addr=<ip:port>` on first sight.

### Response (`type = 6`, Protocol v2)
```text
u8  protocol_version             # 2
u64 latest_sequence
u64 history_minimum_sequence
u16 latest_book_digest_length
bytes latest_book_digest         # 64-char lowercase hex SHA-256 of canonical BookBuilder state at latest_sequence
```
`RecoveryServer::record_published_event` updates `history_`, `published_book_`, and periodic `snapshots_` under a single `state_mutex_`, so `latest_sequence` and `latest_book_digest` in `STATUS_RESPONSE` always describe the exact same point in the published stream.

## 5. `BYE` (`type = 9`) — Protocol v2

```text
u16 subscriber_id_length
bytes subscriber_id (UTF-8)
```
Sent by `reliable_subscriber` after final `STATUS` reconciliation. The exchange removes the subscriber from its active session table and emits `SUBSCRIBER_DISCONNECTED id=<id> reason=bye`. Sessions that stop sending traffic for more than 5 seconds without a `BYE` frame are reaped with `SUBSCRIBER_DISCONNECTED id=<id> reason=timeout`.
