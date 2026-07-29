---
name: codex-independent-review
description: Review an exact published commit in a fresh isolated read-only Codex context and return a structured phase verdict. Use after every bounded phase commit governed by spec-driven-codex-loop.
---

# Codex Independent Review

## Responsibility

This skill owns reviewer isolation, reviewer-transport selection, evidence inspection, and structured verdict reporting.

It does not implement fixes, mutate workflow labels, redesign the issue, create commits, or continue to a later phase.

## Preconditions

The review request must identify:

- repository and checkout path when local execution is required;
- branch;
- controlling issue number;
- execution phase;
- exact full commit SHA or explicit commit range;
- phase specification and acceptance criteria;
- validation commands;
- instruction that the review is read-only.

The target commit should normally be published and remotely resolvable so that the reviewer does not depend on the executor's private checkout.

A draft pull request is useful but is not required to review an exact published commit unless the approved issue explicitly requires it.

## Independence requirements

The reviewer must:

- run in a fresh context;
- not inherit the executor's hidden reasoning or intended conclusion;
- inspect the exact target commit or range;
- remain read-only;
- not modify files;
- not create commits;
- not mutate GitHub labels or workflow state;
- not implement corrections;
- not continue to a later phase.

The main executor may not act as its own independent reviewer.

## Reviewer transport order

Try available independent transports in this order when practical:

1. a fresh read-only Codex CLI process;
2. another fresh Codex CLI sandbox or permissions profile;
3. a fresh isolated Codex Desktop session;
4. another approved independent read-only Codex environment.

The exact product surface is less important than fresh context, isolation, read-only behavior, and access to the exact commit.

## Transport-failure policy

A launcher or sandbox error such as:

```text
bwrap: loopback: Failed RTM_NEWADDR
```

is a reviewer-transport failure. It is not evidence that:

- the implementation is defective;
- phase validation failed;
- the phase commit must change;
- the commit must be recreated or amended;
- the phase should be redesigned.

When one reviewer transport fails:

1. record the transport and exact error;
2. preserve the exact review target;
3. retry with another permitted fresh isolated reviewer;
4. do not fall back to main-executor self-review.

Return `BLOCKED` only when no permitted independent reviewer can access or inspect the exact published target, or when required review evidence is unavailable.

## Review procedure

Verify:

1. the change matches the approved phase scope and accepted architecture;
2. every expected artifact exists and is complete;
3. the reported validation commands and results are credible;
4. required validation can be reproduced or independently inspected;
5. no unexpected files, behavior, dependencies, secrets, or source-of-truth changes were introduced;
6. the commit is safe to use as the base for the next phase;
7. deviations, residual risks, and unverified claims are explicit.

Inspect the exact diff against the approved phase base. Use the published commit rather than a manually transcribed or guessed SHA.

Run the issue-defined validation when the reviewer environment supports it. Distinguish clearly between:

- evidence inspected;
- commands personally run;
- commands not run;
- environmental limitations;
- implementation failures;
- transport failures.

A reviewer must never claim a command passed when it was not run successfully.

## Verdict semantics

Return exactly one verdict:

- `PASS`: scope, artifacts, and validation are sufficient; progression is safe.
- `PASS_WITH_NOTES`: progression is safe, but non-blocking notes must be carried forward.
- `FAIL`: the implementation, evidence, or phase compliance is defective and requires a bounded corrective delta.
- `BLOCKED`: the independent review cannot be completed because required access, environment, dependency, commit publication, or evidence is unavailable.

A single failed launcher is not sufficient for `BLOCKED` when another reviewer transport is permitted.

## Reviewer prompt contract

Use a prompt equivalent to:

```text
Act as an independent, read-only reviewer for execution Phase N of GitHub
issue #ISSUE.

Review exact published commit/range SHA against the phase specification and
repository instructions. Do not implement fixes, edit files, create commits,
change GitHub labels, or continue to later phases.

Verify scope compliance, expected artifacts, validation credibility,
unexpected changes, residual risks, and safety to proceed.

Return exactly one verdict: PASS, PASS_WITH_NOTES, FAIL, or BLOCKED.
For FAIL or BLOCKED, provide the smallest actionable delta or exact restart
condition. Distinguish implementation failures from reviewer-transport
failures and include the commands and evidence inspected or run.
```

## Issue comment

Post a comment with this structure:

```markdown
## [REVIEW][PHASE N]

**Reviewed commit/range:** `<full SHA or range>`
**Reviewer transport:** `<Codex CLI | Codex Desktop | other>`
**Verdict:** PASS | PASS_WITH_NOTES | FAIL | BLOCKED
**Safety to proceed:** YES | NO

**Scope compliance:** <assessment>
**Expected artifacts:** <assessment>
**Validation assessment:** <assessment>
**Unexpected changes:** <none or list>

**Evidence inspected or run:**
- `<command or artifact>` — <result>

**Transport limitations:**
- <none or exact limitation>

**Required delta before progression:**
- <none, corrective delta, or restart condition>

**Notes carried forward:**
- <none or note>
```

## Handoff to the orchestrator

The calling `spec-driven-codex-loop` workflow owns workflow-label transitions and progression.

- For `PASS`, report that progression is safe.
- For `PASS_WITH_NOTES`, report the notes that must be copied into the next phase-start record.
- For `FAIL`, report only the bounded corrective delta; do not implement it.
- For `BLOCKED`, report whether the blocker is missing publication, unavailable evidence, or exhausted independent-review transports.

## Safety rules

- Never review a different commit because the requested SHA is unavailable.
- Never correct an invalid SHA by guessing; obtain the authoritative full SHA from Git or the remote.
- Never modify the worktree to make validation pass.
- Never convert a transport error into an implementation failure.
- Never approve based only on the executor's narrative when the exact diff or required evidence cannot be inspected.