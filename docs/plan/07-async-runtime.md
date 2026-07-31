# Async runtime, miss policies, cache policy, and prefetch

## Phase 7 — Asynchronous Linux I/O and transfer pipeline

### Objectives

- Implement the final bounded demand pipeline and overlap disk, host, transfer, and compute.

### Tasks

#### 7.1 `io_uring` transport

- [x] Introduce a Linux-specific I/O backend behind `ExpertStorage`/transport interfaces.
- [x] Implement bounded SQ/CQ depth.
- [x] Attach request identity/generation to completions.
- [x] Prevent use-after-free on cancellation/model unload.
- [x] Measure registered files and registered buffers versus ordinary submission.

#### 7.2 Direct I/O

- [x] Detect filesystem/device support.
- [x] Compute sector/page alignment from actual requirements.
- [x] Read aligned supersets and expose the requested interior span.
- [x] Compare `O_DIRECT` with buffered `io_uring`.
- [x] Retain a correct buffered fallback.

#### 7.3 Parallel projection reads

- [x] Compare three independent reads, coalesced adjacent ranges, and sequential bundle reads.
- [x] Keep the logical ExpertBundle completion atomic.
- [x] Do not create a new file format until measurements justify it.

#### 7.4 CUDA overlap

- [x] Use dedicated transfer streams and events.
- [x] Allow compute to proceed for ready experts while other experts load where graph semantics permit.
- [x] Ensure the layer waits only for its required demand experts.
- [x] Record disk-ready, host-ready, device-ready, and compute-complete timestamps.

#### 7.5 Priorities

Required priority classes:

```text
DEMAND_CURRENT_LAYER
DEMAND_FUTURE_DEPENDENCY
PREFETCH_NEXT
PREFETCH_SPECULATIVE
```

- [x] Demand can preempt or cancel speculative queue entries.
- [x] Duplicate requests coalesce.
- [x] Promotion of the same ExpertKey is single-flight.

#### Phase 7 evidence closeout

- [x] Capture exact original/split F16/MXFP4 parity and repeated warm execution.
- [x] Capture native and fallback transport, cancellation, overlap, placement, tail, and resource evidence.
- [x] Publish a schema-validated authoritative manifest bound to accepted Checkpoints A and B.
- [x] Receive the independent final complete-PR review.

### Exit gate

- [x] Disk and H2D overlap are demonstrated in traces.
- [x] Resource use remains bounded under queue saturation.
- [x] Error/cancellation tests pass.
- [x] Tail latency is measured, not only average throughput.

### Phase 7 execution record

Issue #24 implemented this phase from project base `96b0b483c6bc0f92b6fb9bb46acfd6bf06a46c4c` and nested base `7a606dd4e11a108929f799253809a904f55feae4`. Checkpoint A accepted async ownership, lifetime, and storage correctness with `PASS`, safety `YES`, in comment `5135836934`. Checkpoint B accepted CUDA readiness, overlap, and cached-only remap placement with `PASS`, safety `YES`, in comment `5140081178`. The final complete-PR review returned `PASS`, safety `YES`, in comment `5140490542` for project head `1b9d040da332e547af4571f81743012cd168a4cc` and nested head `b71e40f91b1a0dab578d56ac733211453704d674`. PR #25 squash-merged into `main` as `97ef68d787c54b443eac72a3480fe70eba88d8dd`.

The technical manifest is `results/2026-07-31/skynet/phase7-async-runtime/phase7-manifest.json`. It records exact original/split F16/MXFP4 parity, repeated 20-step warm execution, direct and buffered asynchronous paths, explicit synchronous fallback, cancellation/retry/unload drain, single-flight and priority/saturation tests, controlled positive disk/H2D and H2D/compute overlap, complete trace accounting, p50/p95/p99 tails, bounded memory/queue/event use, sanitizers, and prior-mode placement. Tiny-fixture performance is descriptive; Phase 8 and later own CPU fallback, production policy/prefetch, concurrency, UMA, multi-GPU, and full-size conclusions.

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
- Treat the WASTE full-K3 cache measurements as an external baseline, not as a transferable default.

### Tasks

- [ ] Define callbacks/events for admission, hit, load, pin, unpin, eviction, prefill transition, and request end.
- [ ] Implement deterministic LRU for test reference.
- [ ] Implement LFRU.
- [ ] Implement SLRU with configurable protected/probationary split.
- [ ] Implement frequency-gated admission.
- [ ] Implement LFU-aging based on reviewed prior art.
- [ ] Include WASTE-style sampled LRU/LFRU as external replay baselines without adopting its policy by default.
- [ ] Replay all policies offline against the committed trace corpus.
- [ ] Validate online behavior against simulator predictions.
- [ ] Separate policy state per layer where required; compare global versus per-layer budgets.
- [ ] Evaluate byte-aware policies if expert sizes vary.
- [ ] Define exact per-model/per-format token working-set bytes and sweep budgets below, at, and above whole working-set multiples.
- [ ] Record minor/major page faults, swap or memory-compression activity where observable, RSS, physically resident cache bytes, and hit service time; a logical hit that faults from OS-managed backing must not be reported as an equivalent resident hit.
- [ ] Select budgets from throughput, tail latency, and physical-residency evidence rather than hit rate alone.
- [ ] Preserve explicit headroom for the resident trunk, KV/recurrent state, CUDA/runtime allocations, filesystem metadata, and the operating system.

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

- [ ] Support optional profile/imatrix-derived initial hot set.
- [ ] Version and validate profile compatibility with model/checkpoint.
- [ ] Measure cold-start improvement and domain-shift harm.
- [ ] Establish random and static per-layer hot-set baselines before evaluating learned predictors.

#### 10.2 Temporal prefetch

- [ ] Predict same-layer expert reuse from recent tokens.
- [ ] Include the previous token's same-layer expert set as a mandatory baseline.
- [ ] Track precision, recall, lead time, wasted bytes, displacement, and useful-consumption latency.
- [ ] Derive predictor break-even from measured storage/H2D bandwidth, hidden latency, exact expert bytes, and cache-pollution cost for each target transport.
- [ ] Classify issuing all already-known current-layer experts after router completion as exact demand scheduling, not predictive prefetch.

#### 10.3 Cross-layer prediction

- [ ] Evaluate transition tables and published FATE/ExpertFlow-style predictors.
- [ ] Ensure prediction computation is cheaper than hidden transfer latency.
- [ ] Avoid assuming N+1 predictability.
- [ ] Compare against WASTE's pinned negative result: on its M5 Pro CPU/UMA path, layer-to-layer co-occurrence reached 29.0% recall, previous-token reuse reached 29.5%, and its measured bandwidth model required about 60% break-even. These figures are `OBSERVED` for that engine and must be re-derived for this runtime.
- [ ] Require any predictor to beat previous-token and static-hot baselines and to remain net-positive after wasted I/O, H2D, and cache displacement.

#### 10.4 Scheduler integration

- [ ] Prefetch is cancellable and lower priority than demand.
- [ ] Set explicit bandwidth and in-flight budgets.
- [ ] Prevent speculative cold entries from evicting protected demand-hot experts without admission approval.
- [ ] Stop or throttle a predictor when measured utility falls below its transport-specific break-even.

### Exit gate

- At least one prefetch policy improves end-to-end tail latency or throughput after accounting for wasted I/O and cache pollution.
- Ineffective policies remain disabled.
- Cross-layer prediction remains disabled unless it beats the required simple baselines and the measured end-to-end break-even on the target hardware.
