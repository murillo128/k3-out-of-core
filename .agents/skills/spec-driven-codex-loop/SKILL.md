---
name: spec-driven-codex-loop
description: Execute a self-contained approved GitHub issue through bounded implementation, native validation, intentional publication, risk-based review, and a concise handoff without reconstructing project history or coupling technical evidence to workflow metadata.
---

# Spec-Driven Codex Loop

## Responsibility

Use this skill for non-trivial implementation under an approved controlling issue.

The controlling issue is the complete phase-specific execution contract. Repository documents define durable architecture. Branches and PRs record implementation. Tests and technical manifests record reproducible evidence.

The executor owns implementation, validation, commits, progression, and handoff. It delegates GitHub transport to `codex-github-operations` and independent review to `codex-independent-review`. The executor may not act as its own independent reviewer.

## Treat the issue as authoritative and complete

Assume the design authority may have more context or stronger reasoning than the executor. Do not weaken, reinterpret, or silently fill gaps in the approved contract.

The issue should already contain all material phase-specific facts. Read its linked sources only to inspect the exact implementation, evidence, or durable decision it identifies—not to reconstruct the intended design from scratch.

When two plausible implementations would differ materially and the issue does not resolve the choice, return to design. Do not choose based on convenience or broad historical inference.

## Load context once

Start with:

1. `AGENTS.md`;
2. the controlling issue.

Then read only the exact plan or decision sections, source, tests, build metadata, prior manifest, and external inputs required by the issue or active change.

Do not preload other role skills, complete historical issues, PR discussions, result directories, or unrelated repository documents.

On resume, verify branch, `HEAD`, worktree, current issue state, and new material comments. Reuse previously inspected facts while their source identity is unchanged. Keep a compact local ledger rather than publishing repeated state summaries.

## Profiles

### STANDARD

Default profile:

- implement one coherent bounded outcome at a time;
- validate before publication;
- use independent review only at declared material checkpoints;
- reuse a final-capable checkpoint as final review when its target remains unchanged;
- report only material events;
- treat editorial or administrative inconsistencies as notes unless they affect authoritative technical evidence or safe progression.

### HIGH_ASSURANCE

Use only when the issue explicitly requires it. Apply the same workflow with additional issue-defined risks or evidence. Do not infer it from issue size or reviewer preference.

## Entry gate

Before editing, confirm:

- the issue is ready or already in progress;
- the intended branch and current worktree are safe;
- the bounded outcome, scope, invariants, failure semantics, and acceptance criteria are clear;
- required technical inputs are available;
- no competing branch or PR creates ambiguous ownership.

An exact base commit is required only when the issue, reproducibility, compatibility, or branch ownership depends on it. Do not create a new administrative baseline after every documentation or metadata change.

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

Confirm the intended behavior, exact technical contract, permitted subsystem, invariants, required validation, evidence, and next risk checkpoint. Do not combine unrelated work for convenience.

### 2. Implement the smallest coherent delta

- follow the accepted issue contract and architecture;
- preserve baseline behavior outside the approved change;
- add or update tests with implementation;
- keep build integration native;
- avoid unrelated cleanup and formatting;
- capture only measurements and evidence needed to support the declared claims;
- stop when new evidence invalidates the design or acceptance strategy.

A commit should represent a reviewable outcome. Mechanical substeps do not require separate commits merely for compliance.

### 3. Work efficiently across a nested repository

When the primary implementation is in nested `llama.cpp`:

- make coherent nested commits as needed for implementation and local validation;
- publish the exact nested target when checkpoint or collaboration requires it;
- do not update the parent gitlink after every nested commit;
- update and commit the parent gitlink at declared checkpoints, the final integration candidate, or another explicit compatibility/recovery boundary;
- keep parent-side code, tooling, and evidence aligned with the nested target whenever they genuinely depend on it.

Do not hide an uncommitted or unpublished nested dependency at a review boundary.

### 4. Validate honestly

Run the issue-required validation and useful narrower checks. Record commands not run, environmental limits, deviations, and generated evidence where they matter to acceptance.

Never claim an unrun check passed. Implementation failures are not blockers; correct them within scope or report the bounded failed outcome.

