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

Phase 3's original 24-cell resident performance confidence gate remains recorded as a 22/24 raw failure. Design authority accepted the Phase 3 technical exit as `PASS_WITH_NOTES` for project progression only; Phase 3 is merged and its evidence remains immutable. Phase 4 issue #17 has completed its synchronous mechanism, Checkpoint A, and standing evidence; the final complete-PR review remains required before merge. No Phase 3 waiver weakens the Phase 4 correctness gate or any later performance gate.

## Final acceptance criteria

The authoritative final acceptance criteria are in [Phase 15](docs/plan/11-scaling-and-hardening.md#final-acceptance-criteria).
