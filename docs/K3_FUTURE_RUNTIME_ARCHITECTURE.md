# K3 future runtime architecture directions

## Status

**OPEN design note.**

This document captures architecture-level directions suggested by full-Kimi-K3 evidence from Phase 13.5. It is intentionally **not** an execution contract, does not modify the roadmap epic or `PLAN.md`, and does not reopen the frozen Phase 13.5 `K3_BEST` result.

This note also deliberately excludes router-locality/admission research. That is a separate research direction. The purpose here is to preserve the runtime-architecture conclusions that remain after separating them from model-representation choices and from router-policy work.

Use repository decision markers literally:

- `OBSERVED` — established by captured evidence;
- `OPEN` — a design direction worth testing but not accepted architecture;
- `SPECULATIVE` — plausible but currently weakly evidenced;
- `REJECTED` — contradicted by available evidence for the tested form.

## Executive summary

Phase 13.5 shows that the current runtime successfully executes complete Kimi K3 on four discrete GPUs, but its scaling is limited by the hierarchy that feeds expert computation rather than by raw expert arithmetic alone.

The strongest future architecture hypothesis is therefore:

> Stop treating GPU execution as the mandatory destination of every routed expert. Treat storage, host RAM, CPU, each GPU's VRAM, and each GPU's compute as first-class resources, then execute each already-selected expert where its bytes can be consumed at the lowest end-to-end critical-path cost while preserving exact routing semantics.

The main architecture candidates preserved here are:

1. a global cooperative expert-residency directory across all GPUs;
2. sticky expert ownership so VRAM hits are executed where the expert already resides instead of moving expert weights;
3. heterogeneous per-expert CPU/GPU execution based on data location and measured cost;
4. fine-grained asynchronous expert tasks, with the synchronization barrier delayed until deterministic expert aggregation;
5. explicit protection of the compulsory ordinary-model host working set from expert-cache eviction pressure;
6. configurable multi-resident ordinary placement as a distinct placement experiment, keeping model representation unchanged.

Quantizing the ordinary model is **not classified here as an architecture improvement**. It is a separate representation/performance experiment and must be measured independently so architecture gains are not confused with a smaller model representation. In particular, the router and routing semantics should remain untouched by such a comparison unless a separate model-quality decision explicitly authorizes otherwise.

---

## 1. Evidence that motivates an architecture change

### OBSERVED — current frozen endpoint

Phase 13.5 froze the unchanged T4/B0 configuration as `K3_BEST` after the bounded optimization loop:

```text
ResidentPool       {0}
ExpertPool         {0,1,2,3}
ordinary CUDA      8 layers
expert hot slots   0:549, 1:1125, 2:1125, 3:1125
storage            positional O_DIRECT
workers            4
queue depth        64
async cold fill    OFF
host cold tier     about 16 GiB
decode screen      about 0.294132 tok/s
```

Authoritative evidence is under:

```text
results/2026-08-10/issue73-k3-optimization/
  topology/
  storage/
  host-cache/
  vram-allocation/
  profiling/
```

and the chronological experimental decisions are preserved in issue #73.

### OBSERVED — four GPUs add much less than four-GPU arithmetic capacity would suggest

The accepted topology screen measured approximately:

```text
T1   Resident={0}, Expert={0}         0.1860 tok/s
T2B  Resident={0}, Expert={1}         0.2048
T2   Resident={0}, Expert={0,1}       0.2276
T3   Resident={0}, Expert={1,2,3}     0.2336
T4   Resident={0}, Expert={0,1,2,3}   0.2446
```

T4 improved about 31.5% over T1, not approximately 4x. At the same time T4 achieved a 26.60% hot-expert hit rate. This supports a working-set/data-movement interpretation: additional GPUs help, but much of routed-layer latency remains outside useful expert kernels.

This is **not** evidence that expert GPU compute is unimportant. The matched Phase 13.5 GPU-vs-current-CPU-fallback experiment showed that the existing GPU expert path is far faster than the project's current CPU fallback. It does show that once expert execution is already on GPU, adding more GPU arithmetic without changing the feeding/residency architecture gives sharply diminishing returns.

