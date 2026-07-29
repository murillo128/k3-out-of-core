---
name: codex-github-operations
description: Publish exact Codex commits and perform GitHub issue, label, comment, branch, and pull-request operations using local git plus the best available GitHub control-plane transport. Use as the first operational skill when Codex starts or resumes execution, whenever workflow state must change, after each phase commit, and for GitHub publication or handoff.
---

# Codex GitHub Operations

## Responsibility

This skill owns Git and GitHub transport decisions for Codex execution.

It does not implement repository features, review code, decide architecture, or decide whether a phase may progress. The calling workflow owns those decisions.

Workflow-state mutation is a hard execution gate, not bookkeeping. A required label transition that has not been performed and verified means execution has not started, resumed, or blocked correctly.

## Transport model

Treat the local Git repository and the GitHub control plane as separate capabilities.

### Local repository operations

Always use local `git` for:

- inspecting repository and worktree state;
- creating or switching branches;
- creating commits;
- obtaining authoritative commit SHAs;
- fetching and pushing refs;
- verifying local and remote SHAs.

### GitHub control-plane operations

Prefer an available connected GitHub app or connector for:

- reading and updating issues;
- workflow-label mutations;
- issue comments;
- pull-request creation and metadata;
- pull-request reviews and discussion.

### GitHub CLI

Use `gh` only as a fallback when:

- the connected GitHub transport cannot perform the required operation;
- the operation is explicitly CLI-only;
- or the approved issue contract explicitly requires GitHub CLI.

Missing or unauthenticated `gh` is not a blocker when the required operation can be completed with local `git` and a connected GitHub control-plane transport.

Do not install or authenticate `gh` merely because a branch must be pushed or a pull request must be created.

## Non-negotiable workflow-state preflight

This preflight is the first GitHub write operation for every main-executor run that starts, resumes, or blocks work.

Before any of the following actions:

- creating or switching to an execution branch;
- editing a repository file;
- creating a commit or pull request;
- posting a phase-start, phase-result, blocked, or handoff comment;
- launching the implementation portion of a phase;

the executor must establish and verify the required machine-readable workflow state.

The only successful output of this preflight is:

```text
STATE_TRANSITION_VERIFIED: <expected-label>
```

Without that verified result, stop. Do not continue with a warning, a comment-only status update, or an assumption that another actor will fix the label later.

### Start execution

To begin a new approved execution:

1. fetch the issue;
2. verify that exactly one workflow-state label exists and it is `execution-ready`;
3. replace `execution-ready` with `in-progress`;
4. fetch the issue again;
5. verify that exactly one workflow-state label exists and it is `in-progress`;
6. emit `STATE_TRANSITION_VERIFIED: in-progress`;
7. only then post the phase-start comment or mutate the repository.

If the issue is already `in-progress`, treat it as a resume only when the calling workflow has explicitly validated that the issue history identifies the same active phase and no unresolved blocker exists. Re-fetch and verify the single `in-progress` label before emitting the success result.

### Block execution

When the calling workflow determines that execution is blocked:

1. fetch the issue and identify its current single workflow-state label;
2. replace that label with `blocked`;
3. fetch the issue again;
4. verify that exactly one workflow-state label exists and it is `blocked`;
5. emit `STATE_TRANSITION_VERIFIED: blocked`;
6. only then post the blocked comment containing evidence and the restart condition.

A `[BLOCKED]` comment posted while the label remains `execution-ready` or `in-progress` is an invalid workflow transition.

### Return to design or investigation

When the calling workflow requires `design-required` or `investigation-required`, perform the same mutate–re-fetch–verify sequence before posting the explanatory comment.

## Workflow-state mutations

When the calling workflow requires an issue-label transition:

1. fetch the issue and inspect all current labels;
2. isolate labels from this exact workflow-state set:
   - `design-required`
   - `investigation-required`
   - `execution-ready`
   - `in-progress`
   - `blocked`
3. require exactly one current workflow-state label unless repairing an explicitly detected invalid state;
4. remove the previous workflow-state label;
5. add the required new workflow-state label;
6. fetch the issue again;
7. require exactly one workflow-state label and require it to equal the intended label;
8. return the explicit `STATE_TRANSITION_VERIFIED` result;
9. stop if mutation or verification fails.

Prefer a single connector mutation that replaces the full label set when that can preserve all non-workflow labels safely. Otherwise remove the old workflow label and add the new one, then verify the final state.

Use `gh issue edit` only as a permitted fallback.

Writing `EXECUTION_READY`, `IN_PROGRESS`, `BLOCKED`, or another status in the issue body or a comment does not mutate a GitHub label and never satisfies this gate.

Equivalent CLI pattern:

