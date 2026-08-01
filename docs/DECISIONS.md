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

### D-007 — Cold cache is initially inclusive on discrete GPUs

**Status:** ACCEPTED

A hot expert normally retains a host copy. Hot eviction therefore does not require GPU-to-host writeback. Cold entries backing pinned/in-flight transfers cannot be evicted.

This can be revisited if host-memory pressure dominates.

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

**Status:** OPEN

Need a directory model for per-device hot slots, host-cache sharing, expert ownership, and peer access. Single-GPU correctness comes first, but interfaces must not preclude multiple devices.

### O-008 — Multi-request policy

**Status:** OPEN

Need fairness, per-request priority, cache pollution control, cancellation, and batching semantics. Global frequency alone may starve minority workloads.

### O-009 — Static hot-set seeding

**Status:** OPEN

Potential inputs include imatrix counts, recorded traces, and model-specific profiles. Must be optional and validated against cold-start and domain-shift workloads.
