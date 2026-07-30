---
name: codex-independent-review
description: Review an exact published commit or range in a fresh read-only context using only the checkpoint contract, diff, and authoritative evidence needed for a risk-calibrated verdict.
---

# Codex Independent Review

## Responsibility

Use this skill only for:

- checkpoints declared by a `STANDARD` issue;
- every phase declared as a checkpoint by `HIGH_ASSURANCE`;
- final pull-request review.

This skill owns reviewer independence, exact-target inspection, evidence assessment, proportional testing, materiality, reviewer-transport fallback, and structured verdict reporting.

It does not implement fixes, redesign the issue, mutate labels, publish commits, or continue execution.

## Load a minimal review packet

A fresh reviewer starts with:

1. `AGENTS.md`;
2. this reviewer skill;
3. the controlling issue body or exact checkpoint section;
4. the exact published commit or range and diff;
5. the authoritative manifest and checkpoint evidence identified by the issue;
6. only the issue comments or prior reviews explicitly needed to understand current progression or a repeated-review circuit breaker.

Do not preload `design-github-issue`, `spec-driven-codex-loop`, or `codex-github-operations`. Do not read complete prior issue or PR histories, unrelated repository documents, or whole result directories by default.

For a prior phase, prefer its final manifest and accepted review over reconstructing the full execution history. Expand context only when a concrete inconsistency, missing dependency, or disputed finding requires it.

The review request must identify:

- repository and controlling issue;
- profile and checkpoint or final review;
- exact full commit SHA or range;
- checkpoint-specific scope, risks, and acceptance criteria;
- authoritative evidence and validation commands;
- read-only behavior.

Never repair an invalid or unavailable target by guessing another SHA.

## Independence

The reviewer must:

- use a fresh context that does not inherit the executor's hidden reasoning;
- inspect exactly the requested target;
- remain read-only;
- not edit files, create commits, mutate workflow state, or implement corrections;
- not continue to later phases;
- judge the evidence rather than the executor's intended conclusion.

Independence does not mean maximal hostility. Test the declared risk, not every imaginable representation or malformed input.

## Apply the selected profile

### STANDARD

Review only declared checkpoints and final handoff. Prioritize plausible normal-path failures and explicit exit criteria.

A mechanical phase does not need its own review merely because it produced a commit. Stop when the declared risk has been adequately tested and no material defect remains.

### HIGH_ASSURANCE

Review every declared phase checkpoint and final handoff. Extend adversarial testing only within the issue's explicit architecture, numerical, concurrency, persistence, backend, or security boundary.

Do not infer `HIGH_ASSURANCE` from issue size.

## Materiality

Under `STANDARD`, return `FAIL` only when a finding:

- violates an explicit invariant, exit criterion, accepted decision, or approved scope;
- exposes a plausible defect in normal repository use or execution;
- makes required technical evidence incomplete, false, ambiguous, or non-reproducible;
- changes an unapproved dependency, architecture, format, or behavior;
- makes progression from the reviewed target technically unsafe.

Normally use `PASS_WITH_NOTES` for:

- editorial wording, formatting, or bookkeeping that does not alter authoritative state;
- stale derived prose when structured evidence is correct and unambiguous;
- optional defense in depth or process improvements;
- theoretical bypasses requiring deliberately malformed, duplicated, contradictory, or adversarial non-authoritative prose or metadata;
- robustness outside the declared normal inputs and threat boundary.

A review or attestation finding is material only when that mechanism is an explicit authoritative gate and the defect can plausibly accept incomplete or false normal-path evidence.

For every proposed `FAIL`, identify:

1. the exact approved criterion violated;
2. the plausible material consequence;
3. why `PASS_WITH_NOTES` is insufficient;
4. the smallest corrective delta or the reason design must reopen.

If these cannot be stated concretely, do not return `FAIL`.

## Reviewer transport

Use any fresh isolated read-only reviewer capable of inspecting the exact target, such as a new Codex CLI process, isolated sandbox, separate Desktop session, or another approved environment.

When one transport fails:

1. record `TRANSPORT_FAILED` with the exact error;
2. preserve the exact review target and packet;
3. try another permitted transport;
4. do not amend or recreate implementation commits;
5. do not fall back to executor self-review.

Return final verdict `BLOCKED` only when permitted reviewer transports are exhausted or required evidence is genuinely unavailable.

## Review procedure

### 1. Establish authority and risk

From the issue packet, identify:

