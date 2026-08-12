# Architecture Decisions

This file records decisions that new ChatGPT or Codex sessions must treat as authoritative. Changes require an explicit update here with rationale and validation impact.

Status vocabulary:

- **ACCEPTED** — implementation must follow this decision.
- **OPEN** — decision deliberately deferred pending evidence.
- **SPECULATIVE** — plausible direction, not yet a requirement.
- **REJECTED** — do not implement without reopening the decision.

## Accepted decisions

### D-001 — Build the final hierarchy, not a page-cache prototype

**Status:** ACCEPTED

The target is explicit NVMe, cold-RAM, and hot-accelerator management. Linux `mmap` and page cache may be used for comparison or fallback, but are not the final cache controller.

Rationale: the runtime needs deterministic capacity, eviction, priority, prefetch, and telemetry at expert granularity.

### D-002 — Two physical caches on discrete GPUs; two logical states on UMA

**Status:** ACCEPTED

Discrete GPU:

```text
NVMe -> cold RAM -> hot VRAM
```

Coherent UMA:

```text
NVMe -> shared physical memory
          ├── logical cold
          └── logical hot / GPU-ready
```

The policy interface is common; transport and physical placement differ.

### D-003 — Introduce an `ExpertWeightProvider` abstraction

**Status:** ACCEPTED

Inference code requests executable experts from a provider. The provider owns cache lookup, remapping, readiness, and release. Required implementations:

- resident/no-cache provider;
- discrete CUDA cached provider;
- UMA cached provider;
- CPU provider/reference.

The kernel must not know whether a weight came from the monolithic model, RAM, or NVMe.

### D-004 — Cache slots are persistent and fixed-address

**Status:** ACCEPTED

Hot-cache memory is owned by the cache subsystem, not by GGML graph-temporary allocation. Pointer equality is not evidence that contents survived a compute epoch.

This directly avoids the stale-buffer/NaN failure documented in prior `llama.cpp` attempts.

### D-005 — Cache at whole-expert granularity

**Status:** ACCEPTED

Admission and eviction operate on:

```text
ExpertKey(layer, expert_id)
ExpertBundle(gate, up, down, quant metadata)
```

The physical tensors can remain separate. Their lifecycle is atomic because every selected routed expert needs all projections.

**Phase 4 observation:** issue #17 implements this accepted decision with one model-owned CUDA pool, a preallocated host directory, generation-checked request pins, and synchronized logical-to-physical ID remapping. Checkpoint A comment `5131012078` found no material ownership, lifetime, sidecar-span, or reduction defect. This does not select the production policy in O-001 or authorize asynchronous transport.

### D-006 — Use GGUF as the initial backing store

**Status:** ACCEPTED

The loader will expose exact file spans for each expert projection. Do not create `experts.ggex` or another format until measured evidence shows that GGUF layout or three-span reads are a material bottleneck.

A future packed expert format remains possible but is not part of the initial implementation contract.

### D-007 — Hot and cold tiers are independent and reclaimable on discrete GPUs

**Status:** ACCEPTED

GGUF/storage remains the authoritative immutable backing store. A valid published
hot expert does not require a live cold-cache reference after device readiness,
and hot eviction never performs GPU-to-host writeback. If no cold copy remains,
a later demand reloads the expert from storage.

Cold residency is retained and reclaimed independently according to cold policy.
A cold entry is evictable once no transfer, request, or CPU-execution reference
needs it; hot residency alone does not pin it. A real cold hit can still promote
through the bounded transfer ring and remain cold-resident.

For a `PROMOTE_AND_GPU` hot miss plus cold miss, the accepted demand path may read
directly from storage into a bounded pinned transfer lane, stream H2D as reads
complete, and publish the hot generation only after device readiness, without a
durable cold admission or cold-to-ring staging copy. Failure, cancellation, and
teardown remain generation-checked and fail closed.

Issue #69 final-capable evidence supersedes the initial strictly inclusive rule:
independent tiers increase useful hierarchy residency, avoid mandatory duplicate
residency, and preserve the durable no-writeback principle.

### D-008 — Do not pin the entire cold cache by default

**Status:** ACCEPTED

Use aligned pageable/hugepage-capable host memory for the large cold cache and a bounded pinned/registered transfer ring for asynchronous H2D. A dedicated configuration may register a larger region on systems where that is safe and beneficial.

### D-009 — CUDA is the target accelerator backend

**Status:** ACCEPTED

