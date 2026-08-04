# Async runtime, miss policies, cache policy, and prefetch

## Phase 7 — Asynchronous Linux I/O and transfer pipeline

### Objectives

- Implement the final bounded demand pipeline and overlap disk, host, transfer, and compute.

### Tasks

#### 7.1 `io_uring` transport

- Introduce a Linux-specific I/O backend behind `ExpertStorage`/transport interfaces.
- Implement bounded SQ/CQ depth.
- Attach request identity/generation to completions.
- Prevent use-after-free on cancellation/model unload.
- Measure registered files and registered buffers versus ordinary submission.

#### 7.2 Direct I/O

- Detect filesystem/device support.
- Compute sector/page alignment from actual requirements.
- Read aligned supersets and expose the requested interior span.
- Compare `O_DIRECT` with buffered `io_uring`.
- Retain a correct buffered fallback.

#### 7.3 Parallel projection reads

- Compare three independent reads, coalesced adjacent ranges, and sequential bundle reads.
- Keep the logical ExpertBundle completion atomic.
- Do not create a new file format until measurements justify it.

#### 7.4 CUDA overlap

- Use dedicated transfer streams and events.
- Allow compute to proceed for ready experts while other experts load where graph semantics permit.
- Ensure the layer waits only for its required demand experts.
- Record disk-ready, host-ready, device-ready, and compute-complete timestamps.

#### 7.5 Priorities

Required priority classes:

```text
DEMAND_CURRENT_LAYER
DEMAND_FUTURE_DEPENDENCY
PREFETCH_NEXT
PREFETCH_SPECULATIVE
```

- Demand can preempt or cancel speculative queue entries.
- Duplicate requests coalesce.
- Promotion of the same ExpertKey is single-flight.

#### Evidence requirements

- Capture exact original/split F16/MXFP4 parity and repeated warm execution.
- Capture native and fallback transport, cancellation, overlap, placement, tail, and resource evidence.
- Publish a schema-validated authoritative manifest bound to accepted checkpoints.
- Receive an independent final complete-PR review.

### Exit gate

- Disk and H2D overlap are demonstrated in traces.
- Resource use remains bounded under queue saturation.
- Error/cancellation tests pass.
- Tail latency is measured, not only average throughput.

---

## Phase 8 — Discrete-GPU miss execution policies

### Objectives

- Avoid making every demand miss a synchronous PCIe stall.

### Tasks

#### 8.1 `PROMOTE_AND_GPU`

- Wait for demand expert readiness, then execute on GPU.
- Measure stall decomposition and preserve it as the stable default.

#### 8.2 `CPU_FALLBACK`

- Execute missed expert rows with the existing CPU path, including MXFP4.
- Execute independent hot hits on GPU concurrently when safe.
- Support explicit, bounded background promotion without enabling it by default.
- Merge results in canonical top-k order.
- Handle fused gate/up/down graph dependencies correctly.

#### 8.3 `AUTO`

- Implement the accepted deterministic v1 cost comparison over explicit caller-supplied CPU, storage, H2D, queue, and background-promotion operands.
- Fail closed to `PROMOTE_AND_GPU` for missing or invalid operands and select GPU on ties.
- Keep `AUTO` explicit and default-inert; do not add online learning, exploration, or a hidden policy switch.

#### 8.4 Evidence requirements

- Capture exact original/split F16/MXFP4 parity and production-path policy coverage with repeated cold and warm processes.
- Capture a larger public MoE F16 bootstrap case and an exact-layout, source-derived full-K3 MXFP4 sparse-store workload without making a model-quality claim.
- Evaluate all explicit policies and controlled AUTO regimes across decode/prefill, hot ratios, reuse classes, and background-promotion states.
- Run CPU, CUDA, ASan+UBSan, accepted TSan, lifetime, cleanup, Phase 7 regression, and fail-closed verifier validation.
- Publish a schema-validated authoritative manifest bound to accepted checkpoints.
- Receive an independent final complete-PR review.

### Exit gate

- All policies are numerically correct in the standing parity and production-path matrix.
- The chosen policy never silently changes.
- Controlled benchmarks identify CPU-favorable, GPU-favorable, and tie regimes; observed real-model process tails are recorded separately.

---

## Phase 9 — Cache-policy framework and trace-driven selection

