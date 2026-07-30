---
name: design-github-issue
description: Turn a non-trivial repository request into the smallest complete GitHub execution contract with bounded phases, objective validation, explicit risk profile, and clear checkpoint and restart rules.
---

# Design an Implementation-Ready GitHub Issue

## Responsibility

Use this skill only in the design-authority role before execution starts or when execution returns for a material contract correction.

This skill owns:

- resolving material design and validation decisions;
- defining phase scope, invariants, exclusions, commands, evidence, checkpoints, and acceptance;
- selecting `STANDARD` or `HIGH_ASSURANCE`;
- creating or correcting the controlling issue and its workflow state.

It does not implement code, operate the execution branch, publish phase commits, or perform independent review.

## Load only design context

Start with the bootstrap context from `AGENTS.md`: `AGENTS.md`, `docs/STATUS.md`, and the relevant request or controlling issue.

Then inspect only what is needed to settle the design:

- relevant source, tests, build metadata, and configuration;
- exact decision and plan sections affected;
- pinned dependency, model, artifact, hardware, and license facts;
- current overlapping issues, PRs, and evidence when they can change scope or feasibility.

Do not preload the executor, GitHub-operations, or reviewer skills. Do not require a future executor to read this design skill.

Prefer the prior phase's final manifest, exact accepted head, and final accepted review over complete prior issue, PR, and result-directory history. Read older records only when an unresolved finding or disputed fact materially affects the new contract.

## The issue is a context packet, not an archive

A fresh executor must be able to begin without private chat context, but that does not require copying the repository into the issue.

The issue should contain the phase-specific delta and reference authoritative context precisely:

- cite decision IDs and exact document sections instead of repeating accepted architecture;
- cite exact files, symbols, manifests, commits, and review comments instead of listing whole directories or complete histories;
- summarize prior-phase outcomes once and link the authoritative final evidence;
- state new constraints, exceptions, and unresolved risks explicitly;
- never list all workflow skills as required reading; `AGENTS.md` routes roles lazily;
- avoid copying generic workflow, review, GitHub transport, and failure procedures owned by skills.

Whole-document or whole-history reading is justified only when the entire object is authoritative to the current decision and no smaller reference is sufficient.

## Readiness states

Use exactly one workflow state for a non-trivial open issue:

- `execution-ready`: design and validation are complete enough to execute;
- `design-required`: a material product, architecture, scope, ownership, behavior, or validation decision remains open;
- `investigation-required`: evidence is needed before the design can be selected;
- `blocked`: a required external capability is unavailable and no permitted alternative or handoff exists;
- `in-progress`: execution has started under an approved contract.

Do not label an issue `execution-ready` while a material decision remains unresolved.

## Design workflow

### 1. Establish the observable outcome

State:

- what must become observably true;
- why it matters now;
- the current limitation and evidence;
- the boundary of the requested change.

Do not begin from a preferred implementation unless architecture is already accepted.

### 2. Build a fact and decision ledger

Classify important statements as:

- `OBSERVED`: supported by code, tests, logs, measurements, or committed evidence;
- `ACCEPTED`: approved design or constraint;
- `OPEN`: unresolved decision;
- `SPECULATIVE`: hypothesis requiring evidence;
- `REJECTED`: ruled-out approach;
- `BLOCKED`: unavailable required capability with no current alternative.

Never convert `OPEN` or `SPECULATIVE` into an implementation requirement.

### 3. Resolve material unknowns

Resolve questions that change external behavior, compatibility, architecture, ownership, lifetime, numerical tolerance, backend support, failure behavior, validation, trust boundaries, licensing, or upstream strategy.

When root cause or feasibility is unknown, create a bounded investigation contract rather than guessing a fix.

Record durable cross-phase decisions in `docs/DECISIONS.md`; keep issue-local choices in the issue. Do not duplicate the same decision prose in both places.

### 4. Select the execution profile

`STANDARD` is the default. Use it when bounded phases have strong local validation and intermediate defects are cheap to detect and correct.

Under `STANDARD`:

- every phase validates and commits;
- independent review occurs only at declared risk checkpoints and final handoff;
- editorial inconsistencies, optional hardening, and theoretical malformed-input bypasses are notes unless the issue makes them material.

Select `HIGH_ASSURANCE` only when explicitly justified by architecture, concurrency or lifetime, numerical semantics, backend correctness, persistent state, cache coherence, security, or another risk where every phase needs independent review.

Do not choose `HIGH_ASSURANCE` because an issue is merely large. Split oversized work instead.

### 5. Define bounded phases

Each phase must identify:

- one primary outcome;
- exact inputs and permitted scope;
- expected artifacts or behavior;
- objective validation and success criteria;
- explicit exclusions;
- commit boundary or documented no-code result;
- checkpoint membership;
- required model capability only when it differs from the issue default.

A phase is too large when it spans unrelated subsystems or a failure would not identify which decision or edit caused it.

### 6. Design native build and test integration

Prefer the repository-native build and test system.

For native code, specify existing or new CMake targets, presets, incremental build commands, and CTest entries where applicable. Permit the build metadata needed by the declared deliverable.

Do not force the executor to create durable manual compiler or linker commands by accidentally excluding `CMakeLists.txt` or equivalent build files from an allowlist. If a manual compile is genuinely required, state why it is exceptional and whether it is disposable or committed.

Do not optimize the issue around minimizing local CPU time. Prefer deterministic native builds over bespoke orchestration that costs additional reasoning and maintenance.

### 7. Design validation

