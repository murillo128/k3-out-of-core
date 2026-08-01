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
- [x] Receive the independent final complete-PR review.

### Exit gate

- [x] All policies are numerically correct in the standing parity and production-path matrix.
- [x] The chosen policy never silently changes.
- [x] Controlled benchmarks identify CPU-favorable, GPU-favorable, and tie regimes; observed real-model process tails are recorded separately.

### Phase 8 execution record

Issue #26 implemented this phase through PR #27, squash-merged into `main` as `05c38f0f11bd33c3654a3d9b2c3c6aa32e7f4c35`. Checkpoint A accepted the bounded miss-policy mechanism at project/nested heads `07da45728b38b2d7c6a3a1b156dffcea6b94ec54` / `4cfee48aacb6b33ebcbda796b26106b69440e633` in comment `5141694340`. Checkpoint B accepted corrected production-path evidence at `30013880641fd2f10a1952b5b9619e6d872e233b` / `a885ff7750a4e73901b7f378e7dc45880a7d1536` in comment `5144721775`. Checkpoint C accepted the bounded descriptor-only cold-cache bootstrap correction at `a52581e23b6192e51a6cd5452c121b5a014371f1` / `dc4d50c68378d908131b518662160fdd08f4e005` with `PASS_WITH_NOTES`, safety `YES`, in comment `5146173479`; no required correction remained. The final complete-PR review returned `PASS_WITH_NOTES`, safety `YES`, with no required delta in comment `5146545713`. The merged nested head is `dc4d50c68378d908131b518662160fdd08f4e005`.

The closeout evidence is under `results/2026-07-31/skynet/phase8-miss-execution/`. It records a 25-case production-path probe, ten positive controlled CPU/GPU-overlap repetitions, original and 218-part split F16/MXFP4 parity with repeated cold and warm processes, a larger public MoE F16 bootstrap case, a 300-cell deterministic policy matrix, an exact 1,446,456,066,048-byte source-derived full-K3 MXFP4 sparse store, device-wide VRAM sampling, and focused CPU/CUDA/sanitizer/lifetime/cleanup validation. The full-size sparse workload validates layout and controlled policy regimes; it is not full-model inference or a model-quality result. Phase 9 policy selection, Phase 10 prefetch, multi-request concurrency, UMA, and multi-GPU remain out of scope.

---

## Phase 9 — Cache-policy framework and trace-driven selection

### Objectives

- Separate mechanism from policy and select defaults from evidence.
- Treat the WASTE full-K3 cache measurements as an external baseline, not as a transferable default.

### Tasks

- [x] Define callbacks/events for admission, hit, load, pin, unpin, eviction, prefill transition, and request end.
- [x] Implement deterministic LRU for test reference.
- [x] Implement LFRU.
- [x] Implement SLRU with configurable protected/probationary split.
- [x] Implement frequency-gated admission.
- [x] Implement LFU-aging based on reviewed prior art.
- [x] Include WASTE-style sampled LRU/LFRU as external replay baselines without adopting its policy by default.
- [x] Replay all policies offline against the committed trace corpus.
- [x] Validate online behavior against simulator predictions.
- [x] Separate policy state per layer where required; compare global versus per-layer budgets.
- [x] Evaluate byte-aware policies if expert sizes vary.
- [x] Define exact per-model/per-format token working-set bytes and sweep budgets below, at, and above whole working-set multiples.
- [x] Record minor/major page faults, swap or memory-compression activity where observable, RSS, physically resident cache bytes, and hit service time; a logical hit that faults from OS-managed backing must not be reported as an equivalent resident hit.
- [x] Select budgets from throughput, tail latency, and physical-residency evidence rather than hit rate alone.
- [x] Preserve explicit headroom for the resident trunk, KV/recurrent state, CUDA/runtime allocations, filesystem metadata, and the operating system.

### Exit gate

- [x] A policy/default recommendation is documented with trace and online benchmark evidence.
- [x] Prefill cannot silently destroy a protected decode hot set without metrics showing it.
- [x] The recommended budget is evaluated around working-set boundaries and avoids a sustained paging/compression cliff.
- [x] Any agreement or disagreement with WASTE's observed cache floor and oversubscription collapse is documented with the differing model format, hardware, and transport.