### 5. Build immutable technical evidence

Use one machine-readable technical manifest only when reproducible evidence is required.

For new phases, the manifest should bind technical facts such as:

- project and nested implementation revisions;
- input identities and hashes;
- environment, configuration, commands, and exit results;
- evidence artifact identities;
- technical metrics, gates, outcomes, and limitations.

Do not add branch names, issue or PR numbers, labels, comment IDs, review verdicts, merge commits, or closeout state unless the issue demonstrates that one is a technical input to the tested system. The review record remains external to the immutable technical manifest.

Do not modify a technical manifest merely to record that it was reviewed or merged.

### 6. Retain evidence proportionally

Keep in Git:

- the technical manifest;
- bounded summaries and selected conclusions;
- schemas and reproduction tooling;
- small deterministic fixtures;
- an archive index with size and checksum when evidence is externalized.

Use an immutable checksum-addressed external archive for large or highly repetitive raw samples, matrices, generated profiles, traces, or logs when the issue permits it. Preserve enough local evidence to reproduce claims and run ordinary tests without downloading unnecessary bulk.

Never externalize secrets, prohibited artifacts, or data whose distribution is not authorized.

### 7. Publish when useful

Publish when remote preservation, collaboration, a declared checkpoint, or PR review requires it. Use `codex-github-operations` for transport.

Exact full SHAs belong at trust boundaries:

- a published review target;
- a final immutable technical manifest or evidence record;
- a recovery handoff where branch identity is otherwise ambiguous;
- a pinned external or nested dependency.

Do not repeat SHAs in routine progress comments, PR prose, roadmap state, or summaries when GitHub or Git already exposes them.

### 8. Report material events only

Under `STANDARD`, comment when:

- execution starts after a real handoff;
- a checkpoint is ready;
- scope or acceptance changes;
- a material failure, blocker, or design return occurs;
- final handoff is ready.

Do not comment for every commit, test invocation, gitlink movement, label transition, or unchanged resume.

Use concise human-readable updates:

```markdown
## <Started | Checkpoint ready | Design required | Blocked | Complete>

**Delivered or confirmed:** <one to three bullets>
**Validation:** <result or authoritative evidence link>
**Material issue:** <none or concise finding>
**Next:** <one bounded action>
```

At a review checkpoint, add the exact published project and nested targets required to reproduce the review. Otherwise omit routine commit metadata.

## Review checkpoints

At a declared checkpoint:

1. publish the exact target;
2. provide the checkpoint scope, material risks, acceptance criteria, and immutable technical evidence;
3. invoke one fresh independent review;
4. continue only after `PASS` or a non-blocking `PASS_WITH_NOTES`.

Do not copy the reviewer procedure into the request. The reviewer skill owns inspection, testing depth, materiality, and verdict format.

### Final-capable checkpoint

When the issue declares a checkpoint final-capable, it serves as the final PR review only when the reviewer inspects:

- the complete final PR diff;
- the final project and nested targets;
- the immutable final technical manifest and required evidence;
- all remaining acceptance criteria and unresolved findings.

After a passing final-capable review, do not request another review of the same target.

A later change to code, tests, technical evidence, manifest, dependencies, configuration, or technical claims requires a new review of the changed target. Changes only to issue/PR prose, labels, roadmap state, merge metadata, or other derived workflow state do not.

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

## Pull request discipline

Use one PR for one controlling issue unless the issue explicitly decomposes delivery.

The PR body should contain only:

- the controlling issue;
- delivered behavior;
- current validation and review state;
- material deviations or residual risks.

GitHub already records base, head, commits, checks, and discussion. Do not reproduce complete issue history, command logs, manifests, or routine SHAs in the PR body.

Keep the PR draft while required implementation or review remains incomplete. Merge only after the issue's final acceptance and required independent review, including a passing unchanged final-capable checkpoint where applicable.

## Handoff

A handoff should contain only what the next actor cannot derive cheaply:

- issue and current bounded outcome;
- branch or PR;
- last accepted checkpoint;
- material validation or evidence link;
- unresolved material finding;
- one immediate next action.

Include exact project or nested heads only when needed to disambiguate the target or preserve a recovery boundary. Do not reconstruct completed history.