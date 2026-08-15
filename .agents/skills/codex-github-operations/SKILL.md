---
name: codex-github-operations
description: Publish branches and commits, operate issues and pull requests, and preserve exact review targets using the simplest available Git and GitHub transport without turning metadata synchronization or parent gitlink churn into implementation gates.
---

# Codex GitHub Operations

## Responsibility

This skill owns Git publication and GitHub control-plane operations requested by the calling workflow.

It does not decide architecture, implementation scope, correctness, review requirements, or progression. Those decisions belong to the controlling issue, executor, design authority, and independent reviewer.

## Use the simplest capable transport

### Local Git

Use local `git` for worktree inspection, branches, commits, fetch, push, and exact ref verification.

### Connected GitHub app

Prefer the connected app for issues, comments, labels, pull requests, reviews, and metadata.

### GitHub CLI

Use `gh` only when it is already available and offers a needed operation not covered by the connected app. Do not install or authenticate it merely for routine publication.

A failure of one replaceable transport is not a technical blocker when another route or a precise handoff can complete the operation.

## Workflow state

The controlling issue's current workflow state is authoritative only through exactly one state label. The allowed state labels are:

- `execution-ready`;
- `in-progress`;
- `design-required`;
- `investigation-required`;
- `blocked`;
- `completed`.

Every non-trivial controlling issue must carry exactly one of those labels. Preserve all unrelated labels, but replace the previous state label instead of adding another. The issue body may record the initial readiness at publication; body prose and comments do not override the current state label.

Apply the corresponding transition when work:

- becomes ready: `execution-ready`;
- starts or resumes active execution: `in-progress`;
- returns for a material design decision: `design-required`;
- needs bounded evidence before design can continue: `investigation-required`;
- encounters a real external blocker: `blocked`;
- reaches its accepted terminal outcome and closes: `completed`.

A transition that changes only workflow state is a label mutation, not an issue comment. Add a comment only when material technical information must be preserved, such as a contract amendment, exact checkpoint target or verdict, blocker cause and unavailable capability, failed evidence, or final handoff. Do not repeat a `State:` field in those comments.

Before relying on issue state, verify that exactly one state label is present. If the label is missing or multiple state labels exist and the intended state is unambiguous, repair the labels without adding a comment. If the intended state is ambiguous, stop and request state clarification. Do not rewrite historical comments or the issue body merely to mirror a routine label transition.

## Publish a branch

Before publication:

- confirm the intended branch;
- ensure unrelated changes are not included;
- require a clean worktree unless the caller explicitly documents otherwise;
- do not rewrite shared valid history.

Publish and verify the remote ref with local Git. Use the full SHA when another actor must inspect an exact target.

Do not repeat the full SHA in every issue comment, PR update, or handoff. Git and GitHub already preserve ordinary branch and commit identity.

## Nested repository publication

When implementation occurs primarily in nested `llama.cpp`:

- publish coherent nested commits to the nested branch as implementation or collaboration requires;
- do not create a parent-repository gitlink commit for every nested commit;
- update the parent gitlink at a declared checkpoint, final integration candidate, or explicit compatibility/recovery boundary;
- verify that the committed parent gitlink equals the exact nested target before requesting a combined parent+nested review;
- never present an uncommitted or unpublished nested checkout as a reviewable parent target.

If parent-side code, tests, evidence, or configuration depends on an intermediate nested revision, the controlling issue may require an earlier gitlink boundary. Otherwise avoid parent commits whose only purpose is to mirror routine nested progress.

## Active Codex PR ownership

Once a Codex executor creates or adopts a pull request for a controlling issue, that executor is the exclusive owner of the PR head branch and PR control plane until the PR is merged/closed or the user explicitly transfers ownership.

Other actors, including ChatGPT acting as design authority, may inspect that PR and branch read-only but must not:

- push, commit, update, delete, reset, rebase, or otherwise modify the PR head branch;
- edit PR title, body, base, state, draft state, labels, reviewers, or other PR metadata;
- add PR conversation comments, inline review comments, reviews, reactions, or other messages intended for the Codex executor;
- merge, close, reopen, or otherwise operate the PR.

The **controlling issue is the only communication channel between ChatGPT/design authority and the Codex executor**. Contract amendments, design decisions, blockers, evidence requests, handoffs, and corrections for an active Codex execution must be posted on the controlling issue. The executor must inspect new material controlling-issue comments when resuming. Do not use PR discussion as a side channel between those actors.

If the user explicitly asks ChatGPT/design authority to make an independent durable repository edit while a Codex-owned PR is active, the edit may target the default branch or a separate non-Codex branch only when it does not require taking ownership of the active PR. Never move or rewrite the Codex PR head. If the requested change belongs to the active issue's implementation/evidence diff, communicate the requirement through the controlling issue and let the owning Codex executor integrate it.

A transport limitation, convenience, documentation-only change, or apparently harmless metadata edit is not an exception to this ownership boundary. When ownership is ambiguous, stop and resolve it through the controlling issue before mutating the PR or branch.

## Pull requests

Create or reuse one PR for one controlling issue unless the issue explicitly requires decomposition.

The PR should:

- use the intended base and head;
- link the controlling issue;
- summarize delivered behavior;
- state current validation and review status;
- list material deviations or residual risks.

GitHub already records branches, commits, files, checks, and discussion. Do not duplicate complete histories, manifests, command logs, phase ledgers, or routine SHAs in the PR body.

Keep the PR draft while required implementation or review remains incomplete. Mark ready or merge only when the calling workflow authorizes it.

## Exact review targets

An independent review request must identify one exact published project commit or range and the exact nested target when applicable. Verify those targets before review and preserve them unchanged during the review.

Do not amend, recreate, reset, rebase, squash, cherry-pick, or force-push a valid target merely to repair comments, state labels, PR descriptions, transport, roadmap state, or other workflow metadata.

A new implementation, test, technical-evidence, dependency, configuration, or technical-claim correction creates a new target; it does not erase the prior reviewed finding.

A final-capable checkpoint may serve as the final PR review when the issue and reviewer confirm that it covers the complete final diff and immutable technical evidence. Do not request another review of the unchanged target merely because a state label, PR prose, merge metadata, or roadmap state changed.

## Technical evidence and workflow metadata

Technical manifests do not need branch names, issue or PR numbers, comment IDs, review verdicts, labels, merge commits, or closeout state unless the controlling issue identifies one as a technical input to the tested system.

Do not mutate implementation or evidence commits solely to embed GitHub review or merge state. Record the external review against the exact immutable target in the issue or PR discussion.

## Degraded control-plane operation

When a requested GitHub operation cannot be completed in the current surface:

1. try another permitted transport when practical;
2. preserve branch and commits unchanged;
3. leave a concise handoff containing the target issue or PR, requested operation, current branch or PR, and exact SHA only when needed to disambiguate;
4. verify the operation before relying on it later.

Use a real `BLOCKED` outcome only when the missing operation is required before safe meaningful progress, no alternative or handoff exists, and continuing risks conflicting ownership, lost work, or violation of an explicit gate.

## Safety

- Never force-push or rewrite shared history without explicit authorization.
- Never stage or publish unrelated changes.
- Never publish secrets, model weights, generated binaries, prohibited artifacts, or evidence without distribution rights.
- Never silently change the controlling issue, base branch, head branch, labels, or PR state.
- Never mutate implementation commits to compensate for GitHub transport limitations.
- Never claim a state change that was not observed.

## Completion report

Report only the operational facts the caller needs:

- branch or PR affected;
- operation completed;
- verification result;
- exact project/nested target only when another actor must use it;
- degraded operation or real blocker, if any.

Do not add administrative ledgers or repeat information already visible in the linked GitHub object.