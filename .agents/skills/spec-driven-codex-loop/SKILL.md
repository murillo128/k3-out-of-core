---
name: spec-driven-codex-loop
description: Execute an approved GitHub issue through bounded implementation, native validation, intentional commits, risk-based checkpoints, and final handoff without repeatedly reconstructing repository context.
---

# Spec-Driven Codex Loop

## Responsibility

Use this skill only in the main-executor role for non-trivial repository work.

The controlling issue is the bounded execution contract. Repository documents remain architectural sources of truth. The pull request records the implementation.

This skill owns:

- execution sequencing and phase boundaries;
- scope control and validation;
- intentional commits and evidence capture;
- progression, failure classification, and handoff.

It delegates Git and GitHub transport to `codex-github-operations` and independent review to `codex-independent-review`. Do not preload or duplicate those procedures.

The main executor may not act as its own independent reviewer.

## Load execution context once

Start with the bootstrap context defined by `AGENTS.md`:

1. `AGENTS.md`;
2. `docs/STATUS.md`;
3. the controlling issue body.

Then load only:

- issue-linked decision, plan, validation, manifest, and evidence sections;
- source, tests, and build metadata relevant to the active phase;
- exact branch, worktree, project head, nested head, and pinned inputs needed by the phase.

Do not read `design-github-issue`, `codex-github-operations`, or `codex-independent-review` until the action they own is required. Do not automatically read complete prior issues, PR histories, result directories, or repository documents that the issue does not link.

### Resume efficiently

On resume:

- verify current branch, `HEAD`, worktree, active phase, and last accepted checkpoint;
- read only issue comments newer than the recorded handoff, plus explicitly referenced earlier comments;
- re-read a file or evidence object only when its commit, blob, checksum, or relevant section changed;
- preserve a compact ledger of exact heads, context loaded, commands run, evidence produced, and unresolved deviations.

Do not repeat a full orientation at every phase.

## Apply the selected profile

### STANDARD

`STANDARD` is the default.

- Execute one bounded phase at a time.
- Validate and commit every phase.
- Publish when preservation, collaboration, a checkpoint, or PR update requires it.
- Review only declared checkpoints and final handoff.
- Group routine issue reporting at session boundaries and checkpoints.
- Fail only for material violations of explicit criteria or plausible normal-path defects.

### HIGH_ASSURANCE

`HIGH_ASSURANCE` must be explicit.

- Apply every `STANDARD` rule.
- Publish and independently review every phase commit.
- Apply stricter issue-defined progression and evidence requirements.
- Exercise adversarial cases only inside the approved risk boundary.

Do not silently upgrade `STANDARD` because work is large or a reviewer can imagine optional hardening.

## Entry gate

Before editing, confirm only what execution needs:

- the issue is `execution-ready` or already `in-progress`;
- exact execution base and branch policy;
- active phase, permitted scope, exclusions, validation, expected commit, and next checkpoint;
- clean worktree or explicitly documented pre-existing changes;
- required technical prerequisites for this phase;
- no current branch or PR conflict that would create competing ownership.

Use `codex-github-operations` to establish `in-progress` and the approved branch when practical. One control-plane transport failure is degraded operation, not an implementation blocker.

If a material contract decision is missing, return to `design-required`. If evidence is needed before design can finish, return to `investigation-required`.

## Native tooling rule

Use the repository-native build and test path declared by the issue and `AGENTS.md`.

- Prefer CMake targets, presets, incremental builds, and CTest over committed scripts that reconstruct compiler or linker commands.
- A persistent helper executable or native test should normally be integrated as a build target.
- Do not spend reasoning time inventing a custom compile/link path merely to save local CPU time.
- Run a plausible issue-provided native command before designing alternatives.
- An ad-hoc compiler command may support disposable investigation, but it should not become durable validation without an explicit contract reason.
- If required build metadata is excluded by mistake, request a bounded contract correction rather than creating a permanent workaround.

A stale or incorrect contract command is a design defect. Record the observed failure and return for correction; do not silently substitute a different methodology that changes evidence.

## Token-efficient command waiting

Long-running local work must wait inside the terminal tool instead of waking the model for frequent status polls.