### Phase 9 execution record

Issue #30 implemented this phase through PR #31, squash-merged into `main` as `035f099de85b3f775ae8cd6769b561156ea52317`. Checkpoints A, B, and C returned `PASS`, safety `YES`, at comments `5148012128`, `5148752231`, and `5149625334`. The final complete-PR review returned `PASS`, safety `YES`, with no required delta in comment `5149762882` for project head `32f5a2390c7e7730ec0a28677195820aedf734f3` and nested head `fd29c0f9e868e838d3641cd13eb6ceb8c1535f01`.

The fixed selection rule retains exact global LRU/ALWAYS for both hot and cold null defaults because no non-LRU pair passed every default gate; explicit non-default and per-layer configurations remain available under the v1 validation rules. The scoped cold-budget recommendations are 44,040,192 bytes for the two-token tiny-K3 F16 boundary workload, 11,698,176 bytes for its MXFP4 counterpart, 1,678,245,888 bytes for the accepted one-token Qwen bootstrap only, and 25,829,572,608 bytes for full-K3 exact-layout physical-residency evidence only. None is a hidden runtime auto-size default, and the full-K3 result is not an inference, quality, or storage-throughput claim.

The authoritative technical manifest is `results/2026-07-31/skynet/phase9-cache-policy/phase9-manifest.json`, SHA-256 `5295ee701dfa24636f03d4bd13e3f250560179ecbda30ad9379580a2ce1c370f`. It binds immutable replay, online agreement, policy/default equivalence, physical residency and headroom, WASTE normalization, fixed statistical selection, required CPU/CUDA/sanitizer validation, exact implementation and nested heads, and the final clean-tree/gitlink gates.

---

## Phase 10 — Prefetch and hot-set seeding

**Disposition:** `REJECTED` for the evaluated static-seeding and predictive-prefetch mechanisms. The investigation is complete enough to reject the tested mechanisms, but the implementation phase did not satisfy its exit gate and was not merged.

### Objectives

- Hide remaining cold/disk misses without polluting caches or starving demand.
- Separate exact issue-ahead after routing from speculative prediction across tokens or layers.

### Tasks

The unchecked implementation tasks below are retained as the original scope and as a record of what remains unaccepted. They are not current implementation work.

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

- [ ] At least one prefetch policy improves end-to-end tail latency or throughput after accounting for wasted I/O and cache pollution.
- [x] Ineffective policies remain disabled.
- [x] Cross-layer prediction remains disabled because it did not beat the required simple baselines and measured end-to-end break-even on the validated hardware.

### Phase 10 investigation record

Issue #32 executed the fixed Phase 10 investigation through draft PR #33. Checkpoints A and B and the corrected online/runtime-equivalence work were accepted with safety `YES`. Phase 10.4 then produced a reviewed negative selection: no predictive envelope cleared its exact held-out break-even, the buffered blocking seed improved cold TTFT but failed steady-throughput and domain-shift gates, and the H2D blocking seed failed cold-TTFT, steady-throughput, and domain-shift gates. The second failure of the blocking-hot-seed mechanism triggered the `STANDARD` circuit breaker.

The authoritative negative-selection artifact on the research branch is `results/2026-08-01/skynet/phase10-prefetch/selection.json`. It selects zero profiles, disables every evaluated profile hash, and preserves the accepted Phase 9 global LRU/ALWAYS defaults. Draft PR #33 is retained only as read-only research evidence at project head `d4159abcb1234287d7a21047a34a354ab6ae7e38` and nested `llama.cpp` head `b617680ca030f7e81d2446c4fabb24225947538c`; it must not be merged, extended, or used as the base for later phases.

Exact current-layer issue-ahead is not speculative prefetch. PR #33 demonstrated that all known current-layer misses can be enqueued and submitted before the first storage wait while preserving routes, but that mechanism is entangled with rejected Phase 10 infrastructure. It therefore moves to the bounded Phase 10R extraction below and must be reimplemented from current `main`, not cherry-picked wholesale.

