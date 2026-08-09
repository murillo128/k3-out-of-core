# Issue 69 Checkpoint B — feed-path mechanism

Status: `PASS` (v3 exact-target independent review pending)

The exact nested target `968e138cab375cbca4406b82b756671eb103ea3e`
implements the bounded feed-path mechanism required by issue #69. The parent
mechanism commit `1ff16b32ccf101b6ed1e3eb67fcee22e2ef616ab` pins that target.
This replaces the failed v2 review target. Cold-ready hits and completed direct
storage reads now share the progress loop, and direct admissions preserve one
transfer lane while cold-ready work is pending. Local and two-GPU same-owner
gated regressions prove that cold H2D begins while a direct read remains blocked.
Terminal publication remains deterministic when that changes transfer order.
The earlier zero/default worker correction remains covered by
`test-expert-async-io`.

## Structural result

All four topology cells resolve the explicit positional worker count to exactly
two. Storage-backed `PROMOTE_AND_GPU` misses now read directly into bounded
pinned transfer lanes, enqueue H2D as individual reads complete, and publish hot
directory entries only after exact device readiness. These demand misses perform
no durable cold admission and no cold-to-lane staging copy.

| Cell | Workers | Direct promotions | Cold admissions | Stage bytes | Hot without cold | Decode tok/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| S0 | 2 | 5,920 | 0 | 0 | 268 | 1.8082 |
| S1 | 2 | 5,491 | 0 | 0 | 536 | 2.3495 |
| D1 | 2 | 5,479 | 0 | 0 | 536 | 1.9013 |
| A1 | 2 | 4,453 | 0 | 0 | 1,573 | 2.4376 |

The trace-enabled S0 capture records 3,142,518 µs of different-flight
storage/H2D overlap across 13,035 pairs, with 5,950 transfer records and no
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
[`issue69-checkpoint-b-feed-path-v3`](https://github.com/murillo128/k3-out-of-core/releases/tag/issue69-checkpoint-b-feed-path-v3)
release.
