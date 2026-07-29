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

Then inspect the current Git branch, commit history, open issues/PRs, and any linked `llama.cpp` checkout.

## Source-of-truth hierarchy

1. Tests and captured evidence establish observed behavior.
2. `docs/DECISIONS.md` establishes accepted architecture.
3. `PLAN.md` establishes sequence and exit gates.
4. `docs/MODELS_AND_VALIDATION.md` establishes models and validation requirements.
5. `docs/STATUS.md` establishes the current handoff state.
6. Chat messages are provisional until committed here.

When sources conflict, stop and document the conflict. Do not silently choose one.

## Status markers

Use these exact labels in plans and design notes:

- `ACCEPTED`
- `OPEN`
- `SPECULATIVE`
- `REJECTED`
- `OBSERVED`
- `BLOCKED`

Never present an `OPEN` or `SPECULATIVE` item as decided.

## Required issue-design workflow

For every non-trivial implementation, refactor, migration, performance investigation, or change to project source-of-truth documents, the design-authority ChatGPT session must use `.agents/skills/design-github-issue/SKILL.md` before Codex implementation begins.

The design session must:

1. inspect the repository and relevant external evidence before prescribing changes;
2. classify observed facts, accepted decisions, open questions, speculation, and blockers explicitly;
3. use available helper skills such as brainstorming, systematic debugging, plan writing, or verification when useful, without depending on their availability;
4. resolve material design and scope decisions or create a design/investigation issue instead of guessing;
5. define bounded phases, model capability classes, validation commands, review gates, and restart conditions;
6. search for overlapping issues and prior attempts;
7. create or update a complete GitHub issue that is understandable without private chat context;
8. apply exactly one GitHub workflow label: `design-required`, `investigation-required`, `execution-ready`, `in-progress`, or `blocked`.

Only an issue carrying the `execution-ready` label may enter the implementation workflow. If architecture or other durable decisions change, update the repository source of truth before implementation or make that update an explicit gated prerequisite.

## GitHub issue workflow labels

Use these exact GitHub labels as mutually exclusive workflow states for every non-trivial open issue:

- `design-required`: material architecture, scope, or validation decisions remain unresolved.
- `investigation-required`: additional evidence or experimentation is required before the issue can be fully designed.
- `execution-ready`: the issue is a complete, self-contained execution contract with no unresolved material blocker.
- `in-progress`: an agent or human is actively executing the approved issue.
- `blocked`: execution cannot progress until a documented external dependency, failed gate, or design defect is resolved.

Apply the labels using these rules:

1. Exactly one workflow-state label must be present on each non-trivial open issue.
2. Assign `execution-ready` only when the issue contains an explicit goal, authoritative context, scope, out-of-scope boundaries, bounded execution steps or constraints, validation requirements, and testable exit criteria.
3. Do not assign `execution-ready` while any material design question, dependency, or validation strategy remains unresolved.
4. Codex may start work only from an `execution-ready` issue. At execution start, replace `execution-ready` with `in-progress` and add a phase-start comment before editing.
5. When progress is impossible, replace the current workflow label with `blocked` and document the evidence, owner or dependency, and exact restart condition in an issue comment.
6. After a blocker is resolved, return the issue to `execution-ready` when execution must restart from the contract, or to `in-progress` when the existing phase may safely resume.
7. Close the issue when all exit criteria are supported by committed evidence. A separate `completed` label is unnecessary.
8. Uppercase status markers such as `ACCEPTED`, `OPEN`, and `BLOCKED` remain document semantics; lowercase workflow labels are GitHub machine-readable state.

## Required execution workflow

For every non-trivial implementation, refactor, migration, performance investigation, or change to project source-of-truth documents, use the `spec-driven-codex-loop` skill in `.agents/skills/spec-driven-codex-loop/SKILL.md`.

The mandatory workflow is:

1. obtain an approved GitHub issue containing the complete design and execution contract before implementation;
2. use the issue as the durable control surface and audit trail;
3. execute exactly one bounded phase at a time;
4. add a phase-start issue update before editing;
5. validate and commit the phase as an independently reviewable outcome;
6. use `codex-github-operations` to publish and verify the exact phase commit and perform required GitHub control-plane operations;
7. add the actual results, evidence, deviations, and authoritative full commit SHA to the issue;
8. use `codex-independent-review` to review that exact published phase in a fresh isolated read-only context;
9. block progression on `FAIL` or `BLOCKED` review verdicts;
10. request a separate top-reasoning ChatGPT review of the complete PR and issue history before merge.

Do not implement a non-trivial task from an underspecified prompt. When implementation exposes a material flaw in the specification, architecture, phase decomposition, validation strategy, or governing skills, stop and revise those artifacts. Prefer a clean, traceable restart over layering compensating patches on an invalid foundation.

Trivial typo-only edits may skip the full issue loop unless the user explicitly requests it, but all source-of-truth and Git rules still apply.

## Codex operational skill routing

`spec-driven-codex-loop` is the workflow orchestrator. It owns sequencing, workflow-state transitions, phase boundaries, progression, and handoff, but it must delegate operational transport decisions:

