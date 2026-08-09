# Issue 69 Checkpoint B — feed-path mechanism

Status: `PASS` (replacement exact-target independent review pending)

The exact nested target `3386c35ff043c2a375b6a83cd6e01db1673be778`
implements the bounded feed-path mechanism required by issue #69. The parent
mechanism commit `046778ff9efd254ebda9c0e6bcefc554b1a75c43` pins that target.
This replaces the failed v1 review target: the zero/default worker setting now
resolves to the legacy device-derived count before unchanged transport
validation, while explicit nonzero positional counts retain their exact new
semantics. `test-expert-async-io` covers both branches.

## Structural result

All four topology cells resolve the explicit positional worker count to exactly
two. Storage-backed `PROMOTE_AND_GPU` misses now read directly into bounded
pinned transfer lanes, enqueue H2D as individual reads complete, and publish hot
directory entries only after exact device readiness. These demand misses perform
no durable cold admission and no cold-to-lane staging copy.

| Cell | Workers | Direct promotions | Cold admissions | Stage bytes | Hot without cold | Decode tok/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| S0 | 2 | 5,920 | 0 | 0 | 268 | 1.8291 |
| S1 | 2 | 5,491 | 0 | 0 | 536 | 2.4563 |
| D1 | 2 | 5,479 | 0 | 0 | 536 | 1.9208 |
| A1 | 2 | 4,453 | 0 | 0 | 1,573 | 2.3492 |

The trace-enabled S0 capture records 3,142,198 µs of different-flight
storage/H2D overlap across 12,948 pairs, with 5,950 transfer records and no
dropped records. A1 reads 36,272,472,064 backing bytes, below S1's
44,741,623,808 bytes for the same generated output.

The new lifetime test uses a cold tier smaller than the four-slot hot tier,
forces cold eviction, and verifies that all corresponding hot slots remain exact
and usable. Failure tests cover direct-read rollback, cancellation/drain,
generation rejection, multi-flight cleanup, and clean unload. Twelve focused
CTest targets pass; every captured runtime closes with zero active reads,
scheduler requests, ring events, cold references, and hot pins.

## Scope

This is mechanism evidence, not final performance acceptance. It contains one
fresh Mode-P process per S0/S1/D1/A1 cell plus one trace-enabled S0 process.
Checkpoint C still owns the Mode-C matrix, five paired throughput runs,
cold-capacity controls, post-structural CPU/Perfetto attribution, and final gates.

Raw workloads, resource samples, the invalid-worker rejection log, and focused
CTest output are checksum-addressed in [manifest.json](manifest.json) and
published in the immutable
[`issue69-checkpoint-b-feed-path-v2`](https://github.com/murillo128/k3-out-of-core/releases/tag/issue69-checkpoint-b-feed-path-v2)
release.
