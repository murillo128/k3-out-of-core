# UMA, concurrency, scaling, and hardening

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

## Phase 12 — Multi-request, batching, and CUDA graphs

### Objectives

- Make the design safe beyond a single sequential request.

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

### Exit gate

- No data race, stale slot, starvation, or cross-request corruption.
- CUDA graph behavior is correct where enabled.
- Multi-request metrics are exposed.
- Reported batching gains decompose storage deduplication, transfer avoidance, compute utilization, and scheduler effects.

---

## Phase 13 — Multi-GPU and topology-aware placement

### Objectives

- Extend the provider without redesigning core policy and storage.

### Tasks

- [ ] Define per-device slot directories and ownership.
- [ ] Support expert partitioning and/or replicated hot sets.
- [ ] Model PCIe/NVLink/peer-access topology.
- [ ] Decide whether cold cache is shared or NUMA-local.
- [ ] Route H2D through the correct NUMA node.
- [ ] Reserve per-device workspaces before cache allocation.
- [ ] Handle device failure/trim independently.
- [ ] Validate deterministic outputs across shard strategies.
- [ ] Report normalized bytes per expert and per token across NVMe, host memory, H2D, and peer links so topology gains can be compared with the one-copy UMA and WASTE CPU/UMA baselines.

### Exit gate

- Correctness across supported sharding modes.
- Topology-aware metrics show where bytes moved.

---

## Phase 14 — Full-size scaling and storage decision

### Objectives

- Prove that the design scales physically before claiming full K3 viability.
- Compare against the pinned WASTE full-K3 result without conflating format, quantization, or hardware effects.

### Tasks

#### 14.1 Synthetic exact-size store

- [ ] Generate exact full-K3 MXFP4 expert spans from real metadata.
- [ ] Validate queue depth, cold/hot budgets, and transfer overlap.
- [ ] Run controlled traces with realistic top-k and layer counts.
- [ ] Cross-check full-K3 layer count, selected-expert count, exact working-set bytes, resident-trunk budget, and expected reads/token against the pinned WASTE metadata, explaining representation differences.
- [ ] Sweep storage and cache regimes that bracket WASTE's observed one-token working-set floor and memory-oversubscription cliff.

#### 14.2 GGUF layout evaluation

Measure:

- three-span reads per expert;
- alignment read amplification;
- split-file behavior;
- queue and syscall overhead;
- storage locality across selected experts;
- bytes per complete expert bundle and bytes per token;
- effective bandwidth and first-read latency at the real expert-record size;
- the performance gap versus WASTE's one-aligned-record-per-expert custom layout, normalized for different expert precision and payload size.

#### 14.3 Optional expert-specific format

Only if GGUF is proven inadequate:

- [ ] Specify a versioned format with checksums and explicit tensor metadata.
- [ ] Keep resident model metadata in GGUF.
- [ ] Build a deterministic converter and verifier.
- [ ] Compare end-to-end gains against added complexity.
- [ ] Measure K3 route-weight distribution before proposing partial-record or per-activation precision. WASTE observed that the lower half of selected experts carried about one third of routing mass, so no low-value tail may be assumed.
- [ ] Compare any compressed expert representation on output quality, bytes/read, direct-kernel cost, conversion footprint, and end-to-end throughput; do not infer benefit from bit width alone.
- [ ] Review the Apache-2.0 WASTE implementation and attribution requirements before reusing any format, direct-kernel, diskbench, or cache code.

#### 14.4 Full checkpoint

- [ ] Acquire/convert the full K3 text checkpoint when resources permit.
- [ ] Validate metadata against synthetic assumptions.
- [ ] Start with trace and I/O dry-run before inference.
- [ ] Scale capacities gradually with hard memory limits.
- [ ] When feasible, run the pinned WASTE commit and this runtime on the same host and NVMe; otherwise publish a normalized comparison covering bytes/token, effective bandwidth, cache residency, I/O/compute decomposition, quality constraints, and tokens/second.
- [ ] Preserve WASTE's published 0.49–0.54 tok/s M5 Pro result as an external reference, not as a universal gate or an unverified local reproduction.

### Exit gate

- Full-size physical behavior is measured.
- Any new storage format is justified quantitatively.
- A realistic throughput/tail-latency envelope is documented.
- Full-size claims include an apples-to-apples WASTE comparison where possible, or a precise explanation of the remaining non-equivalent dimensions.

---

## Phase 15 — Production hardening and upstream strategy

### Objectives

- Make the implementation maintainable, diagnosable, and reviewable.

### Tasks

- [ ] Fuzz storage metadata and short-read/error paths.
- [ ] Run ASan/UBSan/TSan where applicable.
- [ ] Add long-running load/unload and warm-cache stress tests.
- [ ] Expose stable CLI/config and structured metrics.
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
