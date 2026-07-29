---
name: spec-driven-codex-loop
description: Govern non-trivial repository work through an approved GitHub issue, explicit workflow-label transitions, bounded Codex implementation phases, independent fresh-session reviews, one-step commits, traceable evidence, and an external final PR review. Use for implementations, refactors, migrations, performance work, or investigations that change code or project source-of-truth documents. Do not use for trivial typo-only edits unless explicitly requested.
---

# Spec-Driven Codex Loop

Use this skill as the execution protocol for non-trivial work in this repository.

The workflow optimizes for correctness, recoverability, and traceability rather than maximum edit speed. The GitHub issue is the durable control surface. Its workflow label must reflect the real execution state. The pull request is the implementation record. Repository documents remain the architectural source of truth.

## Core principle

Do not begin implementation until the task has a sufficiently precise, approved specification and the GitHub issue is explicitly marked `execution-ready`.

A good implementation cannot compensate for an ambiguous goal, unresolved architecture, missing constraints, weak validation criteria, or stale workflow state. When any of these are incomplete, stop implementation and correct the control artifacts first.

## Roles

Keep these roles separate:

1. **Design authority**
   - Normally a regular ChatGPT session using the strongest suitable reasoning model.
   - Clarifies the goal, inspects relevant specifications, resolves scope, records constraints, defines deliverables, selects validation, and creates or updates the GitHub issue.
   - Decides the recommended model class for each phase.
   - Sets the issue to `execution-ready` only after the execution contract is complete.

2. **Main executor**
   - Codex Desktop or another primary Codex session.
   - Executes exactly one bounded issue phase at a time.
   - Owns workflow-label transitions during execution.
   - Updates the issue before and after each phase.
   - Produces the implementation commit and evidence.

3. **Independent phase reviewer**
   - A fresh Codex CLI process or otherwise isolated Codex session.
   - Reviews one exact commit or commit range against the issue phase.
   - Does not continue implementation and must not modify the working tree.
   - Writes a structured verdict back to the issue.

4. **Final reviewer**
   - Normally a separate ChatGPT session using the strongest suitable reasoning model.
   - Reviews the complete PR, issue history, tests, deviations, and repository source-of-truth changes before merge.

Do not collapse the main executor and independent reviewer into one context.

## Supporting skills

Use relevant installed skills for brainstorming, plan writing, systematic debugging, test-driven development, verification, or code review when they improve a phase.

This skill governs sequencing, scope control, workflow state, evidence, and handoff. If another skill conflicts with this workflow, follow this skill unless the issue or `AGENTS.md` explicitly says otherwise.

## GitHub workflow-state protocol

The workflow labels defined by `AGENTS.md` are machine-readable state, not optional metadata:

- `design-required`
- `investigation-required`
- `execution-ready`
- `in-progress`
- `blocked`

Exactly one of these labels must be present on every non-trivial open issue.

### Mandatory mutation and verification

Whenever this skill requires a label transition, the executor must:

1. fetch the issue and inspect its current labels;
2. replace the previous workflow label with the required new label;
3. fetch the issue again;
4. verify that exactly the intended workflow label is present;
5. stop if mutation or verification fails.

Use the connected GitHub issue tools when available. `gh issue edit` is an acceptable fallback. Never assume that writing `IN_PROGRESS`, `BLOCKED`, or another state in a comment changed the GitHub label.

Equivalent CLI examples:

```bash
gh issue edit "$ISSUE" --repo "$REPO" \
  --remove-label execution-ready \
  --add-label in-progress

gh issue view "$ISSUE" --repo "$REPO" \
  --json labels --jq '.labels[].name'
```

The mutation must be completed and verified before any repository file is edited.

### State transitions

Use these transitions:

```text
design-required ------> execution-ready
investigation-required -> execution-ready
execution-ready -------> in-progress
in-progress -----------> blocked
blocked ---------------> execution-ready   # revised contract or clean restart
blocked ---------------> in-progress       # explicitly authorized safe resume
in-progress -----------> issue closed       # all gates complete
```

Additional rules:

- Do not start from `design-required`, `investigation-required`, or `blocked`.
- Do not add `in-progress` while leaving `execution-ready` attached.
- A failed entry prerequisite is `blocked`, even if no file was edited and no phase began.
- A material design defect is `design-required`, not merely `blocked`.
- Missing evidence needed to finish the specification is `investigation-required`.
- A failed external dependency, permission, environment, or execution gate is `blocked`.
- A `FAIL` review normally remains `in-progress` while the executor performs the bounded corrective delta. Use `blocked` only when progression cannot continue without an external resolution or revised contract.
- Closing the issue represents completion; do not add a `completed` workflow label.

## Entry gate

Before changing files, inspect:

1. `AGENTS.md` and the required repository reading order.
2. The target GitHub issue, its labels, comments, and all linked design documents.
3. The current branch, base branch, existing PR, and working-tree state.
4. Relevant source files, tests, prior decisions, and recent commits.
5. Any external dependency revisions pinned by the issue.

Then verify that the issue contains the contract below and that its current workflow label is valid.

### Required issue contract

The issue must define:

- goal and motivation;
- background and current behavior;
- source-of-truth documents and relevant files;
- accepted architecture or design decisions;
- explicit in-scope work;
- explicit out-of-scope work;
- constraints and invariants;
- ordered phases;
- one bounded outcome per phase;
- expected artifacts per phase;
- validation commands and success criteria;
- independent review criteria per phase;
- model class and rationale per phase;
- final acceptance criteria;
- rollback or restart conditions;
- known risks, open questions, and assumptions.

If a material decision is missing, contradictory, or still speculative:

1. replace the current workflow label with `design-required`;
2. verify the label;
3. record a design-gap comment on the issue;
4. stop before implementation.

If evidence or experimentation is needed before the contract can be completed:

1. replace the current workflow label with `investigation-required`;
2. verify the label;
3. record the required investigation and its exit condition;
4. stop before implementation.

### Approved-spec rule

Treat the issue body, explicit design-authority issue updates, and linked committed design documents as the approved plan. Do not silently redesign the task while implementing it.

When new evidence requires a material design or scope change:

1. stop the current phase;
2. replace `in-progress` with `design-required` and verify it;
3. record the evidence and conflict on the issue;
4. return the task to the design authority;
5. update the issue, repository design documents, and relevant skills;
6. resume only after the revised plan is explicit and the issue is returned to `execution-ready`.

### Entry-prerequisite failure

Run every entry-prerequisite command exactly as written before creating an execution branch or editing files.

When any required prerequisite fails:

1. replace `execution-ready` with `blocked` and verify it;
2. add a `## [RUN][ENTRY GATE][BLOCKED]` issue comment containing the exact command, output, dependency or owner, and restart condition;
3. do not create a branch, PR, file edit, or commit;
4. stop.

Writing the blocked comment without changing the label is an incomplete transition.

### Execution-start transition

After the issue contract and all entry prerequisites pass, but before creating or switching to the execution branch and before editing any file:

1. confirm that the issue has exactly the `execution-ready` workflow label;
2. replace `execution-ready` with `in-progress`;
3. fetch the issue again and verify that exactly `in-progress` is present;
4. add the phase-start issue comment;
5. only then begin repository mutation.

When resuming an already active issue, `in-progress` is acceptable only when the issue history identifies the same approved phase and there is no unresolved blocker. Otherwise stop and obtain an explicit state correction.

## Model selection

The design authority should assign a model class to every phase. Use capability classes rather than relying only on a model name that may become unavailable.

Use the strongest practical reasoning model for:

- architecture and design;
- ambiguous root-cause analysis;
- concurrency, lifetime, numerical, or performance reasoning;
- cross-cutting refactors;
- independent review of high-risk changes;
- final PR review.

Use a faster or cheaper model for:

- mechanical edits with exact instructions;
- repetitive API migrations;
- formatting and cleanup;
- bounded test additions with an established pattern;
- documentation synchronization after decisions are already made.

The main executor must not silently downgrade a phase. If the requested model class is unavailable, record the substitution and its risk on the issue before proceeding.

## Issue phase format

Each issue phase should contain:

```markdown
### Phase N — <bounded outcome>

**Status:** NOT_STARTED | IN_PROGRESS | REVIEW_FAILED | PASSED | BLOCKED
**Recommended model class:** <class>
**Rationale:** <why this class is appropriate>

**Inputs**
- <files, decisions, prior phase artifacts>

**Instructions**
1. <bounded action>
2. <bounded action>

**Expected deliverables**
- <files, behavior, tests, evidence>

**Validation**
- `<exact command>`
- Success means: <observable criteria>

**Independent review checks**
- <scope check>
- <artifact check>
- <test/evidence check>
- <unexpected-change check>

**Out of scope for this phase**
- <explicit exclusions>
```

A phase is too large if its result cannot be reviewed independently or if failure would make it difficult to identify which decision or edit caused the problem.

## Main execution loop

Execute the following loop for exactly one phase at a time.

### 1. Establish a clean phase boundary

Before implementation:

- verify that the issue has exactly the `in-progress` workflow label;
- confirm the previous phase verdict is `PASS` or `PASS_WITH_NOTES`, except for Phase 1;
- confirm the branch and PR are correct;
- confirm the working tree is clean, or document intentional pre-existing changes;
- identify the exact phase and its permitted files;
- identify the exact validation commands;
- identify the expected commit outcome.

Do not combine multiple issue phases for convenience.

### 2. Write the phase-start issue comment

Before editing, add a comment using this format:

```markdown
## [RUN][PHASE N][START]

**Executor:** <Codex surface/session identifier if available>
**Base commit:** `<sha>`
**Workflow label:** `in-progress`
**Target outcome:** <one sentence>
**Planned files:**
- `<path>`

**Planned validation:**
- `<command>`

**Model class:** <requested class>
**Model used:** <actual model, if visible>
**Known assumptions:**
- <assumption or none>

**Explicitly not doing:**
- <phase exclusions>
```

This comment is a prediction of the work, not a retrospective summary. Re-fetch the issue after posting it and confirm that the workflow label remains `in-progress`.

### 3. Implement only the bounded phase

During implementation:

- follow the issue and committed design documents;
- keep the change minimal and cohesive;
- add or update tests in the same phase;
- avoid unrelated cleanup;
- preserve baseline behavior unless the phase explicitly changes it;
- capture commands, outputs, measurements, and revisions needed to reproduce the result;
- stop if new evidence invalidates the approved plan.

Do not opportunistically fix adjacent issues. Record them separately.

### 4. Validate before committing

Run the exact phase validation plus any narrower checks needed during development.

Validation evidence must distinguish:

- commands actually run;
- commands not run;
- passed checks;
- failed checks;
- environmental limitations;
- observed deviations from the issue.

Never claim a test passed if it was not run successfully.

If validation cannot complete because of an external dependency, environment, permission, or failed gate:

1. replace `in-progress` with `blocked` and verify it;
2. record a phase-result comment with `Result: BLOCKED`;
3. include the exact restart condition;
4. stop.

### 5. Commit the phase

Create one intentional commit for the phase outcome where practical.

The commit must:

- contain only the phase work;
- include tests and source-of-truth updates required by that phase;
- avoid unrelated formatting;
- use a message describing the achieved outcome;
- remain reviewable without relying on uncommitted changes.

If review later requires corrections, add focused corrective commits associated with the same phase. Do not rewrite shared history unless explicitly authorized.

### 6. Write the phase-result issue comment

After committing, add:

```markdown
## [RUN][PHASE N][RESULT]

**Commit:** `<sha>`
**Workflow label:** `in-progress` | `blocked`
**Result:** COMPLETED | PARTIAL | BLOCKED

**Delivered:**
- <artifact or behavior>

**Files changed:**
- `<path>` — <reason>

**Validation actually run:**
- `<command>` — PASS | FAIL | BLOCKED

**Evidence:**
- <test counts, benchmark output, artifact path, relevant revision>

**Deviations from plan:**
- <none or exact deviation and reason>

**Unexpected findings:**
- <none or finding>

**Residual risks:**
- <none or risk>

**Reviewer target:**
- Exact commit or range: `<sha-or-range>`

**Restart condition when blocked:**
- <not applicable or exact condition>
```

