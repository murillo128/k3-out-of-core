# AGENTS.md

Instructions for Codex and other coding agents working in this repository.

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

## Current immediate task

Follow Phase 1 in `PLAN.md`. Do not start cache implementation until the monolithic F16 and hybrid MXFP4 baselines are reproducible and committed as evidence.