- The Codex environment should configure `background_terminal_max_timeout = 900000` (15 minutes), or the largest supported equivalent.
- For a command expected to run longer than 10 seconds, request a long initial `yield_time_ms` and use the same long interval for any required `write_stdin` follow-up.
- Do not use short empty polls, repeated process-status commands, or progress narration while the command is known to be running.
- A wait timeout or silence is not a command failure. If the process is still active and there is no contrary evidence, wait again with the same long interval.
- Return to reasoning immediately when the command exits, fails, requests input, or produces output that requires a decision.
- When waiting for an independently running reviewer or agent, request the longest available explicit wait timeout and do not interleave status-only checks.

This changes waiting cadence only. It does not relax command timeouts, validation, reviewer independence, circuit breakers, or failure handling.

## Main execution loop

### 1. Establish the phase boundary

Confirm:

- primary outcome;
- permitted files or subsystem and explicit exclusions;
- exact inputs and revisions;
- native build and validation commands;
- objective success criteria;
- expected evidence and commit outcome;
- whether the phase ends a checkpoint.

Do not combine unrelated phases for convenience.

### 2. Record start only when useful

Under `STANDARD`, post a concise start record when:

- execution first begins or resumes after a handoff;
- a checkpoint group begins;
- scope, base, assumptions, or validation changed;
- another actor needs an explicit ownership record.

Several routine non-checkpoint phases in one uninterrupted session may share one start record. Under `HIGH_ASSURANCE`, record every phase start.

```markdown
## [RUN][<PHASE OR CHECKPOINT>][START]

**Profile:** STANDARD | HIGH_ASSURANCE
**Base commit:** `<full SHA>`
**Target outcome:** <one sentence>
**Scope:** <files or bounded area>
**Validation:** <commands or exact issue section>
**Checkpoint:** <name or none>
**Assumptions/deviations:** <none or list>
```

A comment-transport failure does not invalidate technical work when the same state can be preserved in the branch and handoff.

### 3. Implement only the approved delta

- Follow accepted decisions and phase scope.
- Keep changes minimal and cohesive.
- Add tests with implementation.
- Avoid unrelated cleanup or formatting.
- Preserve baseline behavior unless explicitly changed.
- Preserve project invariants and native build integration.
- Capture exact commands, outputs, revisions, and measurements.
- Stop and return to design if new evidence invalidates architecture, decomposition, validation, or scope.

### 4. Validate

Run the exact phase validation plus narrower checks used during development.

Record:

- commands run and their results;
- commands not run;
- environmental limitations;
- deviations from the issue;
- generated evidence identities.

Never claim unrun validation. A failure caused by implementation is `FAIL`, not `BLOCKED`. Fix it within the phase or record a bounded failed outcome.

### 5. Commit intentionally

Create one reviewable commit per bounded outcome where practical. It must:

- contain only phase work;
- include tests and required evidence;
- avoid unrelated formatting;
- use an outcome-oriented message;
- leave no unexplained uncommitted dependency.

Do not rewrite valid shared history to repair transport, comments, or review publication.

### 6. Publish when required

Load and use `codex-github-operations` only when publication or GitHub mutation is required.

Under `STANDARD`, publish when:

- the phase ends a checkpoint;
- another actor needs the exact commit;
- remote preservation or PR update is needed;
- the issue explicitly requires publication.

Under `HIGH_ASSURANCE`, publish every phase commit before review.

### 7. Record results proportionally

Under `STANDARD`, post a result record at checkpoints, failures, blockers, scope changes, and session handoff. Routine completed phases may be grouped when commits and structured evidence preserve exact phase mapping.

Under `HIGH_ASSURANCE`, record every phase result.

```markdown
## [RUN][<PHASE OR CHECKPOINT>][RESULT]

**Profile:** STANDARD | HIGH_ASSURANCE
**Commits:** `<full SHA or ordered range>`
**Result:** COMPLETED | PARTIAL | FAILED | BLOCKED
**Delivered:** <artifacts or behavior>
**Validation:** <actual results and evidence identity>
**Deviations/residual risks:** <none or list>
**Checkpoint:** <accepted, pending, or not applicable>
**Next action/handoff:** <one bounded action>
```

