---
name: spec-driven-codex-loop
description: Orchestrate non-trivial repository work through an approved GitHub issue, bounded implementation phases, validation, intentional commits, risk-based review checkpoints, and final external review. STANDARD is the default profile; HIGH_ASSURANCE must be explicitly selected.
---

# Spec-Driven Codex Loop

## Purpose

Use this skill as the main execution protocol for non-trivial repository work.

The GitHub issue is the durable execution contract and audit trail. Repository documents remain the architectural source of truth. The pull request records the implementation.

This skill owns sequencing, scope control, phase boundaries, progression, and handoff. It delegates:

- Git and GitHub transport to `codex-github-operations`;
- independent checkpoint and final review to `codex-independent-review`.

Do not duplicate those operational procedures here.

## Core principle

Use gates where they reduce technical risk.

Do not convert recoverable tool, transport, or bookkeeping failures into implementation failures. Do not weaken gates that protect architecture, correctness, numerical behavior, concurrency, persistent state, or final merge quality.

## Roles

Keep these roles separate:

1. **Design authority**
   - Produces the approved issue contract.
   - Selects execution profile, phases, checkpoints, validation, and exit criteria.
   - Resolves material design changes.

2. **Main executor**
   - Executes bounded phases.
   - Validates and commits each phase.
   - Records actual evidence and deviations.

3. **Independent reviewer**
   - Reviews declared checkpoints or every phase under HIGH_ASSURANCE.
   - Uses a fresh isolated read-only context.
   - Does not implement fixes or continue execution.

4. **Final reviewer**
   - Reviews the complete pull request, issue history, validation, and source-of-truth updates before merge.

The main executor may not act as its own independent reviewer.

## Execution profiles

### STANDARD

STANDARD is the default.

- Execute one bounded phase at a time.
- Validate and commit every phase.
- Publish commits when needed for preservation, collaboration, checkpoint review, or PR updates.
- Review only the checkpoints declared by the issue.
- Require final external review before merge.

Use STANDARD for normal implementation, deterministic tooling, documentation, fixtures, manifests, and low-risk refactors with strong local validation.

### HIGH_ASSURANCE

HIGH_ASSURANCE is opt-in and must be explicit in the issue or user instruction.

- Apply every STANDARD rule.
- Publish and independently review every phase commit.
- Use stricter progression and evidence requirements defined by the issue.

Use HIGH_ASSURANCE for work such as:

- architecture or persistent formats;
- concurrency, ownership, and lifetime;
- numerical encodings and routing semantics;
- CUDA or backend correctness;
- cache coherence and persistent storage;
- security-sensitive behavior;
- changes where a faulty intermediate base would be expensive or unsafe.

Do not silently upgrade every large issue to HIGH_ASSURANCE. Split large issues and choose gates based on risk.

## Required issue contract

Before implementation, verify that the issue defines:

- observable goal and motivation;
- current state and evidence;
- source-of-truth documents;
- accepted decisions and explicit exclusions;
- constraints and invariants;
- execution profile;
- ordered bounded phases;
- permitted scope and expected deliverables per phase;
- validation and objective success criteria per phase;
- review checkpoints and their covered phases;
- model capability class where relevant;
- final acceptance criteria;
- rollback, restart, and blocker conditions;
- operational capabilities that are genuinely required.

A STANDARD issue may group several related phases into one checkpoint. A HIGH_ASSURANCE issue normally makes every phase a checkpoint.

If a material design decision is missing, return the issue to `design-required`. If evidence is needed before design can finish, use `investigation-required`.

## Entry

Before editing:

1. read `AGENTS.md`, the issue, its comments, and linked source-of-truth documents;
2. inspect branch, worktree, existing PRs, relevant source and tests, and pinned dependencies;
3. identify the exact phase, permitted scope, validation, and next checkpoint;
4. confirm that required technical prerequisites are available;
5. use `codex-github-operations` to establish or verify `in-progress` state when practical;
6. create or reuse the approved execution branch without rewriting shared history.

### Prerequisite classification

Classify failed checks before stopping:

- **Required technical prerequisite:** model, compiler, hardware, fixture, permission, or dependency necessary for the phase. Failure may be a real blocker.
- **Optional tool or transport:** one GitHub client, one reviewer launcher, formatting helper, or equivalent replaceable mechanism. Failure is degraded operation or handoff when an alternative exists.
- **Stale or incorrect contract command:** return to design authority to correct the contract; do not improvise silently.

Do not mark the issue `blocked` merely because an optional transport is unavailable.

## Main execution loop

Execute one bounded phase at a time.

### 1. Establish the phase boundary

Confirm:

- issue and active phase;
- execution profile;
- previous checkpoint verdict when applicable;
- clean worktree or explicitly documented pre-existing changes;
- permitted files and exclusions;
- validation commands;
- expected commit outcome;
- whether this phase ends a review checkpoint.

Do not combine unrelated phases for convenience.

### 2. Record phase start

Post a concise issue comment when the control plane is available:

```markdown
## [RUN][PHASE N][START]

**Profile:** STANDARD | HIGH_ASSURANCE
**Base commit:** `<sha>`
**Target outcome:** <one sentence>
**Planned scope:** <files or bounded area>
**Validation:** <commands or concise reference>
**Checkpoint after this phase:** <name or none>
**Known assumptions:** <none or list>
```

A recoverable comment-transport failure does not invalidate the phase. Use `codex-github-operations` for handoff and preserve the same branch and scope.

### 3. Implement only the phase