### Objectives

- Separate mechanism from policy and select defaults from evidence.
- Treat the WASTE full-K3 cache measurements as an external baseline, not as a transferable default.

### Tasks

- Define callbacks/events for admission, hit, load, pin, unpin, eviction, prefill transition, and request end.
- Implement deterministic LRU for test reference.
- Implement LFRU.
- Implement SLRU with configurable protected/probationary split.
- Implement frequency-gated admission.
- Implement LFU-aging based on reviewed prior art.
- Include WASTE-style sampled LRU/LFRU as external replay baselines without adopting its policy by default.
- Replay all policies offline against the committed trace corpus.
- Validate online behavior against simulator predictions.
- Separate policy state per layer where required; compare global versus per-layer budgets.
- Evaluate byte-aware policies if expert sizes vary.
- Define exact per-model/per-format token working-set bytes and sweep budgets below, at, and above whole working-set multiples.
- Record minor/major page faults, swap or memory-compression activity where observable, RSS, physically resident cache bytes, and hit service time; a logical hit that faults from OS-managed backing must not be reported as an equivalent resident hit.
- Select budgets from throughput, tail latency, and physical-residency evidence rather than hit rate alone.
- Preserve explicit headroom for the resident trunk, KV/recurrent state, CUDA/runtime allocations, filesystem metadata, and the operating system.

### Exit gate

- A policy/default recommendation is documented with trace and online benchmark evidence.
- Prefill cannot silently destroy a protected decode hot set without metrics showing it.
- The recommended budget is evaluated around working-set boundaries and avoids a sustained paging/compression cliff.
- Any agreement or disagreement with WASTE's observed cache floor and oversubscription collapse is documented with the differing model format, hardware, and transport.

---

## Phase 10 — Prefetch and hot-set seeding

### Objectives

- Hide remaining cold/disk misses without polluting caches or starving demand.
- Separate exact issue-ahead after routing from speculative prediction across tokens or layers.

### Tasks

#### 10.1 Static seeding

- Support optional profile/imatrix-derived initial hot set.
- Version and validate profile compatibility with model/checkpoint.
- Measure cold-start improvement and domain-shift harm.
- Establish random and static per-layer hot-set baselines before evaluating learned predictors.

#### 10.2 Temporal prefetch

- Predict same-layer expert reuse from recent tokens.
- Include the previous token's same-layer expert set as a mandatory baseline.
- Track precision, recall, lead time, wasted bytes, displacement, and useful-consumption latency.
- Derive predictor break-even from measured storage/H2D bandwidth, hidden latency, exact expert bytes, and cache-pollution cost for each target transport.
- Classify issuing all already-known current-layer experts after router completion as exact demand scheduling, not predictive prefetch.

#### 10.3 Cross-layer prediction

- Evaluate transition tables and published FATE/ExpertFlow-style predictors.
- Ensure prediction computation is cheaper than hidden transfer latency.
- Avoid assuming N+1 predictability.
- Compare against WASTE's pinned negative result: on its M5 Pro CPU/UMA path, layer-to-layer co-occurrence reached 29.0% recall, previous-token reuse reached 29.5%, and its measured bandwidth model required about 60% break-even. These figures are `OBSERVED` for that engine and must be re-derived for this runtime.
- Require any predictor to beat previous-token and static-hot baselines and to remain net-positive after wasted I/O, H2D, and cache displacement.

#### 10.4 Scheduler integration

- Prefetch is cancellable and lower priority than demand.
- Set explicit bandwidth and in-flight budgets.
- Prevent speculative cold entries from evicting protected demand-hot experts without admission approval.
- Stop or throttle a predictor when measured utility falls below its transport-specific break-even.

### Exit gate

- A static-seeding or predictive-prefetch policy may be recommended or enabled by default only when it improves end-to-end tail latency or throughput after accounting for wasted I/O, cache pollution, steady-throughput effects, and domain shift.
- Ineffective policies remain explicit opt-in and disabled by default.
- Cross-layer prediction remains disabled unless it beats the required simple baselines and target-specific end-to-end break-even.
- Exact current-layer issue-ahead is treated as demand scheduling and may be evaluated independently from speculative prefetch.
- Later phases must not depend on speculative prefetch unless a future representative full-size gate explicitly reopens that dependency.
