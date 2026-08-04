---
name: design-github-issue
description: Define the smallest execution-ready GitHub issue that resolves material design decisions, bounds implementation, and states objective acceptance criteria without duplicating repository history or workflow procedure.
---

# Design a GitHub Execution Issue

## Responsibility

Use this skill before non-trivial implementation starts, or when execution returns because a material design or validation decision is unresolved.

The design authority owns:

- the observable outcome;
- material architectural and validation decisions;
- scope, invariants, exclusions, and acceptance criteria;
- risk-based review checkpoints;
- the issue's readiness state.

It does not implement code, operate the branch, publish commits, or perform independent review.

## Load only material context

Start with:

1. `AGENTS.md`;
2. the request or existing controlling issue.

Then load only the plan or decision sections, source, tests, build metadata, prior manifest, external revisions, and current overlapping work needed to settle this issue.

Prefer links and exact section references over copied prose. Prefer the prior phase's final manifest and accepted result over its complete issue, PR, and comment history. Do not require future executors to read this design skill.

## The issue is a contract, not an archive

A controlling issue should let a fresh executor start safely, but it should contain only the phase-specific delta.

Include:

- the outcome and current limitation;
- accepted decisions and genuinely open questions;
- scope, exclusions, and invariants;
- objective acceptance criteria;
- risk checkpoints, when needed;
- links to authoritative design, evidence, source, and tests.

Do not copy:

- generic workflow, Git, review, or failure procedures owned by skills;
- historical phase narratives;
- complete command output or machine-readable evidence;
- information already visible in GitHub or Git;
- the same decision in both the issue and repository documentation.

Use exact commits only when reproducibility, an external dependency, a prior accepted result, or branch ownership depends on them. Do not pin or repeat routine heads merely for bookkeeping.

## Readiness

Use one current state for a non-trivial open issue:

- `execution-ready`: no material design or validation decision remains;
- `design-required`: a material decision remains unresolved;
- `investigation-required`: bounded evidence is needed before choosing a design;
- `blocked`: a required external capability is unavailable with no practical alternative;
- `in-progress`: execution has started.

A label is a useful projection of this state, not a separate source of truth. Do not create compliance work solely to synchronize equivalent prose, labels, comments, and PR metadata.

## Design method

### 1. Define the observable outcome

State what must become true, why it matters, the current limitation, and the requested boundary.

### 2. Resolve only material unknowns

Resolve questions that can change behavior, compatibility, architecture, ownership, lifetime, numerical semantics, backend support, failure handling, validation, licensing, or upstream strategy.

Use these classifications only where they clarify a real decision:

- `OBSERVED`
- `ACCEPTED`
- `OPEN`
- `SPECULATIVE`
- `REJECTED`
- `BLOCKED`

Do not turn `OPEN` or `SPECULATIVE` items into implementation requirements.

Record durable cross-phase architecture in `docs/DECISIONS.md`. Keep issue-local choices in the issue.

### 3. Bound the implementation

Define the smallest coherent outcome, permitted subsystem or files, explicit exclusions, and invariants. Split work only when a failure would otherwise obscure which design or edit caused it.

Avoid exhaustive allowlists when normal repository boundaries and review can control scope more clearly.

### 4. Define validation

Validation must prove the observable outcome, not merely compilation.

Specify only what is material:

- native build or test targets;
- correctness, repeated-run, failure-path, numerical, or performance checks;
- required environment or external artifacts;
- objective pass/fail criteria;
- the authoritative manifest or evidence artifact, when one is needed.

Prefer repository-native targets. Use exact commands when they are stable and important; otherwise identify the target and expected result without freezing replaceable invocation details.

### 5. Add risk-based checkpoints

Under `STANDARD`, add an independent checkpoint only for material architecture, ownership or lifetime, persistent state, numerical behavior, backend execution, broad refactoring, or decision-driving performance evidence.

Use `HIGH_ASSURANCE` only when explicitly justified; issue size alone is not a reason.

A checkpoint needs only:

- the covered outcome;
- the exact target semantics;
- the risks and acceptance criteria;
- the evidence to inspect;
- what would make progression unsafe.

The reviewer skill owns the review procedure and verdict format.

### 6. Define restart semantics

Distinguish:

- local implementation defect: correct a bounded delta;
- design defect: return to `design-required`;
- evidence gap: return to `investigation-required`;
- replaceable tool failure: use another transport or leave a handoff;
- real blocker: no safe practical continuation exists.

Under `STANDARD`, two consecutive review failures for substantially the same validation, attestation, parser, documentation-sync, or bookkeeping mechanism trigger design review before a third corrective cycle. This never waives a continuing material defect.

### 7. Check overlap

Inspect only plausibly overlapping open issues, PRs, branches, and recent attempts. Link superseded work rather than copying its history.

## Execution-ready check

Before marking the issue `execution-ready`, confirm:

- the observable outcome is unambiguous;
- material decisions are resolved;
- scope and invariants are clear;
- acceptance is objective;
- required context is linked;
- checkpoints match actual risk;
- a fresh executor can start without reconstructing unrelated history.

## Lean issue template

```markdown
# <Outcome-oriented title>

## Readiness

**State:** execution-ready | design-required | investigation-required | blocked
**Profile:** STANDARD | HIGH_ASSURANCE

## Goal

<Observable outcome and why it matters.>

## Current limitation

<Only the evidence needed to understand this phase.>

## Decisions and references

- <accepted decision or exact link>
- <open question, or none>
- <prior manifest or attempt only when material>

## Scope

### In scope
- <bounded outcome>

### Out of scope
- <explicit exclusion>

### Invariants
- <must remain true>

## Acceptance criteria

- [ ] <objective criterion>
- [ ] <objective criterion>

## Checkpoints

- <risk checkpoint and exact target semantics, or none>

## Delivery

- PR: <one coherent PR>
- Evidence: <manifest/artifact/checks, or none>
- Completion: <observable final state>
```

Add sections only when they carry issue-specific information.
