# UMA, full-size scaling, diagnostic tracing, multi-GPU, benchmark readiness, concurrency, and hardening

The pinned WASTE implementation and measurements recorded in `docs/PRIOR_ART.md` are an external full-K3 feasibility and performance baseline. They are `OBSERVED` on a 64 GB Apple M5 Pro CPU/UMA system with a custom 3-bit expert format and internal NVMe; they are not directly transferable to GGUF/MXFP4, discrete CUDA, or DGX Spark.

## Phase 11 — Coherent UMA transport on DGX Spark

### Objectives

- Reuse the same provider/cache policy with one physical memory pool.
- Determine whether coherent CUDA execution improves on the CPU/UMA streaming regime demonstrated by WASTE without relying on unmanaged paging.

### Tasks

#### 11.1 Platform characterization

- [ ] Build ARM64 CUDA `llama.cpp` and validate monolithic tiny K3.
- [ ] Record actual CUDA/driver unified-memory capabilities.
- [ ] Measure NVMe and unified-memory bandwidth/latency.
- [ ] Verify allocation APIs accessible to both CPU I/O and CUDA kernels.
- [ ] Record normalized bytes per expert, bytes per token, effective storage bandwidth, I/O wait, expert-compute time, and residual runtime time using the same definitions as the pinned WASTE baseline where possible.
- [ ] If practical on available hardware, run the pinned WASTE commit as a same-machine storage/CPU reference; otherwise document why only a normalized comparison is possible.

#### 11.2 UMA slots

- [ ] Implement unified slots with logical cold/hot states.
- [ ] Remove duplicate RAM/VRAM copies.
- [ ] Implement readiness through prefetch/advice/fault preparation as appropriate.
- [ ] Keep fixed addresses.

#### 11.3 UMA eviction

- [ ] Separate logical cache eviction from physical memory reclamation.
- [ ] Ensure resident model and runtime allocations retain protected budgets.
- [ ] Avoid uncontrolled system swap as cache policy.
- [ ] Sweep cache budgets below, at, and above whole token-working-set multiples while recording physical residency, page faults, swap/compression where observable, and service time for logical hits.
- [ ] Treat a logical cache hit that requires OS paging as a degraded miss-equivalent event in performance telemetry.
- [ ] Verify that memory advice or purgeable/freeable mechanisms degrade gracefully and do not destroy useful residency at the normal operating point.

#### 11.4 Comparison

- [ ] Replay the same traces and capacities used on discrete GPU.
- [ ] Compare demand stalls, bytes, and tail latency.
- [ ] Determine whether two logical policy segments are useful without physical duplication.
- [ ] Compare against WASTE using normalized workload and tier metrics, not raw tokens/second alone, because quantization, kernels, format, CPU, and storage differ.
- [ ] Explain whether any gain comes from CUDA compute, greater resident capacity, lower copy cost, faster NVMe, better policy, or a different expert representation.

### Exit gate

- Same correctness suite passes on Spark.
- NVMe-to-unified-memory-to-CUDA pipeline is explicit and measured.
- No reliance on unmanaged swap/page thrashing.
- Cache autofit remains below the measured residency cliff with explicit system/runtime headroom.
- The WASTE comparison is reproducible or its non-comparable dimensions are documented quantitatively.

---

## Phase 12 — Full-size scaling and storage decision

### Objectives

- Prove that the design scales physically before claiming full K3 viability.
- Establish the single-request, single-device full-size baseline before adding multi-GPU or multi-request complexity.
- Compare against the pinned WASTE full-K3 result without conflating format, quantization, or hardware effects.

### Tasks

#### 12.1 Synthetic exact-size store

- [ ] Generate exact full-K3 MXFP4 expert spans from real metadata.
- [ ] Validate queue depth, cold/hot budgets, and transfer overlap.
- [ ] Run controlled traces with realistic top-k and layer counts.
- [ ] Cross-check full-K3 layer count, selected-expert count, exact working-set bytes, resident-trunk budget, and expected reads/token against the pinned WASTE metadata, explaining representation differences.
- [ ] Sweep storage and cache regimes that bracket WASTE's observed one-token-working-set floor and memory-oversubscription cliff.

#### 12.2 GGUF layout evaluation

Measure:

- three-span reads per expert;
- alignment read amplification;
- split-file behavior;
- queue and syscall overhead;
- storage locality across selected experts;
- bytes per complete expert bundle and bytes per token;
- effective bandwidth and first-read latency at the real expert-record size;
- the performance gap versus WASTE's one-aligned-record-per-expert custom layout, normalized for different expert precision and payload size.

#### 12.3 Optional expert-specific format

Only if GGUF is proven inadequate:

- [ ] Specify a versioned format with checksums and explicit tensor metadata.
- [ ] Keep resident model metadata in GGUF.
- [ ] Build a deterministic converter and verifier.
- [ ] Compare end-to-end gains against added complexity.
- [ ] Measure K3 route-weight distribution before proposing partial-record or per-activation precision. WASTE observed that the lower half of selected experts carried about one third of routing mass, so no low-value tail may be assumed.
- [ ] Compare any compressed expert representation on output quality, bytes/read, direct-kernel cost, conversion footprint, and end-to-end throughput; do not infer benefit from bit width alone.
- [ ] Review the Apache-2.0 WASTE implementation and attribution requirements before reusing any format, direct-kernel, diskbench, or cache code.

#### 12.4 Full checkpoint

- [ ] Acquire/convert the full K3 text checkpoint when resources permit.
- [ ] Validate metadata against synthetic assumptions.
- [ ] Start with trace and I/O dry-run before inference.
- [ ] Scale capacities gradually with hard memory limits.
- [ ] When feasible, run the pinned WASTE commit and this runtime on the same host and NVMe; otherwise publish a normalized comparison covering bytes/token, effective bandwidth, cache residency, I/O/compute decomposition, quality constraints, and tokens/second.
- [ ] Preserve WASTE's published 0.49–0.54 tok/s M5 Pro result as an external reference, not as a universal gate or an unverified local reproduction.

### Exit gate

- Full-size physical behavior is measured for a single request on each validated single-device transport.
- Any new storage format is justified quantitatively.
- A realistic throughput/tail-latency envelope is documented.
- Full-size claims include an apples-to-apples WASTE comparison where possible, or a precise explanation of the remaining non-equivalent dimensions.
- Results are explicitly scoped as single-request and single-device; multi-GPU topology and service concurrency remain later gates.

---

## Phase 12.5 — Diagnostic tracing subsets: DeepSeek and NVMe/K3

Phase 12.5 is a **bounded diagnostic subset**, not the complete end-to-end hardware-benchmark-readiness phase. It establishes reusable tracing machinery and causal-attribution methods where evidence was already needed before later architecture work.

The DeepSeek subset was executed through issue #54 / PR #55. The physical-NVMe and real-K3 storage/endpoint subsets were subsequently exercised through Phase 12-NVMe #58. Their historical issue, PR, result-directory, and manifest identities remain unchanged.

### Objectives

- Add default-off Perfetto/CUPTI-capable instrumentation at the existing provider/cache/scheduler/storage/transfer/graph seams without changing runtime semantics.
- Attribute the accepted DeepSeek discrete-provider bottleneck using a coherent application/OS/CUDA trace.
- Reuse the same event vocabulary and analysis method for physical-NVMe and real-K3 storage/endpoint evidence where required by Phase 12.
- Establish bounded trace integrity, perturbation measurement, stable causal identities, and reproducible analysis as prerequisites for later topology work.

### Accepted scope boundary

Phase 12.5 may establish and validate:

- request/token/layer/expert/flight/cache/storage/transfer identities;
- scheduler, syscall, block-I/O, CPU, H2D and CUDA correlation where available;
- exposed versus overlapped wait definitions;
- trace-loss, bounded-volume and perturbation gates;
- reproducible Perfetto SQL/analysis for the validated DeepSeek and NVMe/K3 paths.

Phase 12.5 does **not** claim that:

- every Phase 12 discrete-CUDA full-K3 path has been traced end to end;
- multi-GPU device/peer/topology identities are complete;
- cross-hardware benchmark schemas are final;
- multi-request/batching traces are covered.

Those remaining observability gaps belong to Phase 13.5 after the Phase 12 single-GPU and Phase 13 multi-GPU paths exist.

### Exit gate

- Default-off tracing is behavior-neutral and bounded on the validated subsets.
- The accepted DeepSeek and NVMe/K3 decision-driving traces can be reproduced and causally attributed with explicit residual and perturbation.
- The event vocabulary is stable enough to be reused by Phase 13 topology validation.
- Missing multi-device/cross-hardware coverage is explicitly deferred to Phase 13.5 rather than implied complete.

---

## Phase 13 — Multi-GPU and topology-aware placement

### Objectives

- Extend the provider to multiple GPUs without redesigning core policy or storage.
- Measure same-host topology-aware scaling relative to the Phase 12 single-device full-size baseline using the accepted Phase 12.5 diagnostic event vocabulary.
- Establish multi-device ownership and byte movement before adding multi-request/batching complexity.

### Tasks

