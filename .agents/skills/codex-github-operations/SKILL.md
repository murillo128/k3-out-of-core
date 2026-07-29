---
name: codex-github-operations
description: Publish exact Codex commits and perform GitHub issue, label, comment, branch, and pull-request operations using local git plus the best available GitHub control-plane transport. Use from Codex execution workflows after a phase commit or whenever workflow state must be mutated and verified.
---

# Codex GitHub Operations

## Responsibility

This skill owns Git and GitHub transport decisions for Codex execution.

It does not implement repository features, review code, decide architecture, or decide whether a phase may progress. The calling workflow owns those decisions.

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

## Workflow-state mutations

When the calling workflow requires an issue-label transition:

1. fetch the issue and inspect the current labels;
2. use the connected GitHub transport to replace the current workflow label;
3. fetch the issue again;
4. verify that exactly the intended workflow label is present;
5. stop if mutation or verification fails.

Use `gh issue edit` only as a permitted fallback.

Writing a status word in an issue comment does not mutate a GitHub label.

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

When local `git push` succeeds but the current Codex surface cannot create or mutate the required GitHub object:

1. record the branch, exact remote SHA, repository, and requested GitHub operation on the issue when possible;
2. request a connector-capable ChatGPT or Codex session to perform that operation;
3. preserve the existing commit and branch unchanged;
4. do not classify the implementation itself as failed;
5. block progression only when the calling workflow requires that GitHub object before the next step.

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

## Completion output

Report:

- repository and branch;
- exact published commit SHA;
- remote-SHA verification result;
- GitHub transport used for each control-plane operation;
- pull-request number and draft state when created;
- any handoff or real blocker;
- confirmation that the implementation commit was not rewritten.