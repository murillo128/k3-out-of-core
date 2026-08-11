# Phase 13.6 cache-aware bounded routing

This plan is normative for the bounded cache-aware Kimi K3 routing experiment. It implements issue #77 as amended for an OCI DenseIO CPU host. It does not change the default exact route, authorize router training, or reopen cache-policy design.

## Fixed boundary

- Start from the accepted post-#73 project and nested heads and the pinned complete Kimi K3 artifact.
- Preserve the exact K3 router projection, correction bias, ordinary probabilities, top-16 reference decision, normalization, scale, and expert tensors.
- Permit changed membership only after a positive offline opportunity gate and only in an explicit default-off experimental mode.
- Always emit exactly 16 unique valid experts. A replacement occupies the displaced exact top-16 rank slot.
- Treat changed routing as a semantic experiment. Never describe it as exact inference or quality-neutral without measured evidence.

## Stage A — exact top-M observation and offline gate

The exact path captures a bounded top-M extension only while the existing route observer is explicitly active. `M=32` is the initial decision point. The observer retains, per routed layer/token:

```text
exact ordered top-16 IDs and final weights
ordered top-M IDs
K3 correction-biased selection scores
ordinary unbiased probabilities
request, ubatch, phase, layer and position identity
```

The observer must use the existing publication synchronization, perform no work when disabled, and prove that exact top-16 is the top-M prefix.

Replay exact prefill to establish cache state, then evaluate changed decisions on decode. Simulate deterministic LRU tiers at bounded 20/32/40/60/64/80/96 GiB total capacities using the accepted 17,547,264-byte expert bundle. Sweep candidate counts 16/24/32, maximum swaps 0/1/2/4, and only the observed top-k-boundary gap quantiles (minimum, p10, p25, p50, p75, p90, maximum plus the zero control).

The decision-driving input is
`corpus/phase13/issue73-decision-v1.json`: the accepted #73 `CPU_CONTROL`
100-token prompt, 24 deterministic output steps, and `n_ubatch=4`. Its expected
CPU-control token sequence is part of the corpus so an observer run cannot
silently become a different exact baseline. Short five-token #73 inputs may
screen harness mechanics but cannot determine the opportunity gate.

The DenseIO CPU probe fixes `n_gpu_layers=0` and disables optional CPU weight
repacking. Repacking the complete expert tensor set would require a private
buffer larger than host RAM and is incompatible with mmap-backed out-of-core
execution; the effective setting is recorded in every capture.

The preregistered positive gate is at least one `M=32`, at-most-two-swap point at or below the observed positive-gap p25 that reduces projected backing-store loads/bytes by at least 5%. If no point qualifies, publish `negative-locality` and stop before runtime rerouting. Candidate count may increase to at most 64 only when top-32 boundary substitutions materially contribute at a qualifying point.

## Stage B — conditional runtime mechanism

Stage B begins only after Stage A produces `positive-frontier`.

The pure deterministic policy consumes the exact ordered top-16, ordered top-M scores, a contemporaneous read-only provider tier snapshot, `routing_max_score_regret`, and `routing_max_swaps`. It considers only cheaper-tier unselected candidates and orders legal substitutions by:

1. greatest avoided service tier/cost;
2. least non-negative selection-score regret;
3. better original candidate rank;
4. better displaced exact top-16 rank;
5. lower candidate expert ID, then lower displaced expert ID, as the final stable tie-breaks.

`routing_max_score_regret == 0`, `routing_max_swaps == 0`, and `routing_candidate_count == 16` are exact controls. Invalid, non-finite, stale-generation, duplicate, or out-of-range input fails before expert execution.

The effective experimental configuration is equivalent to:

```text
cache_aware_routing_enabled   false
routing_candidate_count      32
routing_max_swaps             bounded to 0..16
routing_max_score_regret      finite and non-negative
```

