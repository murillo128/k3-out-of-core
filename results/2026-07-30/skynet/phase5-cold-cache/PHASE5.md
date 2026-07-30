# Phase 5 standing evidence

Issue #20 implements the bounded inclusive hierarchy:

```text
resident pageable host source
    -> bounded pageable cold cache
    -> bounded native pinned ring or explicit pageable fallback
    -> fixed-address CUDA hot cache
```

Checkpoint A accepted the mechanism with `PASS`, safety `YES`, in issue comment `5132379446` after a bounded correction that rejects non-pageable source configurations.

The F16 and MXFP4 parity matrix covers all-routed-key cold capacity and a two-slot forced-eviction cold cache, with a two-lane transfer ring. Disabled, native-pinned cold, and forced-pageable cold runs preserve exact prompt IDs, generated IDs, logical route/weight hashes, and full-vocabulary logits hashes. Each all-routed run records 285 cold hits and 51 misses; each forced-small run records 334 deterministic cold evictions. Source pinned bytes remain zero, ring/cold actual bytes remain within requested budgets, transfer/request references return to zero, and hot eviction records no writeback.

The native ring path queues both demanded lanes before one wave barrier. Forced fallback records zero pinned bytes, zero async/in-flight claims, synchronous copies, and the exact fallback reason. CPU, CUDA, and ASan/UBSan mechanism/fault suites cover allocation and budget rejection, copy/staging/pre-enqueue cleanup, stale and wrapped generations, busy trim/surrender, reinitialization, source pageability rejection, and teardown. Two 20-step warm runs record genuine cold hits without resource growth.

Performance and memory measurements are descriptive; Phase 5 claims no H2D/compute overlap and has no production throughput budget. Dedicated transfer streams/events and overlap remain Phase 7 work. CPU miss execution and `CPU_FALLBACK` equivalence remain Phase 8 work. The fixed ordinary-prompt tokenizer limitation and Phase 3 raw 22/24 performance failure remain visible.

`phase5-manifest.json` is the authoritative structured record. `verification-result.json` records strict verification of artifact identities, immutable Phase 4/model inputs, revisions, scope, resource accounting, fallback truthfulness, references, and exact validation commands.