```bash
WORKFLOW_LABELS='["design-required","investigation-required","execution-ready","in-progress","blocked"]'

current="$(gh issue view "$ISSUE" --repo "$REPO" --json labels \
  --jq --argjson wf "$WORKFLOW_LABELS" '[.labels[].name | select(. as $x | $wf | index($x))]')"

test "$(jq 'length' <<<"$current")" -eq 1
test "$(jq -r '.[0]' <<<"$current")" = "execution-ready"

gh issue edit "$ISSUE" --repo "$REPO" \
  --remove-label execution-ready \
  --add-label in-progress

verified="$(gh issue view "$ISSUE" --repo "$REPO" --json labels \
  --jq --argjson wf "$WORKFLOW_LABELS" '[.labels[].name | select(. as $x | $wf | index($x))]')"

test "$(jq 'length' <<<"$verified")" -eq 1
test "$(jq -r '.[0]' <<<"$verified")" = "in-progress"
printf '%s\n' 'STATE_TRANSITION_VERIFIED: in-progress'
```

Do not claim transition success from the mutation response alone. The post-mutation fetch and exact verification are mandatory.

## Transport failure during a required transition

Try the available transports in this order:

1. connected GitHub app or connector;
2. already available and authenticated `gh` fallback.

If neither transport can perform and verify the required mutation:

- stop before repository mutation or status comment;
- report the intended transition, current observed label, attempted transports, and exact failure to the calling workflow;
- request a connector-capable handoff when available;
- do not post a misleading phase or blocked comment that implies the label changed;
- do not continue execution while waiting for another actor to repair state.

The calling workflow may classify the run as operationally blocked, but the issue's machine-readable state must not be described as changed until a capable actor performs and verifies the mutation.

## Publish an exact phase commit

Determine the authoritative branch and commit programmatically:

```bash
BRANCH="$(git branch --show-current)"
COMMIT="$(git rev-parse HEAD)"
```

Require:

```bash
test -n "$BRANCH"
test -z "$(git status --porcelain)"
git push -u origin "$BRANCH"
git fetch origin
test "$(git rev-parse "origin/$BRANCH")" = "$COMMIT"
```

Never manually expand, infer, or transcribe a short SHA.

Do not amend, recreate, rebase, squash, reset, cherry-pick, or otherwise replace an existing phase commit merely to publish it.

Record the exact full SHA returned by `git rev-parse HEAD` in all issue and review handoffs.

## Draft pull request

After the branch is published:

1. prefer the connected GitHub app or connector;
2. use the approved repository, base branch, and head branch;
3. create a draft pull request unless the issue explicitly requires another state;
4. verify that the pull-request head resolves to the published exact commit;
5. link the controlling issue and summarize the phase-to-commit mapping.

Use `gh pr create` only when connector-based PR creation is unavailable and `gh` is already usable.

A draft pull request is not required to inspect an exact published commit unless the approved issue explicitly says otherwise. It must normally exist before the next implementation phase begins.

## Connector handoff

When local `git push` succeeds but the current Codex surface cannot create or mutate a required GitHub object:

1. preserve the existing commit and branch unchanged;
2. record locally the branch, exact remote SHA, repository, observed state, intended operation, and transport failures;
3. request a connector-capable ChatGPT or Codex session to perform the exact operation;
4. do not classify the implementation commit itself as failed;
5. do not progress past any gate that requires that GitHub object;
6. after handoff, re-fetch and verify the object before resuming.

For a required workflow-label transition, do not use an issue comment as the handoff record when posting that comment would falsely imply the transition completed. Return the handoff details to the caller instead.

## Real blockers

Return `BLOCKED` only for a real unresolved condition such as:

- failed Git authentication;
- denied push permission;
- network failure preventing publication;
- remote SHA mismatch;
- conflicting remote branch or pull request that cannot be resolved safely;
- unavailable required GitHub operation with no permitted connector or fallback;
- inability to verify the requested mutation.

The absence or failure of one optional transport is not by itself a blocker.

## Safety rules

- Never force-push or rewrite shared history without explicit user authorization.
- Never push a dirty worktree as if it represented the recorded phase commit.
- Never publish secrets, model weights, generated binaries, or other files prohibited by `AGENTS.md`.
- Never silently change the approved base branch, head branch, issue, labels, or PR state.
- Never let a GitHub transport limitation mutate the implementation commit.
- Never post a workflow-state comment before the corresponding label transition has been verified.
- Never proceed after a required state transition returns anything other than `STATE_TRANSITION_VERIFIED: <expected-label>`.

## Completion output

Report:

- repository and branch;
- exact published commit SHA;
- remote-SHA verification result;
- GitHub transport used for each control-plane operation;
- workflow-state transition requested, observed before state, observed after state, and verification result;
- pull-request number and draft state when created;
- any handoff or real blocker;
- confirmation that the implementation commit was not rewritten.