---
name: codex-independent-review
description: Independently review an exact published target against the controlling issue's material risks and acceptance criteria, returning a concise risk-calibrated verdict without reconstructing project history or enforcing administrative consistency.
---

# Codex Independent Review

## Responsibility

Use this skill for declared `STANDARD` checkpoints, issue-defined `HIGH_ASSURANCE` checkpoints, and final PR review.

The reviewer owns independent exact-target inspection, proportional validation, materiality, and the verdict. It does not implement fixes, redesign the issue, mutate workflow state, publish commits, or continue execution.

## Minimal review packet

A fresh reviewer needs:

1. `AGENTS.md`;
2. this skill;
3. the controlling issue or exact checkpoint section;
4. the exact published commit or range and its diff;
5. the authoritative evidence required by that checkpoint.

Load prior comments or reviews only when an unresolved finding or circuit breaker depends on them. Do not preload executor, design, or GitHub-operations skills; complete historical issues; unrelated PR discussion; or whole result directories.

The request must identify the target, checkpoint outcome, material risks, acceptance criteria, and evidence. Never guess a replacement SHA when the requested target is unavailable.

## Independence

The reviewer must:

- use a fresh context that does not inherit the executor's hidden reasoning;
- inspect exactly the requested target;
- remain read-only;
- judge evidence rather than intent;
- not implement corrections or advance later work.

Independence does not mean maximal hostility. Test the declared risk and plausible normal use, not every imaginable metadata, Markdown, malformed-input, or representational variant.

## Profiles

### STANDARD

Review only declared checkpoints and final handoff. Stop when the material risk is adequately covered and no unsafe defect remains.

### HIGH_ASSURANCE

Apply additional issue-defined checks only within the explicit architecture, numerical, concurrency, persistence, backend, or security boundary. Do not infer this profile from issue size.

## Materiality

Return `FAIL` only when a finding:

- violates an explicit invariant or acceptance criterion;
- exposes a plausible normal-path defect;
- makes required technical evidence materially false, incomplete, ambiguous, or non-reproducible;
- introduces unapproved scope, architecture, dependency, format, or behavior;
- makes progression from the reviewed target unsafe.

Use `PASS_WITH_NOTES` for editorial wording, derived status, bookkeeping, optional hardening, stale non-authoritative prose, or robustness outside the declared boundary when the technical outcome and authoritative evidence remain trustworthy.

For every `FAIL`, state:

1. the exact criterion violated;
2. the material consequence;
3. the smallest corrective delta, or why design must reopen.

When those cannot be stated concretely, do not fail the checkpoint.

## Review procedure

### 1. Establish risk and authority

Identify the checkpoint outcome, scope, invariants, acceptance criteria, authoritative evidence, and explicit threat boundary.

Treat issue comments, labels, PR descriptions, roadmap state, and Markdown summaries as derived unless the issue explicitly declares one authoritative.

### 2. Inspect the exact target

Check:

- diff and scope compliance;
- implementation and native build integration;
- credible required evidence;
- plausible correctness, ownership, lifetime, numerical, backend, or performance failures covered by the checkpoint;
- unexpected dependencies, secrets, prohibited artifacts, or behavior;
- safety to proceed.

For final review, inspect the complete PR diff and unresolved material findings. Do not replay accepted history without a concrete reason.

### 3. Test proportionally

Run issue-defined native validation when the environment supports it. Prioritize checks capable of falsifying the claimed outcome.

Distinguish commands personally run from committed evidence inspected. Do not invent bespoke compiler or linker paths or enumerate endless syntactic variants of non-authoritative data.

### 4. Report honestly and briefly

Record only:

- exact target;
- verdict and safety to proceed;
- material findings;
- validation run or evidence inspected;
- smallest required delta or non-blocking notes.

Mention transport or environment details only when they limit confidence or prevent completion.

## Reviewer transport

Use any fresh isolated read-only reviewer capable of inspecting the exact target.

If one transport fails, preserve the target and try another permitted route. Do not amend implementation commits, guess a different target, or fall back to executor self-review.

Return `BLOCKED` only when required evidence or independent-review capability remains unavailable after practical alternatives are exhausted.

## Repeated-review circuit breaker

When two consecutive reviews have failed for substantially the same validation, attestation, parser, documentation-sync, or bookkeeping mechanism under `STANDARD`:

- do not continue open-ended searches for representational variants;
- use `PASS_WITH_NOTES` when progression is technically safe and the remaining concern is non-material;
- request design review when the validation strategy itself prevents a trustworthy decision;
- require explicit design-authority direction before a third corrective review of the same mechanism.

The circuit breaker never waives a continuing material defect.

## Verdicts

Return exactly one:

- `PASS`: acceptance is met and progression is safe;
- `PASS_WITH_NOTES`: progression is safe with non-blocking notes;
- `FAIL`: a material defect or untrustworthy required evidence makes progression unsafe;
- `BLOCKED`: the review cannot complete because required evidence or independent capability is unavailable.

Transport failure is an attempt result, not a verdict. Do not use `FAIL` to request general hardening, extra documentation, or administrative synchronization.

## Concise review request

```text
Act as a fresh independent read-only reviewer for <checkpoint> of issue #<issue>.

Review exact target <sha-or-range> against the checkpoint's scope, material
risks, acceptance criteria, and authoritative evidence. Inspect only the
context needed to determine whether progression is technically safe.

Return FAIL only for a concrete material violation, plausible normal-path
defect, untrustworthy required evidence, unapproved scope, or unsafe
progression. Treat editorial, bookkeeping, derived-status, and optional
hardening concerns as PASS_WITH_NOTES.

Do not implement fixes or mutate repository or GitHub state. Return exactly
PASS, PASS_WITH_NOTES, FAIL, or BLOCKED.
```

## Concise review comment

```markdown
## <Checkpoint> — PASS | PASS_WITH_NOTES | FAIL | BLOCKED

**Target:** `<full SHA or range>`
**Safe to proceed:** yes | no

**Material findings:**
- <none or finding with violated criterion and consequence>

**Validation/evidence:**
- <command or artifact and result>

**Required delta or notes:**
- <none, smallest correction, design return, missing evidence, or non-blocking note>
```

The executor owns progression after the verdict. Never modify the worktree to make validation pass, approve a different target, or infer a threat model absent from the issue.