CPU remains required for reference correctness, trace generation, and possible discrete-GPU miss execution. CUDA is the final compute backend for DGX Spark and NVIDIA discrete GPUs.

### D-010 — Miss execution policy is explicit

**Status:** ACCEPTED

The design supports:

- `PROMOTE_AND_GPU`;
- `CPU_FALLBACK`;
- `AUTO`.

No cache miss may silently force an unmeasured synchronous transfer strategy.

### D-011 — Reuse prior work selectively, not by adopting a fork

**Status:** ACCEPTED

The current K3-capable `llama.cpp` branch remains the base. Prior branches are references for APIs, algorithms, I/O, tests, and failure modes. Large cherry-picks are prohibited unless reviewed component by component.

### D-012 — Split mechanism from policy

**Status:** ACCEPTED

Storage, transport, directory, scheduler, cache policy, prefetch policy, and execution policy are separate components with testable interfaces.

### D-013 — Deterministic numerical reduction

**Status:** ACCEPTED

Asynchronous expert completion must not change the canonical top-k reduction order. Performance work cannot reorder floating-point accumulation without a separately approved correctness policy.

### D-014 — Tiny K3 models are architecture fixtures, not performance proxies

**Status:** ACCEPTED

The 0.40B models validate K3 graph semantics, conversion, MXFP4 representation, routing, and cache correctness. Full-size I/O behavior must be validated with exact-size synthetic expert stores and eventually the full checkpoint.

### D-015 — Upstream work must be split into reviewable layers

**Status:** ACCEPTED

Do not submit a single large upstream PR. Expected decomposition:

1. neutral provider/storage interfaces and telemetry;
2. CPU/reference behavior;
3. persistent hot cache mechanism;
4. CUDA transport/execution;
5. disk tier;
6. policies and prefetch;
7. UMA-specific implementation.

Actual decomposition may change, but each PR must have independent value and tests.

### D-016 — Resident provider preserves logical IDs and request-scoped leases

**Status:** ACCEPTED

Phase 3 establishes these durable runtime semantics:

- `ExpertKey` is `(layer_index, original_expert_id)`; the original model ID remains authoritative for routing and observation.
- `ExpertSelection.logical_ids` and kernel-facing `execution_ids` are distinct concepts. The resident provider aliases their tensor because it performs no remap; later physical slots must use an explicitly different identity.
- A model owns at most one immutable provider selected at load time. A disabled model owns none, and provider state is destroyed before the model buffers it borrows.
- Graph results own stable bindings, while each context owns its request plans and move-only handles. Handles remain live through asynchronous submission and are released after scheduler completion or immediately when submission never began.
- Provider allocation failures map to `GGML_STATUS_ALLOC_FAILED`; other binding/preparation failures map to `GGML_STATUS_FAILED`; CPU abort continues to map to `GGML_STATUS_ABORTED`.
- Storage, transport, and cache policy remain downstream dependencies. Graph construction and GGML kernels do not depend on them directly.

The only public surface added in Phase 3 is the per-model experimental disabled/resident selection. Runtime provider replacement remains test-only and is forbidden while contexts exist.

### D-017 — Resident-provider administration uses bounded model and ubatch state

**Status:** ACCEPTED

The issue #13 corrective amendment preserves routing, tensors, kernels, graph topology, and residency while bounding administrative work:

- each resident provider owns a model-lifetime, per-routed-layer descriptor registry; the first accepted descriptor receives full validation and later bindings validate stable identity plus the current symbolic selection;
- registry publication is thread-safe, model-owned, and contains no global state;
- each nonempty submitted ubatch holds exactly one resident lease across asynchronous completion, while an empty binding set holds none;
- preparation validates every binding against its provider and registered descriptor, and every failure or cancellation releases an acquired lease before returning;
- provider-enabled graph results reserve their binding vector from the model layer bound before graph construction; disabled contexts retain zero provider storage; and
- descriptor-registration, full-validation, fast-path, lease, and binding-capacity counters remain diagnostic rather than public ABI.

The original issue #13 performance budgets and workload remain unchanged. Design-authority comment `5127774849` authorizes exactly one complete post-optimization v2 standing capture after the corrective commits and all prerequisite evidence are published. Historical captures remain immutable and non-authoritative for that new disposition.

### D-018 — Accept the Phase 3 seam with narrow performance notes

**Status:** ACCEPTED

