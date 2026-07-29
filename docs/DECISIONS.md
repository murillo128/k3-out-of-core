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

**Status:** OPEN

Candidates: LFRU, SLRU plus admission filter, LFU-aging, or a hybrid. LRU exists only as a test baseline. Selection requires K3 prefill and decode traces.

### O-002 — Default discrete-GPU miss execution

**Status:** OPEN

`CPU_FALLBACK` may avoid synchronous PCIe stalls; `PROMOTE_AND_GPU` may be better when CPU MXFP4 throughput is low or reuse probability is high. `AUTO` requires a calibrated cost model.

### O-003 — Direct I/O versus buffered async I/O

**Status:** OPEN

The final Linux transport may use `io_uring` with `O_DIRECT`, buffered `io_uring`, or both. Filesystem support, alignment overhead, cache duplication, and actual NVMe queue behavior must be measured.

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
