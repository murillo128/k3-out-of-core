# UMA, concurrency, scaling, and hardening

## Phase 11 — Coherent UMA transport on DGX Spark

### Objectives

- Reuse the same provider/cache policy with one physical memory pool.

### Tasks

#### 11.1 Platform characterization

- [ ] Build ARM64 CUDA `llama.cpp` and validate monolithic tiny K3.
- [ ] Record actual CUDA/driver unified-memory capabilities.
- [ ] Measure NVMe and unified-memory bandwidth/latency.
- [ ] Verify allocation APIs accessible to both CPU I/O and CUDA kernels.

#### 11.2 UMA slots

- [ ] Implement unified slots with logical cold/hot states.
- [ ] Remove duplicate RAM/VRAM copies.
- [ ] Implement readiness through prefetch/advice/fault preparation as appropriate.
- [ ] Keep fixed addresses.

#### 11.3 UMA eviction

- [ ] Separate logical cache eviction from physical memory reclamation.
- [ ] Ensure resident model and runtime allocations retain protected budgets.
- [ ] Avoid uncontrolled system swap as cache policy.

#### 11.4 Comparison

- [ ] Replay the same traces and capacities used on discrete GPU.
- [ ] Compare demand stalls, bytes, and tail latency.
- [ ] Determine whether two logical policy segments are useful without physical duplication.

### Exit gate

- Same correctness suite passes on Spark.
- NVMe-to-unified-memory-to-CUDA pipeline is explicit and measured.
- No reliance on unmanaged swap/page thrashing.

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
- [ ] Validate slot mapping with CUDA graph capture/replay.
- [ ] Keep dynamic provider preparation outside graph capture while mapping updates remain in place.
- [ ] Measure batch diversity impact on cache hit rate.
- [ ] Add stress tests with concurrent model contexts and requests.

### Exit gate

- No data race, stale slot, starvation, or cross-request corruption.
- CUDA graph behavior is correct where enabled.
- Multi-request metrics are exposed.

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

### Exit gate

- Correctness across supported sharding modes.
- Topology-aware metrics show where bytes moved.

---

## Phase 14 — Full-size scaling and storage decision

### Objectives

- Prove that the design scales physically before claiming full K3 viability.

### Tasks

#### 14.1 Synthetic exact-size store

- [ ] Generate exact full-K3 MXFP4 expert spans from real metadata.
- [ ] Validate queue depth, cold/hot budgets, and transfer overlap.
- [ ] Run controlled traces with realistic top-k and layer counts.

#### 14.2 GGUF layout evaluation

Measure:

- three-span reads per expert;
- alignment read amplification;
- split-file behavior;
- queue and syscall overhead;
- storage locality across selected experts.

#### 14.3 Optional expert-specific format

Only if GGUF is proven inadequate:

- [ ] Specify a versioned format with checksums and explicit tensor metadata.
- [ ] Keep resident model metadata in GGUF.
- [ ] Build a deterministic converter and verifier.
- [ ] Compare end-to-end gains against added complexity.

#### 14.4 Full checkpoint

- [ ] Acquire/convert the full K3 text checkpoint when resources permit.
- [ ] Validate metadata against synthetic assumptions.
- [ ] Start with trace and I/O dry-run before inference.
- [ ] Scale capacities gradually with hard memory limits.

### Exit gate

- Full-size physical behavior is measured.
- Any new storage format is justified quantitatively.
- A realistic throughput/tail-latency envelope is documented.

---

## Phase 15 — Production hardening and upstream strategy

### Objectives

- Make the implementation maintainable, diagnosable, and reviewable.

### Tasks

- [ ] Fuzz storage metadata and short-read/error paths.
- [ ] Run ASan/UBSan/TSan where applicable.
- [ ] Add long-running load/unload and warm-cache stress tests.
- [ ] Expose stable CLI/config and structured metrics.
- [ ] Add safe autofit that reserves CUDA/runtime/KV/workspace budgets.
- [ ] Document unsupported combinations as errors.
- [ ] Produce architecture and operations documentation.
- [ ] Split upstream contributions into small PRs according to accepted decisions.
- [ ] Follow upstream AI-use and contribution rules.
- [ ] Preserve third-party attribution and license compliance.

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
