# Detailed Implementation Plan

This plan targets the final architecture. Intermediate steps exist to isolate correctness and performance, not as alternative endpoints or shortcuts.

Each phase has an **exit gate**. Do not begin a dependent phase until the gate is satisfied and its evidence is committed. The detailed, normative tasks are split only for maintainability; together these files are the implementation plan.

## Plan map

- [Phases 0–3: foundation, reproducible K3 baseline, observability, and provider abstraction](docs/plan/00-foundation.md)
- [Phases 4–6: persistent hot cache, cold cache, and GGUF-backed storage](docs/plan/04-cache-and-storage.md)
- [Phases 7–10: asynchronous runtime, miss execution, cache policy, and prefetch](docs/plan/07-async-runtime.md)
- [Phases 11–15: UMA, full-size scaling, concurrency, multi-GPU, and hardening](docs/plan/11-scaling-and-hardening.md)

## Research references

- [Alternative MoE validation targets](docs/ALTERNATIVE_MOE_VALIDATION_TARGETS.md) records non-normative Qwen3-Coder-Next and DeepSeek-V4-Flash candidates, reproducibility requirements, and the proposed Phase-12 quality-versus-residency comparison. It does not change current issue scope or K3 acceptance criteria.

## Execution rule

Work proceeds strictly in phase order unless a phase explicitly contains independent subwork. A later phase may be researched in parallel, but no dependent implementation may be accepted until the earlier phase exit gate and evidence are committed.

The remaining sequence deliberately validates physical full-size viability before service and topology complexity: Phase 11 establishes coherent UMA transport, Phase 12 establishes full-size single-request/single-device behavior and makes the storage-format decision, Phase 13 adds multi-request and batching, Phase 14 adds multi-GPU placement, and Phase 15 hardens the accepted architecture. Phase 12 does not claim concurrent-service or multi-GPU performance; those remain independent later gates.

Phase 3's original 24-cell resident performance confidence gate remains recorded as a 22/24 raw failure. Design authority accepted the Phase 3 technical exit as `PASS_WITH_NOTES` for project progression only; Phase 3 is merged and its evidence remains immutable. Phase 4 issue #17 completed its synchronous mechanism and evidence, passed the final complete-PR review with safety `YES`, and merged through PR #18 as `b196cc07249726651d39aaa624703bc4256a3012`. Phase 5 issue #20 completed its bounded cold-cache and transfer-ring mechanism, passed Checkpoint A and final complete-PR review with safety `YES`, and merged through PR #21 as `c5512bc073ae7aab4a14773028828e516e16f3f6`. No Phase 3 waiver weakens any later correctness or performance gate.

Phase 6 issue #22 completed positional GGUF storage, deferred routed loading, synchronous storage-to-cold integration, and corrected standing evidence. Checkpoint A returned `PASS`, safety `YES`, at comment `5133647261`; final complete-PR review returned `PASS`, safety `YES`, with no required delta at comment `5134171772`. PR #23 squash-merged into `main` as `66ab6dba60b55ce47d0ecf94fcf88a778df9cdc6` with nested `llama.cpp` head `7a606dd4e11a108929f799253809a904f55feae4`.

Phase 7 issue #24 completed the bounded asynchronous Linux I/O, direct-I/O fallback, priority/single-flight, CUDA event/dependency, cancellation/unload, overlap, and standing-evidence work. Checkpoint A, Checkpoint B, and final complete-PR review returned `PASS`, safety `YES`, at comments `5135836934`, `5140081178`, and `5140490542`. PR #25 squash-merged into `main` as `97ef68d787c54b443eac72a3480fe70eba88d8dd` with nested `llama.cpp` head `b71e40f91b1a0dab578d56ac733211453704d674`.

Phase 8 issue #26 completed explicit `PROMOTE_AND_GPU`, `CPU_FALLBACK`, deterministic `AUTO`, bounded background promotion, fail-closed production evidence, and descriptor-only cold-cache bootstrap scaling. Checkpoints A, B, and C returned safety `YES` at comments `5141694340`, `5144721775`, and `5146173479`; the mandatory final complete-PR review returned `PASS_WITH_NOTES`, safety `YES`, with no required delta at comment `5146545713`. PR #27 squash-merged into `main` as `05c38f0f11bd33c3654a3d9b2c3c6aa32e7f4c35` with nested `llama.cpp` head `dc4d50c68378d908131b518662160fdd08f4e005`. The authoritative manifest is `results/2026-07-31/skynet/phase8-miss-execution/phase8-manifest.json`.

Phase 9 issue #30 completed the bounded cache-policy framework, exact LRU compatibility, deterministic LFRU/SLRU/LFU-aging candidates, frequency-gated admission, hard global/per-layer domains, canonical replay, physical-residency evidence, and fixed-rule selection. Checkpoints A, B, and C returned `PASS`, safety `YES`, at comments `5148012128`, `5148752231`, and `5149625334`; the mandatory final complete-PR review returned `PASS`, safety `YES`, with no required delta at comment `5149762882`. No non-LRU pair passed every default gate, so exact global LRU/ALWAYS is retained for hot and cold tiers and per-layer policy remains explicit-only. PR #31 squash-merged into `main` as `035f099de85b3f775ae8cd6769b561156ea52317` with nested `llama.cpp` head `fd29c0f9e868e838d3641cd13eb6ceb8c1535f01`. The authoritative manifest is `results/2026-07-31/skynet/phase9-cache-policy/phase9-manifest.json`.

Phase 10 issue #32 reached its fixed performance selection at project evidence head `d665d424bccc37a549a845c41f3f78074fb00c90` and nested head `b617680ca030f7e81d2446c4fabb24225947538c`, but its exit gate is **BLOCKED**. Seven predictive envelopes have held-out precision lower bounds at or below exact break-even, four lack exact held-out evidence for their online-compatible profile, and random prefetch is never selectable. The buffered and H2D blocking static-seed envelopes are **REJECTED** after failing fixed steady-state, domain-shift, and, for H2D, cold-TTFT gates. The STANDARD two-failure circuit breaker is triggered; `results/2026-08-01/skynet/phase10-prefetch/selection.json` selects no profile, disables all evaluated profile hashes, and preserves the Phase 9 defaults. Phase 10 remains `investigation-required`; Phase 10.5 closeout and Phase 11 must not begin until a new approved investigation produces at least one qualifying envelope.

## Final acceptance criteria

The authoritative final acceptance criteria are in [Phase 15](docs/plan/11-scaling-and-hardening.md#final-acceptance-criteria).