Do not edit away failed attempts. The issue remains an audit trail, but do not duplicate complete machine-readable evidence into comments.

## Review checkpoints

When a declared checkpoint is reached:

1. publish the exact checkpoint commit or range;
2. identify the issue section containing checkpoint risks and criteria;
3. provide the authoritative manifest and only the evidence needed by that checkpoint;
4. invoke `codex-independent-review` once;
5. continue only after `PASS` or acceptable `PASS_WITH_NOTES`.

Do not copy the review procedure into the executor prompt. The reviewer skill owns independence, context loading, testing depth, materiality, transport fallback, and verdict format.

### Findings and progression

- `PASS`: continue.
- `PASS_WITH_NOTES`: continue when notes do not violate an exit gate.
- `FAIL`: classify before editing.
- `BLOCKED`: stop only when required evidence or independent-review capability is genuinely unavailable.
- `TRANSPORT_FAILED`: try another permitted reviewer transport; it is not a verdict.

Classify `FAIL` as:

- **material local defect:** implement only the bounded corrective delta;
- **material design defect:** return to `design-required`;
- **investigation gap:** return to `investigation-required`;
- **non-material note presented as FAIL:** request design-authority calibration rather than starting an unapproved hardening loop;
- **transport or evidence limitation:** handle without mutating valid implementation commits.

Preserve the finding and classification. Do not mechanically implement every reviewer suggestion.

## Repeated-review circuit breaker

Under `STANDARD`, after two consecutive `FAIL` verdicts concerning substantially the same validation, attestation, parser, documentation-sync, or bookkeeping mechanism:

1. stop automatic corrective patches;
2. summarize exact findings, completed technical evidence, and remaining material risk;
3. determine whether the validation design or trust boundary is flawed;
4. return to `design-required` unless a new defect is materially different and affects an explicit normal-path criterion;
5. require an explicit design-authority decision before a third corrective review of that mechanism.

Syntax, Markdown styling, duplicate-field forms, URL capitalization, or equivalent representational variants are not materially different by themselves.

## Attestation and closeout

When a checkpoint or final gate requires attestation:

- use one issue-declared machine-readable authoritative record;
- bind it to the exact reviewed commit or range and accepted verdict;
- keep pre-review technical verification non-circular;
- make the post-review attestation update small;
- treat summaries, comments, labels, and PR descriptions as derived unless explicitly authoritative;
- do not make the verifier prove semantic consistency across arbitrary prose.

Do not create an extra attestation review unless the issue declares it or the attestation mechanism materially changed.

## Failure semantics

Use these categories consistently:

- **Implementation failure:** code, artifact, validation, or scope is materially defective; correct a bounded delta while progress remains possible.
- **Design defect:** architecture, decomposition, trust boundary, validation, or acceptance is wrong or incomplete; return to `design-required`.
- **Investigation gap:** more evidence is needed before choosing a design; return to `investigation-required`.
- **Operational degradation:** a replaceable tool or transport failed; use an alternative or provide a precise handoff.
- **Editorial discrepancy:** derived prose or bookkeeping is stale while authoritative technical state remains unambiguous; correct or note it without failing implementation.
- **Real blocker:** meaningful technical progress cannot continue because a required external condition is unavailable and no permitted alternative or handoff exists.

Only the last category uses `blocked`.

## Pull-request discipline

Create or reuse one draft PR for the controlling issue after the first useful published commit or before the first PR-level checkpoint.

The PR should contain only current state needed by reviewers:

- controlling issue;
- exact base and head;
- concise phase-to-commit mapping;
- current validation and checkpoint state;
- deviations, residual risks, and next action.

Do not paste complete issue histories or machine-readable evidence into the PR body. Reference authoritative records.

Keep the PR draft while execution or required review remains incomplete. Never mark ready or merge before issue-declared final acceptance and independent final review.

## Session closeout

Apply the source-of-truth update rules in `AGENTS.md`. Update only records whose state actually changed. Leave an exact handoff containing:

- issue and phase;
- branch and full project/nested heads;
- last accepted checkpoint;
- validation and evidence identity;
- unresolved material finding or blocker;
- one immediate next action.

A fresh executor should resume from this handoff without rereading unchanged history.