- [ ] Define per-device slot directories and ownership.
- [ ] Support expert partitioning and/or replicated hot sets.
- [ ] Model PCIe/NVLink/peer-access topology.
- [ ] Decide whether cold cache is shared or NUMA-local.
- [ ] Route H2D through the correct NUMA node.
- [ ] Reserve per-device workspaces before cache allocation.
- [ ] Handle device failure/trim independently.
- [ ] Validate deterministic outputs across shard strategies.
- [ ] Report normalized bytes per expert and per token across NVMe, host memory, H2D, and peer links.
- [ ] Keep decision-driving acceptance single-request so topology/placement effects are not confounded with cross-request coalescing or batching.
- [ ] Extend trace identity only as much as needed to distinguish device, ownership, peer movement and topology for same-host Phase 13 evidence; broader benchmark-schema completion belongs to Phase 13.5.

### Exit gate

- Correctness across supported sharding modes.
- Per-device ownership, generations, lifecycle and failure isolation are explicit and bounded.
- Topology-aware metrics show where bytes moved across NVMe, host memory, H2D and peer links.
- Incremental scaling is reported against the Phase 12 single-device baseline using compatible Phase 12.5 metric definitions.
- No multi-request semantics or authoritative cross-hardware ranking are required for Phase 13 acceptance.

---

## Phase 13.5 — End-to-end observability and hardware-benchmark readiness

Phase 13.5 closes the observability work **not** completed by the Phase 12.5 DeepSeek/NVMe subsets. It begins after the selected Phase 12 single-GPU path and Phase 13 multi-GPU topology path are available, so the benchmark schema can describe the actual final single-request architectures rather than pre-design abstractions.

### Objectives

- Make the complete selected Phase 12 single-GPU token path causally observable from decode through storage, H2D, GPU compute, synchronization, sampling and delivery.
- Make the Phase 13 multi-GPU token path causally observable across device ownership, peer/NVLink/PCIe movement, per-device compute and synchronization.
- Version the Phase 12.5 event vocabulary only where new multi-device identities or metrics require it.
- Establish the authoritative reproducible benchmark schema for later cross-hardware and service-concurrency campaigns.

### Tasks

#### 13.5.1 Complete selected single-GPU coverage

- [ ] Reuse the Phase 12.5 instrumentation and Phase 12 final configuration; do not repeat broad DeepSeek or storage-only tracing.
- [ ] Attribute exposed NVMe wait, cold-cache service, H2D, hot-cache avoidance, GPU expert compute, remaining graph compute, synchronization, sampling and residual wall time.
- [ ] Correlate application intervals with CUPTI kernels/memcpy/synchronization and Linux scheduler/block activity where material.
- [ ] Quantify active trace perturbation against an adjacent untraced run.

#### 13.5.2 Add multi-GPU identity and topology correlation

- [ ] Add stable device ID, device-local slot/generation, ownership/shard identity, peer source/destination, interconnect class, stream and correlation identities where the Phase 12.5 schema lacks them.
- [ ] Correlate per-device H2D, peer transfers, kernels, synchronization and request/token/layer/expert identities.
- [ ] Distinguish useful peer movement, replicated loads, remote ownership waits, topology queueing and synchronization from GPU compute.
- [ ] Preserve bounded label cardinality and no pointer/payload export.

#### 13.5.3 Authoritative metric schema

Produce compatible token-level decomposition for single- and multi-GPU paths:

```text
end-to-end token latency
├── graph/router/provider work
├── exposed storage wait
├── exposed H2D wait
├── exposed peer/topology wait
├── GPU expert compute
├── remaining GPU/CPU compute
├── backend/device synchronization
├── sampling/token delivery
└── residual/unattributed
```

- [ ] Report elapsed service separately from exposed critical-path stall.
- [ ] Record NVMe, host, H2D and peer bytes per token and per device.
- [ ] Record hot/cold occupancy, queue depths, in-flight reads/transfers, GPU utilization and device-memory high-water.
- [ ] Define normalized fields required for later comparisons across A10/3090-class PCIe systems, higher-VRAM discrete GPUs and coherent UMA without claiming raw-TPS equivalence across formats or quantization.

#### 13.5.4 Reproducibility and readiness

- [ ] Verify tracing-disabled, compiled-in inactive and active-capture correctness/performance boundaries.
- [ ] Preserve bounded trace memory and explicit drop/loss counters.
- [ ] Publish reproducible capture configurations and analysis queries for the selected single- and multi-GPU paths.
- [ ] Bind every authoritative benchmark to exact revisions, hardware/topology, driver/CUDA/kernel, storage, runtime configuration, workload and trace identity.

### Exit gate

