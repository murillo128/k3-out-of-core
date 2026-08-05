# Cross-model portability follow-up — heterogeneous expert layout classes

This addendum records the architecture work exposed by the bounded DeepSeek-V4-Flash validation in issue #45 and PR #47. It is an independent cross-model portability track, not a replacement for Kimi K3 Phase 12 and not a change to the accepted Phase 9 or Phase 10 defaults.

## Observed boundary

The exact `UD-Q3_K_XL` artifact executes normally with the provider disabled, and the current loader-owned split/source-span, deferred-payload, synchronous-storage, and buffered-`io_uring` paths are valid for the artifact.

The artifact contains three complete routed-expert bundle layout classes:

- 41 layers: `IQ3_XXS / IQ3_XXS / MXFP4`;
- one layer: `MXFP4 / MXFP4 / Q6_K`;
- one layer: `IQ3_XXS / IQ3_XXS / Q6_K`.

The current provider derives one prototype layout and uses it to size hot slots, cold-cache entries, and the transfer ring. It therefore fails closed before cache allocation when a second class is discovered. This is an architecture boundary, not an I/O defect or a DeepSeek-specific exception to patch around.

## Objectives

- Represent a bounded number of heterogeneous routed-expert layout classes under the existing model-owned provider.
- Preserve immutable `ExpertKey(layer, original_expert_id)`, routing weights, canonical accumulation order, single-flight semantics, generations, cancellation, and unload safety.
- Keep storage, policy, scheduling, and transport architecture-neutral; do not add a model-specific provider or cache hierarchy.
- Account independently for logical cache state and physical bytes owned by each layout class.
- Preserve the existing one-layout K3 path without behavioral or default-policy changes.
- Resume the DeepSeek-V4 provider-enabled slot/kernel probe and full-model validation only after the class boundary is accepted and proven bounded.

## Design decisions required before implementation

A controlling issue must resolve these choices rather than leaving them to the executor:

1. **Physical slot organization**
   - Compare universal slots sized to the maximum class against per-class banks.
   - Select one using measured memory waste, fragmentation, victim flexibility, transfer-ring complexity, and failure semantics.

2. **Layout-class identity**
   - Derive a deterministic sealed identity from required projection/component roles, GGML types, logical shapes, physical strides, aligned source bytes, runtime transforms, and target buffer requirements.
   - Reject missing, ambiguous, mutable, or unsupported identities before worker start or cache allocation.

3. **Bounded resources**
   - Declare a hard maximum class count.
   - Bind whole-slot hot capacity, cold bytes, pinned/registered transfer lanes, events, queue entries, and administrative memory per class or through an explicitly shared bound.
   - Fail closed when the requested class set cannot satisfy minimum useful capacity while preserving declared host and device headroom.

4. **Policy semantics**
   - Preserve global hot/cold `LRU` with `ALWAYS` admission as the null/default behavior.
   - Keep one logical replacement policy across experts unless evidence justifies a separately designed class-aware policy.
   - Do not let per-class allocation silently convert logical LRU into fixed per-model or per-layer partitions.

5. **Compatibility**
   - The existing K3 single-class descriptor, storage, cache, transfer, scheduler, and provider behavior must remain equivalent.
   - Generic class support may not introduce `deepseek4` branches across storage, scheduler, cache, or transport.

## Validation

The implementation issue must include at least two risk checkpoints.

### Checkpoint A — heterogeneous class mechanism

- Add a deterministic synthetic mixed-layout fixture with at least three classes and different bundle/aligned sizes.
- Prove class discovery, sealing, lookup, resource sizing, admission, eviction, promotion, cancellation, stale-completion rejection, cleanup, and repeated load/unload.
- Prove exact physical-byte accounting and bounded pinned, pageable, VRAM, event, queue, and administrative memory.
- Re-run the accepted K3 focused suites and demonstrate that the one-class path remains unchanged.
- Re-run the real DeepSeek inventory and execute one complete expert from every observed class through storage -> cold -> ring -> fixed CUDA slot -> routed expert operation.
- Require route and type-specific numerical parity against the provider-disabled CUDA path where possible.

### Checkpoint B — resumed full-model validation

- Reuse the exact artifact identity and hardware/resource limits retained by #45/#47 unless a new controlling issue explicitly replaces them.
- Execute the bounded provider-enabled single-request matrix and compare against the strongest conventional placement available in the same pinned runtime.
- Measure TTFT, prompt/decode throughput, token p50/p95/p99, useful/submitted storage bytes, bundle latency, cache hits/admissions/evictions, H2D, synchronization, RSS, pinned RAM, VRAM, faults, swap, and physical residency.
- Preserve `PROMOTE_AND_GPU`, demand-only scheduling, one request, one GPU, no expert dropping, no speculative decoding, and no default prefetch or seed mechanism.
- Accept a correct bounded negative result when an exact remaining architecture or kernel boundary is demonstrated.

## Sequencing and exclusions

- This track may be researched independently, but implementation that touches provider, hot/cold cache, transfer-ring, or scheduler ownership must not run concurrently with another active change to the same seams without an explicit integration plan.
- It does not satisfy or weaken Phase 12 K3 gates and does not make the K3 storage-format decision.
- Phase 12.5 still begins only after the Phase 12 mechanism, layout decision, and full-size dry-run are accepted.
- Multi-request, batching, multi-GPU, automatic policy changes, expert dropping, repacking to evade the class problem, and model-specific parallel runtimes remain out of scope.

## Exit gate

- A bounded heterogeneous class model can be represented and executed without model-specific cache or transport code.
- Physical resource ownership remains explicit and bounded for every class.
- K3 one-class behavior and accepted defaults remain unchanged.
- DeepSeek-V4 completes the provider-enabled slot/kernel gate and bounded full-model matrix, or publishes an exact independently reviewed remaining blocker.