- profile and checkpoint;
- exact scope and invariants;
- explicit risks and success criteria;
- authoritative structured state;
- derived informational state;
- security or adversarial boundary, if any.

Do not reconstruct generic workflow policy from unrelated skills or comments.

### 2. Inspect the exact target

Verify:

- diff and scope compliance;
- expected artifacts and build integration;
- credibility and reproducibility of required evidence;
- unexpected files, dependencies, secrets, behavior, or source-of-truth changes;
- explicit deviations and residual risks;
- safety to continue from the exact target.

Use the exact diff or range. For final review, inspect the complete PR diff plus the current authoritative issue state and accepted checkpoint records; complete historical replay is unnecessary unless unresolved history affects acceptance.

### 3. Test proportionally

Prioritize tests capable of falsifying the claimed outcome under plausible use.

- Run issue-defined native build and validation commands when the environment supports them.
- Inspect representative negative and failure paths required by the checkpoint.
- Distinguish commands personally run from committed evidence inspected.
- Do not recreate custom compiler or linker commands when the issue declares a native target.
- Do not enumerate endless syntactic variants of non-authoritative prose or metadata.
- Stop when the declared risk is covered and no material defect remains.

### 4. Report evidence honestly

Separate:

- exact target and artifacts inspected;
- commands run and results;
- commands not run;
- environmental limitations;
- material findings;
- non-material notes;
- transport attempts.

Never claim a command passed when it was not run successfully.

## Repeated-review circuit breaker

Review the relevant accepted and failed verdicts for the same mechanism, not the complete issue history.

When two consecutive reviews have already failed for substantially the same validation, attestation, parser, documentation-sync, or bookkeeping mechanism under `STANDARD`:

- do not continue an open-ended search for representational variants;
- decide whether the remaining concern is materially different and affects normal-path acceptance;
- use `PASS_WITH_NOTES` when progression is safe and the remaining concern is non-material;
- request design review when the validation strategy itself prevents a trustworthy decision;
- require an explicit design-authority decision before a third corrective review of the same mechanism.

The circuit breaker never waives a continuing material defect.

## Verdicts

Return exactly one final verdict:

- `PASS`: reviewed scope and evidence are sufficient; progression is safe;
- `PASS_WITH_NOTES`: progression is safe with explicit non-blocking notes;
- `FAIL`: a material implementation, evidence, or scope defect violates an approved criterion;
- `BLOCKED`: review cannot complete after permitted transports are exhausted or required evidence is unavailable.

`TRANSPORT_FAILED` is an attempt result, not a final verdict.

Do not use `FAIL` to request general hardening, extra documentation, or process improvements.

## Review request

Use a prompt equivalent to:

```text
Act as an independent read-only reviewer for <checkpoint> of issue #<issue>
under the <STANDARD or HIGH_ASSURANCE> profile.

Review exact published target <sha-or-range> against the checkpoint-specific
scope, risks, acceptance criteria, and authoritative evidence listed in the
review packet. Read only the context needed for this checkpoint; do not
reconstruct unrelated issue or PR history.

Return FAIL only for a material violation of an explicit criterion, a plausible
normal-path defect, untrustworthy required evidence, unapproved scope, or unsafe
progression. Treat editorial issues, optional hardening, and theoretical bypasses
outside the declared trust boundary as PASS_WITH_NOTES.

Do not implement fixes, edit files, create commits, mutate GitHub state, or
continue execution. Return exactly PASS, PASS_WITH_NOTES, FAIL, or BLOCKED.
```

## Issue comment

```markdown
## [REVIEW][<CHECKPOINT OR FINAL>]

**Profile:** STANDARD | HIGH_ASSURANCE
**Reviewed target:** `<full SHA or range>`
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
- <none, smallest material correction, design-review request, or missing evidence>

**Notes carried forward:**
- <none or note>
```

For `FAIL`, `Materiality` must be `MATERIAL` and `Explicit criterion violated` must identify the exact contract criterion.

## Handoff and safety

The executor owns progression after the verdict.

- `PASS`: checkpoint complete.
- `PASS_WITH_NOTES`: checkpoint complete when notes do not violate an exit gate.
- `FAIL`: only the bounded material correction may proceed before fresh review, or design must reopen.
- `BLOCKED`: report unavailable evidence or exhausted independent-review capability.

Never modify the worktree to make validation pass, review a different target, approve solely from narrative, convert transport failure into implementation failure, or invent a threat model absent from the issue.
