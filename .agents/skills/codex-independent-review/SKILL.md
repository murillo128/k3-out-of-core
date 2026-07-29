---
name: codex-independent-review
description: Review an exact published commit or range in a fresh isolated read-only context and return a risk-calibrated structured verdict. Use only at review checkpoints declared by a STANDARD issue, after every phase in HIGH_ASSURANCE, or for final pull-request review.
---

# Codex Independent Review

## Responsibility

This skill owns:

- reviewer independence;
- reviewer-transport selection and fallback;
- inspection of the exact requested commit or range;
- evidence assessment;
- risk and materiality calibration;
- structured verdict reporting.

It does not implement fixes, mutate workflow labels, redesign the issue, create commits, or continue execution.

## When review is required

### STANDARD profile

Independent review is required only at checkpoints explicitly declared by the issue and at final handoff.

Typical checkpoints group related phases, for example:

- environment, fixtures, and tokenizer preparation;
- numerical format and backend correctness;
- benchmark methodology and final closeout.

A mechanical phase does not require its own independent review merely because it created a commit.

STANDARD review is risk-based. It is not an adversarial security audit unless the issue explicitly declares a security boundary or threat model.

### HIGH_ASSURANCE profile

Independent review is required after every phase commit and at final handoff.

The issue must explicitly select this profile. Do not infer it from task size alone.

HIGH_ASSURANCE may justify adversarial negative cases when they protect an explicit architecture, numerical, concurrency, persistence, backend, or security risk.

## Materiality and profile calibration

Before assigning a verdict, classify each finding by materiality and by the execution profile.

Under STANDARD, use `FAIL` only when at least one finding is a material violation of the approved contract. A material finding must satisfy at least one of these conditions:

- it violates an explicit exit criterion, invariant, or accepted decision;
- it exposes a plausible defect in normal repository use or execution;
- it makes required technical evidence incomplete, false, ambiguous, or non-reproducible;
- it crosses approved scope or changes an unapproved dependency, architecture, format, or behavior;
- it makes progression from the reviewed target technically unsafe.

Normally use `PASS_WITH_NOTES`, not `FAIL`, for:

- editorial wording, formatting, or bookkeeping inconsistencies that do not alter authoritative technical state;
- stale prose in a derived summary when authoritative structured evidence is correct and unambiguous;
- defense-in-depth improvements not required by the issue;
- theoretical bypasses that require deliberately malformed, duplicated, contradictory, or adversarial Markdown, comments, URLs, or metadata outside an explicit security boundary;
- robustness improvements against inputs that the normal workflow never produces and the issue did not require supporting;
- process improvements whose absence does not invalidate the reviewed implementation or evidence.

Do not silently expand the threat model. Do not convert STANDARD into HIGH_ASSURANCE by inventing malformed-input, parser-hardening, or hostile-actor requirements.

A finding about review or attestation machinery is material only when that machinery is an explicit authoritative exit gate and the defect can plausibly accept an incomplete or false normal-path attestation. Once normal-path state is unambiguous, further adversarial parser hardening is non-blocking unless explicitly required.

For every proposed `FAIL`, identify:

1. the exact approved criterion violated;
2. the plausible normal-path failure or untrustworthy evidence it causes;
3. why `PASS_WITH_NOTES` is insufficient.

If these cannot be stated concretely, do not return `FAIL`.

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

Independence does not mean maximal hostility. Apply the approved profile and materiality rules rather than searching indefinitely for theoretical bypasses.

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

### 1. Establish authority and risk

Before testing edge cases, identify:

- the execution profile;
- the explicit checkpoint risks and exit criteria;
- the authoritative structured state, if any;
- which Markdown, issue comments, PR descriptions, or summaries are informational or derived;
- whether the issue defines a real security or adversarial-input boundary.

Do not treat every textual representation as an independent security authority. Prefer the source explicitly designated by the issue.

### 2. Inspect the exact target

Verify:

1. scope compliance against the checkpoint or phase specification;
2. completeness of expected artifacts;
3. credibility and reproducibility of reported validation;
4. unexpected files, dependencies, secrets, behavior, or source-of-truth changes;
5. explicit deviations and residual risks;
6. safety to continue from the reviewed target.

Inspect the exact diff or range. Run issue-defined validation when the reviewer environment supports it.

### 3. Test proportionally

Prioritize tests that can falsify the claimed technical outcome under plausible use.

Under STANDARD:

- reproduce required commands and objective gates;
- inspect representative negative and failure paths defined by the issue;
- do not enumerate endless syntactic variants of non-authoritative prose or metadata;
- stop when the declared risk has been adequately tested and no material defect remains.

