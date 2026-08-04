# Persistent hot cache, cold cache, and GGUF-backed storage

## Phase 4 — Persistent hot cache in accelerator memory

### Objectives

- Keep a bounded set of routed experts in fixed-address accelerator memory.
- Establish logical-to-physical slot remapping without changing model routing.

### Tasks

#### Cache-owned storage

- Allocate fixed-address expert slots outside graph-temporary memory.
- Keep gate/up/down projections and sidecars atomic at the expert-bundle lifecycle level.
- Support bounded configurable capacity.
- Preserve stable slot addresses until an explicit quiescent surrender/reinitialization.
- Reserve graph/backend workspace before allocating persistent cache storage.

#### Directory and generations

- Maintain bidirectional mappings between logical `ExpertKey` and physical slot.
- Increment slot generations whenever content identity changes.
- Reject stale handles and stale slot generations.
- Keep metadata and content publication atomic.
- Use a minimal deterministic LRU mechanism for validation, not as the final policy decision.

#### Execution remapping

- Preserve original selected expert IDs for route observation.
- Create distinct execution IDs only in cached mode.
- Materialize logical IDs at the accepted scheduler checkpoint, populate required slots synchronously, and rewrite execution IDs to physical slots.
- Keep activation, final routing weights, graph semantics after the checkpoint, kernels, and canonical reduction unchanged.

#### Lifetime and failure

- Hold graph-generation and request leases while referenced.
- Support cancellation, compute failure, context destruction, model unload, trim, surrender, and reinitialization.
- Prevent slot eviction while pinned or referenced.
- Keep disabled and resident-provider paths unchanged.

### Exit gate

- F16 and MXFP4 CUDA hot-cache execution matches the monolithic/disabled reference under accepted correctness semantics.
- A real miss becomes a persistent cross-epoch hit with stable address and no repeated H2D copy.
- Directory, generation, pin, eviction, trim, surrender, and failure tests pass.
- Persistent expert bytes are not owned by graph-temporary or scheduler-staging storage.
- Disabled and resident paths perform no hot-cache work.
- Memory and administration remain bounded.

---

## Phase 5 — Cold host-memory cache and pinned transfer ring

### Objectives

- Add a bounded pageable host cache beneath the hot cache.
- Decouple large cold capacity from a smaller bounded pinned/registered H2D transfer ring.

### Tasks

#### Cold cache

- Add an explicitly byte-budgeted pageable host cache for complete expert bundles.
- Derive exact bundle layout and reject ambiguous or non-contiguous per-expert spans.
- Use generation-checked bidirectional mappings and deterministic validation LRU.
- Keep the initial discrete-GPU hierarchy inclusive: every hot entry has exact cold backing.
- Populate cold misses from the still-resident host source in this phase.
- Prohibit hot eviction writeback.

#### Transfer ring

- Allocate a separately byte-budgeted ring with lanes sized for one complete aligned bundle.
- Prefer native CUDA host memory or bounded registration for the ring only.
- Provide an explicit bounded pageable synchronous fallback.
- Queue multiple H2D copies before one completion barrier when native pinned/registered transfer is available.
- Never pin/register the full cold cache, source tensors, or whole model.

#### Promotion and coherence

- Serve hot miss/cold hit without rereading the monolithic source.
- Serve cold miss by atomically populating cold before promotion.
- Coalesce duplicate keys in one checkpoint.
- Publish hot only after complete transfer and preserve cold backing references.
- Prevent eviction of loading, pinned, referenced, hot-backed, or in-flight cold entries.
- Roll back partial source, lane, H2D, synchronization, or cancellation failures.

#### Validation and telemetry

- Record exact requested/effective cold and ring bytes, slot/lane counts, references, generations, copies, H2D bytes, waves, synchronizations, and fallback state.
- Prove source-copy bytes increase only on cold misses.
- Prove cold-to-lane/H2D bytes increase only on hot misses.
- Prove hot hits perform no transfer.
- Stress trim, surrender, reinitialize, cancellation, unload, and failure cleanup.

### Exit gate

- F16 and MXFP4 tiered execution preserves exact accepted routes, weights, generated IDs, and logits.
- Repeated hot evictions become genuine cold hits without source reread.
- Inclusive hot/cold invariants survive deterministic hot and cold eviction.
- Native bounded multi-lane and forced pageable fallback paths are truthful and correct.
- Cold and pinned allocations never exceed their configured budgets.
- Source tensors and the cold arena remain unpinned/unregistered.
- References, lanes, and handles balance after success, failure, cancellation, trim, surrender, and unload.
- No disk I/O, async compute overlap, CPU miss execution, prefetch, or production policy is introduced.

---

## Phase 6 — GGUF-backed storage and synchronous demand reads

### Objectives

- Stop eagerly materializing routed expert payloads in cold-cache mode.
- Read exact missing bundles positionally from loader-owned GGUF handles into cold slots.

### Tasks

#### Model-owned storage

- Introduce focused expert storage separate from cache, transport, and policy.
- Duplicate loader-opened read-only handles without reopening by path.
- Validate native file identity, size, split index, alignment, and lifetime.
- Build an immutable split-aware `ExpertKey -> spans` directory.
- Support one-file and split GGUFs, including bundles whose projections reside in different splits.

#### Deferred routed payloads

- Keep routed tensor metadata without allocating, mmap-binding, prefetching, mlocking, or loading routed payload bytes.
- Keep routed `data` and `buffer` null in cold-cache mode.
- Preserve normal resident loading for shared, latent, router, attention, embedding, output, and other non-routed tensors.
- Reject unsupported load modes or any non-provider attempt to use deferred routed data.

#### Synchronous positional reads

- Use true positional reads with no shared cursor or read mutex.
- Read directly into reserved cold destinations with zero routed-payload scratch.
- Use bounded chunks and cancellation checks.
- Handle `EINTR`, partial positive reads, short reads, EOF, ordinary I/O errors, and cancellation deterministically.
- Publish a cold entry only after every required span and integrity check succeeds.
- Keep ordinary I/O failure/cancellation retryable after cleanup and poison the provider on hard integrity mismatch.

#### Validation and accounting

- Compare populated cold bytes independently against immutable GGUF source spans.
- Validate original and generated-split F16/MXFP4 parity.
- Prove cold hit avoids reread and deterministic eviction causes exact reread.
- Prove file handles, directories, spans, cancellation scratch, and administration remain topology-bounded.
- Record zero routed allocation, mmap binding, prefetch, source pinning, and demand-read payload scratch.
- Preserve the accepted cold -> ring -> hot hierarchy with no storage-to-hot bypass.

### Exit gate

- F16 and MXFP4 original/split execution preserves exact accepted routes, weights, generated IDs, and logits.
- Every routed tensor is metadata-only in cold-cache mode and every non-routed tensor retains normal behavior.
- The storage directory covers every required routed projection/sidecar exactly once with checked bounds.
- First demand reads exact source spans, a cold hit reads zero bytes, and post-eviction demand rereads the exact bundle.
- Populated cold bundles are byte/SHA-256 exact against independent extraction.
- Error, cancellation, retry, poison, trim, surrender, reinitialize, and unload paths balance all resources.
- Owned cache/transfer bytes remain within configured budgets; storage administration is bounded and repeated runs do not grow hidden state.
- No asynchronous/direct I/O, prefetch, CPU miss execution, concurrency, UMA, multi-GPU, or new storage format is introduced.
