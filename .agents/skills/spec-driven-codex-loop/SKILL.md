---
name: spec-driven-codex-loop
description: Execute an approved GitHub issue through bounded implementation, native validation, intentional publication, risk-based review, and a concise handoff without reconstructing or duplicating project history.
---

# Spec-Driven Codex Loop

## Responsibility

Use this skill for non-trivial implementation under an approved controlling issue.

The issue defines the bounded outcome. Repository documents define durable architecture. The branch and PR record the implementation. Tests and manifests record reproducible evidence.

The executor owns implementation, validation, commits, progression, and handoff. It delegates GitHub transport to `codex-github-operations` and independent review to `codex-independent-review`. The executor may not act as its own independent reviewer.

## Load context once

Start with:

1. `AGENTS.md`;
2. the controlling issue.

Then read only the exact plan or decision sections, source, tests, build metadata, prior manifest, and external inputs linked by the issue or required by the active change.

Do not preload other role skills, complete historical issues, PR discussions, result directories, or unrelated repository documents.

On resume, verify branch, `HEAD`, worktree, current issue state, and new material comments. Reuse previously inspected facts while their source identity is unchanged. Keep a compact local ledger rather than publishing repeated state summaries.

## Profiles

### STANDARD

Default profile:

- implement one coherent bounded outcome at a time;
- validate before publication;
- use independent review only at declared checkpoints and final handoff;
- report only material events;
- treat editorial or administrative inconsistencies as notes unless they affect authoritative evidence or safe progression.

### HIGH_ASSURANCE

Use only when the issue explicitly requires it. Apply the same workflow with additional issue-defined checkpoints or evidence. Do not infer it from issue size or reviewer preference.

## Entry gate

Before editing, confirm:

- the issue is ready or already in progress;
- the intended branch and current worktree are safe;
- the bounded outcome, scope, invariants, and acceptance criteria are clear;
- required technical inputs are available;
- no competing branch or PR creates ambiguous ownership.

An exact base commit is required only when the issue, reproducibility, or branch ownership depends on it. Do not create a new administrative baseline after every documentation or metadata change.

Return to design when a material contract decision is missing. Return to investigation when evidence is required before the design can be chosen.

## Native tooling

Use repository-native build and test paths. Prefer CMake targets, presets, incremental builds, and CTest over durable ad-hoc compiler or linker orchestration.

A disposable diagnostic command is acceptable during investigation, but required validation should use the approved native path unless the issue explicitly defines an exception.

If the issue contains a stale replaceable invocation but the intended native target and acceptance remain unambiguous, use the equivalent current invocation and note the deviation. Return to design only when changing the command would change what is being proven.

## Efficient command waiting

For long-running commands, use the longest practical terminal wait instead of repeated short polls. Silence or a wait timeout is not a failure while the process is still active. Do not narrate routine waiting.

This does not relax validation, command timeouts, reviewer independence, or failure handling.

## Execution loop

### 1. Establish the bounded outcome

Confirm the intended behavior, permitted subsystem, invariants, required validation, evidence, and next risk checkpoint. Do not combine unrelated work for convenience.

### 2. Implement the smallest coherent delta

- follow accepted architecture and issue scope;
- preserve baseline behavior outside the approved change;
- add or update tests with implementation;
- keep build integration native;
- avoid unrelated cleanup and formatting;
- capture measurements and evidence needed to support the claim;
- stop when new evidence invalidates the design or acceptance strategy.

A commit should represent a reviewable outcome. Mechanical substeps do not require separate commits merely for compliance.

### 3. Validate honestly

Run the issue-required validation and useful narrower checks. Record commands not run, environmental limits, deviations, and generated evidence where they matter to acceptance.

Never claim an unrun check passed. Implementation failures are not blockers; correct them within scope or report the bounded failed outcome.

### 4. Publish when useful

Publish when remote preservation, collaboration, a declared checkpoint, or PR review requires it. Use `codex-github-operations` for transport.

Exact full SHAs belong at trust boundaries:

- a published review target;
- a final accepted manifest or evidence record;
- a recovery handoff where branch identity is otherwise ambiguous;
- a pinned external or nested dependency.

Do not repeat SHAs in routine progress comments, PR prose, roadmap state, or summaries when GitHub or Git already exposes them.

### 5. Report material events only

Under `STANDARD`, comment when:

- execution starts after a real handoff;
- a checkpoint is ready;
- scope or acceptance changes;
- a material failure, blocker, or design return occurs;
- final handoff is ready.

Do not comment for every commit, test invocation, label transition, or unchanged resume.

Use concise human-readable updates:

```markdown
## <Started | Checkpoint ready | Design required | Blocked | Complete>

**Delivered or confirmed:** <one to three bullets>
**Validation:** <result or authoritative evidence link>
**Material issue:** <none or concise finding>
**Next:** <one bounded action>
```

At a review checkpoint, add the exact published target. Otherwise omit routine commit metadata.

## Review checkpoints

At a declared checkpoint:

1. publish the exact target;
2. provide the checkpoint scope, risks, acceptance criteria, and authoritative evidence;
3. invoke one fresh independent review;
4. continue only after `PASS` or a non-blocking `PASS_WITH_NOTES`.

Do not copy the reviewer procedure into the request. The reviewer skill owns inspection, testing depth, materiality, and verdict format.

### Progression

- `PASS`: continue.
- `PASS_WITH_NOTES`: continue unless a note violates an exit gate.
- `FAIL`: classify the material defect before editing.
- `BLOCKED`: stop only when required evidence or review capability is unavailable with no safe alternative.
- transport failure: use another permitted route or leave a precise handoff; it is not an implementation verdict.

For a `FAIL`, choose one:

- bounded local correction;
- return to `design-required`;
- return to `investigation-required`;
- request calibration when a non-material note was presented as a failure.

Do not mechanically implement every reviewer suggestion.

## Repeated-review circuit breaker

Under `STANDARD`, after two consecutive failures concerning substantially the same validation, attestation, parser, documentation-sync, or bookkeeping mechanism:

- stop automatic compensating patches;
- preserve the material findings and valid technical evidence;
- return to design authority before a third corrective cycle unless the new defect is materially different.

Representational variants alone are not materially different. The circuit breaker never waives a continuing technical defect.

## Evidence and closeout

Use one machine-readable authoritative record only when the issue needs reproducible evidence or attestation. Comments, labels, PR descriptions, Markdown summaries, and roadmap state are derived unless explicitly declared otherwise.

Do not create extra verifier layers or semantic cross-checks across equivalent prose. Do not require an additional review solely because a derived summary changed.

## Pull request discipline

Use one PR for one controlling issue unless the issue explicitly decomposes delivery.

The PR body should contain only:

- the controlling issue;
- the delivered behavior;
- current validation and review state;
- material deviations or residual risks.

GitHub already records base, head, commits, checks, and discussion. Do not reproduce complete issue history, command logs, manifests, or routine SHAs in the PR body.

Keep the PR draft while required implementation or review remains incomplete. Merge only after the issue's final acceptance and required independent review.

## Handoff

A handoff should contain only what the next actor cannot derive cheaply:

- issue and current bounded outcome;
- branch or PR;
- last accepted checkpoint;
- material validation or evidence link;
- unresolved material finding;
- one immediate next action.

Include exact project or nested heads only when needed to disambiguate the target or preserve a recovery boundary. Do not reconstruct completed history.