### OBSERVED — storage-path selection materially changes endpoint TPS

On frozen T4, positional `O_DIRECT` with four workers (`B0`) beat the buffered path by about 20.3% end to end. Reducing the worker count from four to two lost about 14.6% TPS. Conversely, native `io_uring` was genuinely active but performed substantially worse because the implemented queue behavior remained effectively serial and accumulated large request queue wait.

This establishes that the feed path matters materially at the endpoint level. It also argues against assuming storage API sophistication by itself is sufficient; the winning path was the simpler positional path that happened to expose less critical-path latency.

### OBSERVED — more host expert cache can harm the compulsory model working set

The host-cache campaign is especially important architecturally.

`H64` reduced logical expert-storage traffic by about 13.0% yet changed decode TPS by only about +0.21% and worsened TTFT.

`H96` and the empirical reserve-safe maximum reduced logical expert-storage traffic by about 17%, but throughput collapsed. `H96` accumulated about 15.8 million major faults and about 2.98 TB of guest block reads; the larger endpoint reached about 4.30 TB guest block reads. The accepted interpretation is ordinary mapped-weight refault amplification: expert-cache memory displaced file-backed pages belonging to the compulsory ordinary model working set.

The important architecture lesson is:

> A capacity-safe RAM allocation is not necessarily a performance-safe residency allocation.

Host memory should not be treated as one fungible pool whose only safety condition is `MemAvailable`. The runtime has at least two semantically different resident sets:

```text
compulsory working set
  ordinary model tensors needed repeatedly

opportunistic working set
  cached routed experts whose future reuse is uncertain
```

The opportunistic set must not be allowed to destroy residency of the compulsory set.

### OBSERVED — moving more ordinary work to GPU was performance-positive but not admissible under the strict Phase 13.5 identity gate

The owner-amended VRAM-allocation screen increased ordinary CUDA residency from 8 to 16 layers while removing GPU0 from the expert pool. The 16-layer cell increased decode TPS by about 3.81%, despite hot-expert hit rate dropping from 26.60% to 11.79% and expert I/O increasing about 20.2%.

However, all per-forward logit digests changed, while generated IDs and text remained identical. Under the Phase 13.5 contract that candidate was correctly rejected.

Architectural interpretation:

- `REJECTED` as a Phase 13.5 candidate because it violated that phase's exact-logit gate;
- **not** evidence that broader ordinary placement is performance-negative;
- `OPEN` whether a future architecture with an explicitly defined numerical-equivalence contract can benefit from multi-resident ordinary placement.

### OBSERVED — current CPU fallback is not a competitive heterogeneous backend

The bounded CPU-only expert diagnostic in Phase 13.5 achieved only about 0.022 tok/s. Therefore simply changing `PROMOTE_AND_GPU` to the current `CPU_FALLBACK` is not useful.

This result must be interpreted narrowly. It characterizes the project's current fallback implementation, not the ceiling of a K3-specific CPU expert backend. The accepted external Colibri evidence on OCI demonstrates that a specialized K3 CPU implementation can run the full model far faster than the current project fallback.

### OBSERVED — final profiling points at provider/feed latency, not GPU kernels

The valid bounded Phase 13.5 trace on frozen B0/T4 measured representative routed-layer intervals with roughly:

```text
provider wall / routed-layer wall         63.2%
storage-service union                     47.4%
H2D-scope union                           14.2%
dependency-or-host-gap critical bucket    30.6%
peer activation/result copy                0.14% class
```

The service unions overlap and must not be added as independent wall-time percentages.

The trace also showed that about 88.3% of traced H2D already overlapped storage. An eight-worker positional experiment reduced mean storage queue wait but did not reproduce an end-to-end improvement and was rejected. This closed the remaining bounded storage/QD/overlap interventions permitted by Phase 13.5.

Architectural implication:

> Further large gains are unlikely to come from another small QD/worker/io_uring adjustment. The next meaningful search space is how the runtime represents residency and schedules execution around where expert bytes already are.

---

## 2. Separate architecture from representation

### OPEN — maintain two orthogonal experiment axes

Future work should avoid mixing these two questions:

```text
A. Runtime architecture
   Where do bytes live?
   Where is each expert computed?
   What moves?
   What waits for what?

B. Model representation
   BF16 vs Q8 vs Q4 ordinary matrices
   storage/VRAM/RAM footprint
   numerical/quality trade-off
```

A smaller ordinary representation can have very large performance effects, especially because it frees RAM for expert residency and reduces memory bandwidth. That can be a valuable product optimization. But it is not evidence that the out-of-core runtime architecture improved.

Recommended evaluation matrix:

```text
                         ordinary representation
                    baseline       Q8          Q4
runtime baseline       A0          R1          R2
new architecture       A1          C1          C2
```

Interpretation:

- `A1 / A0` = architecture gain with representation held constant;
- `R1 / A0`, `R2 / A0` = representation gain on old architecture;
- `C1`, `C2` = combined product envelope.

The router should remain in its qualified representation/semantics unless a separate model-quality decision explicitly studies it. Do not use router quantization as an invisible implementation shortcut.

### OPEN — two correctness envelopes may eventually be useful

The current project has strong exactness requirements. Those remain valuable as a scientific reference.

A future product-performance investigation may need to distinguish:

```text
STRICT
  existing arithmetic/representation contract
  exact route identity
  strongest practical output/logit identity

FAST/ALTERNATIVE-REPRESENTATION
  exact router and top-k semantics
  exact expert identity and routing weights
  representation changes explicitly declared
  quality/logit-distance gates defined in advance
```

This is only a design observation. No relaxed correctness mode is accepted by this note.

---

## 3. Architecture direction A — cooperative expert residency

### OPEN

Treat the aggregate VRAM expert tier as one logical cooperative cache implemented by multiple physical devices.

Introduce or strengthen a global residency directory conceptually equivalent to:

```text
(layer, expert) ->
  STORAGE_ONLY
  HOST_RESIDENT
  GPU0_RESIDENT
  GPU1_RESIDENT
  GPU2_RESIDENT
  GPU3_RESIDENT
  LOADING(target, generation)
```

The directory must preserve existing lifetime/generation correctness and remain separate from the cache replacement policy.

### Sticky expert ownership

Once an expert is resident on a GPU, prefer to execute that expert on its owner GPU rather than moving its 17.55-MB bundle to another device.

For a routed expert already resident on GPU2:

```text
current generic temptation
  move/replicate weights toward current execution site

preferred ownership model
  send small activation to GPU2
  run expert on GPU2
  return small result
```

Phase 13.5 peer activation/result-copy attribution was tiny relative to provider/storage time, which makes this direction especially attractive on the measured four-GPU machine.

### Goals

A cooperative residency mechanism should attempt to:

- avoid accidental duplicate residency unless replication is explicitly chosen;
- reduce weight movement by moving activations/results instead;
- let all four expert VRAM pools contribute to one globally visible working set;
- make residency/ownership a mechanism independent of admission/eviction policy;
- expose exact telemetry for remote-hit, local-hit, replication, activation-copy, result-copy and avoided H2D bytes.

### Required control

Compare against the current T4 mechanism with the same model, routing and capacities. A successful result must improve end-to-end TPS/tails, not merely aggregate hit rate.

---

## 4. Architecture direction B — heterogeneous compute where the data is

### OPEN

Replace the current global choice between CPU fallback and GPU promotion with a per-expert execution decision.

Conceptually:

```text
selected expert
      |
      v
residency lookup
      |
      +-- GPU resident ------> execute on owner GPU
      |
      +-- host resident ------> choose CPU or H2D->GPU from measured cost
      |
      +-- storage only -------> load once, then choose execution target
```

The router still selects the same top-16 experts with the same weights. Only execution location changes.

### Why this differs from the Phase 13.5 CPU control

The existing CPU fallback is much too slow to justify heterogeneous scheduling. A future heterogeneous architecture therefore requires a K3-specific CPU expert implementation that can consume the authoritative MXFP4 expert representation efficiently.

The relevant comparison is not:

```text
current CPU_FALLBACK vs current GPU path
```

but:

```text
optimized CPU service time for a host-resident expert
vs
H2D service + GPU queue + GPU expert compute for that same expert
```

