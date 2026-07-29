---
name: design-github-issue
description: Turn a non-trivial repository request into an approved, self-contained GitHub issue with bounded phases, objective validation, an explicit execution profile, risk-based review checkpoints, materiality rules, and clear restart conditions before Codex execution.
---

# Design an Implementation-Ready GitHub Issue

## Purpose

Use this skill in the design-authority session before `spec-driven-codex-loop` execution.

The issue is the durable contract. A fresh executor must be able to understand the goal, scope, evidence, phases, validation, checkpoints, trust boundaries, and exit criteria without private chat context.

## Core rules

Do not label an issue `execution-ready` while a material architecture, behavior, numerical, performance, ownership, dependency, or validation decision remains unresolved.

A long issue is not necessarily a good issue. Prefer the smallest complete contract that removes material ambiguity.

Design checkpoints to reduce material technical risk. Do not turn documentation, comments, labels, URLs, or other bookkeeping surfaces into security boundaries unless the project genuinely needs that threat model.

Under STANDARD, explicitly separate material exit-gate failures from editorial, derived-state, defense-in-depth, and theoretical adversarial concerns.

## Role boundary

The design-authority session may:

- inspect repository state, history, issues, pull requests, tests, and evidence;
- investigate facts needed for design;
- compare viable approaches;
- resolve scope and architecture;
- define phases, validation, execution profile, checkpoints, trust boundaries, materiality, and exit criteria;
- create or update the issue and workflow label.

It must not:

- start implementation while designing;
- present `OPEN` or `SPECULATIVE` items as accepted;
- prescribe uninspected files, APIs, hardware, models, or commands;
- encode replaceable tool choices as mandatory capabilities without reason;
- make every phase independently reviewed by default;
- create an issue whose success cannot be observed objectively;
- make arbitrary Markdown or GitHub prose authoritative without an explicit need;
- require STANDARD reviewers to defend against hostile or malformed inputs that the issue does not define.

## Readiness states

- `EXECUTION_READY`: design and validation are complete enough to execute.
- `DESIGN_REQUIRED`: material design or product decisions remain open.
- `INVESTIGATION_REQUIRED`: more evidence is needed before choosing the design.
- `BLOCKED`: a required external capability is unavailable and no permitted alternative or handoff exists.

Only `EXECUTION_READY` enters implementation.

## Design workflow

### 1. Establish the outcome

State:

- what must become observably true;
- why it matters now;
- the current limitation;
- the boundary of the requested change.

Do not begin from a preferred implementation.

### 2. Inspect context

Read `AGENTS.md` and the relevant source-of-truth documents. Inspect:

- current code, tests, scripts, fixtures, and configuration;
- recent commits and overlapping issues or PRs;
- pinned upstream branches and commits;
- model, dataset, hardware, dependency, and license assumptions;
- existing evidence and failed attempts.

Use repository and authoritative external evidence rather than memory when facts can change.

### 3. Build a fact and decision ledger

Classify important statements:

- `OBSERVED`: directly supported by code, tests, logs, measurements, or committed evidence;
- `ACCEPTED`: approved design or constraint;
- `OPEN`: unresolved decision;
- `SPECULATIVE`: unsupported hypothesis;
- `REJECTED`: ruled-out approach;
- `BLOCKED`: unavailable required capability with no current resolution.

Never convert `OPEN` or `SPECULATIVE` into implementation requirements.

### 4. Resolve material unknowns

Resolve questions that change:

- external behavior or compatibility;
- architecture and subsystem boundaries;
- ownership and lifetime;
- numerical tolerance;
- hardware and backend support;
- failure and fallback behavior;
- observability and validation;
- authoritative versus derived state;
- trust boundaries and adversarial assumptions;
- security, privacy, licensing, or upstream strategy.

When root cause or feasibility is unknown, create an investigation issue instead of guessing a fix.

### 5. Record durable decisions

When a decision affects future work broadly, update or plan an explicit update to repository source-of-truth documents.

Do not let the issue become the only durable location for important architecture.

### 6. Choose the execution profile

#### STANDARD

STANDARD is the default.

Choose it when:

- phases have strong local validation;
- intermediate commits are low or moderate risk;
- related phases can be reviewed together coherently;
- a faulty intermediate base is cheap to detect and correct.

Under STANDARD:

- every phase validates and commits;
- independent review happens at declared checkpoints;
- final external review is mandatory;
- review failures must be material to an explicit criterion or plausible normal-path use;
- editorial inconsistencies and theoretical malformed-input bypasses are normally notes, not blockers.

#### HIGH_ASSURANCE

Choose HIGH_ASSURANCE only when explicitly justified by risk.

Typical reasons:

- architecture or persistent format changes;
- concurrency, ownership, or lifetime;
- numerical encodings or routing semantics;
- CUDA or backend correctness;
- persistent storage or cache coherence;
- security-sensitive behavior;
- a faulty intermediate base would be expensive or unsafe.