Speculative prefetch is no longer a prerequisite for Phases 11–15. Those phases use the accepted Phase 9 demand-only baseline plus Phase 10R if Phase 10R independently passes its own exit gate.

---

## Phase 10R — Exact current-layer demand issue-ahead extraction

**Status:** `OPEN`. This is the immediate next implementation phase.

### Objectives

- Extract the useful non-speculative result of the Phase 10 investigation into a minimal demand-only change based on current `main`.
- Submit every already-known current-layer demand miss before the first storage wait where scheduler and transport capacity permit.
- Preserve Phase 9 global LRU/ALWAYS defaults, exact routing/output semantics, and all accepted Phase 7–9 ownership and lifetime rules.

### Tasks

#### 10R.1 Minimal demand scheduling

- [ ] At the accepted router/remap checkpoint, deduplicate selected current-layer expert keys in canonical order while retaining all execution occurrences.
- [ ] Reserve and enqueue the complete bounded current-layer miss set before taking or waiting on individual requests.
- [ ] Submit every acquired exact storage read before the first storage wait; join an existing exact same-key generation rather than duplicating work.
- [ ] Preserve canonical accumulation order, selected expert IDs and weights, miss-policy semantics, and exact generation identity.
- [ ] Do not add profiles, static seed, predictor state, speculative origins, utility circuits, new cache policy, or a Phase 10 public prefetch API.

#### 10R.2 Demand safety and boundedness

- [ ] Reserve enough scheduler headroom for the maximum legal current-layer demand width or synchronously reclaim lower-priority work without waiting before the full demand set is enqueued.
- [ ] Ensure cancellation or drain of an older same-key request cannot cause a wait before later current-layer demands are enqueued.
- [ ] Keep demand non-droppable and higher priority than all existing optional background work.
- [ ] Prove bounded scheduler, request, transfer, event, and telemetry storage under saturation.
- [ ] Preserve cancellation, retry, trim, surrender, context destruction, and model unload drain behavior.

#### 10R.3 Evidence and performance

- [ ] Add a test-only serial-demand control; it must not be a public mode or selectable runtime policy.
- [ ] Record selected/unique/missing keys, enqueued-before-first-take, submitted-before-first-wait, ready-before-use, first wait, storage/H2D overlap, and demand completion.
- [ ] Validate duplicate-lane, multi-miss, saturation, same-key cancellation/drain, retry, shuffled completion, original/split F16/MXFP4, and prior Phase 7–9 modes.
- [ ] Run focused CPU, CUDA, ASan+UBSan, and accepted TSan validation.
- [ ] Compare fresh-process demand issue-ahead versus the serial control and current `main` using exact revisions, routes, outputs, endpoint timings, tails, bytes, and resource use.

### Exit gate

- [ ] Every legal current-layer demand miss is enqueued before the first scheduler-release or storage wait and every acquired read is submitted before the first storage wait.
- [ ] Routes, weights, generated tokens, logits, canonical reduction, cache defaults, and miss-policy behavior remain equivalent to the accepted baseline.
- [ ] No demand is dropped, delayed behind unrelated optional work, assigned a stale generation, or cancelled after promotion.
- [ ] Cancellation, retry, trim, surrender, context destruction, and model unload terminate with no leak, UAF, stale publication, or undrained request.
- [ ] At least one controlled storage-bound cell shows positive exposed-wait or endpoint improvement without a material regression in mandatory real-model cells.
- [ ] The final implementation contains no static-seeding or predictive-prefetch mechanism and is reviewable independently of PR #33.

### Sequence after Phase 10R

- Phase 11 and Phase 12 proceed with demand-only execution whether or not speculative prefetch is ever revisited.
- Phase 12.5 remains the observability gate before authoritative cross-hardware benchmarking.
- Speculative prefetch may be reconsidered only after Phase 12.5 if representative full-size traces show material exposed storage/readiness latency and a materially different mechanism has a predeclared, target-specific path for the held-out confidence bound to clear break-even without repeating steady-throughput or domain-shift regressions.
- If those conditions are not met, speculative prefetch remains permanently disabled and Phase 10 stays `REJECTED`.