Validation must prove the outcome, not merely compilation. Define only applicable items:

- exact baseline and candidate revisions;
- native build and test commands;
- unit, integration, regression, repeated-run, negative, and failure-path tests;
- numerical comparisons and tolerances;
- performance methodology, budgets, order, sample count, and resource metrics;
- environment, hardware, model, artifact, and dataset identities;
- required evidence paths and schemas;
- required versus optional checks.

Use exact commands when they are known and inspected. Do not make replaceable tool choices mandatory when another capability provides equivalent evidence.

### 8. Define review checkpoints and materiality

For `STANDARD`, group phases when the combined checkpoint remains independently understandable and testable. Add a checkpoint when work introduces material architecture, ownership or lifetime, persistent state, numerical behavior, backend execution, broad cross-cutting refactoring, or decision-driving performance evidence.

For `HIGH_ASSURANCE`, every phase is normally a checkpoint.

Each checkpoint must identify:

- covered phases and exact commit range semantics;
- artifacts and evidence to inspect;
- validation to reproduce or verify;
- scope and unexpected-change checks;
- progression criteria;
- material findings that require `FAIL`;
- non-material findings that should be `PASS_WITH_NOTES`;
- authoritative structured state, if any;
- explicit security or adversarial-input boundary, if any.

Do not copy the reviewer procedure into the issue. State the checkpoint-specific risks and criteria; `codex-independent-review` owns the review method.

### 9. Define authoritative state and attestations

When machine-enforced closeout is needed:

- choose one structured authoritative record;
- bind it to the exact reviewed commit or range and accepted verdict;
- keep pre-review technical verification separate from post-review attestation;
- keep any post-review update small and non-circular;
- treat summaries, issue comments, labels, and PR descriptions as derived unless explicitly declared authoritative.

Do not make multiple Markdown renderings or comments independent security authorities under `STANDARD`.

### 10. Define failure and restart semantics

Distinguish:

- implementation failure: fix a bounded material delta while remaining in progress;
- design defect: return to `design-required`;
- evidence gap: return to `investigation-required`;
- operational degradation: use an alternative transport or handoff;
- editorial discrepancy: correct or carry as a note unless explicitly material;
- real blocker: meaningful progress is impossible and no permitted alternative exists.

For `STANDARD`, after two consecutive review failures in substantially the same validation, attestation, parser, documentation-sync, or bookkeeping mechanism, stop automatic compensating patches and return to design authority. A third corrective review requires an explicit decision or a materially different defect.

### 11. Define required capabilities

State capabilities, not product preference:

- branch publication required: yes or no;
- draft PR required before which checkpoint;
- independent review required at which checkpoints;
- local command execution required for review: yes or no;
- external hardware or data required: yes or no;
- connector-capable handoff allowed: yes or no.

Exact transport selection belongs to `codex-github-operations` and `codex-independent-review`.

### 12. Search for overlap

Inspect only current or plausibly overlapping issues, PRs, branches, and commits. Link superseded attempts. Reuse an existing issue only when its contract still matches; otherwise create a clean contract.

## Execution-ready quality gate

Before applying `execution-ready`, verify that the issue answers:

- What exact outcome must become true?
- What current evidence matters?
- Which exact decisions, files, sections, commits, and artifacts govern this phase?
- What is new, in scope, excluded, invariant, and rejected?
- Which profile applies and why?
- What are the bounded phases, native build path, validation, and objective gates?
- Where are the risk checkpoints and what is material?
- Which structured state is authoritative?
- What causes correction, redesign, investigation, blocking, or restart?
- Can a fresh executor start without reconstructing prior history or rereading unrelated context?

If a material answer is missing, do not use `execution-ready`.

## Lean issue template

```markdown
# <Outcome-oriented title>

## Readiness

**State:** EXECUTION_READY | DESIGN_REQUIRED | INVESTIGATION_REQUIRED | BLOCKED  
**Execution profile:** STANDARD | HIGH_ASSURANCE  
**Repository/base:** `<repo>` / `<exact base>`  
**Execution branch:** `<branch>`

## Goal and motivation

<Observable outcome and why it matters.>

## Current evidence

- **OBSERVED:** <only facts needed by this phase>
- **ACCEPTED:** <phase-specific decisions or decision IDs>
- **OPEN:** <none or unresolved item>
- **REJECTED:** <relevant rejected shortcut>

## Context packet

- `AGENTS.md`
- `docs/STATUS.md`
- `<exact decision or plan section>`
- `<exact source/test/build paths>`
- `<prior final manifest and accepted review, when needed>`

## Scope and invariants

### In scope
- <item>

### Out of scope
- <item>

### Invariants
- <item>

## Native build and validation

- Build targets/files: <exact targets and permitted metadata>
- Commands: `<exact commands>`
- Evidence: `<paths and schemas>`
- Success criteria: <objective gates>

## Phases and checkpoints

### Phase 1 — <outcome>
- Scope: <bounded files or subsystem>
- Deliverable: <artifact or behavior>
- Validation: <commands and criteria>
- Checkpoint: <name or none>

## Materiality and authoritative state

- Authoritative record: <path or none>
- `FAIL`: <material conditions>
- `PASS_WITH_NOTES`: <non-material conditions>
- Threat boundary: <normal inputs or explicit adversarial boundary>

## Failure, restart, and completion

- Correction/design/investigation/blocker rules: <concise rules>
- Final acceptance: <observable complete state>
- Required operational capabilities: <capabilities only>
```

Add sections only when they carry issue-specific information. Do not copy generic skill text into the issue.
