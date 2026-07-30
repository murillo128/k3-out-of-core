# AGENTS.md

Instructions for ChatGPT, Codex, and other coding agents working in this repository.

## Mission

Implement the K3 out-of-core expert runtime described by the committed architecture and plan. This repository is the cross-session source of truth. Chat history is provisional when it conflicts with committed state.

## Load context progressively

For non-trivial work, load this bootstrap context once:

1. `AGENTS.md`;
2. `docs/STATUS.md`;
3. the controlling GitHub issue body.

Then load only the context needed for the active role and phase:

- exact decision IDs, plan sections, validation sections, manifests, or evidence linked by the issue;
- relevant source, tests, build files, and pinned dependency state;
- the one workflow skill that owns the current action.

Do not preload every repository document, every skill, complete prior issue or pull-request histories, or whole result directories. Read a complete document only when the issue makes the whole document authoritative or section-level reading cannot resolve the task.

On session resume, verify branch, `HEAD`, worktree state, and new issue comments since the recorded handoff. Do not replay unchanged history. Reuse already inspected facts and file contents while their path and commit or blob identity remain unchanged.

## Source-of-truth hierarchy

1. Tests and captured evidence establish observed behavior.
2. `docs/DECISIONS.md` establishes accepted architecture.
3. `PLAN.md` and linked `docs/plan/` sections establish sequence and exit gates.
4. `docs/MODELS_AND_VALIDATION.md` establishes model and validation requirements.
5. `docs/STATUS.md` establishes the current handoff state.
6. The controlling issue establishes the bounded execution contract for its scope.
7. Chat messages are provisional until committed or recorded in the issue.

When sources materially conflict, stop and document the conflict. Do not silently choose one.

Use these status markers exactly in plans and design notes: `ACCEPTED`, `OPEN`, `SPECULATIVE`, `REJECTED`, `OBSERVED`, and `BLOCKED`. Never present an `OPEN` or `SPECULATIVE` item as decided.

## Role routing and instruction ownership

Load skills lazily by role:

- design authority: `.agents/skills/design-github-issue/SKILL.md`;
- main executor: `.agents/skills/spec-driven-codex-loop/SKILL.md`;
- Git and GitHub mutation or publication: `.agents/skills/codex-github-operations/SKILL.md`;
- independent checkpoint or final review: `.agents/skills/codex-independent-review/SKILL.md`.

Do not read a role skill merely because it exists. The executor does not need the design or reviewer procedure; the reviewer does not need the executor or GitHub-operations procedure.

`AGENTS.md` owns repository-wide invariants and routing. Each skill owns its procedure. Issues own phase-specific scope, commands, and gates. Avoid copying the same rule into all three places; reference the owning source and record only the phase-specific delta.

`STANDARD` is the default execution profile. `HIGH_ASSURANCE` is opt-in and must be explicit. Detailed profile, label, comment, checkpoint, publication, and review procedures belong to the workflow skills, not this file.

Trivial typo-only edits may skip the complete issue workflow unless the user explicitly requests it, but repository safety and source-of-truth rules still apply.

## Context and inference economy

Reasoning and tool exploration are project resources. Use them where they reduce technical risk, not to reconstruct known state repeatedly.

- Maintain a compact working ledger: controlling issue, active phase, branch, exact project and nested heads, documents or sections read, and last accepted checkpoint.
- Re-read an input only when its identity changed, new evidence affects it, or a conflict requires broader inspection.
- Prefer the previous phase's final machine-readable manifest and accepted review over complete historical issue, PR, and results traversal. Read older records only when the current issue identifies an unresolved dependency or dispute.
- Prefer exact paths, symbols, commands, and section anchors supplied by the issue over repository-wide searches.
- Under `STANDARD`, group routine progress reporting at session boundaries and checkpoints unless a failure, scope change, or handoff needs an immediate record.
- Do not create redundant summaries of authoritative data. Link or identify the authoritative record and describe only changes, deviations, and conclusions.

## Native build and test tooling

Prefer repository-native build and test systems over bespoke compiler, linker, or test orchestration.

- Use existing CMake targets, presets, incremental builds, and CTest integration where available.
- A persistent C or C++ helper, probe, fixture generator, or test executable should normally be a CMake target rather than a script that reconstructs include paths, library order, `rpath`, or linker flags.
- Do not invent committed manual `c++`, `-L`, `-l`, or `-Wl,...` command construction merely to reduce local CPU time or avoid an incremental native build. Local computation is cheaper than repeated agent reasoning and custom build maintenance.
- Ad-hoc compiler commands are acceptable for disposable investigation. They are not the default durable implementation or validation path.
- If the approved deliverable requires a new native target, the issue should permit the necessary build metadata. If its allowlist accidentally excludes required build files, return for a bounded contract correction instead of building a permanent workaround.
- When the issue provides an exact native command, run it before designing an alternative. Classify actual failures rather than speculating about tool behavior.

## Implementation discipline

Before changing a phase:

- identify the smallest independently verifiable outcome and its exit gate;
- confirm exact upstream or nested revisions only when the phase touches or validates them;
- confirm model, artifact, and checksum inputs only when the phase consumes them;
- inspect prior art and licensing only when code or design is being reused.

During implementation:

- make one architectural step per commit where practical;
- keep storage, cache mechanism, policy, transport, and execution separate;
- add tests with the implementation;
- add telemetry before optimizing a path;
- preserve the baseline path for A/B comparison;
- record reproducible commands and results in machine-readable form;
- do not report performance without exact revisions and configuration;
- avoid unrelated cleanup and formatting.

At the end of an implementation session:

- update `docs/STATUS.md` only when the project handoff state changes, including exact relevant commit SHAs;
- update only completed tasks, exit gates, and affected evidence in `PLAN.md` or linked plan sections;
- update `docs/DECISIONS.md` only when a decision is added, changed, or reopened;
- update model, repository, artifact, and manifest records only when their inputs or evidence changed;
- commit required source-of-truth changes;
- leave the working tree clean or clearly document intentional uncommitted work.

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

Do not optimize from hit rate alone. Record prompt and decode throughput; p50, p95, and p99 token latency; tier requests and hits; bytes moved; disk and H2D wait and overlap; CPU miss compute; useful and wasted prefetch; and RAM, pinned RAM, VRAM, and UMA usage.

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
