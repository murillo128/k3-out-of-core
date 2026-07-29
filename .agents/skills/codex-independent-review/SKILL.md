---
name: codex-independent-review
description: Review an exact published commit or range in a fresh isolated read-only context and return a structured verdict. Use only at review checkpoints declared by a STANDARD issue, after every phase in HIGH_ASSURANCE, or for final pull-request review.
---

# Codex Independent Review

## Responsibility

This skill owns:

- reviewer independence;
- reviewer-transport selection and fallback;
- inspection of the exact requested commit or range;
- evidence assessment;
- structured verdict reporting.

It does not implement fixes, mutate workflow labels, redesign the issue, create commits, or continue execution.

## When review is required

### STANDARD profile

Independent review is required only at checkpoints explicitly declared by the issue and at final handoff.

Typical checkpoints group related low-risk phases, for example:

- environment, fixtures, and tokenizer preparation;
- numerical format and backend correctness;
- benchmark methodology and final closeout.

A mechanical phase does not require its own independent review merely because it created a commit.

### HIGH_ASSURANCE profile

Independent review is required after every phase commit and at final handoff.

The issue must explicitly select this profile. Do not infer it from task size alone.

## Review target

The request must identify:

- repository;
- controlling issue;
- execution profile;
- checkpoint or phase;
- exact full commit SHA or explicit range;
- approved scope and acceptance criteria;
- relevant validation commands;
- read-only instruction.

The target should normally be published and remotely resolvable. A draft pull request is useful but is not required to review an exact published commit unless the issue explicitly requires PR context.

## Independence

The reviewer must:

- use a fresh context;
- not inherit the executor's hidden reasoning or intended conclusion;
- inspect exactly the requested target;
- remain read-only;
- not modify files, create commits, mutate labels, or implement corrections;
- not continue to later phases.

The main executor may not act as its own independent reviewer.

## Reviewer transport

Use any fresh isolated read-only reviewer that can inspect the exact target, such as:

1. a fresh Codex CLI process;
2. another Codex CLI sandbox or permissions profile;
3. a fresh isolated Codex Desktop session;
4. another approved independent environment.

The product surface is secondary to independence, exact-target access, and read-only behavior.

## Transport failures

A launcher or sandbox error is a transport failure, not an implementation verdict.

Examples include:

```text
bwrap: loopback: Failed RTM_NEWADDR
```

When one transport fails:

1. record `TRANSPORT_FAILED` with the exact error;
2. preserve the exact review target;
3. try another permitted fresh isolated reviewer;
4. do not amend or recreate the implementation commit;
5. do not fall back to main-executor self-review.

Return review verdict `BLOCKED` only when no permitted independent reviewer can inspect the target or required evidence is genuinely unavailable.

A single failed launcher is never sufficient for `BLOCKED` when another reviewer transport remains available.

## Review procedure

Verify:

1. scope compliance against the checkpoint or phase specification;
2. completeness of expected artifacts;
3. credibility and reproducibility of reported validation;
4. unexpected files, dependencies, secrets, behavior, or source-of-truth changes;
5. explicit deviations and residual risks;
6. safety to continue from the reviewed target.

Inspect the exact diff or range. Run issue-defined validation when the reviewer environment supports it.

Distinguish clearly between:

- evidence inspected;
- commands personally run;
- commands not run;
- environmental limitations;
- implementation failures;
- reviewer-transport failures.

Never claim a command passed when it was not run successfully.

## Verdicts

Return exactly one review verdict:

- `PASS`: reviewed scope and evidence are sufficient; progression is safe.
- `PASS_WITH_NOTES`: progression is safe with explicit non-blocking notes.
- `FAIL`: implementation, evidence, or scope compliance is defective and needs a bounded corrective delta.
- `BLOCKED`: review cannot be completed after permitted transports are exhausted or required evidence is unavailable.

`TRANSPORT_FAILED` is an attempt result, not a final review verdict. It triggers fallback to another reviewer.

## Reviewer prompt

Use a prompt equivalent to:

```text
Act as an independent, read-only reviewer for <checkpoint or phase> of GitHub
issue #<issue> using the <STANDARD or HIGH_ASSURANCE> profile.

Review exact published commit/range <sha-or-range> against the approved scope,
acceptance criteria, repository instructions, and validation evidence.

Do not implement fixes, edit files, create commits, change GitHub labels, or
continue execution.

Return exactly one verdict: PASS, PASS_WITH_NOTES, FAIL, or BLOCKED.
For FAIL, provide the smallest corrective delta. For BLOCKED, identify the
missing evidence or exhausted review capability. Distinguish transport failure
from implementation failure and list evidence inspected and commands run.
```

## Issue comment

Post:

```markdown
## [REVIEW][<CHECKPOINT OR PHASE>]

**Profile:** STANDARD | HIGH_ASSURANCE
**Reviewed commit/range:** `<full SHA or range>`
**Reviewer transport:** `<transport>`
**Verdict:** PASS | PASS_WITH_NOTES | FAIL | BLOCKED
**Safety to proceed:** YES | NO

**Scope and artifacts:** <assessment>
**Validation:** <assessment>
**Unexpected changes:** <none or list>

**Evidence inspected or run:**
- `<command or artifact>` — <result>

**Transport attempts:**
- `<transport>` — PASS | TRANSPORT_FAILED

**Required delta:**
- <none, corrective delta, or restart condition>

**Notes carried forward:**
- <none or note>
```

## Handoff

The calling workflow owns progression and labels.

- `PASS`: checkpoint is complete.
- `PASS_WITH_NOTES`: checkpoint is complete when notes do not violate an exit gate.
- `FAIL`: only the bounded corrective delta may be implemented before fresh review.
- `BLOCKED`: report the unavailable evidence or exhausted independent-review capability.

## Safety

- Never review a different target because the requested SHA is unavailable.
- Never repair an invalid SHA by guessing.
- Never modify the worktree to make validation pass.
- Never convert transport failure into implementation failure.
- Never approve solely from the executor's narrative when the exact diff or required evidence cannot be inspected.