- `.agents/skills/codex-github-operations/SKILL.md` owns local Git publication, exact remote-SHA verification, GitHub issue/label/comment/PR transport selection, and connector handoff.
- `.agents/skills/codex-independent-review/SKILL.md` owns reviewer isolation, reviewer-transport selection, transport fallback, evidence inspection, and verdict structure.

For these responsibilities, the dedicated operational skills supersede transport-specific examples or assumptions elsewhere in repository skills.

In particular:

- local `git` is authoritative for branch creation, commit creation, fetch, push, and SHA verification;
- the connected GitHub app or connector is preferred for issue, label, comment, and pull-request operations;
- GitHub CLI `gh` is an optional fallback unless an approved issue explicitly requires a CLI-only capability;
- failure of one optional GitHub or reviewer transport is not a phase implementation failure when an allowed alternative exists;
- a reviewer-launcher or sandbox failure must be retried through another fresh isolated review transport before the review is considered globally blocked;
- the main executor must never replace the independent reviewer.

Issues should declare required operational capabilities and gates, not duplicate product-specific transport procedures unless a particular transport is itself part of the task.

## Working method

### Before implementation

- Identify the current phase and exit gate in `PLAN.md`.
- Confirm the exact upstream `llama.cpp` commit and local diff.
- Confirm model/checkpoint revisions and checksums.
- State the smallest independently verifiable step.
- Identify which prior-art code will be inspected and its license.
- Add or update an issue with scope, deliverables, and validation if issue tracking is in use.

### During implementation

- Make one architectural step per commit where practical.
- Keep storage, cache mechanism, policy, transport, and execution separate.
- Add tests with the implementation, not later.
- Add telemetry before optimizing a path.
- Preserve the baseline path for A/B comparison.
- Record commands and results in machine-readable form.
- Do not report performance without exact revisions and configuration.

### At the end of a session

- Update `docs/STATUS.md`.
- Update completed checkboxes and evidence in `PLAN.md`.
- Record new decisions or reopened decisions in `docs/DECISIONS.md`.
- Update model commands/results in `docs/MODELS_AND_VALIDATION.md`.
- Commit all source-of-truth changes.
- Leave the working tree clean or clearly document intentional uncommitted work.

## Architectural constraints

Agents must not:

- replace the final design with a page-cache-only implementation;
- use graph-temporary staging memory as persistent expert storage;
- infer backing files via `/proc/self/maps`;
- pin the complete cold cache without an explicit bounded configuration;
- add global singleton state that prevents multiple models or devices;
- mix cache policy with CUDA or I/O implementation;
- change selected expert IDs or routing weights;
- reorder top-k accumulation without an approved numerical decision;
- create a new expert file format before Phase 14 evidence;
- make N+1 prefetch mandatory without trace evidence;
- silently downgrade unsupported configurations;
- claim CUDA/UMA support from compilation alone;
- copy a prior fork wholesale;
- import third-party code without license and attribution review;
- combine K3 model support, CPU, CUDA, disk, and policies into one upstream PR.

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

Every phase that changes execution must compare with the monolithic baseline.

Hard failures include:

- NaN or Inf not present in baseline;
- invalid expert ID;
- stale slot generation;
- missing projection or scale;
- cache metadata/content disagreement;
- use-after-free during unload/cancellation;
- nondeterministic result caused by asynchronous completion order;
- unexplained tokenization or EOS change;
- hidden unbounded memory growth.

Tests must include repeated warm runs because prior work failed across compute epochs.

## Performance requirements

Do not optimize from hit rate alone. Record:

- prompt/decode throughput;
- p50/p95/p99 token latency;
- hot, cold, and disk hits;
- bytes moved per tier;
- disk and H2D wait/overlap;
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
5. adapt ownership/lifetime to this architecture;
6. preserve attribution;
7. benchmark against the unmodified baseline.

Primary references are listed in `docs/PRIOR_ART.md`.

## Upstream `llama.cpp` integration

- Pin an exact K3 PR commit; do not develop against an unrecorded moving head.
- Follow upstream `AGENTS.md` and `CONTRIBUTING.md` in the `llama.cpp` checkout.
- Keep upstream PRs small and independently testable.
- The first upstream change for a new capability should follow maintainers' requested backend scope; backend follow-ups should be separate unless an RFC explicitly agrees otherwise.
- Disclose AI assistance according to upstream policy.
- Never force-push or rewrite shared history without explicit user approval.

## Git behavior

- Do not commit generated model weights, GGUFs, large traces, or benchmark binaries.
- Commit manifests, scripts, summarized evidence, and small deterministic fixtures.
- Use explicit paths when staging.
- Avoid unrelated formatting changes.
- Commit messages should describe one intentional outcome.
- Direct commits to the default branch require explicit user instruction; otherwise use a feature branch and draft PR.
- Codex publication and GitHub control-plane operations must follow `.agents/skills/codex-github-operations/SKILL.md`.

## Current immediate task

Follow Phase 1 in `PLAN.md`. Do not start cache implementation until the monolithic F16 and hybrid MXFP4 baselines are reproducible and committed as evidence.