Do not edit away failed attempts or deviations. The issue is an audit trail.

## Independent phase review

Launch a fresh Codex CLI process or isolated Codex session after every phase commit.

The reviewer must receive only the information needed to independently inspect the repository state:

- repository path and branch;
- issue number;
- exact commit or commit range;
- phase text and acceptance criteria;
- validation commands;
- instruction that the review is read-only.

The reviewer must not inherit the main executor's hidden reasoning, assumptions, or intended conclusion.

### Reviewer prompt contract

Use a prompt equivalent to:

```text
Act as an independent, read-only reviewer for Phase N of GitHub issue #<issue>.

Review exact commit/range <sha-or-range> against the phase specification and repository instructions. Do not implement fixes, do not edit files, do not create commits, do not change GitHub labels, and do not continue to later phases.

Verify:
1. the change matches the intended scope and architecture;
2. every expected artifact exists and is complete;
3. tests and validation are sufficient and their reported results are credible;
4. no unexpected files, behavior, dependencies, or source-of-truth changes were introduced;
5. the commit is safe to use as the base for the next phase;
6. any deviations, risks, or unverified claims are identified.

Return exactly one verdict: PASS, PASS_WITH_NOTES, FAIL, or BLOCKED.
For FAIL or BLOCKED, provide a minimal, actionable delta that the executor must resolve before progression.
Include the commands and evidence you inspected or ran.
```

Prefer a read-only sandbox or permissions profile. If an independent read-only review cannot be launched, the main executor must replace `in-progress` with `blocked`, verify it, record the missing review capability, and stop. Do not replace independent review with self-review and proceed silently.

### Reviewer issue comment

Write the review back to the issue:

```markdown
## [REVIEW][PHASE N]

**Reviewed commit/range:** `<sha-or-range>`
**Verdict:** PASS | PASS_WITH_NOTES | FAIL | BLOCKED

**Scope compliance:** <assessment>
**Expected artifacts:** <assessment>
**Validation assessment:** <assessment>
**Unexpected changes:** <none or list>
**Safety to proceed:** YES | NO

**Evidence inspected or run:**
- `<command or artifact>` — <result>

**Required delta before progression:**
- <none or exact corrective action>

**Notes carried forward:**
- <none or note>
```

### Progression rule

- `PASS`: keep `in-progress` and proceed to the next phase.
- `PASS_WITH_NOTES`: keep `in-progress` and proceed only when notes do not violate an exit gate; copy relevant notes into the next phase-start comment.
- `FAIL`: keep `in-progress`; do not proceed to the next phase. The next main-executor iteration may only fix the stated delta, validate it, commit it, and request a fresh review.
- `BLOCKED`: replace `in-progress` with `blocked`, verify it, and do not proceed until the missing evidence, environment, permission, or decision is resolved.

The executor may not overrule the reviewer without an explicit issue update from the design authority or user.

## Resuming a blocked issue

A blocked issue may resume only after an explicit issue update documents that the restart condition has been satisfied.

Use one of these paths:

- **Revised contract or clean restart:** design authority replaces `blocked` with `execution-ready`. The executor then repeats the complete entry gate and changes it to `in-progress` before mutation.
- **Safe continuation of the same approved phase:** an explicit design-authority or user issue comment authorizes resume. The executor replaces `blocked` with `in-progress`, verifies it, and continues only from the documented boundary.

Never resume merely because the external condition appears to have changed.

## Pull request discipline

Use one PR for one approved issue unless the issue explicitly defines multiple PRs.

The PR should remain draft while phases are incomplete. Its description should link the issue and summarize:

- approved goal and scope;
- phase/commit mapping;
- validation status;
- known deviations;
- unresolved risks;
- source-of-truth documents changed;
- final review status.

The commit history should make the execution plan understandable. Do not squash or reorder phase history before final review unless explicitly requested.

## Final review gate

After all phases pass:

1. verify that the issue still has exactly the `in-progress` workflow label;
2. update repository status, decisions, plans, and validation evidence required by `AGENTS.md`;
3. run the complete final validation suite from the issue;
4. confirm the branch has no accidental or uncommitted changes;
5. prepare a final issue comment and PR summary;
6. request external final review from ChatGPT using the strongest suitable reasoning model;
7. do not merge until that review approves the PR and the user explicitly authorizes merge when required.

### Final handoff comment

```markdown
## [RUN][FINAL HANDOFF]

**PR:** #<number>
**Head commit:** `<sha>`
**Workflow label:** `in-progress`
**Issue phases:** <all PASS/PASS_WITH_NOTES>

**Phase-to-commit map:**
- Phase 1 — `<sha>` — <outcome>

**Final validation:**
- `<command>` — PASS | FAIL | BLOCKED

**Source-of-truth updates:**
- `<path>` — <summary>

**Deviations and accepted notes:**
- <none or list>

**Known residual risks:**
- <none or list>

**Requested final review:**
Verify the complete PR against the issue, all phase reviews, repository architecture, validation evidence, workflow-state history, and out-of-scope boundaries. Approve, request changes, or recommend restart.
```

The final reviewer should inspect the entire diff and issue history, not only the latest commit.

When final validation or review is blocked, replace `in-progress` with `blocked`, verify it, document the restart condition, and stop.

After approved merge and completion of all exit criteria, close the issue. Do not add a `completed` workflow label.

## Restart-over-repair policy

When the problem is local and the approved design remains valid, fix the smallest reviewed delta.

When the implementation reveals that the specification, architecture, phase decomposition, validation strategy, or governing skill is materially wrong, prefer a clean restart over compounding patches.

A restart means:

1. stop implementation;
2. replace `in-progress` with `design-required` or `blocked`, according to the cause, and verify it;
3. preserve the failed branch and PR for traceability rather than deleting evidence;
4. mark the attempt as superseded or abandoned in the issue/PR;
5. record the root cause and which planning artifact failed;
6. update the specification, plan, `AGENTS.md`, and/or skills first;
7. create a clean branch from the intended base after the issue returns to `execution-ready`;
8. implement the corrected plan without blindly carrying forward generated code;
9. reuse old changes only when each reused unit is explicitly justified and revalidated.

Recommend a restart when any of these are true:

- the architecture is wrong or no longer accepted;
- the implementation crossed explicit scope boundaries;
- several phases were bundled and cannot be independently reviewed;
- tests validate the wrong behavior;
- generated structure is broadly inconsistent with repository design;
- fixes are becoming compensating patches for an incorrect foundation;
- provenance or traceability is too weak to trust the result;
- the reviewer cannot determine what changed or why.

Do not destructively delete branches, close PRs, or discard user work without explicit authorization. Prefer superseding and preserving the audit trail.

## Prohibited shortcuts

Do not:

- implement a non-trivial task without an approved issue contract;
- start unless the issue has exactly `execution-ready`, or resume unless it has an explicitly valid `in-progress` state;
- edit files before changing and verifying `execution-ready` to `in-progress`;
- write a status comment without performing the corresponding workflow-label mutation;
- leave multiple workflow-state labels on the issue;
- infer architecture from chat when committed documents disagree;
- execute more than one bounded phase before review;
- let the reviewer modify code, labels, or continue implementation;
- self-certify a phase when independent review is required;
- retroactively rewrite issue history to hide failures;
- claim unrun validation;
- mix unrelated cleanup into a phase commit;
- silently change models, scope, dependencies, or acceptance criteria;
- merge before the final external review gate;
- keep patching when the plan itself is the defect.

## Completion criteria

This workflow is complete only when:

- every required workflow-label transition is present and verified in issue history;
- every phase has start, result, and review records;
- every phase has a traceable commit or explicitly documented no-code outcome;
- all required validation has passed or has an explicitly accepted exception;
- the PR matches the approved scope;
- repository source-of-truth documents are current;
- the final external review has examined both the PR and issue history;
- no unresolved `FAIL` or `BLOCKED` verdict remains;
- merge occurs only under the repository's authorization rules;
- the issue is closed only after all exit criteria are supported by committed evidence.