If CPU compute is slower than GPU compute but faster than moving a cold expert through the transfer path at that moment, CPU can still be the correct critical-path choice.

### Candidate decision inputs

The first implementation should use a simple deterministic cost model, for example:

```text
expert residency tier
measured H2D bandwidth/latency for the target GPU
GPU queue depth / outstanding expert work
CPU worker availability
measured CPU MXFP4 expert service time
peer activation/result-copy cost
```

Avoid ML in the execution selector until a simple cost model has been tested.

### Important opportunity — simultaneous CPU and GPU experts from one top-16 set

The most interesting version is not “this token runs on CPU” or “this token runs on GPU.” It is:

```text
expert  7  hot on GPU1  -> GPU1
expert 19  hot on GPU3  -> GPU3
expert 81  host-only    -> CPU worker
expert 44  storage miss -> pinned -> GPU2
...
```

The 16 expert contributions are independent until their required deterministic aggregation point. CPU and multiple GPUs can therefore contribute concurrently if the runtime exposes expert-level tasks.

---

## 5. Architecture direction C — asynchronous per-expert task graph

### OPEN

The provider should evolve from a phase that prepares expert availability before graph execution into a scheduler that releases each expert for computation as soon as its dependencies are satisfied.

Target conceptual flow:

```text
route top-16
    |
    +-- VRAM hit -------------> launch immediately
    +-- host/CPU candidate ---> CPU task immediately
    +-- host/GPU candidate ---> H2D; launch on completion
    +-- storage miss ---------> I/O; then CPU or GPU path
                                  |
                                  v
                         deterministic contribution slot
                                  |
                    barrier only when aggregation requires it
```

### Design requirement — deterministic aggregation

Asynchronous completion order must never change expert accumulation semantics accidentally. Each expert result needs a stable logical contribution slot keyed by the original selected-expert order, with aggregation performed according to the accepted numerical contract.

This preserves the existing architectural constraint against nondeterminism caused by asynchronous completion order.

### Why this may matter despite existing overlap

Phase 13.5 already overlaps much H2D with storage. The remaining opportunity is broader: overlap **expert computation itself** with unresolved misses for other experts, and overlap host-resident CPU experts with GPU/storage work.

That requires expert-level scheduling, not another storage-worker increment.

---

## 6. Architecture direction D — protect compulsory host residency

### OPEN

Introduce explicit accounting between compulsory ordinary-model residency and opportunistic expert-cache residency.

The H96/HMAX result demonstrates that the current system can satisfy a nominal memory-reserve threshold while catastrophically refaulting ordinary mapped weights.

A future host-memory manager should have explicit concepts equivalent to:

```text
HOST_COMPULSORY
  ordinary tensors required repeatedly
  protected residency budget / measured working-set floor

HOST_RUNTIME
  anonymous state
  pinned/registered transfer memory
  scheduler/runtime memory

HOST_EXPERT_CACHE
  reclaimable opportunistic expert residency
```

### Mechanism candidates

These are candidates, not accepted choices:

- selective `mlock`/prefault of the measured compulsory ordinary working set;
- explicit anonymous resident buffers for selected ordinary tensors;
- cgroup/madvise-based residency controls where semantics are sufficiently predictable;
- dynamic expert-cache ceiling derived from measured compulsory working-set residency rather than only host `MemAvailable`.

The mechanism should be chosen by evidence. Locking the entire model mapping is not a goal and may be unsafe.

### Performance safety rule

A host expert-cache `MAX_SAFE` should eventually satisfy two independent gates:

```text
capacity safe:
  no swap/OOM and declared absolute reserve

residency safe:
  no material ordinary-weight refault amplification
```

This is a stronger definition than the Phase 13.5 capacity-only boundary and follows directly from its negative evidence.

---

## 7. Architecture direction E — configurable multi-resident ordinary placement

### OPEN

The current D-020 v1 architecture intentionally uses one resident device and allows one or more expert devices. Phase 13.5 showed that this is a useful simplification, but it can become a limiting placement rule on a four-GPU machine.

Future placement should be representable independently for ordinary tensors and expert execution, for example:

