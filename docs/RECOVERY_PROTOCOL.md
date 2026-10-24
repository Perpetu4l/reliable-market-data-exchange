# Recovery TCP protocol

Status: codec implemented; no exchange listener or subscriber client is wired
to it yet. This document is the contract for that next step.

Recovery traffic uses TCP independently from order entry. The planned default
port is `9100`. A TCP stream contains one or more frames:

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
| `RECOVERY_REQUEST` | 1 | Codec implemented |
| `RECOVERY_RESPONSE` | 2 | Codec implemented |
| `SNAPSHOT_REQUEST` | 3 | Reserved |
| `SNAPSHOT_RESPONSE` | 4 | Reserved |
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
market-data protocol. A future server must chunk ranges across frames instead
of silently truncating them.
