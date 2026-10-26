# Recovery TCP protocol

Status: the exchange hosts this listener when `--recovery-port` is non-zero
(default `9090`), `recovery_client` is a command-line request client, and
`reliable_subscriber` invokes it after a live UDP sequence gap. TCP snapshot
transfer is implemented for in-memory composite checkpoints. There is no
persistent store, authentication, encryption, or snapshot catch-up replay yet.

Recovery traffic uses TCP independently from order entry. The default port is
`9090`. A TCP stream contains one or more frames:

```text
u32 big-endian frame_payload_length
u8  message_type
... type-specific payload
```

The length excludes the four-byte prefix, includes the type byte, and is
limited to 1 MiB. Receivers retain partial input until a whole frame arrives;
invalid lengths or unknown types are rejected rather than allocating arbitrary
memory.

| Type | Value | Status |
| --- | ---: | --- |
| `RECOVERY_REQUEST` | 1 | Implemented and accepted by the exchange listener |
| `RECOVERY_RESPONSE` | 2 | Implemented for retained-event replies |
| `SNAPSHOT_REQUEST` | 3 | Implemented |
| `SNAPSHOT_RESPONSE` | 4 | Implemented for in-memory checkpoints |
| `STATUS_REQUEST` | 5 | Reserved |
| `STATUS_RESPONSE` | 6 | Reserved |

## Recovery request

```text
u16 subscriber_id_length
bytes subscriber_id (UTF-8)
u64 start_sequence
u64 end_sequence
```

Sequences are global and inclusive. Empty IDs, zero start sequences, and
inverted ranges are invalid.

## Recovery response

```text
u8  status                 # OK, TOO_OLD, BAD_REQUEST, ERROR
u32 event_count
repeat event_count:
  u32 original_event_length
  bytes original_market_data_datagram
u16 detail_length
bytes detail (UTF-8)
```

The event bytes are the original binary market-data datagrams, preserving the
existing packed `MessageHeader`; recovery does not translate or replace the
market-data protocol. The current server rejects requests that would make the
encoded reply exceed the frame limit instead of silently truncating them.

## Snapshot request and response

A snapshot request has one field:

```text
u64 minimum_sequence
```

The exchange returns the earliest retained checkpoint at or after that
sequence. Returning an earlier checkpoint would leave an unreplayed portion of
the requested gap and is therefore unsafe. A successful response contains the
checkpoint sequence, timestamp, its SHA-256 hash-chain fields, and each
absolute price level:

```text
u8  status
u64 checkpoint_sequence
u32 snapshot_instrument_id       # 0 for a composite multi-instrument snapshot
u64 timestamp_ns
u32 level_count
repeat level_count:
  u32 instrument_id
  u64 price_in_cents
  u64 aggregate_quantity
  u8  side                       # BUY=1, SELL=2
u16 previous_hash_length; bytes previous_hash
u16 hash_length;          bytes hash
u16 detail_length;        bytes detail
```

`reliable_subscriber` verifies the checkpoint hash, applies its absolute price
levels to its local market-data book, and only then advances sequence
continuity. A bad hash is a recovery failure, not a successful resync.

## Scheduling and service counters

`--recovery-policy fcfs` serves retained-event requests in arrival order;
`--recovery-policy round-robin` rotates among subscriber identities with
pending requests. At clean shutdown the exchange prints measured
connection/request/response counts, recovered-message count, per-subscriber
service count, and queue-wait average/max. Snapshot requests currently bypass
this fairness queue and are handled by the connection handler.

## Status reconciliation

`STATUS_REQUEST` has an empty payload. `STATUS_RESPONSE` is two big-endian
`u64` values: `latest_sequence` and `history_minimum_sequence`. At the end of
a timed subscriber run, `reliable_subscriber` requests this status and feeds
`latest_sequence` through its heartbeat gap detector before shutdown. A status
failure is reported as `failed_integrity`, rather than silently accepting a
possibly lost stream tail.

## Controlled live loss experiments

The exchange can withhold outbound **application messages** deterministically:

```text
--feed-a-loss RATE --feed-b-loss RATE --fault-seed N
--correlated-loss RATE --fault-seed N
```

Independent Feed A/B rates use distinct deterministic streams; correlated loss
uses one decision for both feeds. These controls run before multicast socket
sends, preserve the authoritative recovery history, and never drop heartbeats.
They are not evidence of OS-level or physical-network packet loss.