Under HIGH_ASSURANCE, extend adversarial testing only within the explicitly approved risk boundary.

### 4. Distinguish evidence and failures

Distinguish clearly between:

- evidence inspected;
- commands personally run;
- commands not run;
- environmental limitations;
- material implementation or evidence failures;
- non-material notes;
- reviewer-transport failures.

Never claim a command passed when it was not run successfully.

## Repeated-review circuit breaker

Review the issue history for repeated failures in the same validation, attestation, parser, or bookkeeping mechanism.

When two consecutive reviews have already failed for substantially the same mechanism under STANDARD:

- do not continue an open-ended search for additional representational bypasses;
- determine whether the remaining concern is materially different and affects the target technical outcome under plausible use;
- if it is not materially different, return `PASS_WITH_NOTES` when progression is technically safe and recommend simplification by the design authority;
- if the validation design itself is preventing a trustworthy decision, state that the contract needs design review rather than prescribing another compensating parser patch.

A third corrective review of the same mechanism should occur only after an explicit design-authority decision or when a newly found defect is materially different.

## Verdicts

Return exactly one review verdict:

- `PASS`: reviewed scope and evidence are sufficient; progression is safe.
- `PASS_WITH_NOTES`: progression is safe with explicit non-blocking notes.
- `FAIL`: a material implementation, evidence, or scope defect violates an approved criterion and needs a bounded corrective delta.
- `BLOCKED`: review cannot be completed after permitted transports are exhausted or required evidence is unavailable.

`TRANSPORT_FAILED` is an attempt result, not a final review verdict. It triggers fallback to another reviewer.

Do not use `FAIL` as a request for general hardening or editorial cleanup.

## Reviewer prompt

Use a prompt equivalent to:

```text
Act as an independent, read-only reviewer for <checkpoint or phase> of GitHub
issue #<issue> using the <STANDARD or HIGH_ASSURANCE> profile.

Review exact published commit/range <sha-or-range> against the approved scope,
acceptance criteria, repository instructions, and validation evidence.

Calibrate findings to the selected profile. Under STANDARD, return FAIL only
for a material violation of an explicit criterion, a plausible normal-path
technical defect, untrustworthy required evidence, or unsafe progression. Treat
editorial inconsistencies, defense-in-depth suggestions, and theoretical
bypasses requiring deliberately malformed non-authoritative Markdown or
metadata as PASS_WITH_NOTES unless the issue explicitly defines them as a
security boundary.

Do not implement fixes, edit files, create commits, change GitHub labels, or
continue execution.

Return exactly one verdict: PASS, PASS_WITH_NOTES, FAIL, or BLOCKED.
For FAIL, name the exact criterion violated, explain the plausible material
impact, explain why PASS_WITH_NOTES is insufficient, and provide the smallest
corrective delta. For BLOCKED, identify the missing evidence or exhausted
review capability. Distinguish transport failure from implementation failure
and list evidence inspected and commands run.
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
**Materiality:** NONE | NON_MATERIAL | MATERIAL
**Explicit criterion violated:** <none or exact criterion>

**Scope and artifacts:** <assessment>
**Validation:** <assessment>
**Unexpected changes:** <none or list>

**Evidence inspected or run:**
- `<command or artifact>` — <result>

**Transport attempts:**
- `<transport>` — PASS | TRANSPORT_FAILED

**Required delta:**
- <none, material corrective delta, design-review request, or restart condition>

**Notes carried forward:**
- <none or note>
```

For `FAIL`, `Materiality` must be `MATERIAL` and `Explicit criterion violated` must not be `none`.

## Handoff

The calling workflow owns progression and labels.

- `PASS`: checkpoint is complete.
- `PASS_WITH_NOTES`: checkpoint is complete when notes do not violate an exit gate.
- `FAIL`: only the bounded material corrective delta may be implemented before fresh review.
- `BLOCKED`: report the unavailable evidence or exhausted independent-review capability.

When the reviewer identifies a flawed validation or attestation design rather than a local implementation defect, the calling workflow must return to the design authority instead of repeatedly patching the mechanism.

## Safety

- Never review a different target because the requested SHA is unavailable.
- Never repair an invalid SHA by guessing.
- Never modify the worktree to make validation pass.
- Never convert transport failure into implementation failure.
- Never approve solely from the executor's narrative when the exact diff or required evidence cannot be inspected.
- Never invent an adversarial threat model that the issue and selected profile do not define.
- Never keep producing `FAIL` findings from cosmetic variants of the same non-material mechanism after the repeated-review circuit breaker applies.
