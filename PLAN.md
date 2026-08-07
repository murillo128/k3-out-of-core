# Detailed Implementation Plan

This plan targets the final architecture. Intermediate steps exist to isolate correctness and performance, not as alternative endpoints or shortcuts.

Each phase has an **exit gate**. Do not begin a dependent phase until the gate is satisfied and its evidence is committed. The detailed, normative tasks are split only for maintainability; together these files are the technical implementation plan.

Live phase status, active ownership, ordering changes, and links to controlling issues are maintained in [epic #39](https://github.com/murillo128/k3-out-of-core/issues/39). Do not add execution histories, merge SHAs, review transcripts, or current-work status to this document.

## Plan map

- [Phases 0–3: foundation, reproducible K3 baseline, observability, and provider abstraction](docs/plan/00-foundation.md)
- [Phases 4–6: persistent hot cache, cold cache, and GGUF-backed storage](docs/plan/04-cache-and-storage.md)
- [Phases 7–10: asynchronous runtime, miss execution, cache policy, and prefetch](docs/plan/07-async-runtime.md)
- [Phases 11–15: UMA, full-size scaling, multi-GPU, end-to-end observability, concurrency, and hardening](docs/plan/11-scaling-and-hardening.md)
- [Phase 12–14 Colibrì comparison addendum](docs/plan/12-colibri-comparison.md) establishes mandatory storage-layout, I/O-submission, full-size K3, trace-identity, and single-request chunked-prefill comparisons without changing the accepted technical boundaries.
- [Cross-model portability follow-up](docs/plan/12-heterogeneous-layout-classes.md) defines bounded heterogeneous expert layout classes and the conditions for resuming full-model DeepSeek-V4 validation without changing K3 Phase 12 gates or runtime defaults.

## Research references

- [Alternative MoE validation targets](docs/ALTERNATIVE_MOE_VALIDATION_TARGETS.md) records non-normative Qwen3-Coder-Next and DeepSeek-V4-Flash candidates, reproducibility requirements, and the proposed Phase-12 quality-versus-residency comparison. It does not change current issue scope or K3 acceptance criteria.
- [Colibrì Kimi K3 prior art](docs/COLIBRI_K3_PRIOR_ART.md) records the v1.4.0 source-MXFP4 K3 engine, source/repacked safetensors layouts, direct-I/O and pipeline results, chunked-prefill baseline, and bounded reuse decisions.

## Execution rule

Work proceeds strictly in dependency order unless a phase explicitly contains independent subwork. A later phase may be researched in parallel, but no dependent implementation may be accepted until its prerequisites and evidence are committed.

The remaining sequence deliberately validates physical full-size viability before topology and service complexity: Phase 11 establishes coherent UMA transport; Phase 12 establishes full-size single-request/single-device behavior and makes the storage-format decision; **Phase 13 adds multi-GPU and topology-aware placement**; **Phase 13.5 is the end-to-end tracing and hardware-benchmark-readiness work that was executed early under the historical name Phase 12.5 through #54/#55**; Phase 14 adds multi-request, batching, and CUDA-graph service behavior; and Phase 15 hardens the accepted architecture.

The Phase 13.5 tracing schema is therefore already an accepted prerequisite input to Phase 13 despite its renumbered roadmap slot. Renumbering does not require re-execution, does not alter historical issue/PR/manifests, and does not move the causal prerequisite after multi-GPU in actual execution history.

Phase 12 does not claim concurrent-service or multi-GPU performance. Phase 13 is single-request multi-GPU topology work, not batching. Phase 14 adds concurrency only after the single-request multi-GPU ownership and byte-movement model is established.

Phase 10 mechanisms may be retained for explicit experimentation, but no static-seeding or predictive-prefetch profile may be recommended or enabled by default without satisfying the Phase 10 gate. The null/default behavior remains the accepted demand-only cache and miss-policy baseline.

## Final acceptance criteria

The authoritative final acceptance criteria are in [Phase 15](docs/plan/11-scaling-and-hardening.md#final-acceptance-criteria).
