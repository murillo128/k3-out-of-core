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
**Status:** `OUTLINE`

## 1.1 Paragraph flow

### P1 — Sparse compute does not imply a small memory footprint

Target claim:

> Sparse Mixture-of-Experts models activate only a small fraction of their parameters per token, but conventional inference still needs access to the complete expert pool.

Purpose: start with the broad systems tension, not K3 implementation history.

### P2 — Kimi K3 makes the mismatch acute

Introduce the concrete scale:

- 896 routed experts per routed layer;
- top-16 selected;
- 92 routed layers;
- 17,547,264-byte MXFP4 expert bundle;
- 1,472 selected expert bundles across one token's routed layers.

Target transition:

> Moving the inactive pool to NVMe solves capacity, but transforms expert selection into an online memory-service problem.

### P3 — Exact routing creates a locality/service bottleneck

Use measured evidence rather than intuition:

- workload-conditioned K3 routing;
- backing loads vary across prompts;
- #105 shows backing loads/token is an exceptionally strong predictor of measured TPS in the #102 domain.

Do not introduce the full regression here if it makes the Introduction dense; one number is enough.

### P4 — Router slack is an additional systems control

Target claim:

> The top-16 decision is not isolated from nearby candidates: K3 often exposes near-tied alternatives whose router-score cost can be bounded explicitly.

Position novelty narrowly:

- cache-aware routing has prior art;
- this paper studies deterministic, training-free, hard-regret-bounded substitution on full K3 under real out-of-core execution;
- real downstream route evolution and long-horizon quality are measured rather than assumed.

### P5 — System/method overview

One paragraph only:

- explicit expert-residency runtime;
- bounded backing/cache service;
- contemporaneous residency exposed to routing;
- hard per-swap regret and swap-count limits;
- exact routing remains the default/reference.

### P6 — Main findings

Candidate order:

1. broad physical systems result;
2. physical mechanism/locality→TPS;
3. equivalent exact-cache/capacity interpretation;
4. quality/feedback cost;
5. task-level result once accepted.

## 1.2 Contributions

Target four contributions, not a feature list:

1. **Out-of-core full-K3 runtime and measurement substrate.** An explicit, observable expert-residency/backing path for full Kimi K3 that makes physical service demand measurable rather than relying on implicit OS caching.
2. **Full-K3 locality characterization.** A cross-workload study showing strong workload dependence and that backing expert loads are the dominant predictor of physical decode throughput in the measured regime.
3. **Bounded cache-aware routing.** A training-free deterministic mechanism that trades strictly bounded router preference for residency under hard per-swap regret and swap-count limits.
4. **Quality and feedback characterization.** Controlled short- and long-horizon measurements showing predictive drift, token-mediated autoregressive amplification, and the limits of simple regret-based safety proxies.

Task-level capability should be mentioned as evaluation evidence, not necessarily a fifth contribution.

## 1.3 Reviewer-risk check

Before marking ready:

- novelty language acknowledges Cache-Conditional Experts / MoE-ERAS / ReMoE;
- no broad cross-model claim;
- no quality-neutral language;
- no post-hoc #105 result presented as confirmatory;
- no implementation feature list displacing the problem→insight→evidence flow.

---

# 2. Background and Motivation

**Coordination:** [#112](https://github.com/murillo128/k3-out-of-core/issues/112)  
**Status:** `OUTLINE`

## 2.1 Sparse MoE inference and Kimi K3

Explain only what later sections require:

- routed layer and expert-selection terminology;
- top-k membership selection;
- K3's 896→16 routed selection across 92 layers;
- ExpertBundle as the unit of storage/cache service;
- selected membership versus original expert weighting semantics.

Avoid a generic transformer/MoE tutorial.

## 2.2 The memory mismatch

Candidate table:

| Quantity | Kimi K3 value | Meaning |
|---|---:|---|
| Routed experts/layer | 896 | Total routed choice set |
| Selected experts/layer | 16 | Exact active membership |
| Routed layers | 92 | Routed decisions per token |
| Expert bundle | 17,547,264 B | Whole routed expert storage/service unit |
| Selected bundles/token | 1,472 | Cumulative top-16 selections across routed layers |
| Selected payload/token | 25,829,572,608 B | Payload demand before reuse/cache hits; not resident-memory requirement |

Add total checkpoint/routed-pool size only after sourcing it from the exact model artifact/model card used by the paper.

## 2.3 Why ordinary offloading is insufficient

Baseline service path:

```text
router selection
   -> residency lookup
      -> hit: execute resident expert
      -> miss: service expert from backing store -> execute
```

Motivating points:

- NVMe solves storage capacity, not miss service cost;
- adding exact cache consumes scarce RAM/VRAM;
- exact-routing systems optimize service of the selected set but generally leave demand itself unchanged;
- page-cache behavior is not a sufficient reproducible cache policy for the paper's measurements.

Target transition:

> The key question is therefore not only how quickly an exact miss can be served, but how much of K3's exact expert demand is intrinsically necessary when nearby router candidates are almost tied.

### Figure 1 candidate — memory mismatch

Conceptual scales:

```text
total routed expert pool  >>  available memory  >>  per-layer top-16 active set
```

Do not visually imply that `25.83 GB/token` is simultaneously resident.

---

# 3. Characterizing Out-of-Core K3

**Coordination:** [#113](https://github.com/murillo128/k3-out-of-core/issues/113)  
**Status:** `OUTLINE`

This section establishes the measurable problem before presenting the solution.

## 3.1 K3 routing is workload-dependent

Primary evidence: #102 physical/observer corpus; #105 curated secondary analysis.

State:

```text
128 primary prompts
16 semantic families
8 within-family length levels
cross-prompt dispersion = high
semantic-family effect = strong
token-length effect = weak
route coverage = broad
```

Candidate claims:

- semantic workload affects selected expert demand materially;
- prompt length alone explains comparatively little of the measured variation;
- broad route coverage means one tiny static hot set cannot be assumed to capture all demand.

Boundary: the corpus is deliberately diverse, not a random sample of every K3 workload.

### Figure 2A candidate

Family × family selected-route overlap, or a compact family-wise locality/working-set distribution.

## 3.2 Exact routing creates a backing-service bottleneck

Primary physical data: #102. Secondary model: #105.

Headline candidate:

```text
loads/token primary LOFO R² = 0.993536
```

Safe language:

> Within the measured #102 physical domain, backing expert loads per token strongly predict decode throughput even when each semantic family is held out in turn.

Required qualifiers:

- post-hoc exploratory statistical analysis;
- measured-domain relation, not hardware-independent law;
- physical TPS must come only from measured rows.

### Figure 2B / hero candidate

Measured physical TPS vs backing loads/token with family-aware LOFO annotation.

## 3.3 More exact cache helps, but memory is the scarce resource

Use exact replay/capacity curves with explicit evidence class.

Target framing:

> Increasing an exact-routing cache improves locality by spending the resource that out-of-core execution is intended to conserve.

Set up the later equivalence question:

> Can bounded routing flexibility produce locality comparable to a larger exact cache without increasing the measured physical cache capacity?

Do not call replay/counterfactual capacity physically measured memory savings.

## 3.4 Routing contains bounded slack

Use #77 / policy-selection evidence to establish nearby candidates and a usable locality–regret frontier.

Do not explain the full algorithm yet.

End with four design requirements:

1. reduce backing demand without simply adding cache;
2. keep exact routing as reference/default;
3. make every intentional membership change deterministic and bounded;
4. measure semantic consequences explicitly.

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
|---|---|---|---|---|
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
