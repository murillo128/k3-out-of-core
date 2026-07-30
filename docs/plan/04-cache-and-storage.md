# Persistent caches and GGUF storage

## Phase 4 — Persistent hot cache in accelerator memory

### Objectives

- Create correct fixed-address hot slots and runtime ID remapping while all source experts remain host-resident.

### Tasks

#### 4.1 Hot-slot allocation

- [x] Allocate persistent slots outside the graph allocator.
- [x] Allocate the gate/up/down physical regions required by each logical slot.
- [x] Preserve stable addresses for graph compatibility.
- [x] Reserve CUDA/cuBLAS workspace before consuming all VRAM.
- [x] Support cache trim/surrender on memory pressure.

#### 4.2 Directory

- [x] Implement bidirectional mapping:

  ```text
  ExpertKey -> slot
  slot -> ExpertKey
  ```

- [x] Maintain generation/version counters to detect stale handles.
- [x] Use the synchronized execution-ID tensor as the Phase 4 device-visible mapping; a persistent GPU directory is not required.
- [x] Update mapping in place without per-token allocation.

#### 4.3 State machine

Implement explicit states:

```text
FREE
RESERVED
LOADING
READY
PINNED
EVICTING
FAILED
```

- [x] Define legal transitions and assertions.
- [x] Pin slots while kernels use them.
- [x] Prevent eviction with nonzero reference count or outstanding request ownership.

#### 4.4 Synchronous correctness path

- [x] Initially populate a missed slot from host source synchronously.
- [x] Remap selected IDs to slot IDs.
- [x] Execute existing CUDA MXFP4 grouped MoE kernels against slot buffers.
- [x] Preserve canonical output reduction.

This synchronous path is a phase-isolation mechanism, not the final transport.

#### 4.5 Tests

- [x] Deterministic capacity/eviction tests.
- [x] Stale-handle and generation tests.
- [x] Compute-epoch persistence tests.
- [x] Repeated warm inference.
- [x] Allocation, injected-copy, abort, and scheduler-error cleanup.

### Exit gate

- Forced hot-cache inference matches monolithic logits/tokens.
- True cross-epoch hits occur from cache-owned memory.
- No stale data or graph-temporary dependency exists.

Status: `ACCEPTED`. Issue #17 standing evidence satisfies the exit gate; Checkpoint A and final complete-PR review returned `PASS_WITH_NOTES`, safety `YES`. PR #18 squash-merged as `b196cc07249726651d39aaa624703bc4256a3012` with nested `llama.cpp` head `57fe1eabbe3d0ced59096a0744efc91e286fb1c7`.

---

## Phase 5 — Cold host-memory cache and pinned transfer ring

### Objectives

- Add an explicit, bounded host cache and decouple large cold capacity from pinned transfer capacity.

### Tasks

#### 5.1 Cold slots

- [x] Allocate aligned cold slots by bytes, not only expert count.
- [x] Implement cold directory and state machine.
- [x] Enforce the initial inclusive invariant for hot entries.
- [x] Define host-memory pressure and allocation failure behavior.
- [x] Leave hugepage advice optional and unused; correctness does not depend on it.

#### 5.2 Pinned ring

- [x] Allocate bounded pinned/registered transfer buffers.
- [x] Support multiple queued H2D transfers per synchronous wave.
- [x] Fall back cleanly when pinned acquisition fails.
- [x] Track pinned-memory budget and expose acquisition/fallback telemetry.

#### 5.3 Promotion

- [x] Cold hit copies to a pinned lane if needed, then asynchronously or synchronously to hot according to the current phase.
- [x] Preserve ready hot hits without recopy or eviction; current-layer misses complete in bounded waves.
- [x] Populate a cold slot from the existing pageable host-resident monolithic tensor for this phase.

#### 5.4 Eviction

- [x] Implement deterministic LRU mechanism for tests.
- [x] Prevent cold eviction while hot, transferring, loading, or referenced.
- [x] Verify hot eviction requires no writeback.

### Exit gate

- Hot miss/cold hit behavior is correct and bounded.
- Inclusive-cache invariants hold under stress.
- No whole-model pinning occurs.

Status: `ACCEPTED`. Issue #20 standing evidence satisfies the exit gate; Checkpoint A returned `PASS`, safety `YES`, and final complete-PR review returned `PASS_WITH_NOTES`, safety `YES`, with no required delta. PR #21 squash-merged as `c5512bc073ae7aab4a14773028828e516e16f3f6` with nested `llama.cpp` head `26317ee1d848dd7a73f22a3666a055cad5d5cb03`.

---

## Phase 6 — GGUF-backed storage and synchronous demand reads

### Objectives

- Stop treating the complete expert tensor as host-resident.
- Read an absent `ExpertBundle` from its exact GGUF spans into bounded buffers/cold slots.

### Tasks

#### 6.1 Storage API

- [x] Open and retain explicit file handles supplied by the GGUF loader.
- [x] Validate all offsets and sizes at model load.
- [x] Support split GGUF files.
- [x] Define storage lifetime through model unload.

#### 6.2 Read path

- [x] Implement a simple robust read-at-offset path first.
- [x] Read all three projections for a logical expert.
- [x] Verify destination extents and independently captured source/split identities before standing evidence.
- [x] Compare source-read and final-destination digests before publishing a cold entry; poison storage on mismatch.
- [x] Handle short read, EINTR, I/O error, and cancellation.
- [x] Ensure the complete expert tensor is not accidentally faulted into RAM by another reference.

#### 6.3 Model loading changes

- [x] Keep routed expert metadata resident but avoid eagerly materializing routed expert bytes.
- [x] Keep resident/shared/latent tensors in their normal backend allocation.
- [x] Make out-of-core mode explicit and validate configuration before inference.

### Exit gate

- With cold/hot capacities forced small, experts are read from GGUF on demand and inference matches the monolithic baseline.
- Host memory remains within the configured budget.

Status: `ACCEPTED` for implementation and Checkpoint A. Issue #22 standing evidence records exact original/split F16 and MXFP4 parity, bounded forced-eviction demand reads, cold-hit-without-reread behavior, integrity-before-publication, independent source-span-to-cold SHA-256 equality, split-cross-file bundles, bounded administration and handle lifetime, cancellation cleanup/retry, final-head sanitizers, and two 20-step captures per representation. Final complete-PR review remains the acceptance gate.

---
