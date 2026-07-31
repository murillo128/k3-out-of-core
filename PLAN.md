# Detailed Implementation Plan

This plan targets the final architecture. Intermediate steps exist to isolate correctness and performance, not as alternative endpoints or shortcuts.

Each phase has an **exit gate**. Do not begin a dependent phase until the gate is satisfied and its evidence is committed. The detailed, normative tasks are split only for maintainability; together these files are the implementation plan.

## Plan map

- [Phases 0–3: foundation, reproducible K3 baseline, observability, and provider abstraction](docs/plan/00-foundation.md)
- [Phases 4–6: persistent hot cache, cold cache, and GGUF-backed storage](docs/plan/04-cache-and-storage.md)
- [Phases 7–10: asynchronous runtime, miss execution, cache policy, and prefetch](docs/plan/07-async-runtime.md)
- [Phases 11–15: UMA, concurrency, multi-GPU, full-size scaling, and hardening](docs/plan/11-scaling-and-hardening.md)

## Execution rule

Work proceeds strictly in phase order unless a phase explicitly contains independent subwork. A later phase may be researched in parallel, but no dependent implementation may be accepted until the earlier phase exit gate and evidence are committed.

Phase 3's original 24-cell resident performance confidence gate remains recorded as a 22/24 raw failure. Design authority accepted the Phase 3 technical exit as `PASS_WITH_NOTES` for project progression only; Phase 3 is merged and its evidence remains immutable. Phase 4 issue #17 completed its synchronous mechanism and evidence, passed the final complete-PR review with safety `YES`, and merged through PR #18 as `b196cc07249726651d39aaa624703bc4256a3012`. Phase 5 issue #20 completed its bounded cold-cache and transfer-ring mechanism, passed Checkpoint A and final complete-PR review with safety `YES`, and merged through PR #21 as `c5512bc073ae7aab4a14773028828e516e16f3f6`. No Phase 3 waiver weakens any later correctness or performance gate.

Phase 6 issue #22 completed positional GGUF storage, deferred routed loading, synchronous storage-to-cold integration, and corrected standing evidence. Checkpoint A returned `PASS`, safety `YES`, at comment `5133647261`; final complete-PR review returned `PASS`, safety `YES`, with no required delta at comment `5134171772`. PR #23 squash-merged into `main` as `66ab6dba60b55ce47d0ecf94fcf88a778df9cdc6` with nested `llama.cpp` head `7a606dd4e11a108929f799253809a904f55feae4`.

Phase 7 issue #24 completed the bounded asynchronous Linux I/O, direct-I/O fallback, priority/single-flight, CUDA event/dependency, cancellation/unload, overlap, and standing-evidence work. Checkpoint A, Checkpoint B, and final complete-PR review returned `PASS`, safety `YES`, at comments `5135836934`, `5140081178`, and `5140490542`. PR #25 squash-merged into `main` as `97ef68d787c54b443eac72a3480fe70eba88d8dd` with nested `llama.cpp` head `b71e40f91b1a0dab578d56ac733211453704d674`.

Phase 8 issue #26 completed explicit `PROMOTE_AND_GPU`, `CPU_FALLBACK`, deterministic `AUTO`, bounded background promotion, fail-closed production evidence, and descriptor-only cold-cache bootstrap scaling. Checkpoints A, B, and C returned safety `YES` at comments `5141694340`, `5144721775`, and `5146173479`; the mandatory final complete-PR review returned `PASS_WITH_NOTES`, safety `YES`, with no required delta at comment `5146545713`. PR #27 squash-merged into `main` as `05c38f0f11bd33c3654a3d9b2c3c6aa32e7f4c35` with nested `llama.cpp` head `dc4d50c68378d908131b518662160fdd08f4e005`. The authoritative manifest is `results/2026-07-31/skynet/phase8-miss-execution/phase8-manifest.json`. Phase 9 has not begun.

## Final acceptance criteria

The authoritative final acceptance criteria are in [Phase 15](docs/plan/11-scaling-and-hardening.md#final-acceptance-criteria).
