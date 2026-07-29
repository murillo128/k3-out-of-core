---
name: codex-github-operations
description: Publish Codex branches and exact commits, mutate GitHub workflow state, and create or update issues and pull requests using local git plus the best available GitHub control-plane transport. Use whenever the executor needs Git publication or GitHub operations. Do not use this skill to decide technical scope, review verdicts, or workflow progression.
---

# Codex GitHub Operations

## Responsibility

This skill owns operational transport for Git and GitHub.

It does not:

- implement repository changes;
- decide architecture or issue scope;
- decide whether independent review is required;
- classify implementation correctness;
- decide progression between phases.

Those decisions belong to the calling workflow and approved issue.

## Capability model

Choose tools by capability, not by product preference.

### Local Git

Use local `git` for:

- repository and worktree inspection;
- branch creation and switching;
- commits;
- authoritative full SHAs;
- fetch and push;
- local and remote ref verification.

### GitHub control plane

Prefer a connected GitHub app or connector for:

- issues and comments;
- workflow labels;
- pull-request creation and metadata;
- reviews and discussion.

### GitHub CLI

Use `gh` only as a fallback when it is already available and authenticated, or when an approved issue explicitly requires a CLI-only operation.

Missing or unauthenticated `gh` is not a blocker when local Git and a connected GitHub control-plane transport can complete the required work.

Do not install or authenticate `gh` merely to publish a branch or create a pull request.

## Workflow-state operations

Workflow labels summarize durable issue state. They do not gate every comment or command.

The calling workflow may request one of these transitions:

```text
design-required
investigation-required
execution-ready
in-progress
blocked
```

When a transition is requested:

1. fetch the issue and inspect the current workflow label;
2. preserve unrelated labels;
3. apply the requested workflow label using the best available GitHub transport;
4. fetch the issue again and verify the resulting workflow label;
5. report the observed before and after state.

A transition should normally be performed:

- when execution first starts;
- when an explicitly authorized blocked issue resumes;
- when work returns to design or investigation;
- when a real unresolved blocker prevents meaningful progress;
- when the issue is closed after completion.

Do not require redundant label verification before every phase comment, commit, review, or Git operation.

## Degraded control-plane operation

Failure of one GitHub transport does not automatically block technical work.

When the requested control-plane operation cannot be completed in the current surface:

1. try another permitted transport;
2. preserve the repository branch and commit unchanged;
3. produce a precise handoff containing repository, issue or PR, requested operation, current observed state, branch, and exact SHA;
4. let a connector-capable session perform the operation;
5. verify the result before relying on it.

Use `CONTROL_PLANE_DEGRADED` for a recoverable operation that can be handed off without invalidating implementation work.

Use `BLOCKED` only when:

- the operation is required before meaningful technical progress;
- no permitted transport or timely handoff can perform it;
- and continuing would create conflicting ownership, lose work, or violate an explicit issue gate.

Do not describe an issue label or PR state as changed until it has actually been verified.

## Publish an exact commit

Determine branch and commit programmatically:

```bash
BRANCH="$(git branch --show-current)"
COMMIT="$(git rev-parse HEAD)"
```

Require a clean worktree and publish without rewriting history:

```bash
test -n "$BRANCH"
test -z "$(git status --porcelain)"
git push -u origin "$BRANCH"
git fetch origin
test "$(git rev-parse "origin/$BRANCH")" = "$COMMIT"
```

Rules:

- never manually expand or guess a short SHA;
- never amend, recreate, reset, rebase, squash, or cherry-pick a valid phase commit merely to publish it;
- report the authoritative full SHA from `git rev-parse HEAD`;
- classify authentication, permission, network, or remote-SHA failures separately from implementation failures.

A published branch is sufficient to preserve and independently inspect an exact commit. A pull request is not required before that inspection unless the issue explicitly says otherwise.

## Pull requests

Create a draft pull request after the first useful published commit, or at the latest before the first checkpoint that benefits from PR-level review.

Prefer the connected GitHub app or connector. Use `gh pr create` only as an available fallback.

The pull request must:

- use the approved base and head branches;
- remain draft while execution is incomplete;
- link the controlling issue;
- identify the exact head SHA;
- summarize phase-to-commit mapping and validation state.

Failure to create the PR in the current surface is normally a handoff, not an implementation failure. It becomes a blocker only when the approved workflow requires the PR before further meaningful work and no alternative can create it.

## Real blockers

Return an operational `BLOCKED` result only for unresolved conditions such as:

- failed Git authentication or denied push permission;
- network failure preventing required publication;
- remote SHA mismatch;
- an unsafe conflicting branch or pull request;
- an unavailable required GitHub operation with no permitted transport or handoff;
- inability to verify a state that the workflow must rely on before continuing.

The absence or failure of one optional transport is not itself a blocker.

## Safety

- Never force-push or rewrite shared history without explicit user authorization.
- Never stage or publish unrelated changes.
- Never publish secrets, model weights, generated binaries, or prohibited artifacts.
- Never change the approved issue, base branch, head branch, labels, or PR state silently.
- Never mutate implementation commits to compensate for GitHub transport limitations.

## Completion report

Report only the operational facts the caller needs:

- repository and branch;
- exact local and remote SHA;
- push and verification result;
- GitHub operation requested and transport used;
- observed before and after state;
- PR number and draft state when applicable;
- handoff, degraded operation, or real blocker;
- confirmation that implementation history was not rewritten.