Design-authority comments `5128658370` and `5128726338` accept the Phase 3 technical exit as `PASS_WITH_NOTES` for project progression only. The immutable post-optimization capture remains a raw `fail`: 22 of 24 original cells pass, while MXFP4 CUDA disabled-versus-resident prompt throughput and TTFT exceed their unchanged one-sided confidence budget. The capture, statistics, and budgets are not relabelled or modified.

The waiver is limited to those two representations of the same five-token prompt duration. Every baseline-to-disabled and decode cell passes, as do correctness, lifecycle, structural-zero-work, graph, scope, and evidence-integrity prerequisites. No further Phase 3 optimization or capture is authorized.

This decision is not precedent for waiving correctness, default-path performance, steady-state decode, later cache/transport/miss or multi-request performance, full-size performance, or tail latency. Those later gates remain independently binding.

### D-019 — Buffered `io_uring` is the default Linux cold transport; direct I/O is explicit

**Status:** ACCEPTED

Phase 7 resolves O-003 for the initial Linux implementation. `COLD_CACHE + MMAP` attempts bounded buffered `io_uring` and visibly falls back to the accepted synchronous positional reader when the ring or required operations are unavailable. `LLAMA_LOAD_MODE_DIRECT_IO` is an explicit opt-in: it attempts validated `O_DIRECT` handles and aligned asynchronous reads, records useful/aligned/scatter bytes, and visibly falls back per source or operation to buffered `io_uring` when direct I/O cannot represent the request safely. Hard media/I/O errors are not retried through a different path.

Issue #24 evidence records 218 direct sources on the validated split F16 fixture, 117 direct operations, 30670848 useful bytes within 30730752 aligned bytes, and 21 explicit buffered fallback operations with exact disabled/cold outputs. Buffered mode remains the default because direct mode is filesystem- and alignment-dependent and the tiny fixture does not establish a universal performance advantage. This decision does not authorize automatic direct-I/O selection, GDS, a storage-to-hot bypass, or a new expert format.

### D-020 — Resolve resident and expert device roles once at model initialization

**Status:** ACCEPTED

Multi-GPU out-of-core execution is described by two logical sets of physical CUDA devices: `ResidentPool` for the ordinary persistent model/primary graph and `ExpertPool` for routed-expert hot residency, transfers, and execution. In v1, `ResidentPool` contains exactly one primary device; `ExpertPool` contains one or more devices and may overlap the resident device or be disjoint.

The immutable role plan is resolved before model placement and selects exactly one implementation shape:

- local single-device: resident and sole expert device are identical, using the accepted one-device implementation without steady-state pool dispatch, role lookups, branch vectors, peer transport, extra execution-ID tensors, allocations, or synchronization;
- remote single-expert-device: the sole expert device differs from the resident device, using one remote expert branch and explicit activation/result transport back to the resident graph;
- striped multi-expert-device: two or more expert devices, reusing the accepted Phase-13 owner-striped provider and graph machinery, with canonical merge on the resident device.

CUDA UUID is the durable physical identity. Normalized PCI BDF is recorded for topology and determines canonical `ExpertPool` order, with UUID as the deterministic tie-breaker; CUDA ordinal is only a process-local resolution detail. Expert ownership remains `ExpertPool[original_expert_id % ExpertPool.size()]` and does not depend on capacity. Adaptive placement, migration, replication, weighted ownership, and failover are not part of v1.

Expert hot capacity is an exact whole-expert slot count for each physical expert device. Allocation either satisfies every requested count or fails closed; runtime auto-sizing or silent capacity reduction is forbidden. `MAX_SAFE` is a separate bounded evidence workflow that probes exact configurations and emits a versioned capacity manifest for a later production run; it is not a production allocation mode.

Ordinary router, attention, dense, shared-expert/shared-projection, primary-graph, and persistent-state tensors remain ordinary resident placement. Only routed expert bundles remain owned by `ExpertWeightProvider`. Inter-device movement remains explicit `HOST_STAGED` or `P2P` transport on the required resident-to-expert and expert-to-resident directed edges; unavailable requested transport, device failure, generation mismatch, or teardown with live work fails closed and never silently falls back to resident expert execution.

Legacy one-device configuration continues to resolve to the exact local single-device path. The Phase-13 symmetric configuration maps to resident DeviceId 0 plus the existing uniformly sized expert-device list. No multi-GPU topology becomes an automatic production default until explicit topology/capacity evidence supports that policy.

Rationale: the resident GPU and an expert-only GPU have materially different VRAM pressure. Separating roles lets several devices contribute bounded usable expert-cache capacity without pretending that VRAM is physically unified, while preserving the accepted provider/cache/scheduler architecture and isolating the one-GPU hot path from multi-device administration.