- A complete token can be reconstructed on the selected single-GPU path and across every material multi-GPU handoff.
- Application tracks correlate unambiguously with relevant scheduler, storage, CUDA and peer/topology activity.
- The standard analysis reports exposed storage/H2D/peer waits separately from overlapped service and reports material residual explicitly.
- Trace overhead/loss is bounded and disclosed.
- The benchmark schema is sufficient for authoritative cross-hardware comparisons and for Phase 14 to add concurrency without inventing incompatible metrics.

---

## Phase 14 — Multi-request, batching, and CUDA graphs

### Objectives

- Make the design safe beyond a single sequential request after the single-request multi-GPU ownership model is established.
- Measure concurrency and batching gains relative to the Phase 12 single-request baseline and Phase 13 topology baseline using the Phase 13.5 benchmark schema.

### Tasks

- [ ] Define per-model shared cache and per-request scheduler state.
- [ ] Coalesce identical expert demand across requests.
- [ ] Add fairness and priority rules.
- [ ] Handle request cancellation without canceling shared useful loads.
- [ ] Deduplicate experts across batched prefill.
- [ ] Record unique expert records loaded separately from token-expert compute pairs so I/O deduplication is not mistaken for compute reduction.
- [ ] Measure whether batching and cross-request coalescing compose with the Phase 7 overlap pipeline rather than assuming independent speedups multiply.
- [ ] Reproduce or refute WASTE's `OBSERVED` CPU result that token grouping removed roughly 70–76% of marginal expert I/O while removing none of the per-token expert compute; derive the corresponding ceiling for each target backend.
- [ ] Validate slot mapping with CUDA graph capture/replay.
- [ ] Keep dynamic provider preparation outside graph capture while mapping updates remain in place.
- [ ] Measure batch diversity impact on cache hit rate.
- [ ] Add stress tests with concurrent model contexts and requests.
- [ ] Verify concurrency semantics across selected single- and multi-GPU configurations without silently changing Phase 13 placement policy.

### Exit gate

- No data race, stale slot, starvation, or cross-request corruption.
- CUDA graph behavior is correct where enabled.
- Multi-request metrics are exposed.
- Reported batching gains decompose storage deduplication, transfer avoidance, compute utilization, scheduler effects, and interaction with Phase 13 placement.
- Incremental results are compared with accepted single-request baselines and decomposed through the Phase 13.5 schema.

---

## Phase 15 — Production hardening and upstream strategy

### Objectives

- Make the implementation maintainable, diagnosable, and reviewable.

### Tasks

- [ ] Fuzz storage metadata and short-read/error paths.
- [ ] Run ASan/UBSan/TSan where applicable.
- [ ] Add long-running load/unload and warm-cache stress tests.
- [ ] Expose stable CLI/config and structured metrics.
- [ ] Preserve or explicitly version the Phase 13.5 E2E event and metric schema for production diagnostics while keeping active high-volume tracing opt-in.
- [ ] Distinguish logical cache hits, physically resident hits, faulted/degraded hits, and true storage misses in stable telemetry.
- [ ] Add safe autofit that reserves CUDA/runtime/KV/workspace budgets.
- [ ] Make safe autofit reason in whole token-working-set increments where useful and retain explicit OS headroom rather than filling nominal RAM.
- [ ] Add runtime guardrails for sustained major faults, swap/compression, or cache-hit service-time collapse; reduce or reject an unsafe budget instead of silently reporting a higher hit rate.
- [ ] Document unsupported combinations as errors.
- [ ] Produce architecture and operations documentation.
- [ ] Split upstream contributions into small PRs according to accepted decisions.
- [ ] Follow upstream AI-use and contribution rules.
- [ ] Preserve third-party attribution and license compliance, including Apache-2.0 obligations for any WASTE-derived code.

### Final acceptance criteria

The project is successful only when:

1. K3 tiny models match monolithic correctness through every residency path.
2. Full-size synthetic experts demonstrate bounded NVMe/RAM/VRAM behavior.
3. Discrete and UMA transports implement the same provider contract.
4. Cache and prefetch behavior is observable and trace-replayable.
5. Tail latency and throughput are measured under misses, not only warm hits.
6. Multi-request lifetimes are safe.
7. The runtime can explain every expert's current state and every byte transfer.
8. The design can progress to the full K3 checkpoint without another architecture rewrite.
9. Cache defaults remain physically resident under their declared operating envelope and do not trade higher logical hit rate for uncontrolled paging.
10. Full-size performance is compared with the pinned WASTE baseline on common hardware where practical, or through a documented normalized comparison that exposes all material differences.
11. Authoritative hardware benchmarks can attribute end-to-end token time across `llama.cpp`/GGML, the expert runtime, storage, host transfer or UMA readiness, multi-GPU peer/topology movement, backend synchronization, and compute, with tracing overhead and residual time disclosed.
