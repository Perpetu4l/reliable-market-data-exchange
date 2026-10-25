# Live reliability implementation plan

This file tracks the conversion from the current deterministic in-process
reliability harness to a measured live UDP/TCP implementation. It deliberately
does not describe unavailable functionality as completed.

- [x] Phase 0 — baseline current behaviour, portability cleanup, repository hygiene.
- [x] Phase 1 — correct sequence, gap, fault, snapshot/hash, and scheduler logic with unit tests.
- [x] Phase 2 — dual multicast transport, heartbeats, bounded history, and TCP recovery server in `exchange`.
- [ ] Phase 3 — real `reliable_subscriber` executable (implemented); loopback end-to-end test remains blocked by this sandbox's socket policy.
- [ ] Phase 4 — headless multi-process experiment runner and provenance-preserving results.
- [ ] Phase 5 — GUI control room wired only to live processes and collected CSVs.
- [ ] Phase 6 — make the in-process baseline use the shared correctness library and declare provenance.
- [ ] Phase 7 — protocol, live-testing, design-errata, README, and verification documentation.
- [ ] Phase 8 — only after the live path is verified, prepare a viva script from actual commands.

## Branch and commit note

Work is on `reliability-live`. Logical commits are made after each verified
phase. Generated build directories and results are ignored and must not be
committed.
