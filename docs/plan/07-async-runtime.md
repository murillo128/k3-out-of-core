# Async runtime, miss policies, cache policy, and prefetch

## Phase 7 — Asynchronous Linux I/O and transfer pipeline

### Objectives

- Implement the final bounded demand pipeline and overlap disk, host, transfer, and compute.

### Tasks

#### 7.1 `io_uring` transport

- [ ] Introduce a Linux-specific I/O backend behind `ExpertStorage`/transport interfaces.
- [ ] Implement bounded SQ/CQ depth.
- [ ] Attach request identity/generation to completions.
- [ ] Prevent use-after-free on cancellation/model unload.
- [ ] Measure registered files and registered buffers versus ordinary submission.

#### 7.2 Direct I/O

- [ ] Detect filesystem/device support.
- [ ] Compute sector/page alignment from actual requirements.
- [ ] Read aligned supersets and expose the requested interior span.
- [ ] Compare `O_DIRECT` with buffered `io_uring`.
- [ ] Retain a correct buffered fallback.

#### 7.3 Parallel projection reads

- [ ] Compare three independent reads, coalesced adjacent ranges, and sequential bundle reads.
- [ ] Keep the logical ExpertBundle completion atomic.
- [ ] Do not create a new file format until measurements justify it.

#### 7.4 CUDA overlap

- [ ] Use dedicated transfer streams and events.
- [ ] Allow compute to proceed for ready experts while other experts load where graph semantics permit.
- [ ] Ensure the layer waits only for its required demand experts.
- [ ] Record disk-ready, host-ready, device-ready, and compute-complete timestamps.

#### 7.5 Priorities

Required priority classes:

```text
DEMAND_CURRENT_LAYER
DEMAND_FUTURE_DEPENDENCY
PREFETCH_NEXT
PREFETCH_SPECULATIVE
```

- [ ] Demand can preempt or cancel speculative queue entries.
- [ ] Duplicate requests coalesce.
- [ ] Promotion of the same ExpertKey is single-flight.

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

- [ ] Wait for demand expert readiness, then execute on GPU.
- [ ] Measure stall decomposition.

#### 8.2 `CPU_FALLBACK`

- [ ] Execute missed expert rows with the existing CPU MXFP4 path.
- [ ] Execute hot hits on GPU concurrently when safe.
- [ ] Optionally continue background promotion for likely reuse.
- [ ] Merge results in canonical top-k order.
- [ ] Handle fused gate/up/down graph dependencies correctly.

#### 8.3 `AUTO`

- [ ] Build a cost model from measured CPU expert time, disk state, cold state, H2D time, queue depth, and reuse score.
- [ ] Add guardrails: periodically sample baseline behavior and disable a harmful strategy.
- [ ] Do not enable by default until stable across workloads.

### Exit gate

- All policies are numerically correct.
- The chosen policy never silently changes.
- Benchmarks identify regimes where each policy wins.

---

## Phase 9 — Cache-policy framework and trace-driven selection

### Objectives

- Separate mechanism from policy and select defaults from evidence.

### Tasks

- [ ] Define callbacks/events for admission, hit, load, pin, unpin, eviction, prefill transition, and request end.
- [ ] Implement deterministic LRU for test reference.
- [ ] Implement LFRU.
- [ ] Implement SLRU with configurable protected/probationary split.
- [ ] Implement frequency-gated admission.
- [ ] Implement LFU-aging based on reviewed prior art.
- [ ] Replay all policies offline against the committed trace corpus.
- [ ] Validate online behavior against simulator predictions.
- [ ] Separate policy state per layer where required; compare global versus per-layer budgets.
- [ ] Evaluate byte-aware policies if expert sizes vary.

### Exit gate

- A policy/default recommendation is documented with trace and online benchmark evidence.
- Prefill cannot silently destroy a protected decode hot set without metrics showing it.

---

## Phase 10 — Prefetch and hot-set seeding

### Objectives

- Hide remaining cold/disk misses without polluting caches or starving demand.

### Tasks

#### 10.1 Static seeding

- [ ] Support optional profile/imatrix-derived initial hot set.
- [ ] Version and validate profile compatibility with model/checkpoint.
- [ ] Measure cold-start improvement and domain-shift harm.

#### 10.2 Temporal prefetch

- [ ] Predict same-layer expert reuse from recent tokens.
- [ ] Track precision, recall, lead time, and wasted bytes.

#### 10.3 Cross-layer prediction

- [ ] Evaluate transition tables and published FATE/ExpertFlow-style predictors.
- [ ] Ensure prediction computation is cheaper than hidden transfer latency.
- [ ] Avoid assuming N+1 predictability.

#### 10.4 Scheduler integration

- [ ] Prefetch is cancellable and lower priority than demand.
- [ ] Set explicit bandwidth and in-flight budgets.
- [ ] Prevent speculative cold entries from evicting protected demand-hot experts without admission approval.

### Exit gate

- At least one prefetch policy improves end-to-end tail latency or throughput after accounting for wasted I/O and cache pollution.
- Ineffective policies remain disabled.

---
