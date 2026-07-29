---
name: spec-driven-codex-loop
description: Orchestrate non-trivial repository work through an approved GitHub issue, bounded implementation phases, validation, intentional commits, risk-based review checkpoints, and final external review. STANDARD is the default profile; HIGH_ASSURANCE must be explicitly selected.
---

# Spec-Driven Codex Loop

## Purpose

Use this skill as the main execution protocol for non-trivial repository work.

The GitHub issue is the durable execution contract and audit trail. Repository documents remain the architectural source of truth. The pull request records the implementation.

This skill owns sequencing, scope control, phase boundaries, progression, failure classification, and handoff. It delegates:

- Git and GitHub transport to `codex-github-operations`;
- independent checkpoint and final review to `codex-independent-review`.

Do not duplicate those operational procedures here.

## Core principles

Use gates where they reduce material technical risk.

Do not convert recoverable tool, transport, editorial, or bookkeeping failures into implementation failures. Do not weaken gates that protect architecture, correctness, numerical behavior, concurrency, persistent state, security boundaries, or final merge quality.

Apply the selected execution profile. Do not silently turn STANDARD into HIGH_ASSURANCE because a reviewer can imagine additional defensive checks.

Prefer one authoritative structured state for machine-enforced decisions. Treat Markdown summaries, issue comments, and PR descriptions as informational or derived unless the issue explicitly makes them authoritative.

## Roles

Keep these roles separate:

1. **Design authority**
   - Produces the approved issue contract.
   - Selects execution profile, phases, checkpoints, validation, trust boundaries, and exit criteria.
   - Resolves material design changes and repeated-review loops.
2. **Main executor**
   - Executes bounded phases.
   - Validates and commits each phase.
   - Records actual evidence and deviations.
   - Classifies review findings before applying corrective patches.
3. **Independent reviewer**
   - Reviews declared checkpoints or every phase under HIGH_ASSURANCE.
   - Uses a fresh isolated read-only context.
   - Applies profile-calibrated materiality from `codex-independent-review`.
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
- Fail checkpoints only for material violations of explicit criteria or plausible normal-path defects.

Use STANDARD for normal implementation, deterministic tooling, documentation, fixtures, manifests, and low-risk refactors with strong local validation.

Under STANDARD, theoretical bypasses that require deliberately malformed, duplicated, or contradictory non-authoritative Markdown, comments, URLs, or metadata are normally non-blocking notes. They become blocking only when the issue explicitly defines that input as an authoritative security boundary.

### HIGH_ASSURANCE

HIGH_ASSURANCE is opt-in and must be explicit in the issue or user instruction.

- Apply every STANDARD rule.
- Publish and independently review every phase commit.
- Use stricter progression and evidence requirements defined by the issue.
- Exercise adversarial cases only within explicitly approved risk and trust boundaries.

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
- authoritative versus derived workflow state;
- any explicit security or adversarial-input boundary;
- material versus non-material checkpoint failures where ambiguity is likely;
- model capability class where relevant;
- final acceptance criteria;
- rollback, restart, repeated-review, and blocker conditions;
- operational capabilities that are genuinely required.

A STANDARD issue may group several related phases into one checkpoint. A HIGH_ASSURANCE issue normally makes every phase a checkpoint.

If a material design decision is missing, return the issue to `design-required`. If evidence is needed before design can finish, use `investigation-required`.

If an issue requires a machine-enforced closeout or attestation gate, it should identify one structured authoritative record. Do not infer that every Markdown representation or external comment must be parsed as an independent source of truth.

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
- Stop and return to design if new evidence invalidates the approved architecture, validation strategy, or scope.

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

### Review-finding classification

Do not implement a reviewer `FAIL` mechanically. Before editing, classify the finding:

- **Material local defect:** an explicit criterion is violated and a bounded correction preserves the approved design. Implement only that delta.
- **Material design defect:** the architecture, validation strategy, trust boundary, or acceptance criterion is wrong or incomplete. Return to `design-required`.
- **Non-material note presented as FAIL:** editorial inconsistency, optional hardening, or theoretical adversarial bypass outside the approved profile. Do not start a compensating patch loop; return to the design authority for verdict calibration or contract clarification.
- **Transport or evidence limitation:** handle according to the failure semantics below.

The executor must preserve the review comment and explain the classification. It must not silently override a verdict, but it also must not turn a non-material reviewer suggestion into unapproved implementation scope.