The provider remains the owner of residency, generation, admission, eviction, materialization, and failure cleanup. The routing component receives only a bounded read-only tier snapshot at the existing per-layer remap/materialization boundary. The portable CPU implementation uses one explicit, counted routing-policy checkpoint per routed layer. Provider-backed execution retains its distinct existing remap callback/checkpoint and counter; evidence must report both when both are active. No synchronization may be hidden inside the claimed locality or performance result.

## Validation

Focused native tests cover disabled zero-work, all exact controls, near-tie replacement, outside-bound rejection, deterministic multi-swap ordering, tier preference, unique cardinality, unaffected slots, unchanged unbiased weight gathering, invalid configuration, stale provider generation, repeated warm execution, abort, unload, and teardown.

The retained quality mechanism is harness-only and explicit: supplying a quality-trace path installs the graph evaluation callback, while omitting it leaves the callback null. Measurement mode captures decode-time `ffn_moe_out` and `l_out` tensors plus full per-step logits in a versioned binary stream. Exact generation supplies the reference token IDs; changed routing consumes those same IDs through teacher forcing. The paired analyzer streams exact and changed traces to compute local-MoE and hidden-state relative L2/cosine/norm-ratio statistics, logit KL/JS, top-token/top-k agreement, reference-token NLL delta, intentional swaps, and induced subsequent exact-top-k divergence. Raw vectors and run summaries remain external issue-owned evidence.

The real-route analyzer independently replays the actual selected expert IDs from fresh exact and changed generated streams. It warms the fixed LRU from prefill, measures decode using unique layer/expert requests from one immutable batch snapshot, and reports both total generated-output and routed-decode-token denominators so first-token prefill is not silently mixed into decode locality.

On complete Kimi K3:

- compare fresh-process exact-disabled routes, weights, logits and generated tokens with the accepted exact baseline;
- use the offline frontier only to choose a bounded runtime knee and at most a few materially distinct points;
- capture the real generated route stream for every retained exact and changed point;
- replay each real stream at the fixed capacity anchors;
- report hit/miss ratio, distinct loads per token, backing bytes per token, service/wait where measured, changes, swaps and regret;
- report teacher-forced logit/NLL divergence, top-token agreement, deterministic generated outputs, CPU throughput and latency.

If Stage B runs, quality instrumentation is a separate explicit default-off
measurement mode. At bounded representative swaps, retain reproducible compact
local-MoE relative-L2/cosine/norm-ratio comparisons, hidden-state divergence by
meaningful depth/token boundary, and teacher-forced predictive divergence on a
fixed versioned corpus. The teacher-forced exact route supplies the common
reference token sequence; report probability/logit divergence, top-1 agreement,
top-k overlap where useful, and reference-token NLL delta. Correlate these with
intentional regret/swaps and subsequent induced route divergence. A small
heterogeneous deterministic generation set is only a sanity check, not a
substitute for teacher forcing or a claim of task-level equivalence.

GPU throughput, H2D/peer bytes, VRAM and multi-GPU synchronization are not Phase 13.6 acceptance requirements under the DenseIO amendment.

## Evidence and checkpoints

The repository retains only reproducibility mechanisms: bounded schemas, the
versioned corpus, capture/replay/quality tools, and focused fixtures/tests. Per
issue amendment `5246984985`, issue #77 is the experiment notebook. Put host
identity, exact commands, run-specific configuration, metrics, frontier,
quality evidence, implementation heads, and final disposition in checkpoint
comments rather than a committed `results/` packet or run-specific manifest.
Raw top-M routes and traces remain external immutable artifacts with recorded
size and SHA-256 identity, preferably as GitHub Release assets when they do not
fit in an issue comment.

Checkpoint A reviews the exact project+nested target after conditional policy/provider/runtime integration and focused tests, before the full-model campaign. A fresh final-capable review verifies the immutable full-model frontier, quality claims, exact-path non-regression and final recommendation. A negative Stage A result requires only the bounded observer/replay evidence and final review; it must not acquire unused runtime machinery.