Consequences: topology and capacity are explicit configuration; every run emits a per-device memory/transport ledger; disjoint and asymmetric-overlap configurations are supported without rewriting the Phase-13 mechanism; dedicated versus striped performance is selected empirically rather than assumed; and multi-resident ordinary-model partitioning remains future work.

### D-021 — Permit one bounded default-off cache-aware routing experiment

**Status:** ACCEPTED

Phase 13.6 introduces one deliberate exception to the invariant that selected expert IDs never change. The exception applies only when the explicit cache-aware routing experiment is enabled. The ordinary exact top-k selection remains computed and available as the reference; every replacement must move from a more expensive provider service tier to a cheaper tier, satisfy the configured non-negative finite score-regret bound, preserve the displaced expert's rank slot, and remain within the hard per-layer/token swap bound. A configured maximum regret of zero, a maximum swap count of zero, or a candidate count equal to top-k is an exact control and performs no substitutions.

The policy is deterministic, bounded, model-owned, and separate from provider ownership and cache policy. It consumes only the ordered exact top-k, bounded ordered candidates, original K3 selection scores, and a contemporaneous read-only provider residency/service snapshot. It always returns the original top-k cardinality with unique valid logical expert IDs. It is not learned or adaptive and cannot inspect or mutate expert tensors.

K3 correction bias remains selection-only. After the experimental membership decision, expert weights are gathered from the unchanged ordinary unbiased probabilities and use the existing normalization and scale rules. Router projection, router logits, correction-bias values, routing probabilities, expert weights, and accumulation order are not redefined by this decision.

The exact path remains the production/default path and cannot activate the experiment implicitly. Disabled mode retains the existing route IDs, order, weights, graph/provider boundary, and structural zero-work behavior: no top-M copy, residency query, host reranking, provider callback, synchronization, or steady-state allocation is allowed solely for Phase 13.6. This exception does not authorize expert dropping, pruning, arbitrary rerouting, learned router changes, approximate arithmetic, or a new cache policy.

### D-022 — Share physical system-memory expert residency and stop the common mechanism at `HOST_READY`

**Status:** ACCEPTED

CPU-only cold execution, coherent UMA execution, and the durable cold tier for discrete CUDA share `ColdExpertCache` as the one physical system-memory expert store. A bounded internal current-layer demand coordinator borrows the model-owned storage, scheduler, and asynchronous transport; it performs exact occurrence planning, preserves the semantic order frozen by each provider, keeps that semantic order separate from physical I/O issue and completion order, performs direct-to-final-slot backing reads, deterministic publication, failure cleanup, and temporary lifetime retention only through validated `HOST_READY` cold references. It does not impose a universal canonical reservation order and does not own another cache, policy, scheduler, worker, ring, transport, or device-specific state.

CPU, UMA, and discrete CUDA remain distinct after `HOST_READY`. CPU acquires generation-protected execution references to the same cold slots. UMA applies its accepted coherent-memory readiness and pressure policy to those slots without a second payload pool or expert H2D copy. Discrete CUDA retains its independent hot tier, direct storage-to-hot service, transfers, events, and device-role semantics. Publication-to-adapter lifetime is bridged by an explicit bounded cache reference and never by the current single-request restriction.

### D-023 — Prefer the cleaner coherent architecture when performance is equivalent

**Status:** ACCEPTED

Optimization choices use preregistered paired endpoints, ordering, noise estimates, and materiality floors. Alternatives are performance-equivalent when the paired confidence interval includes parity and no endpoint, tail, or resource delta exceeds its floor. Until tighter evidence justifies another bound, the floors are 3% for median decode TPS, 5% for p95 routed-forward latency, 5% for owned, pinned, or device memory with no unbounded state, and no material all-hit regression; correctness and lifetime have zero tolerance.

When alternatives are equivalent, select the design with clearer ownership and lifetime, fewer independent mechanisms and public surfaces, stronger deterministic failure semantics, and greater reuse across CPU, UMA, and discrete CUDA. A materially faster divergent mechanism may win only when its gain exceeds the preregistered floor, its cause is demonstrated, and design authority explicitly accepts the semantic and maintenance cost. Equivalent and negative results remain evidence rather than being discarded or selected by point estimate.

## Rejected shortcuts

### R-001 — Rely exclusively on `mmap` and OS page replacement

**Status:** REJECTED

It does not provide expert-aware capacity, priority, or predictable latency.