### Verdict progression

- `PASS`: continue.
- `PASS_WITH_NOTES`: continue when notes do not violate an exit gate; carry them forward.
- `FAIL`: classify materiality and failure domain before implementing only a bounded material corrective delta.
- `BLOCKED`: stop only for unavailable required evidence or exhausted independent-review capability.
- `TRANSPORT_FAILED`: try another reviewer transport; it is not a final verdict.

## Repeated-review circuit breaker

Prevent open-ended hardening loops under STANDARD.

After two consecutive `FAIL` verdicts concerning substantially the same validation, attestation, parser, documentation-synchronization, or bookkeeping mechanism:

1. stop applying automatic corrective patches;
2. summarize the exact findings, completed technical evidence, remaining material risk, and whether normal workflow operation is already unambiguous;
3. determine whether the validation design or trust boundary is flawed;
4. return the issue to `design-required` unless the next proposed defect is materially different and affects an explicit technical exit criterion under plausible use;
5. require an explicit design-authority decision before a third corrective review of that mechanism.

Changing Markdown styling, duplicate-field syntax, URL capitalization, comment formatting, or another representational variant is not a materially different defect by itself.

The circuit breaker does not permit ignoring a continuing material defect. It changes the response from repeated compensating patches to design correction.

## Attestation and closeout gates

When a checkpoint or final gate needs post-review attestation:

- use one machine-readable authoritative record where practical;
- bind it to the exact reviewed commit or range and accepted verdict;
- keep the pre-review technical verifier non-circular;
- perform a small post-review attestation update only after review;
- treat summaries, issue comments, and PR descriptions as derived communication unless the issue explicitly states otherwise;
- do not parse arbitrary Markdown as a hostile security protocol under STANDARD;
- do not require the verifier to prove the semantic consistency of every possible textual representation.

A normal-path attestation must be unambiguous and reproducible. It need not be hardened against deliberately contradictory prose unless the issue explicitly selects that threat model.

## Failure and blocker semantics

Use these categories consistently:

### Implementation failure

The code, artifact, validation, or scope is materially defective. Keep the issue `in-progress` while fixing a bounded delta when progress is possible.

### Design defect

The approved architecture, decomposition, validation strategy, trust boundary, or acceptance criteria are wrong or incomplete. Return to `design-required` and preserve the failed attempt for traceability.

### Investigation gap

Additional evidence is needed before choosing the correct design. Return to `investigation-required`.

### Operational degradation

A replaceable tool or transport failed, but implementation can continue or a handoff can complete the operation. Record the limitation without marking the implementation failed.

### Editorial or bookkeeping discrepancy

A derived description, comment, label, or summary is stale or inconsistent while authoritative technical state remains correct and unambiguous. Correct it when useful, carry it as a note, and do not classify it as an implementation failure unless the issue explicitly makes it an exit gate and the discrepancy can misrepresent acceptance under normal operation.

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

A stale PR description is normally an editorial correction, not a technical checkpoint failure, unless it is the explicitly declared authoritative merge record.

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

Final external review is mandatory under both profiles. It remains profile-calibrated and materiality-based.

## Restart policy

Prefer a clean restart or explicit redesign when:

- architecture or validation strategy is materially wrong;
- the trust boundary is unclear or self-referential;
- scope boundaries were crossed broadly;
- unrelated phases were bundled beyond reviewability;
- tests prove the wrong behavior;
- provenance or traceability is too weak to trust;
- fixes are becoming compensating patches for a flawed foundation;
- the repeated-review circuit breaker triggers.

Preserve abandoned branches and PRs for audit unless the user explicitly authorizes deletion.

Use focused corrective commits when the approved design remains valid and the defect is local and material.

## Prohibited shortcuts

Do not:

- implement a non-trivial task without an approved issue;
- invent architecture or acceptance criteria during execution;
- combine unrelated phases;
- claim unrun validation;
- let the reviewer modify code or continue execution;
- self-certify required checkpoints;
- turn optional tool failures into implementation failures;
- turn non-authoritative editorial variants into technical failures under STANDARD;
- use `blocked` for recoverable handoffs;
- silently change profile, scope, dependencies, thresholds, threat model, or trust boundary;
- merge before final external review;
- keep patching when the plan or validation mechanism itself is defective;
- continue past the repeated-review circuit breaker without an explicit design-authority decision.
