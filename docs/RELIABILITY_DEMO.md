# Reliability demonstration

`reliability_demo` is an in-process demonstration of the reliability path. It
uses deterministic fault injection, not live multicast or TCP sockets.

Build it with the normal CMake build, then run one of these examples:

```sh
./build-reliability/src/reliability_demo \
  --events 20 --loss 0.25 --mode independent --seed 7 \
  --csv reliability-metrics.csv
```

```sh
./build-reliability/src/reliability_demo \
  --events 20 --loss 0.25 --mode single --seed 7
```

```sh
./build-reliability/src/reliability_demo \
  --events 20 --loss 0.25 --mode correlated --seed 7
```

The terminal output demonstrates Feed A/Feed B delivery, duplicate
suppression, gap detection, bounded-history recovery, ordered replay, snapshot
checkpoint verification, and summary metrics. Reuse the same seed to reproduce
the same fault decisions.

The executable is intentionally a local demo. A future phase must connect the
same interfaces to UDP Feed A/Feed B endpoints, a framed TCP recovery service,
live order-book snapshots, and pybind11 GUI telemetry.
