# Trading Routing Slack for Memory Locality in Out-of-Core Kimi K3 Inference

> **Manuscript status:** outline v0  
> **Editorial workflow:** interactive drafting; GitHub issues are coordination/state only, not issue-driven development  
> **Coordination epic:** [#109](https://github.com/murillo128/k3-out-of-core/issues/109)  
> **Source of truth:** this file  
> **Primary evidence state:** systems/locality and long-horizon predictive-quality evidence frozen; task-level quality still pending #100/#101

Alternate working title:

> **Out-of-Core Kimi K3: Bounded Cache-Aware Routing for Memory-Constrained MoE Inference**

Do not freeze the title until task-level evaluation and venue positioning are settled.

---

## Editorial contract

### Working thesis

Sparse-MoE routing contains enough near-tie slack that bounded, cache-aware expert substitutions can reduce out-of-core expert demand and improve physical locality/throughput at fixed memory capacity, but the gain comes with measurable predictive perturbation and autoregressive feedback.

### Narrative flow

```text
frontier MoE capacity does not fit
        ↓
sparse activation makes out-of-core execution plausible
        ↓
exact routing creates large backing-store demand
        ↓
physical expert loads dominate decode throughput in the measured regime
        ↓
more exact cache helps, but spends the scarce resource
        ↓
K3 routing exposes bounded near-tie slack
        ↓
explicit out-of-core residency + bounded cache-aware routing
        ↓
fewer physically serviced expert bundles at the same measured cache capacity
        ↓
broad physical gain across the frozen workload corpus
        ↓
counterfactual locality equivalent to a larger exact cache
        ↓
changed membership produces measurable predictive damage
        ↓
autoregressive token feedback amplifies part of that damage
        ↓
simple local/cumulative regret statistics do not fully predict long-horizon quality
        ↓
performance, memory capacity, and quality must be evaluated jointly
```

The paper must **not** read as a chronological project report or as “we made Kimi K3 load from NVMe.”

### Structure/flow references

Use the closest prior art as a stylistic model:

- **FreeToken** — problem/challenges → design → implementation → evaluation → related work.
- **Cache-Conditional Experts** — characterize the opportunity/tolerance before introducing cache-aware routing.
- **MoE-Infinity** — make trace/locality characterization part of the scientific contribution.
- **ReMoE** — establish a measurable locality gap before presenting the routing mechanism.

The target language is compact systems/ML prose: make one claim per paragraph, connect each mechanism to a previously established problem, quantify important claims, and state boundaries directly rather than defensively.

### Claim classes

Every non-trivial manuscript claim should be classifiable as one of:

```text
FROZEN_MEASURED
FROZEN_DERIVED
POST_HOC_EXPLORATORY
PENDING_TASK_LEVEL
RELATED_WORK
LIMITATION
```

When a #105-derived table/figure is used, also preserve its underlying evidence class:

```text
MEASURED_PHYSICAL
MEASURED_OBSERVER
CURATED_FROM_MEASURED
EXACT_REPLAY
FIXED_ROUTE_COUNTERFACTUAL
EXACT_REPLAY_COUNTERFACTUAL
TPS_PROJECTION
SEMANTIC_SANITY
POST_HOC_EXPLORATORY
```

### Evidence authority map

| Authority | Role in paper | Important frozen facts / boundary |
|---|---|---|
| #77 | Short-horizon routing perturbation / quality instrumentation and early frontier | Use for mechanism/method context. Do not replace later #99 long-horizon authority. |
| #98 | Protocol-distinct first-full physical policy selection | S2_P50 selected physically before later cross-prompt/quality analyses. Do **not** pool #98 absolute TPS/locality with #102. |
| #102 | Primary cross-workload systems/generalization authority | 128 prompts = 16 semantic families × 8 length levels; Stage C contains 24 unique prompts; S2_P50 better than EXACT and KNEE in 24/24 measured Stage-C prompts; semantic-family effect strong; token-length effect weak; route coverage broad. |
| #105 | Canonical curated data/figure layer; locality→TPS, virtual-cache, working-set/core-periphery analysis | Final reviewed target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468`; analysis `76e0c3d578c4dba56e91d15ad643d8740037788a`; release `issue105-curated-analysis-v3`; SHA-256 `e0fe96c2f4dd3d2cfc8ced16901949936ba3e72c79ebdd4eb412f371fe843fb3`. Newly introduced scientific analyses are `POST_HOC_EXPLORATORY`. |
| #99 | Primary long-horizon predictive-quality and route-feedback authority | Final evidence target `eeaab5fa3f62047e8617ab3ed408ccbddbb56872`; merged by `610cfb3eb1870c89016ba5ce25b875cd4e8ae14c`; release `issue99-long-horizon-quality-v1`. |
| #100 | GPQA task-level validation | **Pending.** Current design explicitly states that no GPQA task-quality result has yet been generated/inspected. |
| #101 | Independent MMLU-family / GSM8K-family task validation | **Pending / investigation-required.** Use only if/when accepted results exist. |
| #81/#84/#85/#86 | Optional future cross-model generality | Not required for a K3-specific paper. Do not imply cross-model support before results exist. |

### Frozen headline numbers available now

#### K3 / policy constants

```text
routed layers                 92
routed experts / layer       896
selected experts / layer      16
selected bundles / token    1472
expert bundle bytes    17,547,264
selected payload/token 25,829,572,608 bytes (~25.83 GB)

S2_P50
  candidate_count             32
  max_swaps                    2
  max_score_regret             0.007303759455680847
```

`selected payload/token` is cumulative selected expert payload before reuse/cache hits. It is **not** a resident-memory requirement and must never be labeled as such.

#### #98 policy-selection context

```text
S2_P50 screening median TPS       0.505408936414
vs KNEE screening median TPS      +7.708%
loads/bytes per token vs KNEE     -15.866%
paired confirmation gains         ~+6.832%, +6.857%, +7.110%
```

These are #98 protocol-specific context only.

#### #102 systems/generalization

```text
primary prompts                    128
semantic families                   16
within-family length levels          8
observer captures                   44
Stage-C unique prompts              24
Stage-C failed cells                 0
S2_P50 vs EXACT/KNEE wins          24/24

S2P50_CROSS_PROMPT_DISPERSION      high
SEMANTIC_FAMILY_EFFECT             strong
TOKEN_LENGTH_EFFECT                weak
ROUTE_COVERAGE                     broad
S2P50_PAIRED_GAIN                  broad
KNEE_VS_S2P50_PROMPT_INTERACTION   moderate
```

#### #105 secondary analysis

```text
TPS_PROJECTION_GATE                  PASS
loads/token primary LOFO R²          0.993536
best single working-set LOFO R²      0.465884
committee-pin regressing cells       308
committee-pin infeasible cells       196
```

The LOFO/working-set/core-periphery/capacity analyses newly introduced by #105 are `POST_HOC_EXPLORATORY`, even when based on measured inputs.

#### #99 long-horizon quality

```text
LONG_HORIZON_PREDICTIVE_DRIFT       gradual
KNEE_VS_S2_QUALITY_ORDERING         no_clear_difference
CUMULATIVE_REGRET_PREDICTIVE        weak
RAW_REGRET_ADDS_SIGNAL              weak
PERTURBED_FRACTION_ADDS_SIGNAL      no
TOKEN_MEDIATED_ROUTE_FEEDBACK       material
FEEDBACK_GROWTH_TO_1024             heterogeneous
FOLLOWUP_ROUTING_DESIGN_JUSTIFIED   no

S2_P50 vs EXACT mean ΔNLL           +0.012030
95% cluster-bootstrap               0.008440 .. 0.015435
KNEE vs S2_P50 quality delta        +0.001090
95% interval                        -0.001228 .. 0.003205
feedback amplification mean         1.4210x
95% interval                        1.2225 .. 1.7099
```

The `1.4210x` value is a controlled token-mediated feedback amplification factor for the measured effect, **not** “1.42x worse quality.”

### Hard claim guardrails

Do not write any of the following unless later evidence changes the boundary:

- “cache-aware routing is quality-neutral”;
- “no accuracy loss” before accepted task-level evidence;
- “general MoE method” or “works across MoEs” from K3-only primary evidence;
- “first cache-aware MoE routing”;
- “virtual cache saving = measured RAM saving”;
- “observer/replay TPS = measured physical TPS”;
- “physical − replay = route feedback”;
- “Moonshot GPQA 93.5% = our local EXACT baseline”;
- “#105 post-hoc analysis was preregistered/confirmatory”;
- “full K3 is practical on ordinary consumer hardware” unless absolute throughput and use case justify that statement.

Prefer precise scope wording:

- “on Kimi K3”;
- “within the measured #102 domain”;
- “across the frozen 128-prompt corpus”;
- “post-hoc exploratory analysis”;
- “counterfactual exact-cache equivalence”;
- “bounded router-score substitution”;
- “measurable predictive damage”;
- “no clear difference” rather than “equivalent” when the interval crosses zero.

---

# Abstract

**Coordination:** [#110](https://github.com/murillo128/k3-out-of-core/issues/110)  
**Status:** `EVIDENCE_CHECK` / task-level claims deliberately excluded pending #100/#101

Mixture-of-Experts (MoE) models reduce active computation by routing each token to a small subset of experts, but their full expert pool can remain too large for memory-constrained inference. Moving experts to NVMe allows the expert pool to exceed memory capacity, but turns routing decisions into physical storage demand. We study this problem on full Kimi K3 and combine an explicit out-of-core runtime with deterministic, training-free cache-aware substitutions drawn from a bounded candidate set, with at most two swaps and a hard per-swap router-score regret limit. Across a frozen 128-prompt corpus, the selected S2_P50 policy achieves higher measured decode TPS than both exact routing and the frozen KNEE policy in all 24 Stage-C prompts. Within the same measured domain, a post-hoc leave-one-family-out analysis finds backing expert loads per token to be a strong predictor of physical decode throughput (R² = 0.993536), linking routing locality to systems performance. The gain is not quality-neutral: under controlled fixed-context evaluation, S2_P50 increases reference-token NLL by 0.012030 on average, while free-generation experiments show a mean 1.4210× amplification of the measured perturbation through token-mediated autoregressive feedback. These results establish a K3-specific performance–memory-locality–quality trade-off; task-level accuracy preservation and cross-model generality remain outside the current evidence.

---

# 1. Introduction

**Coordination:** [#111](https://github.com/murillo128/k3-out-of-core/issues/111)  
**Status:** `EVIDENCE_CHECK`

Mixture-of-Experts (MoE) models decouple total parameter capacity from the amount of expert computation used for one token: a router activates only a small subset of experts at each routed layer. That sparsity, however, does not remove the need to make the complete expert pool serviceable. On a memory-constrained machine, placing experts outside fast memory solves the capacity problem only by creating a second one: every selected expert that is not resident must be serviced from a slower tier.

Kimi K3 makes this tension concrete. Its 92 routed layers each expose 896 routed experts and select the top 16. In the accepted MXFP4 runtime, one `(layer, expert)` bundle occupies 17,547,264 bytes. A generated token therefore produces 1,472 routed expert selections, corresponding to 25,829,572,608 bytes (about 25.83 GB) of selected expert payload before reuse or cache hits. This quantity is neither a resident-memory requirement nor measured storage traffic; it is the cumulative service-demand scale presented by exact routing. Moving the inactive pool to NVMe makes out-of-core execution possible, but a nonresident exact selection still becomes physical backing work.

Existing MoE offloading systems reduce the cost of serving exact selections through caching, prefetching, and heterogeneous execution. MoE-Infinity makes activation traces and cache behavior part of the serving design, while WASTE and Colibrì stream router-selected K3 experts from secondary storage and FreeToken optimizes exact expert service across a RAM-resident pool, VRAM caching, and heterogeneous CPU/GPU execution. These approaches are complementary to our setting: they improve where, when, or how exact router-selected experts are materialized or executed. Our measurements on K3 show why the demand itself is also important. The frozen #102 corpus spans 128 prompts across 16 semantic families and eight within-family length levels and exhibits broad, workload-conditioned routing and locality. Using those measured rows, the final #105 analysis found, **post hoc**, that backing expert loads per token predict physical decode throughput with leave-one-family-out R² = 0.993536 within the measured domain. We therefore treat physical expert demand, rather than cache hit rate alone, as a first-class systems quantity.

The router offers a second control surface. K3 selects 16 experts from a much larger ranked set, and the frozen policy-selection evidence identifies alternatives close enough in the corrected K3 selection score to satisfy explicit hard regret thresholds. Cache-aware and residency-aware expert selection is not new: Cache-Conditional Experts and MoE-ERAS already make residency part of expert selection, while ReMoE fine-tunes the router to increase temporal reuse. We study a narrower K3-specific mechanism with explicit operational bounds. The frozen S2_P50 policy is deterministic and training-free: it considers only the top 32 candidates, changes at most two selected experts per routed layer, accepts a substitution only when corrected K3 router-score regret is at most 0.007303759455680847, and preserves the model's original expert-weighting semantics after membership is chosen.

We integrate this bounded substitution rule with an explicit full-K3 out-of-core runtime that owns expert residency and exposes physical hits, misses, backing loads, and bytes. Exact routing remains the reference path. In the primary full-prompt protocol, K3 consumes the prompt under exact routing, preserves the resulting cache state, and enables the changed policy only for decode. A resident near-tie candidate can then replace a nonresident exact selection only when every hard bound passes. The mechanism spends bounded routing slack rather than additional measured cache capacity; it does not assume that a local router-score bound is a semantic-safety guarantee.

The systems gain is broad in the frozen K3 workload, but it is not free. In all 24 frozen Stage-C prompts, S2_P50 achieves higher measured physical decode TPS and locality than both EXACT and KNEE at the same measured cache capacity, although the gain magnitude remains prompt-conditioned. Under the controlled long-horizon fixed-token evaluation in #99, S2_P50 increases reference-token NLL relative to EXACT by 0.012030 on average (95% cluster-bootstrap interval 0.008440–0.015435). When changed generations feed their own tokens back into the model, the measured perturbation is amplified by 1.4210× on average (95% interval 1.2225–1.7099), with heterogeneous growth through 1,024 tokens. Simple cumulative-regret summaries are only weakly predictive of this long-horizon damage. These results support a K3-specific performance–memory-locality–quality trade-off; they do not establish task-level accuracy preservation or cross-model generality, both of which remain outside the current evidence.

This paper makes four contributions:

1. **An explicit out-of-core full-K3 runtime and measurement substrate.** We make expert residency and physical backing service observable for full Kimi K3, providing a controlled basis for relating routing demand to physical execution rather than relying on implicit cache behavior.
2. **A cross-workload characterization of K3 expert locality.** Across the frozen 128-prompt corpus, we measure workload-conditioned routing/locality and physical performance; using these frozen measurements, a post-hoc leave-one-family-out analysis connects backing expert loads per token to decode TPS within the measured domain.
3. **Deterministic bounded cache-aware routing on K3.** We use contemporaneous residency to substitute at most two experts from a top-32 candidate set under a hard corrected router-score regret bound, without training or changing the model's expert-weighting semantics.
4. **A long-horizon quality and feedback characterization.** Controlled fixed-context and free-generation experiments measure predictive drift, token-mediated autoregressive amplification, and the limited predictive value of simple accumulated-regret summaries.

---

# 2. Background and Motivation

**Coordination:** [#112](https://github.com/murillo128/k3-out-of-core/issues/112)  
**Status:** `EVIDENCE_CHECK`

## 2.1 Sparse MoE inference and Kimi K3

A routed Mixture-of-Experts layer contains many expert feed-forward networks but evaluates only a small router-selected subset for each token. This sparsity reduces the amount of expert computation performed by one token; it does not remove the requirement that the serving system be able to supply any expert the router may select. Kimi K3 makes this distinction concrete: 92 layers contain routed MoE blocks, each with 896 routed experts, while exact inference selects the top 16 experts at each routed layer. One generated token therefore produces 1,472 layer-expert selections across the routed stack.

For storage and cache management, we treat one `(layer, expert_id)` as a logical `ExpertKey` and the routed gate/up/down weights plus their required quantization metadata as one `ExpertBundle`. In the accepted K3 runtime, an ExpertBundle occupies 17,547,264 bytes. `ExpertBundle` is a systems service unit used by this paper; it does not change the model's MoE computation.

Expert membership and expert weighting are separate semantics. Exact K3 first chooses the top-16 membership, then applies the model's original expert weights and normalization to that selected set. The mechanism introduced later may change a bounded number of selected IDs, but it leaves that weighting rule unchanged. This distinction lets us separate a systems change in which bundles are requested from the model semantics used to combine the selected experts.

## 2.2 The memory mismatch

K3 activates only `16/896 ≈ 1.8%` of the routed experts in any one layer, which makes sparse execution attractive on hardware that cannot keep the complete expert pool in fast memory. The active path is nevertheless large as a memory-service workload. Across 92 routed layers, one token selects 1,472 ExpertBundles; summing their bundle sizes gives 25,829,572,608 bytes (about 25.83 GB) of selected expert payload before any reuse or cache hits are considered.

| Quantity | Kimi K3 value | Meaning |
|---|---:|---|
| Routed experts/layer | 896 | Total routed choice set in each routed layer |
| Selected experts/layer | 16 | Exact active membership for one token |
| Routed layers | 92 | Routed decisions traversed by one token |
| Expert bundle | 17,547,264 B | Storage/cache service unit for one layer-expert pair |
| Selected bundles/token | 1,472 | Cumulative top-16 selections across the routed stack |
| Selected payload/token | 25,829,572,608 B | Sum of selected bundle payload before reuse/cache hits |

The last row is a **service-demand scale**, not a resident-memory requirement and not measured backing-store traffic. A resident bundle can satisfy a selection without a backing load, and repeated use can amortize its storage cost across many tokens. The memory mismatch is therefore not simply “model size versus RAM”: sparse activation makes out-of-core execution possible, while temporal reuse and cache locality determine how much of the selected demand must actually cross a slower memory boundary.

### Figure 1 candidate — memory mismatch

A compact explanatory figure can show the same distinction without assigning an unsourced total checkpoint size:

```text
per routed layer:   896 available experts -> exact top-16 membership
across 92 layers:   1,472 selected ExpertBundles / token
payload scale:      25.83 GB / token before reuse or cache hits
```

The figure must label the 25.83 GB quantity as cumulative selected payload, not simultaneously resident memory or physical bytes read.

## 2.3 Why exact expert offloading does not remove expert service

Offloading decouples model capacity from fast-memory capacity. A serving system can keep only a bounded expert working set resident and place the rest in host memory or secondary storage. Once the router has made an exact selection, however, each selected ExpertKey still follows a service path:

```text
exact router selection
   -> residency lookup
      -> hit: execute resident expert
      -> miss: service selected expert from a lower tier -> execute
```

Prior systems reduce the cost of this exact-selection path in complementary ways: MoE-Infinity uses activation-aware caching and prefetching; WASTE and Colibrì stream router-selected K3 experts from secondary storage; and FreeToken serves exact selections from a host-RAM expert pool through VRAM caching and heterogeneous miss execution. These techniques improve where, when, or how exact router-selected experts are materialized or executed; they do not, by themselves, reduce the set of experts requested by exact routing.

The remaining cost is the demand presented to that service path. If an exactly selected ExpertBundle is not resident, it must still be fetched or executed from a lower tier. A larger exact cache can reduce such misses, but only by spending more of the scarce RAM or VRAM that motivated offloading; faster backing I/O and prefetch can reduce or hide miss latency, but do not make the selected demand disappear. OS page cache may assist some implementations, but this paper uses explicit managed residency because implicit page-cache state is not a sufficiently controlled cache policy for reproducible physical measurements; we do not assume that other offloading systems rely on page cache.

Thus exact offloading solves a capacity problem without, by itself, eliminating the expert-service problem. Exact-routing execution techniques remain complementary; the remaining question is whether locality can also be improved by reducing the demand presented to the service path rather than only by serving exact misses faster or allocating more cache. Section 3 first characterizes K3's exact demand and routing slack before the routing mechanism is introduced.

---

# 3. Characterizing Out-of-Core K3

**Coordination:** [#113](https://github.com/murillo128/k3-out-of-core/issues/113)  
**Status:** `EVIDENCE_CHECK`

Out-of-core K3 is not a uniform stream of expert reads. The number of experts activated per layer is fixed, but which ExpertKeys are requested, how often they are reused, and how many of those requests fall outside the resident set depend on the workload and cache state. We therefore characterize the problem in four steps: how expert demand varies across workloads, whether physical backing service explains decode performance, how far exact caching can address that demand by itself, and whether the router exposes a bounded amount of local flexibility that could be used without simply allocating more memory.

## 3.1 K3 expert demand is workload-dependent

The frozen #102 corpus contains 128 deliberately diverse prompts arranged as 16 semantic families × 8 within-family prompt-length levels. It is a controlled workload grid, not a random sample of all K3 use. Across that corpus, #102 classifies cross-prompt dispersion as high, the semantic-family effect as strong, and the token-length effect as weak. Repeated sentinels keep deterministic route/locality state fixed while exposing host-level timing noise, so the observed prompt dependence cannot be reduced to process drift alone.

Separate observer captures provide route-level evidence without treating observer timing as performance data. Across 44 frozen captures, #102 finds broad route coverage, and the curated #105 route features show materially different working-set and overlap structure across prompts and families. These observations do not assign semantic functions to individual experts, nor do they imply that one family deterministically selects one expert set. They establish the narrower systems fact we need: the resident working set useful for one workload cannot be assumed to be the useful working set for another.

This distinction matters for static caching. A recurring core may exist, but broad, workload-conditioned peripheral demand remains. The later #105 committee-pin counterfactual makes the limitation concrete—many static-pinning cells regress or are infeasible—but that result is post-hoc and counterfactual rather than physical production evidence. For the present characterization, the measured and observer evidence is sufficient to reject a workload-independent hot-set assumption.

## 3.2 Physical backing service is the measured bottleneck

The primary #102 campaign makes backing service explicit. Physical rows were collected with a fixed 7,849-slot managed cache (137,728,475,136 bytes), fresh processes, a cold managed-cache start, CPU-only execution, native `io_uring` + `O_DIRECT`, and a single local-NVMe expert backing device. Stage C adds physical EXACT and KNEE runs for 24 selected prompts at that same capacity, alongside the already-frozen S2_P50 rows. The resulting counters distinguish logical expert selections from the ExpertBundles that actually have to be loaded from backing storage.

Using those measured physical rows, #105 asks a narrower post-hoc question: how well does backing demand predict decode throughput when an entire semantic family is held out? Backing expert loads per token achieves leave-one-family-out R² = 0.993536. By comparison, the best single working-set feature reaches LOFO R² = 0.465884. The physical rows are `MEASURED_PHYSICAL`; the regression and feature comparison are `POST_HOC_EXPLORATORY`. We therefore use the result as an in-domain characterization, not as a hardware-independent law or a causal coefficient.

Within that measured domain, the implication is nevertheless strong: route structure matters because it shapes reuse, but decode performance tracks the number of ExpertBundles the system actually services from backing storage much more closely than a compact working-set descriptor. Arithmetic sparsity fixes how many experts are executed; it does not fix how many expert bundles must cross the slow memory boundary. For out-of-core K3, physical backing demand is therefore the systems quantity that the design must control.

## 3.3 A larger exact cache buys locality with the scarce resource

The obvious response to backing demand is to keep more exact-route experts resident. The physical #102 campaign already operates at a fixed high-cache point—7,849 ExpertBundle slots, about 128.27 GiB—so increasing the exact cache would consume more of the memory resource that motivated out-of-core execution in the first place. This makes “add cache” a valid baseline, but not a free solution.

#105 evaluates that baseline offline using the frozen exact-route capacity curve. These rows are `EXACT_REPLAY` or exact-replay counterfactuals, and the virtual-cache analysis built from them is additionally `POST_HOC_EXPLORATORY`. They answer a counterfactual question: given the frozen route, what exact-cache capacity would be required to reach a target locality? They do **not** measure physical TPS at those hypothetical capacities, and an exact-cache equivalence must not be relabeled as measured RAM savings.

The replay nevertheless establishes the right comparison. Exact routing can trade more memory for fewer backing loads. The question for an out-of-core design is whether comparable locality can instead be obtained at the same measured physical cache capacity by changing a very small number of routing decisions. That distinction—larger exact cache versus bounded demand reduction at fixed capacity—sets up the mechanism evaluated later.

## 3.4 The K3 router exposes bounded local slack

The final ingredient is whether exact top-16 membership is separated sharply from every alternative. #77 captured the exact top-32 K3 candidate ordering and swept observed boundary-score gaps offline before changed routing was used as a systems mechanism. The resulting replay frontier was positive: nearby candidates existed often enough that small, explicitly bounded membership changes could reduce replayed backing demand. This is `FIXED_ROUTE_COUNTERFACTUAL` evidence, not a physical speedup and not a semantic-quality result.

A conservative point illustrates the opportunity without assuming the later policy outcome. At a 96-GiB replay anchor, allowing at most one substitution at the observed p10 score-gap threshold (`0.00129789114`) changed 1.486% of selected slots while reducing replayed expert loads by 4.162%; the p25 threshold (`0.00308857858`) changed 2.747% of slots and reduced replayed loads by 6.575%. The same sweep identified the p50 boundary `0.00730375946`, later used as the hard score-regret scale for the frozen S2_P50 policy. These quantities describe router selection-score slack and replayed locality only. They do not imply semantic equivalence, and the physical effect must be measured on the real generated route stream.

Together, the characterization yields four design requirements. The system should (1) reduce physical backing demand without simply increasing cache capacity; (2) preserve ordinary exact routing as the reference and default path; (3) make every intentional membership change deterministic and bounded in both candidate rank, swap count, and router selection-score regret; and (4) measure the semantic consequences of those changes rather than inferring safety from a small local score gap. The measurements therefore point to two coupled controls: explicit expert residency, so physical demand is observable and reproducible, and a bounded routing rule that can prefer an already-resident near-tie candidate only under hard approximation limits.

---

# 4. K3 Out-of-Core Design

**Coordination:** [#114](https://github.com/murillo128/k3-out-of-core/issues/114)  
**Status:** `OUTLINE`

The design section should answer challenges introduced in §3, not document classes chronologically.

## 4.1 Design goals

- bounded memory;
- explicit/reproducible residency;
- exact baseline correctness;
- asynchronous/bounded backing service;
- sufficient telemetry to connect demand to physical performance.

## 4.2 ExpertWeightProvider and whole-expert residency

Conceptual interface:

```text
inference graph
      -> ExpertWeightProvider
            -> executable ExpertBundle
```

Logical identity:

```text
ExpertKey(layer, expert_id)
```

Storage/cache unit:

```text
ExpertBundle = routed gate/up/down tensors + required quantization metadata
```

Important lifetime rule:

> Persistent cache metadata must own persistent cache buffers; graph-temporary allocations are never treated as durable residency.

## 4.3 Explicit storage hierarchy

General design:

```text
model/backing store (NVMe)
        -> bounded host cache / staging
        -> optional accelerator-visible hot tier
        -> expert execution
```

Be precise in evaluation text: the main #102 campaign is a CPU-only full-K3 local-NVMe/cache regime. Do not imply every tier shown in the general architecture was exercised in every headline run.

## 4.4 Demand scheduling and asynchronous I/O

Explain only the scientifically relevant properties:

- explicit backing offsets;
- bounded in-flight reads;
- native async I/O on qualified Linux campaign;
- `O_DIRECT` where used;
- demand requests take priority;
- exact same-layer issue-ahead is distinct from predictive prefetch;
- failures/fallbacks/resource pressure are observable.

## 4.5 Cache management and physical truth

- whole-expert fixed-capacity residency;
- deterministic hit/miss/eviction semantics;
- backing loads and bytes are primary physical observables;
- logical hit rate alone is not sufficient when memory pressure changes service behavior;
- fresh process / cold managed cache for primary physical measurements.

## 4.6 Routing interaction

The residency directory is visible to the bounded routing mechanism, while storage, execution, and routing policy remain conceptually separate.

### Figure 3 candidate — architecture

```text
                        residency directory
                              |
                              v
Router -> bounded membership decision -> ExpertWeightProvider
                                           |          |
                                           | hit      | miss
                                           v          v
                                        execute   backing read
                                                     |
                                                     v
                                                   cache
                                                     |
                                                     v
                                                   execute
```

If GPU/UMA tiers are shown, mark them as architectural variants rather than implying they are part of every reported measurement.

## 4.7 Prior-art boundary

Acknowledge that provider/cache hierarchy concepts have strong prior art in llama.cpp/vLLM, MoE-Infinity, WASTE, Colibrì, FreeToken, etc.

Do not sell the storage hierarchy alone as the novel contribution.

---

# 5. Bounded Cache-Aware Expert Routing

**Coordination:** [#115](https://github.com/murillo128/k3-out-of-core/issues/115)  
**Status:** `OUTLINE`

This section is the main algorithmic contribution.

## 5.1 Exact K3 selection

Define notation around the **actual accepted K3 corrected selection score**, not an oversimplified generic MoE score if semantics differ.

Let:

- `k = 16` be the exact selected membership;
- `M = 32` be the bounded candidate set used by S2_P50;
- `s(e)` be the exact K3 selection score used for ordering/eligibility;
- `w(e)` be the original unbiased expert weighting semantic applied after membership selection.

Make clear that membership and weighting are distinct.

## 5.2 Residency-aware bounded substitution

Conceptual flow:

```text
compute exact top-16
        ↓
inspect top-32 candidate set
        ↓
consult contemporaneous residency
        ↓
consider resident near-tie substitutions
        ↓
apply deterministic substitutions only if all hard bounds pass
```

## 5.3 Hard regret and swap budget

For an exact selected expert `e_s` and candidate `e_c`, define the local selection regret using the accepted K3 score:

\[
r(e_s, e_c) = s(e_s) - s(e_c).
\]

Require:

\[
0 \le r(e_s,e_c) \le \epsilon,
\]

and:

\[
N_{\mathrm{swaps}} \le S_{\max}.
\]

Frozen S2_P50 parameters:

```text
M = 32
S_max = 2
epsilon = 0.007303759455680847
```

Do not call `r` semantic regret. It is router selection-score regret.

## 5.4 Preserve K3 weighting semantics

Key method sentence:

> The mechanism changes bounded expert membership but preserves the model's original unbiased expert weighting semantics for the experts that remain selected.

This is an important distinction from applying a cache prior directly to final expert weights.

## 5.5 Policy selection and freeze

Explain briefly:

- #77 established the frontier/instrumentation;
- #98 physically selected S2_P50 using frozen criteria;
- later #102 generalization and #99 long-horizon quality did not retune S2 from their outcomes.

Relevant #98 context can be cited, but its absolute TPS must remain protocol-distinct.

## 5.6 A local bound is not a semantic guarantee

Close the method section with the limitation that motivates §8:

> Bounding each intentional selection change constrains the local router decision, but does not guarantee bounded hidden-state, logit, or long-horizon task divergence after autoregressive feedback.

### Figure 4 candidate — routing mechanism

Visualize exact top-16, candidate ranks 17–32, resident status, one/two accepted swaps, hard regret bound, and unchanged weighting stage.

## 5.7 Prior-art boundary

Must cite:

- Cache-Conditional Experts;
- MoE-ERAS;
- ReMoE.

Safe differentiation:

- full K3 896→16;
- deterministic hard per-swap regret + hard swap budget;
- training-free;
- original weighting preserved;
- real downstream route evolution;
- physical NVMe-backed evaluation;
- long-horizon direct-vs-free quality attribution.

Never claim “first cache-aware MoE routing.”

---

# 6. Implementation

**Coordination:** [#116](https://github.com/murillo128/k3-out-of-core/issues/116)  
**Status:** `OUTLINE`

Keep this section short. Design explains *why*; Implementation records enough detail to reproduce and interpret the evaluation.

## 6.1 llama.cpp / GGML integration

Cover conceptually:

- accepted Kimi K3 execution path;
- provider seam around routed expert materialization;
- persistent cache ownership independent of graph-temporary allocation;
- exact routing default;
- routing/storage separability.

Do not include commit history or file-by-file detail.

## 6.2 GGUF / MXFP4 expert storage

- retain accepted K3 MXFP4 routed experts;
- explicit expert backing spans/offsets;
- no quality-changing routed-expert requantization introduced by this mechanism;
- mention repacking/layout only where it changes measured I/O behavior.

## 6.3 Linux backing path used by primary physical evidence

Primary #102 regime to describe exactly:

```text
full K3
CPU-only Mode-P/BATCHED
32 inference threads
native io_uring + O_DIRECT
single local NVMe expert backing
fixed 7,849-slot cache
fresh process / cold managed cache
```

Verify final host CPU/memory/NVMe details from the immutable #102 artifacts before prose freeze.

## 6.4 Instrumentation and reproducibility

Main-paper level:

- hit/miss/load/byte telemetry;
- routing change/swap/regret telemetry;
- resource pressure/fallback truth;
- observer captures separated from timed physical runs;
- immutable evidence releases;
- offline deterministic regeneration for curated analysis.

Move exact commands, full schemas, hashes, and resume logic to appendix/artifact documentation.

---

# 7. Evaluation: Systems, Locality, and Capacity

**Coordination:** [#117](https://github.com/murillo128/k3-out-of-core/issues/117)  
**Status:** `OUTLINE`

Open with research questions rather than a results dump.

## 7.1 Experimental setup

### Model

```text
Kimi K3
92 routed layers
896 routed experts/layer
top-16 exact selection
expert bundle 17,547,264 bytes
```

### Primary #102 physical regime

```text
cache slots           7,849
cache bytes           137,728,475,136
backend               CPU-only Mode-P/BATCHED
threads               32
I/O                    native io_uring + O_DIRECT
backing               single local NVMe
process discipline    fresh process, cold managed cache
```

Before final text, copy exact host/device/model/software identities from immutable evidence rather than issue prose.

### Policies

```text
EXACT
KNEE
S2_P50
```

Explain the role of KNEE as a historical/frozen comparison, not a candidate being retuned during #102/#99.

### Workload

- 128 S2_P50 primary prompts, 16 families × 8 length levels;
- 8 deterministic sentinels;
- 16 family representatives / observer analysis;
- 24 unique Stage-C prompts with physical EXACT/KNEE comparison and frozen S2 rows.

## 7.2 Q1 — Can full K3 be served out of core in this regime?

Establish feasibility and absolute envelope without overselling usability.

Candidate evidence:

- successful full-K3 physical campaigns;
- stable native direct-I/O path;
- resource/fallback correctness;
- observed absolute TPS distribution.

Do not turn feasibility into “practical consumer inference” unless absolute performance supports the use case.

## 7.3 Q2 — Does S2_P50 improve physical performance?

Primary #102 Stage-C result:

> S2_P50 improves the measured systems result versus both EXACT and KNEE in 24/24 selected prompts.

Report paired distributions:

- TPS ratio/delta;
- loads/token delta;
- bytes/token delta;
- hit-ratio delta.

Do not report only the 24/24 count.

### Figure 5 candidate

Paired Stage-C physical results across all 24 prompts, preferably sorted by EXACT locality or semantic family.

## 7.4 Q3 — Does the gain generalize across workloads?

Use the complete 128-prompt S2 distribution and Stage-C explanations.

Report:

```text
cross-prompt dispersion       high
semantic-family effect        strong
token-length effect           weak
route coverage                broad
S2_P50 paired gain            broad
KNEE/S2 interaction           moderate
```

Include sentinel evidence to distinguish deterministic route/locality stability from host TPS noise.

### Figure 6 candidate

128-prompt family/length distribution, or combine with Figure 5 to save space.

## 7.5 Q4 — What explains throughput?

Use measured #102 rows through final #105 v3 curated analysis.

Headline:

```text
loads/token primary LOFO R²       0.993536
best single working-set LOFO R²   0.465884
```

Interpretation:

> Route structure matters, but actual physical backing-service demand is a much stronger predictor of throughput in this measured regime.

### Figure 7 — hero figure

Measured TPS vs loads/token, with held-out-family/LOFO validation and measured-domain annotation.

Caption must state:

- source physical rows are measured;
- statistical relationship/model is post-hoc exploratory;
- no extrapolation outside measured calibration domain.

## 7.6 Q5 — What exact-cache capacity would match S2 locality?

Use final #105 virtual-cache/exact-replay analysis.

Question:

> At the locality physically achieved by S2_P50, what exact-routing cache capacity would be required according to the frozen exact-replay capacity curve?

This is a **counterfactual equivalence**.

Use final corrected interval propagation from v3 only. Do not reuse failed-review v1/v2 schema/value interpretations.

### Figure 8 candidate

S2 physical locality point projected onto exact-routing capacity/locality curve, with interval/evidence-class labeling.

Do not caption this as a measured RAM saving.

## 7.7 Evidence legend

Every result figure should visually or textually distinguish:

```text
physical measured
observer measured
replay/counterfactual
projection
after-the-fact exploratory analysis
```

---

# 8. Performance–Quality Trade-off

**Coordination:** [#118](https://github.com/murillo128/k3-out-of-core/issues/118)  
**Status:** `OUTLINE`; §8.6 `NEEDS_RESULT`

Quality is a primary result, not a limitations footnote.

## 8.1 Short-horizon controlled perturbation

Use #77 to introduce the measurement chain:

```text
intentional expert swap
      -> local MoE output
      -> hidden state
      -> induced future route differences
      -> logits / reference-token NLL
```

Teacher-forced exact reference tokens separate direct predictive effects from token-mediated free-generation feedback.

## 8.2 Long-horizon predictive drift

Primary authority: final #99.

Frozen classification:

```text
LONG_HORIZON_PREDICTIVE_DRIFT = gradual
```

Headline fixed-context S2 vs EXACT result:

```text
mean ΔNLL                     +0.012030
95% cluster bootstrap         0.008440 .. 0.015435
prompt clusters               16
```

Safe language:

> S2_P50 causes small but measurable positive reference-token NLL damage under the controlled fixed-token intervention, with gradual rather than abrupt growth over the measured horizon.

Do not call this task accuracy.

### Figure 9A candidate

ΔNLL versus decode horizon, with per-family dispersion and aggregate uncertainty.

## 8.3 Is lower local regret clearly better for long-horizon quality?

Frozen KNEE vs S2 result:

```text
quality delta    +0.001090
95% interval     -0.001228 .. 0.003205
classification  no_clear_difference
```

Do not say equivalent. Say no clear ordering/difference under the measured metric.

## 8.4 Token-mediated autoregressive feedback

Controlled evidence classes:

```text
DIRECT_FIXED_CONTEXT
FREE_TRAJECTORY
```

Frozen result:

```text
TOKEN_MEDIATED_ROUTE_FEEDBACK  material
mean amplification             1.4210x
95% interval                   1.2225 .. 1.7099
growth to 1024                 heterogeneous
```

Safe interpretation:

> Once changed generations feed their own tokens back into the model, token-mediated route feedback amplifies the directly measured perturbation on average, but the growth is heterogeneous across the frozen bridge prompts.

### Figure 9B candidate

Direct fixed-context vs free-trajectory effect by horizon for the three bridge prompts.

## 8.5 Can simple routing statistics predict damage?

Make the negative result explicit:

```text
cumulative regret predictive      weak
raw regret adds signal             weak
perturbed fraction adds signal     no
follow-up routing design justified no
```

Candidate conclusion:

> Hard local regret bounds are useful operational constraints, but simple accumulated local statistics are not sufficient long-horizon semantic safety proxies.

Do not reinterpret this as “regret is irrelevant.”

## 8.6 Task-level capability — pending

### #100 — GPQA Diamond

Current intended evidence:

- 30 paired EXACT/S2_P50 items;
- full 198-item S2_P50 campaign;
- public Moonshot 93.5% only as `OFFICIAL_PROTOCOL_NEAR_MATCH`;
- no GPQA quality outcome is currently available.

Placeholder questions:

- paired accuracy delta;
- both-correct / both-wrong / EXACT-only / S2-only;
- uncertainty / disagreement statistic;
- full-S2 accuracy and interval;
- capacity distribution/imbalance caveat;
- delta to Moonshot reference with explicit near-match protocol caveat.

### #101 — independent task suite

If executed, use its two causal modes rather than broad leaderboard farming:

- MMLU-family likelihood scoring → direct predictive/logit-sensitive task effect;
- GSM8K-family generation → autoregressive feedback-sensitive task effect.

Do not collapse heterogeneous task results into one opaque “quality preserved” statement.

## 8.7 Section takeaway

Target wording:

> The locality gain is real and physically measurable, but it is purchased with a bounded routing approximation whose predictive effect is also measurable and can be amplified by autoregressive feedback. The current evidence supports a performance–quality trade-off, not semantic equivalence.

---

# 9. Understanding K3 Expert Locality

**Coordination:** [#119](https://github.com/murillo128/k3-out-of-core/issues/119)  
**Status:** `OUTLINE` / optional main-paper section

This section may be shortened or moved partly to appendix if page limits are tight.

All newly introduced #105 structure analyses are `POST_HOC_EXPLORATORY`.

## 9.1 Workload-conditioned working sets

Use curated observer features to describe:

- distinct ExpertKey working sets;
- concentration/reuse/effective-expert metrics where supported;
- within-family versus between-family overlap;
- B1→B8 endpoint sensitivity.

Relate structure to the systems result without overclaiming:

> Workload structure explains where reuse differs, but direct physical backing demand remains substantially more predictive of measured TPS.

## 9.2 Shared core and workload-specific periphery

Use CommitteeAudit-inspired analysis only at the observable supported by K3 route captures.

Safe structural language:

```text
recurring cross-family routed core
        +
workload-conditioned peripheral demand
```

Do not assign functions to experts from routing frequency alone.

Position against:

- PipeNetwork K3 REAP/domain saliency;
- CommitteeAudit Standing Committee.

Do not numerically pool their overlap metrics with ours.

## 9.3 Why static committee pinning is insufficient

Preserve negative/counterfactual result:

```text
committee-pin regressions   308 cells
committee-pin infeasible    196 cells
```

Safe claim:

> The existence of a recurring routed core does not imply that statically pinning it improves the full locality/capacity frontier.

This is counterfactual, not physical production validation.

## 9.4 Structure, demand, and performance

Close with three levels:

```text
route structure         -> where reuse may exist
physical backing loads  -> what the system actually services
TPS                      -> measured systems consequence
```

### Optional figure

Choose at most one main-paper structure figure:

- family×family route overlap;
- within-family vs between-family similarity;
- core/periphery distribution;
- committee-pin beneficial/regressing/infeasible map.

Move the rest to appendix.

---

# 10. Related Work

**Coordination:** [#120](https://github.com/murillo128/k3-out-of-core/issues/120)  
**Status:** `OUTLINE`

Keep Related Work grouped by problem, not one paragraph per paper.

## 10.1 Expert offloading, caching, and local MoE serving

Core works from `docs/PRIOR_ART.md`:

- MoE-Infinity;
- WASTE;
- Colibrì;
- FreeToken;
- llama.cpp expert-cache/offload work;
- vLLM ExpertWeightProvider/cached-provider work;
- tinyserve where useful.

Core positioning:

> These systems primarily optimize where and how the exact router-selected experts are materialized or executed. This paper additionally asks whether a small, explicitly bounded change in selected membership can reduce the demand presented to the out-of-core memory system.

Important regime distinction:

```text
FreeToken:
  host-RAM expert pool -> VRAM cache / CPU execution

this paper's primary regime:
  NVMe-backed expert pool -> bounded host residency
  + routing used as a bounded demand-control mechanism
```

WASTE and Colibrì are stronger same-model K3 context, but representation/hardware/protocol differ; raw TPS should remain qualitative unless a comparison is truly normalized.

## 10.2 Cache-aware / residency-aware routing

Must cite:

- **Mixture of Cache-Conditional Experts for Efficient Mobile Device Inference** — direct training-free cache-aware routing prior art;
- **MoE-ERAS: Expert Residency Aware Selection** — residency-aware performance/accuracy trade-off;
- **ReMoE** — training-based router fine-tuning for temporal reuse.

Do not claim first cache-aware routing.

Safe differentiation:

- K3 896→16 setting;
- hard per-swap selection-score regret;
- hard swap-count bound;
- deterministic training-free inference-time substitution;
- original weighting semantics retained;
- full-K3 physical NVMe-backed measurement;
- real route evolution after swaps;
- controlled long-horizon direct/free quality attribution.

## 10.3 Routing locality and expert structure

Relevant work:

- Local Routing Consistency;
- PipeNetwork K3 REAP/domain overlap;
- CommitteeAudit / Standing Committee.

Use these to motivate measurement/structure questions, not transfer their numerical/model-specific conclusions.

## 10.4 Language rule

For each related-work group:

1. shared problem;
2. concise examples;
3. exact difference from this paper.

Avoid claiming external results were reproduced unless they were.

---

# 11. Discussion and Limitations

**Coordination:** [#121](https://github.com/murillo128/k3-out-of-core/issues/121)  
**Status:** `OUTLINE`

## 11.1 What the evidence establishes

Subject to final claim review:

- full K3 can be served/measured out of core under an explicit physical regime;
- backing expert service demand dominates throughput variation in the measured domain;
- bounded residency-aware substitutions reduce physical service demand at fixed measured cache capacity;
- the systems gain is broad but workload-conditioned across the frozen corpus;
- changed routing has measurable predictive cost and material token-mediated feedback.

## 11.2 Model generality

Primary evidence is Kimi K3.

Do not infer:

- same slack on every MoE;
- same threshold on another model;
- universal core/periphery structure.

#81/#84/#85/#86 may later broaden the claim, but are not required for a K3-specific paper.

## 11.3 Hardware/storage generality

State:

- absolute TPS depends on hardware, storage, representation, memory capacity, and execution backend;
- #105's locality→TPS model is in-domain/post-hoc exploratory;
- external WASTE/Colibrì/FreeToken results represent different physical regimes;
- physical service demand is the transferable concept, not necessarily the measured numerical slope.

## 11.4 Quality/task generality

Evidence hierarchy:

```text
short-horizon internal perturbation   #77
long-horizon predictive / feedback    #99
task-level GPQA                        #100 pending
independent MMLU/GSM8K                #101 pending
```

Even if task deltas are small, do not claim universal semantic equivalence beyond measured tasks/protocols.

## 11.5 Evidence classes

The paper intentionally combines:

- measured physical runs;
- observer traces;
- replay/counterfactuals;
- projections;
- post-hoc exploratory analyses;
- controlled quality interventions.

Captions/tables must make those classes visible enough that measurement and inference are not conflated.

## 11.6 Practical implications

Candidate implications:

- optimize physical expert-service demand, not hit rate alone;
- exact-routing execution optimizations and bounded routing changes are complementary;
- router-score bounds are not semantic safety guarantees;
- workload-conditioned locality limits one universal static hot-set story;
- when routing becomes a systems control, performance, memory capacity, and quality must be measured jointly.

## 11.7 Anticipated reviewer objections

### “Is this just another expert cache?”

Answer: cache hierarchy is prior art; distinctive evidence is bounded routing slack reducing physical demand on full K3 plus explicit quality/feedback measurement.

### “Why not just optimize exact misses?”

Answer: exact-routing systems are complementary. This paper asks whether demand itself can be reduced in a harder NVMe-backed capacity regime; it does not claim exact miss optimization is universally exhausted.

### “Does local regret guarantee quality?”

Answer: no; #99 directly measures drift and weak simple safety predictors.

### “Is it general across models?”

Answer: not yet; primary claim is K3-specific.

### “Are virtual-cache savings real RAM savings?”

Answer: they are replay/counterfactual equivalence estimates unless physically validated at those exact capacities.

### “Was locality→TPS preregistered?”

Answer: no; #105 labels newly introduced secondary analyses post-hoc exploratory.

---

# 12. Conclusion

**Coordination:** [#122](https://github.com/murillo128/k3-out-of-core/issues/122)  
**Status:** `OUTLINE`

Keep the conclusion short and conceptual.

## Target flow

1. Sparse activation reduces compute but not the expert-capacity/memory mismatch.
2. Out-of-core inference turns routing into an expert-service problem.
3. On full Kimi K3, explicit residency plus bounded cache-aware substitutions can reduce physical expert demand and improve locality/throughput across the frozen workload evidence.
4. Physical backing demand is the strongest measured systems variable in the evaluated regime.
5. The gain is not free: routing changes cause measurable predictive drift and token-mediated feedback, while simple accumulated regret is not a sufficient long-horizon semantic proxy.
6. Final conceptual takeaway.

Candidate final sentence:

> MoE sparsity can be exploited not only to reduce computation, but to make model capacity itself an online systems resource. Once routing participates in that control loop, performance, memory capacity, and predictive quality must be evaluated jointly.

Alternative:

> For out-of-core MoE inference, the relevant frontier is not simply cache size versus throughput, but routing flexibility versus physical service demand versus quality.

Use at most one or two quantitative results in the Conclusion after final headline claims are frozen.

---

# Appendix plan

The appendix should absorb reproducibility and extended evidence that would otherwise break the main narrative.

## Appendix A — Exact model/runtime/evidence identities

Include:

- exact K3 source/model manifest;
- project/nested commits;
- host/device/storage identity;
- cache capacity and context settings;
- primary release tags and SHA-256;
- evidence-class definitions.

## Appendix B — Full systems distributions

Candidate contents:

- all 128 prompt-level S2 metrics;
- 8 sentinel series;
- all 24 Stage-C paired EXACT/KNEE/S2 results;
- family and length decompositions;
- locality→TPS residuals/LOFO detail;
- exact capacity/replay curves;
- full virtual-cache interval table.

## Appendix C — Route-structure analysis

- family overlap matrices;
- B1/B8 sensitivity;
- working-set features;
- core/periphery construction;
- committee-pin beneficial/regressing/infeasible regions;
- explicit `POST_HOC_EXPLORATORY` label.

## Appendix D — Quality instrumentation and long-horizon results

- #77 short-horizon measurement details;
- all #99 16-family fixed-context trajectories;
- three 1024-token direct/free bridge prompts;
- predictor models / held-out tests;
- negative-result details.

## Appendix E — Task-level protocol/results

When #100/#101 complete:

- exact benchmark/harness/dataset revisions;
- prompt/template/sampling/scoring semantics;
- capacity admission behavior;
- paired item-level outcomes;
- invalid/truncated treatment;
- intervals/statistics;
- official-reference fidelity classification.

## Appendix F — Prior-art comparability matrix

Use #105/`docs/PRIOR_ART.md` classification logic:

| Work | Same model? | Same representation? | Same physical regime? | Raw TPS directly comparable? | Main relevance |
|---|---|---|---|---|---|
| WASTE | K3 | No | Different | Generally no | full-K3 streamed systems baseline |
| Colibrì | K3 | close/source-MXFP4 | Different | generally no | high-fidelity K3 layout/execution baseline |
| FreeToken | no current K3 support | different | RAM-resident pool | no | exact-routing RAM↔VRAM/CPU systems baseline |
| Cache-Conditional Experts | other MoEs | N/A | different | no | direct cache-aware routing prior |
| MoE-ERAS | other MoEs | N/A | different | no | residency-aware routing prior |
| ReMoE | other MoEs | N/A | different | no | training-based reuse/locality prior |

Verify publication metadata and exact comparability labels before final submission.

---

# Main figure/table plan

Target roughly 7–9 primary visual elements before appendix material.

| ID | Candidate | Section | Evidence class | Priority |
|---|---|---|---|
| Fig. 1 | K3 memory mismatch / active demand scale | §2 | model constants / explanatory | high |
| Fig. 2 | K3 workload route/locality variation | §3 | measured observer + post-hoc descriptive | medium |
| Fig. 3 | Out-of-core architecture | §4 | design diagram | high |
| Fig. 4 | Bounded cache-aware routing mechanism | §5 | method diagram | high |
| Fig. 5 | 24-prompt Stage-C paired physical result | §7 | `MEASURED_PHYSICAL` | high |
| Fig. 6 | 128-prompt workload/generalization view | §7 | `MEASURED_PHYSICAL` | high/mergeable |
| Fig. 7 | **Measured loads/token → TPS hero plot** | §7 | physical inputs + `POST_HOC_EXPLORATORY` model | very high |
| Fig. 8 | Exact-cache / S2 virtual capacity equivalence | §7 | replay/counterfactual + post-hoc | high |
| Fig. 9 | Long-horizon ΔNLL + direct/free feedback | §8 | controlled quality evidence | very high |
| Tbl. 1 | System/model/evaluation setup | §7 | measured protocol | high |
| Tbl. 2 | Final task-level paired result | §8 | pending #100/#101 | high if available |

Likely compression strategy for a page-limited venue:

- combine Fig. 5 + Fig. 6;
- combine Fig. 9A + Fig. 9B;
- move §9 structure figures to appendix except one compact panel;
- keep Fig. 7 and Fig. 8 in the main paper.

---

# Citation / bibliography plan

The manuscript should ultimately use Pandoc/LaTeX-compatible citations and a `references.bib` file. Do not hand-maintain final reference numbering in prose.

Minimum related-work set expected:

- Kimi K3 model paper/card;
- MoE-Infinity;
- WASTE;
- Colibrì;
- FreeToken;
- Cache-Conditional Experts;
- MoE-ERAS;
- Local Routing Consistency;
- ReMoE;
- PipeNetwork K3 REAP work where appropriate;
- CommitteeAudit / Standing Committee;
- relevant llama.cpp/vLLM caching architecture references where citable.

Use `docs/PRIOR_ART.md` as the project-curated starting point, but verify publication metadata before final submission.

---

# Submission-readiness checklist

The paper can be drafted now. It is **not** submission-ready until the following are explicitly decided.

## Scientific completeness

- [x] Full-K3 systems/locality evidence frozen (#98/#102).
- [x] Curated paper-ready analysis layer frozen (#105).
- [x] Long-horizon predictive quality and feedback frozen (#99).
- [ ] Task-level GPQA evidence accepted or explicitly omitted with rationale (#100).
- [ ] Decide whether a bounded #101 standard-suite result is required after seeing #100.
- [ ] Decide whether K3-only scope is sufficient for target venue; do not wait for cross-model work by default.

## Claim discipline

- [ ] Every headline number mapped to immutable source/release.
- [ ] Every figure caption states measured/replay/projection/post-hoc status where relevant.
- [ ] No #98/#102 protocol pooling.
- [ ] No task-quality claim from #99 internal metrics alone.
- [ ] No semantic-equivalence wording unsupported by paired task evidence.
- [ ] No general-MoE claim from K3-only evidence.
- [ ] Prior-art novelty statement reviewed against Cache-Conditional Experts / MoE-ERAS / ReMoE.

## Editorial completeness

- [ ] Title/Abstract stable (#110).
- [ ] Introduction contribution ladder stable (#111).
- [ ] Main 7–9 figures selected and sourced.
- [ ] Appendix/evidence table complete.
- [ ] Related Work publication metadata reverified.
- [ ] Venue template/length selected.
- [ ] Markdown converted to venue LaTeX only after content stabilizes.

---

# Coordination index

These issues are state/info/coordination only; writing remains interactive and this Markdown remains the manuscript source of truth.

- [#109 — Epic](https://github.com/murillo128/k3-out-of-core/issues/109)
- [#110 — Title and Abstract](https://github.com/murillo128/k3-out-of-core/issues/110)
- [#111 — Introduction](https://github.com/murillo128/k3-out-of-core/issues/111)
- [#112 — Background and Motivation](https://github.com/murillo128/k3-out-of-core/issues/112)
- [#113 — Characterizing Out-of-Core K3](https://github.com/murillo128/k3-out-of-core/issues/113)
- [#114 — K3 Out-of-Core Design](https://github.com/murillo128/k3-out-of-core/issues/114)
- [#115 — Bounded Cache-Aware Expert Routing](https://github.com/murillo128/k3-out-of-core/issues/115)
- [#116 — Implementation](https://github.com/murillo128/k3-out-of-core/issues/116)
- [#117 — Evaluation: Systems, Locality, and Capacity](https://github.com/murillo128/k3-out-of-core/issues/117)
- [#118 — Performance–Quality Trade-off](https://github.com/murillo128/k3-out-of-core/issues/118)
- [#119 — Understanding K3 Expert Locality](https://github.com/murillo128/k3-out-of-core/issues/119)
- [#120 — Related Work](https://github.com/murillo128/k3-out-of-core/issues/120)
- [#121 — Discussion and Limitations](https://github.com/murillo128/k3-out-of-core/issues/121)
- [#122 — Conclusion](https://github.com/murillo128/k3-out-of-core/issues/122)