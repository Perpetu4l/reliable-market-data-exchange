# Baseline findings — 2026-10-24

Commands below were run on the development host before beginning live
reliability work. They establish the starting point; they are not evidence of
a live networked recovery system.

## Existing scope

- `exchange` has one UDP multicast publisher and a TCP order-entry server.
- `reliability_demo` creates feeds, history, snapshots, and recovery objects
  inside one process. The exchange and `src/client` do not use those classes.
- The existing subscriber abstraction is not a process and does not open UDP
  or TCP sockets.

## Reproduced observations

```text
./build-local/src/reliability_demo --events 1000 --loss 0.9 --mode single \
  --seed 42 --history 128 --snapshot-interval 0
```

The baseline reported `delivered events: 977`, `recovered events: 886`, and
`unrecoverable gaps: 0`. Its final tail is not independently reconciled against
the expected final sequence: there is no heartbeat or status request. Thus it
cannot prove complete delivery.

```text
./build-local/src/reliability_demo --events 30 --loss 0.5 --mode single \
  --seed 1 --history 128 --snapshot-interval 0
```

The first event delivered was sequence 6, yet no missing head range was
reported. `SequenceTracker` adopts the first received event as its starting
point, which is incorrect for a global stream beginning at sequence 1.

```text
./build-local/src/reliability_demo --events 12 --loss 0.30 --mode single \
  --seed 9 --history 0 --snapshot-interval 1
```

The run reported snapshot recovery, but the current snapshot builder is called
with an empty book state. The FNV-1a placeholder is not cryptographic and the
snapshot does not reconstruct market state.

## Environment limits

The sandbox denied binding a local TCP listener (`Bind failed ... Operation not
permitted`), so live socket and multicast behaviour cannot be verified here.
It must be exercised on a normal macOS/Linux host after implementation.