```text
ordinary devices  = {0,1}
expert devices    = {1,2,3}

ordinary devices  = {0,1}
expert devices    = {2,3}

ordinary devices  = {0,1,2,3}
expert devices    = CPU + selected GPUs
```

This does **not** imply these examples are good configurations. It means the runtime should be able to express and test them without changing ownership semantics ad hoc.

### Prefer contiguous layer partitions initially

For the first multi-resident mechanism, contiguous layer ranges are attractive:

```text
GPU0  early layer block
GPU1  next layer block
GPU2  next layer block
GPU3  final layer block
```

This bounds ordinary hidden-state transfers to partition boundaries instead of moving the ordinary execution context every layer.

A more advanced per-tensor placement may later rank ordinary tensors by measured time-saved per VRAM byte, but it should not be the first implementation.

### V1 interpretation

The Phase 13.5 V1 result remains useful prior evidence:

- more ordinary GPU residency can increase TPS;
- taking VRAM away from expert caching can sharply reduce expert hot hits;
- the tradeoff should be measured explicitly;
- numerical equivalence requirements must be defined before such a future campaign.

---

## 8. Architecture direction F — memory value should be measured by avoided critical-path cost

### OPEN design principle

Do not optimize VRAM/RAM placement from hit rate alone.

A byte of VRAM used for an ordinary tensor and a byte used for an expert cache entry have different reuse frequencies and different avoided costs. Likewise, a host expert hit that still requires H2D is not equivalent to a VRAM expert hit.

Future telemetry should estimate the marginal value of residency:

```text
ordinary VRAM byte
  avoided ordinary CPU/memory service

expert VRAM byte
  avoided storage/host service + avoided H2D

expert host-RAM byte
  avoided storage service only when GPU execution remains required
  or avoided storage + H2D when CPU execution is selected
```

This provides a common currency for placement decisions: **avoided exposed nanoseconds per resident byte**, not raw hit rate.

---

## 9. Proposed architecture stack

A future K3 runtime could converge toward the following responsibility split while preserving the repository's existing required abstractions:

```text
Exact K3 Router
      |
      v
Selected top-16 experts
      |
      v
ExpertDirectory
  global tier + owner + generation identity
      |
      +-----------------------+
      |                       |
      v                       v
Residency mechanism     Execution cost model
      |                       |
      +-----------+-----------+
                  v
            ExpertScheduler
      per-expert dependency tasks
        /        |         \
       /         |          \
      v          v           v
 CPU MXFP4    GPU owner    storage/H2D
 executor     executor      transport
      \          |           /
       \         |          /
        +--------+---------+
                 v
       deterministic aggregation
```

Caches and policies remain separate:

```text
HotExpertCache     mechanism
ColdExpertCache    mechanism
CachePolicy        replacement/admission policy
MissExecutionPolicy execution-target policy
ExpertTransport    data movement
ExpertScheduler    dependency scheduling
```

Do not merge these responsibilities into one K3-specific monolith merely because the first prototype targets K3.

---

## 10. What not to prioritize first

### REJECTED for immediate architectural priority — another broad storage API/QD search

Phase 13.5 already compared buffered positional I/O, positional `O_DIRECT`, native `io_uring`, async cold fill, worker count and queue behavior. The final eight-worker intervention reduced queue wait but did not improve endpoint TPS.

Reopen storage scheduling only if a new architecture changes the demand pattern enough that prior evidence no longer applies.

### REJECTED for immediate architectural priority — treating more GPU FLOPS as the main answer

The topology screen showed sharply diminishing returns from simply adding expert GPUs while leaving the memory hierarchy intact.

Additional compute can become valuable after the feed/residency architecture changes. It is not the first missing resource on the measured machine.

### OPEN but separate — ordinary-model quantization

A smaller representation may be highly valuable and should be benchmarked, including to normalize external-runtime comparisons. It is intentionally not counted as an architecture win in this note.

### SPECULATIVE / separate — GDS or direct-storage-to-VRAM mechanisms

A mechanism such as GDS/cuFile could reduce host staging overhead on suitable hardware, but Phase 13.5 does not show host staging as the dominant missing gain. It should not precede the higher-level residency/execution experiments above.

