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

- [x] Wait for demand expert readiness, then execute on GPU.
- [x] Measure stall decomposition and preserve it as the stable default.

#### 8.2 `CPU_FALLBACK`

- [x] Execute missed expert rows with the existing CPU path, including MXFP4.
- [x] Execute independent hot hits on GPU concurrently when safe.
- [x] Support explicit, bounded background promotion without enabling it by default.
- [x] Merge results in canonical top-k order.
- [x] Handle fused gate/up/down graph dependencies correctly.

#### 8.3 `AUTO`

- [x] Implement the accepted deterministic v1 cost comparison over explicit caller-supplied CPU, storage, H2D, queue, and background-promotion operands.
- [x] Fail closed to `PROMOTE_AND_GPU` for missing or invalid operands and select GPU on ties.
- [x] Keep `AUTO` explicit and default-inert; do not add online learning, exploration, or a hidden policy switch.

#### 8.4 Evidence closeout

- [x] Capture exact original/split F16/MXFP4 parity and production-path policy coverage with repeated cold and warm processes.
- [x] Capture a larger public MoE F16 bootstrap case and an exact-layout, source-derived full-K3 MXFP4 sparse-store workload without making a model-quality claim.
- [x] Evaluate all explicit policies and controlled AUTO regimes across decode/prefill, hot ratios, reuse classes, and background-promotion states.
- [x] Run CPU, CUDA, ASan+UBSan, accepted ASLR-disabled TSan, lifetime, cleanup, Phase 7 regression, and fail-closed verifier validation.
- [x] Publish a schema-validated authoritative manifest bound to accepted Checkpoints A, B, and C.
- [ ] Receive the independent final complete-PR review.

### Exit gate

- [x] All policies are numerically correct in the standing parity and production-path matrix.
- [x] The chosen policy never silently changes.
- [x] Controlled benchmarks identify CPU-favorable, GPU-favorable, and tie regimes; observed real-model process tails are recorded separately.

### Phase 8 execution record

Issue #26 implements this phase on branch `codex/phase8-miss-execution` and draft PR #27. Checkpoint A accepted the bounded miss-policy mechanism at project/nested heads `07da45728b38b2d7c6a3a1b156dffcea6b94ec54` / `4cfee48aacb6b33ebcbda796b26106b69440e633` in comment `5141694340`. Checkpoint B accepted corrected production-path evidence at `30013880641fd2f10a1952b5b9619e6d872e233b` / `a885ff7750a4e73901b7f378e7dc45880a7d1536` in comment `5144721775`. Checkpoint C accepted the bounded descriptor-only cold-cache bootstrap correction at `a52581e23b6192e51a6cd5452c121b5a014371f1` / `dc4d50c68378d908131b518662160fdd08f4e005` with `PASS_WITH_NOTES`, safety `YES`, in comment `5146173479`; no required correction remained.

The closeout evidence is under `results/2026-07-31/skynet/phase8-miss-execution/`. It records a 25-case production-path probe, ten positive controlled CPU/GPU-overlap repetitions, original and 218-part split F16/MXFP4 parity with repeated cold and warm processes, a larger public MoE F16 bootstrap case, a 300-cell deterministic policy matrix, an exact 1,446,456,066,048-byte source-derived full-K3 MXFP4 sparse store, device-wide VRAM sampling, and focused CPU/CUDA/sanitizer/lifetime/cleanup validation. The full-size sparse workload validates layout and controlled policy regimes; it is not full-model inference or a model-quality result. Phase 9 policy selection, Phase 10 prefetch, multi-request concurrency, UMA, and multi-GPU remain out of scope.

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
