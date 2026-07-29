# AGENTS.md

Instructions for ChatGPT, Codex, and other coding agents working in this repository.

## Mission

Implement the K3 out-of-core expert runtime described in `README.md` and `PLAN.md`. This repository is the cross-session source of truth. Do not infer architectural changes from chat history when committed documents say otherwise.

## Required reading order

Before changing code or plans, read:

1. `README.md`
2. `docs/STATUS.md`
3. `docs/DECISIONS.md`
4. `PLAN.md`
5. `docs/MODELS_AND_VALIDATION.md`
6. `docs/PRIOR_ART.md`
7. `.agents/skills/design-github-issue/SKILL.md`
8. `.agents/skills/spec-driven-codex-loop/SKILL.md`
9. `.agents/skills/codex-github-operations/SKILL.md`
10. `.agents/skills/codex-independent-review/SKILL.md`

Then inspect the current Git branch, commit history, open issues and pull requests, and any linked `llama.cpp` checkout.

## Source-of-truth hierarchy

1. Tests and captured evidence establish observed behavior.
2. `docs/DECISIONS.md` establishes accepted architecture.
3. `PLAN.md` establishes sequence and exit gates.
4. `docs/MODELS_AND_VALIDATION.md` establishes models and validation requirements.
5. `docs/STATUS.md` establishes the current handoff state.
6. Chat messages are provisional until committed here.

When sources conflict, stop and document the conflict. Do not silently choose one.

## Status markers

Use these exact markers in plans and design notes:

- `ACCEPTED`
- `OPEN`
- `SPECULATIVE`
- `REJECTED`
- `OBSERVED`
- `BLOCKED`

Never present an `OPEN` or `SPECULATIVE` item as decided.

## Agent workflow routing

For non-trivial implementation, refactoring, migration, performance work, investigations, or source-of-truth changes:

- the design-authority session uses `.agents/skills/design-github-issue/SKILL.md`;
- the main executor uses `.agents/skills/spec-driven-codex-loop/SKILL.md`;
- Git and GitHub operations use `.agents/skills/codex-github-operations/SKILL.md`;
- independent checkpoint and final reviews use `.agents/skills/codex-independent-review/SKILL.md`.

`AGENTS.md` defines repository-wide invariants and routes work to skills. It intentionally does not duplicate operational procedures owned by those skills. When instructions overlap, the dedicated skill owns its stated responsibility.

Trivial typo-only edits may skip the complete issue workflow unless the user explicitly requests it, but repository safety and source-of-truth rules still apply.

## Execution profiles

`STANDARD` is the default profile.

- Validate and commit each bounded phase.
- Publish exact commits when needed for preservation, collaboration, or review.
- Require independent review only at checkpoints declared by the issue and at final handoff.
- Treat recoverable tool or transport failures as degraded operation or handoff, not as implementation failure.

`HIGH_ASSURANCE` is opt-in and must be explicitly selected by the issue or user.

Use it for high-risk work such as architecture, concurrency and lifetime, numerical formats, routing semantics, CUDA/backend correctness, persistent storage, cache coherence, security, or other changes where every phase warrants independent review.

The detailed profile rules belong to `design-github-issue` and `spec-driven-codex-loop`.

## GitHub workflow labels

Use exactly one workflow-state label for each non-trivial open issue:

- `design-required`
- `investigation-required`
- `execution-ready`
- `in-progress`
- `blocked`

Labels summarize durable workflow state; they are not a substitute for issue evidence. `blocked` is reserved for a real unresolved condition that prevents meaningful technical progress and has no permitted alternative or handoff. Tool-specific failures are not automatically blockers.

Label mutation, verification, and transport selection belong to `codex-github-operations`.

## Required execution outcomes

Every non-trivial execution must provide:

1. an approved, self-contained issue contract;
2. bounded implementation phases;
3. validation appropriate to each phase;
4. intentional, reviewable commits;
5. recorded evidence and deviations;
6. independent reviews at the issue-declared checkpoints;
7. a final external review of the complete pull request and issue history before merge.

Do not implement from an underspecified prompt. When implementation exposes a material defect in the specification, architecture, phase decomposition, or validation strategy, return to design instead of accumulating compensating patches.

## Working method

### Before implementation

- Identify the current phase and exit gate in `PLAN.md`.
- Confirm the exact upstream `llama.cpp` commit and local diff.
- Confirm model and checkpoint revisions and checksums.
- State the smallest independently verifiable step.
- Identify prior-art code to inspect and its license.