---

## 11. Suggested experimental sequence for a future architecture phase

This is an `OPEN` design sequence, not an execution-ready plan.

### A. Preserve a strict baseline

Freeze one BF16-trunk/native-MXFP4 configuration and long-run endpoint envelope as the architecture-normalized baseline.

### B. Implement cooperative residency without changing execution arithmetic

Test:

```text
current per-device behavior
vs
global directory + sticky GPU ownership
```

Measure remote/local hot hits, duplicated expert bytes, peer activation/result traffic, H2D bytes and endpoint TPS.

### C. Build an optimized CPU MXFP4 microkernel/backend

Before heterogeneous execution, demonstrate a representative per-expert CPU service time that is materially better than the current fallback. This is a mechanism qualification, not yet an endpoint claim.

### D. Add deterministic heterogeneous per-expert scheduling

Compare:

```text
GPU_ONLY_PROMOTION
vs
HETEROGENEOUS_CPU_GPU
```

with identical routing and model representation.

### E. Add expert-level asynchronous release

Measure how much unresolved storage/H2D can overlap useful CPU/GPU expert work.

### F. Protect compulsory host residency

Repeat host expert-cache capacity points only after ordinary residency is explicitly protected/accounted. Verify both capacity safety and residency safety.

### G. Only then test multi-resident ordinary placement

Keep representation unchanged and test a small causal set of ordinary/expert device partitions.

### H. Separately test representation changes

After architecture results are known, run Q8/Q4 ordinary-representation comparisons as a separate axis. Do not fold those gains into the architecture claim.

---

## 12. Required metrics for future claims

Architecture experiments should retain at least:

```text
endpoint
  decode tok/s
  TTFT
  p50/p95/p99/max token latency

expert residency
  local VRAM hits
  remote-owner VRAM hits
  host hits
  storage misses
  duplicate residency bytes
  useful residency lifetime

execution
  CPU expert service time
  GPU expert service time
  experts executed per backend
  CPU/GPU overlap

movement
  storage submitted/useful bytes
  H2D bytes
  peer activation/result bytes
  exposed storage/H2D/peer wait

host memory
  compulsory ordinary residency
  expert-cache residency
  major faults/refault bytes
  pinned/runtime memory
  swap/OOM

GPU memory
  ordinary bytes per device
  expert bytes per device
  workspace/high-water/headroom

correctness
  exact route identity
  original expert IDs/weights
  deterministic aggregation
  declared numerical-equivalence envelope
```

Selection remains by end-to-end TPS/tails under the declared correctness contract.

---

## 13. External comparator interpretation

The accepted Colibri CPU campaign is valuable prior art but not a clean hardware comparison with Phase 13.5 because several dimensions differ simultaneously, including runtime architecture, ordinary-weight representation, cache organization and physical storage.

The most useful lesson is architectural:

- a specialized K3 CPU path can make host-resident expert bytes computationally useful without H2D;
- large RAM expert residency can be effective when the compulsory model working set leaves enough headroom;
- an implementation optimized around K3's exact expert representation can be radically faster than the project's generic CPU fallback.

A future same-host representation-normalized comparator should separate:

```text
runtime architecture difference
from
ordinary-model representation difference
```

before attributing the final TPS gap to CPU versus GPU hardware.

---

## 14. Current recommendation

### OPEN

If a future phase is created specifically to improve K3 TPS on the same four-GPU class of machine without changing model representation, prioritize:

```text
1. cooperative global expert residency + sticky GPU ownership
2. efficient CPU MXFP4 backend + per-expert heterogeneous CPU/GPU execution
3. asynchronous per-expert dependency scheduling
4. compulsory host-working-set protection
5. configurable multi-resident ordinary placement
```

These directions directly address the Phase 13.5 critical-path evidence and remain conceptually distinct from model quantization and from router/admission-policy research.

The central architectural objective is:

> Minimize movement of large immutable expert weights. Move small activations/results when possible, execute where expert bytes already reside, and let storage, RAM, CPU, and all GPUs contribute concurrently under one deterministic scheduler.