- Follow the approved issue and committed decisions.
- Keep the change minimal and cohesive.
- Add tests with implementation.
- Avoid unrelated cleanup.
- Preserve baseline behavior unless explicitly changed.
- Capture reproducible commands, outputs, revisions, and measurements.
- Stop and return to design if new evidence invalidates the approved architecture or scope.

### 4. Validate

Run the exact phase validation plus narrower checks used during development.

Record separately:

- commands run;
- passes and failures;
- commands not run;
- environmental limitations;
- deviations from the issue.

Never claim unrun validation.

A validation failure caused by implementation is `FAIL`, not `BLOCKED`. Fix it within the same phase before committing, or record a bounded failed result.

Use `BLOCKED` only when a required external condition prevents meaningful progress and no permitted alternative or handoff exists.

### 5. Commit

Create one intentional commit for the phase outcome where practical.

The commit must:

- contain only phase work;
- include its tests and required evidence;
- avoid unrelated formatting;
- use an outcome-oriented message;
- remain reviewable without uncommitted changes.

Do not rewrite shared history to repair publication or review transport.

### 6. Publish when required

Use `codex-github-operations`.

Under STANDARD, publication is required when:

- the phase ends a checkpoint;
- another actor needs the commit;
- work must be preserved remotely;
- the draft PR must be updated;
- the issue explicitly requires publication.

Under HIGH_ASSURANCE, publish every phase commit before review.

### 7. Record phase result

```markdown
## [RUN][PHASE N][RESULT]

**Profile:** STANDARD | HIGH_ASSURANCE
**Commit:** `<authoritative full SHA>`
**Result:** COMPLETED | PARTIAL | FAILED | BLOCKED
**Delivered:** <artifacts or behavior>
**Validation:** <actual results>
**Deviations:** <none or list>
**Residual risks:** <none or list>
**Checkpoint:** <name, pending | not applicable>
**Operational handoffs:** <none or list>
```

Do not edit away failed attempts. The issue is an audit trail.

## Review checkpoints

### STANDARD

When a phase ends a declared checkpoint:

1. publish the exact checkpoint range;
2. ensure the issue identifies covered phases and acceptance criteria;
3. invoke `codex-independent-review` once for the checkpoint;
4. continue only after `PASS` or acceptable `PASS_WITH_NOTES`.

Phases before the checkpoint may continue without independent review when their own validation passes and they remain within the approved checkpoint scope.

### HIGH_ASSURANCE

Every phase commit is a checkpoint. Review before starting the next phase.

### Verdict progression

- `PASS`: continue.
- `PASS_WITH_NOTES`: continue when notes do not violate an exit gate; carry them forward.
- `FAIL`: implement only the bounded corrective delta and request fresh review of the updated range.
- `BLOCKED`: stop only for unavailable required evidence or exhausted independent-review capability.
- `TRANSPORT_FAILED`: try another reviewer transport; it is not a final verdict.

## Failure and blocker semantics

Use these categories consistently:

### Implementation failure

The code, artifact, validation, or scope is defective. Keep the issue `in-progress` while fixing a bounded delta when progress is possible.

### Design defect

The approved architecture, decomposition, or acceptance criteria are wrong or incomplete. Return to `design-required` and preserve the failed attempt for traceability.

### Investigation gap

Additional evidence is needed before choosing the correct design. Return to `investigation-required`.

### Operational degradation

A replaceable tool or transport failed, but implementation can continue or a handoff can complete the operation. Record the limitation without marking the implementation failed.

### Real blocker

Meaningful technical progress cannot continue because of a required external dependency, access, hardware, evidence, or decision, and no permitted alternative or handoff exists. Only this category uses `blocked`.

## Pull-request discipline

Use one draft pull request per approved issue unless the issue explicitly defines otherwise.

Create it after the first useful published commit or before the first checkpoint needing PR context. Do not require a PR merely to inspect an exact published commit.

Keep the PR description current with:

- approved goal and scope;
- execution profile;
- phase-to-commit mapping;
- checkpoint verdicts;
- validation status;
- deviations and residual risks;
- source-of-truth changes.

Do not merge while execution is incomplete.

## Final gate

After all phases and checkpoints pass:

1. update required source-of-truth documents;
2. run the complete final validation suite;
3. confirm the branch and worktree are clean;
4. publish the final head and update the PR;
5. prepare a final handoff summarizing phases, commits, checkpoints, validation, deviations, and risks;
6. request a separate top-reasoning review of the complete PR and issue history;
7. merge only after approval and any required user authorization;
8. close the issue after merge and supported exit criteria.

Final external review is mandatory under both profiles.

## Restart policy

Prefer a clean restart when:

- architecture or validation strategy is materially wrong;
- scope boundaries were crossed broadly;
- unrelated phases were bundled beyond reviewability;
- tests prove the wrong behavior;
- provenance or traceability is too weak to trust;
- fixes are becoming compensating patches for a flawed foundation.

Preserve abandoned branches and PRs for audit unless the user explicitly authorizes deletion.

Use focused corrective commits when the approved design remains valid and the defect is local.

## Prohibited shortcuts

Do not:

- implement a non-trivial task without an approved issue;
- invent architecture or acceptance criteria during execution;
- combine unrelated phases;
- claim unrun validation;
- let the reviewer modify code or continue execution;
- self-certify required checkpoints;
- turn optional tool failures into implementation failures;
- use `blocked` for recoverable handoffs;
- silently change profile, scope, dependencies, or thresholds;
- merge before final external review;
- keep patching when the plan itself is defective.