### During implementation

- Make one architectural step per commit where practical.
- Keep storage, cache mechanism, policy, transport, and execution separate.
- Add tests with the implementation, not later.
- Add telemetry before optimizing a path.
- Preserve the baseline path for A/B comparison.
- Record commands and results in machine-readable form.
- Do not report performance without exact revisions and configuration.

### At the end of an implementation session

- Update `docs/STATUS.md` only when the project handoff state changes, including exact relevant commit SHAs.
- Update only completed tasks, exit gates, and evidence affected in `PLAN.md`.
- Record decisions in `docs/DECISIONS.md` only when a decision is added, changed, or reopened.
- Update `docs/MODELS_AND_VALIDATION.md` only when model commands, validation requirements, or evidence change.
- Update repository/artifact records and machine-readable manifests only when revisions or artifacts change.
- Commit required source-of-truth changes.
- Leave the working tree clean or clearly document intentional uncommitted work.

## Architectural constraints

Agents must not:

- replace the final design with a page-cache-only implementation;
- use graph-temporary staging memory as persistent expert storage;
- infer backing files through `/proc/self/maps`;
- pin the complete cold cache without an explicit bounded configuration;
- add global singleton state that prevents multiple models or devices;
- mix cache policy with CUDA or I/O implementation;
- change selected expert IDs or routing weights;
- reorder top-k accumulation without an approved numerical decision;
- create a new expert file format before Phase 14 evidence;
- make N+1 prefetch mandatory without trace evidence;
- silently downgrade unsupported configurations;
- claim CUDA or UMA support from compilation alone;
- copy a prior fork wholesale;
- import third-party code without license and attribution review;
- combine K3 model support, CPU, CUDA, disk, and policies into one upstream pull request.

## Required abstractions

Implementation should converge on components equivalent to:

```text
ExpertWeightProvider
ExpertDirectory
HotExpertCache
ColdExpertCache
ExpertStorage
ExpertScheduler
ExpertTransport
CachePolicy
PrefetchPolicy
MissExecutionPolicy
Telemetry
```

Names may follow GGML conventions, but responsibilities must remain separated.

## Correctness requirements

Every phase that changes execution must compare against the monolithic baseline.

Hard failures include:

- NaN or Inf not present in the baseline;
- invalid expert ID;
- stale slot generation;
- missing projection or scale;
- cache metadata and content disagreement;
- use-after-free during unload or cancellation;
- nondeterminism caused by asynchronous completion order;
- unexplained tokenization or EOS changes;
- hidden unbounded memory growth.

Tests must include repeated warm runs because prior work failed across compute epochs.

## Performance requirements

Do not optimize from hit rate alone. Record:

- prompt and decode throughput;
- p50, p95, and p99 token latency;
- hot, cold, and disk hits;
- bytes moved per tier;
- disk and H2D wait and overlap;
- CPU miss compute;
- useful and wasted prefetch;
- RAM, pinned RAM, VRAM, and UMA usage.

A strategy that improves warm average throughput but worsens cold or tail latency must be described accurately.

## Prior-art reuse protocol

Before porting code:

1. record repository, URL, branch, commit, and license;
2. identify the smallest reusable unit;
3. explain why the original design did or did not merge;
4. write an isolated test;
5. adapt ownership and lifetime to this architecture;
6. preserve attribution;
7. benchmark against the unmodified baseline.

Primary references are listed in `docs/PRIOR_ART.md`.

## Upstream `llama.cpp` integration

- Pin an exact K3 pull-request commit; do not develop against an unrecorded moving head.
- Follow upstream `AGENTS.md` and `CONTRIBUTING.md` in the `llama.cpp` checkout.
- Keep upstream pull requests small and independently testable.
- Follow maintainers' requested backend scope for the first upstream change; separate backend follow-ups unless an RFC explicitly agrees otherwise.
- Disclose AI assistance according to upstream policy.
- Never force-push or rewrite shared history without explicit user approval.

## Git behavior

- Do not commit generated model weights, GGUFs, large traces, or benchmark binaries.
- Commit manifests, scripts, summarized evidence, and small deterministic fixtures.
- Use explicit paths when staging.
- Avoid unrelated formatting changes.
- Commit messages should describe one intentional outcome.
- Direct commits to the default branch require explicit user instruction; otherwise use a feature branch and draft pull request.

## Current work

Read `docs/STATUS.md` for the current phase, active issue, exact handoff state, and immediate next action. Do not encode a phase-specific task in this file.