### R-002 — Reuse the scheduler staging tensor as a persistent cache

**Status:** REJECTED

Graph-temporary lifetime invalidates resident metadata and can produce stale reads and NaNs.

### R-003 — Put the implementation in global state inside `ggml-backend.cpp`

**Status:** REJECTED

Prior prototypes demonstrate that this becomes unreviewable and unsafe for multiple models, requests, and devices.

### R-004 — Discover model files through `/proc/self/maps`

**Status:** REJECTED

The GGUF loader must provide the file identity and offsets directly.

### R-005 — Pin all host expert weights without a budget

**Status:** REJECTED

Pinned memory is bounded and explicit.

### R-006 — Assume an N+1 prefetcher is useful

**Status:** REJECTED as an assumption

It can be implemented only as an evaluated policy. At least one prior prototype reported negligible hit rate for its N+1 approach.

### R-007 — Create a new expert file format before measurement

**Status:** REJECTED for the initial implementation

GGUF remains the backing store until profiling proves otherwise.

### R-008 — Treat model-card claims as more authoritative than checkpoint tensors

**Status:** REJECTED

Conversion and runtime behavior follow the actual downloaded checkpoint. Discrepancies must be documented.

## Open decisions

### O-001 — Production cache policy

**Status:** ACCEPTED

For the supported single-request discrete-GPU envelope, exact global LRU with `ALWAYS` admission is the production null default for both hot and cold tiers. Phase 9 issue #30 evaluated deterministic LFRU, SLRU, frequency-gated SLRU admission, LFU-aging, WASTE-style sampled replay baselines, and hard per-layer domains against immutable trace replay, online agreement, fixed statistical gates, physical-residency sweeps, and prefill/decode behavior. No non-LRU global pair satisfied every frozen default gate, so retaining LRU is an evidence-derived choice rather than a test-only placeholder.

Per-layer policy remains explicit-only in v1. Explicit valid caller configurations are preserved, invalid configurations fail closed before cache initialization, and no runtime auto-sizing or adaptive policy is authorized. The scoped evidence-derived cold-budget recommendations are not compiled defaults. Concurrent requests, UMA, multi-GPU, speculative prefetch, new formats, and full production-K3 performance require their later-phase decisions and evidence.

### O-002 — Default discrete-GPU miss execution

**Status:** ACCEPTED

Phase 8 selects `PROMOTE_AND_GPU` as the stable default. `CPU_FALLBACK` is an explicit opt-in that executes missed routed rows on the existing CPU path, may overlap independent hot GPU work, and preserves canonical top-k accumulation order. Background promotion is disabled unless explicitly requested.

`AUTO` is also explicit and deterministic. It consumes only caller-supplied, versioned cost operands and selects CPU only when its complete predicted current-output cost is strictly lower; missing or invalid operands fail closed to `PROMOTE_AND_GPU`, and ties select GPU. The runtime does not learn, explore, or silently switch policy online. A future calibrated or adaptive model requires a new decision and evidence; Phase 8's controlled matrix does not establish such a production default.

### O-004 — `cuFile` / GPUDirect Storage

**Status:** SPECULATIVE

Potential future transport for discrete GPUs. It is not required for the first correct design and must not bypass cold-cache semantics without a measured reason.

### O-005 — Full-model expert storage format

**Status:** OPEN

Keep GGUF initially. A contiguous expert-bundle store may be introduced only after measuring read amplification and queue overhead.

### O-006 — UMA allocation mechanism

**Status:** OPEN

Candidates include system allocations visible to CUDA, managed allocations, or explicit CUDA/driver APIs. The choice must be tested on DGX Spark for residency control, page faults, and kernel accessibility.

### O-007 — Multi-GPU ownership and sharding

**Status:** ACCEPTED

Resolved by D-020. V1 uses one initialization-resolved resident primary, an overlapping or disjoint expert-device set, canonical BDF/UUID ordering, modulo ownership over original logical expert IDs, exact per-device whole-expert capacities, explicit peer transport, and fail-closed lifecycle. Topology performance and any future weighted/adaptive ownership remain evidence questions, not unresolved v1 architecture.

### O-008 — Multi-request policy

**Status:** OPEN

Need fairness, per-request priority, cache pollution control, cancellation, and batching semantics. Global frequency alone may starve minority workloads.

### O-009 — Static hot-set seeding

**Status:** OPEN

Potential inputs include imatrix counts, recorded traces, and model-specific profiles. Must be optional and validated against cold-start and domain-shift workloads.