Under HIGH_ASSURANCE, every phase is independently reviewed.

The issue must explain why HIGH_ASSURANCE is necessary. Do not select it solely because the issue is large; split oversized issues instead.

Do not declare HIGH_ASSURANCE merely because a verifier or parser can be tested adversarially. State the actual protected risk and threat model.

### 7. Define bounded phases

Each phase must have:

- one primary outcome;
- explicit inputs and permitted scope;
- expected artifacts or behavior;
- validation commands where knowable;
- objective success criteria;
- explicit exclusions;
- model capability class when relevant;
- a commit boundary or documented no-code result;
- checkpoint membership.

A phase is too large when it spans unrelated subsystems or failure would not reveal which decision or edit caused the problem.

### 8. Define review checkpoints

For STANDARD, group phases only when the combined checkpoint is still independently understandable and testable.

Good checkpoint examples:

- environment, fixture, and tokenizer reproducibility;
- numerical format and backend correctness;
- performance methodology and final closeout.

Require a dedicated checkpoint when a phase introduces:

- accepted architecture;
- concurrency or lifetime risk;
- persistent state or data format;
- numerical correctness risk;
- backend-specific execution behavior;
- broad cross-cutting refactoring;
- performance claims used for decisions.

For HIGH_ASSURANCE, each phase is a checkpoint.

Every checkpoint must identify:

- covered phases and commit range;
- artifacts to inspect;
- validation to reproduce or verify;
- scope and unexpected-change checks;
- progression criteria;
- material findings that require `FAIL`;
- non-material findings that should be `PASS_WITH_NOTES`;
- the authoritative state used for machine-enforced acceptance;
- any explicit adversarial-input or security boundary.

### 9. Define materiality

For each checkpoint, make the blocking boundary clear enough that a fresh reviewer does not have to invent it.

A STANDARD checkpoint should normally use `FAIL` only for:

- violation of an explicit invariant, threshold, accepted decision, or exit criterion;
- a plausible defect in normal execution or repository use;
- missing, false, ambiguous, or non-reproducible required technical evidence;
- unapproved scope, dependency, architecture, format, or behavior changes;
- unsafe progression from the reviewed commit.

A STANDARD checkpoint should normally use `PASS_WITH_NOTES` for:

- editorial wording, formatting, or bookkeeping inconsistencies;
- stale derived prose when authoritative state is correct and unambiguous;
- optional defense-in-depth improvements;
- theoretical bypasses that require deliberately malformed, duplicated, contradictory, or adversarial Markdown, comments, URLs, or metadata;
- robustness outside the issue's declared normal inputs and threat model.

If a textual surface is intended to be authoritative or security-sensitive, state that explicitly and justify why.

### 10. Design validation

Validation must prove the requested outcome, not merely compilation.

Define as applicable:

- exact baseline revision and configuration;
- reproduction commands;
- unit, integration, regression, and repeated-run tests;
- negative and failure-path tests;
- numerical comparison and tolerance;
- performance protocol and metrics;
- resource limits;
- telemetry and artifact paths;
- environment, hardware, model, and dataset identifiers;
- required versus optional checks.

Do not make an optional tool command an entry gate when another capability provides equivalent evidence.

Negative tests must be proportional to the selected profile and declared trust boundary. Do not ask STANDARD reviewers to enumerate arbitrary syntactic variants of non-authoritative prose.

### 11. Design authoritative state and attestations

When machine-enforced closeout or review attestation is required:

- choose one structured authoritative record, such as JSON or another schema-validated file;
- bind it to the exact reviewed commit or range and accepted verdict;
- separate the pre-review technical verifier from the post-review attestation update;
- keep the post-review update small and non-circular;
- treat `SUMMARY.md`, `STATUS.md`, issue comments, labels, and PR descriptions as derived communication unless explicitly declared otherwise;
- avoid semantic parsing of arbitrary Markdown or external comments as a security protocol;
- verify only the normal-path representations the workflow itself produces under STANDARD.

Do not require one checkpoint to prove its own future review has already happened. The pre-review state may be explicitly pending; a bounded post-review attestation completes the gate.

If external comment linkage is useful for audit, store an identifier or URL in the structured record. Do not make every possible textual rendering of that comment an independent source of truth.

### 12. Define operational capabilities

State capabilities rather than product-specific transports:

- branch publication required: yes or no;
- draft PR required before which checkpoint;
- independent review required at which checkpoints;
- local command execution required for review: yes or no;
- external hardware or data required: yes or no;
- connector-capable handoff allowed: yes or no.

Tool selection belongs to `codex-github-operations` and `codex-independent-review`.

A specific tool such as `gh` or a particular Codex sandbox may be mandatory only when that exact tool is part of the task or no equivalent capability exists.

### 13. Define failure, repeated-review, and restart semantics

Distinguish:

- implementation failure: fix a bounded material delta while remaining in progress;
- design defect: return to `design-required`;
- evidence gap: return to `investigation-required`;
- operational degradation: use an alternative or handoff;
- editorial or derived-state discrepancy: correct or carry as a note unless explicitly material;
- real blocker: required progress is impossible and no alternative or handoff exists.

State when to continue, correct, split, redesign, or restart cleanly.

For STANDARD, include this circuit breaker unless a stronger project-specific rule is justified:

- after two consecutive review failures concerning substantially the same validation, attestation, parser, documentation-synchronization, or bookkeeping mechanism, stop automatic corrective patches;
- return to design authority to decide whether the validation strategy or trust boundary is wrong;
- require explicit approval before a third corrective review of that same mechanism;
- do not treat syntax-only variants as materially different defects.

The circuit breaker does not waive unresolved material defects. It prevents compensating patches from replacing design correction.

### 14. Search for overlap

Before creating an issue:

- inspect open and recently closed issues and PRs;
- link superseded attempts;
- reuse an existing issue only when its contract still matches;
- create a new issue when a clean contract is needed.

### 15. Quality gate

An `EXECUTION_READY` issue must answer:

- What exact outcome must become true?
- What is the observed current state?
- Which source-of-truth documents govern the work?
- What is accepted, open, speculative, and rejected?
- What is in and out of scope?
- Which invariants must not change?
- Which execution profile applies and why?
- What are the bounded phases and deliverables?
- Which validations prove each phase?
- Which checkpoints require independent review?
- Which findings are material blockers versus notes?
- Which state is authoritative and which text is derived?
- Is there an explicit security or adversarial-input boundary?
- Which operational capabilities are genuinely required?
- What is degraded operation versus a real blocker?
- What triggers correction, redesign, the repeated-review circuit breaker, or restart?
- What is the final acceptance criterion?
- Can a fresh executor begin Phase 1 without guessing?

If a material answer is missing, do not use `execution-ready`.

## Canonical issue template

```markdown
# <Outcome-oriented title>

## Readiness

**State:** EXECUTION_READY | DESIGN_REQUIRED | INVESTIGATION_REQUIRED | BLOCKED
**Execution profile:** STANDARD | HIGH_ASSURANCE
**Design authority:** <session or person>
**Repository/base:** `<repo>` / `<immutable base or branch policy>`
**Execution branch:** `<branch convention>`

## Goal

<Observable outcome.>

## Motivation

<Why it is needed now.>

## Current state and evidence

- **OBSERVED:** <fact>
- **ACCEPTED:** <decision>
- **OPEN:** <none or unresolved decision>
- **REJECTED:** <rejected alternative>

## Source of truth

- `<path or exact revision>`

## Scope

### In scope
- <item>

### Out of scope
- <item>

## Constraints and invariants

- <constraint>

## Review materiality and trust boundary

- Authoritative machine-enforced state: <structured path or none>
- Derived informational state: <docs/comments/PR text>
- Security or adversarial-input boundary: <none or explicit boundary>
- Material `FAIL` findings: <criteria>
- Non-material `PASS_WITH_NOTES` findings: <criteria>
- Repeated-review circuit breaker: after two same-mechanism failures, return to design authority before a third review

## Operational capabilities

- Branch publication: required | optional
- Draft PR required before: <checkpoint or final gate>
- Independent review: <checkpoint list or every phase>
- Local reviewer execution: required | optional
- External hardware/data: <requirements>
- Connector handoff: allowed | disallowed with reason

## Phases

### Phase N — <bounded outcome>

**Model class:** TOP_REASONING | STRONG_CODING | FAST_CODING | LIGHTWEIGHT
**Checkpoint:** <name or none>
**Inputs:** <paths or prior artifacts>
**Permitted scope:** <files or subsystem>
**Instructions:** <bounded actions>
**Deliverables:** <artifacts or behavior>
**Validation:**
- `<command>`
- Success means: <observable result>
**Out of scope:** <exclusions>

## Review checkpoints

### Checkpoint A — <name>

**Covered phases:** <range>
**Review target:** <commit range policy>
**Checks:** <scope, artifacts, validation, unexpected changes>
**Material blockers:** <explicit criteria>
**Non-blocking notes:** <examples>
**Progression:** PASS or acceptable PASS_WITH_NOTES

## Failure, degradation, and restart

- Implementation failure: <bounded material correction>
- Editorial/derived-state discrepancy: <note or correction policy>
- Operational degradation: <fallback or handoff>
- Real blocker: <condition>
- Return to design when: <condition>
- Repeated-review circuit breaker: <same-mechanism rule>
- Restart cleanly when: <condition>

## Final acceptance

- <testable exit criterion>
```

## Creation and approval

After approval:

- create or update the issue using the GitHub connector;
- apply the appropriate single workflow label;
- preserve approved scope, materiality, trust boundaries, and checkpoints;
- link dependencies and superseded attempts;
- do not begin implementation in the design-authority session unless the user explicitly changes roles.
