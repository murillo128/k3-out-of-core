---
name: codex-independent-review
description: Independently review an exact published target against the controlling issue's material risks and acceptance criteria, returning a concise risk-calibrated verdict without reconstructing project history or enforcing workflow metadata consistency.
---

# Codex Independent Review

## Responsibility

Use this skill for declared `STANDARD` checkpoints, issue-defined `HIGH_ASSURANCE` checkpoints, final-capable checkpoints, and final PR review when a separate final review is actually required.

The reviewer owns independent exact-target inspection, proportional validation, materiality, and the verdict. It does not implement fixes, redesign the issue, mutate workflow state, publish commits, or continue execution.

## Trust the issue as the complete technical contract

Assume the controlling issue was written by a design authority with more context than the reviewer or executor. Judge the implementation against the issue's explicit contract; do not replace settled decisions with a new design merely because another approach is possible.

The issue must provide all material phase-specific facts. Open linked sources only to inspect the exact implementation, durable decision, or evidence identified by the issue. Do not require the executor to reconstruct design intent from complete historical issues or PRs.

## Minimal review packet

A fresh reviewer needs:

1. `AGENTS.md`;
2. this skill;
3. the controlling issue or exact checkpoint section;
4. the exact published project and nested target or range;
5. the immutable technical manifest and evidence required by that checkpoint.

Load prior comments or reviews only when an unresolved material finding or circuit breaker depends on them. Do not preload executor, design, or GitHub-operations skills; complete historical issues; unrelated PR discussion; or whole result directories.

The request must identify the checkpoint outcome, exact target, material risks, acceptance criteria, and authoritative technical evidence. Never guess a replacement target when the requested one is unavailable.

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

Review only declared material checkpoints. Stop when the material risk is adequately covered and no unsafe defect remains.

### HIGH_ASSURANCE

Apply additional issue-defined checks only within the explicit architecture, numerical, concurrency, persistence, backend, or security boundary. Do not infer this profile from issue size.

## Authority and evidence boundaries

Treat these as authoritative when the issue declares them:

- code and tests at the exact target;
- immutable input and artifact identities;
- technical manifests containing revisions, environment, commands, results, metrics, gates, and limitations;
- reproduced or credibly inspected native evidence.

Treat these as derived workflow state unless they are themselves the object under test:

- issue and PR numbers;
- branch names;
- labels;
- comment IDs;
- review fields embedded in manifests;
- merge identity or closeout state;
- roadmap and Markdown summaries.

Do not fail a technically trustworthy checkpoint because a technical manifest omits GitHub workflow metadata. Do not request a post-review manifest commit merely to record the review verdict.

When raw evidence is stored in an immutable checksum-addressed external archive, validate its identity, index, relevant samples, reproduction path, and claimed aggregates proportionally. Do not require all bulk data to be committed to Git.

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

Identify the checkpoint outcome, scope, invariants, acceptance criteria, technical evidence, exact project/nested targets, and explicit threat boundary.

### 2. Inspect the exact target

Check:

- diff and scope compliance;
- implementation and native build integration;
- credible required evidence;
- plausible correctness, ownership, lifetime, numerical, concurrency, backend, or performance failures covered by the checkpoint;
- unexpected dependencies, secrets, prohibited artifacts, or behavior;
- safety to proceed.

Do not replay an accepted earlier range without a concrete unresolved risk.

### 3. Test proportionally

Run issue-defined native validation when the environment supports it. Prioritize checks capable of falsifying the claimed outcome.

Distinguish commands personally run from committed or external evidence inspected. Do not invent bespoke compiler or linker paths or enumerate endless syntactic variants of non-authoritative data.

### 4. Determine whether the review is final

A checkpoint also serves as final PR review when the issue declares it final-capable and the reviewer confirms that the exact target includes:

- the complete final PR diff;
- the final project and nested revisions;
- immutable final technical evidence;
- all remaining acceptance criteria and unresolved material findings.

State explicitly whether the target is final-capable and whether another final review is needed.

A later change to code, tests, technical evidence, manifest, dependencies, configuration, or technical claims invalidates that final verdict and requires review of the changed target. Changes only to issue/PR prose, labels, roadmap state, merge metadata, or other derived workflow state do not.

### 5. Report honestly and briefly

Record only:

- exact project and nested target;
- verdict and safety to proceed;
- whether this review serves as final review;
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

Transport failure is an attempt result, not a verdict. Do not use `FAIL` to request general hardening, extra documentation, workflow metadata, or administrative synchronization.

## Concise review request

```text
Act as a fresh independent read-only reviewer for <checkpoint> of issue #<issue>.

Review exact project/nested target <sha-or-range> against the issue's complete
technical contract, material risks, acceptance criteria, and immutable technical
evidence. Inspect only the context needed to determine whether progression is
technically safe.

This checkpoint is <final-capable | not final-capable>. If final-capable, confirm
whether the target contains the complete final PR diff and all remaining evidence.

Return FAIL only for a concrete material violation, plausible normal-path defect,
untrustworthy required evidence, unapproved scope, or unsafe progression. Treat
workflow metadata, editorial, bookkeeping, derived-status, and optional hardening
concerns as PASS_WITH_NOTES.

Do not implement fixes or mutate repository or GitHub state. Return exactly PASS,
PASS_WITH_NOTES, FAIL, or BLOCKED.
```

## Concise review comment

```markdown
## <Checkpoint> — PASS | PASS_WITH_NOTES | FAIL | BLOCKED

**Target:** `<project SHA/range>`; nested `<SHA/range if applicable>`
**Safe to proceed:** yes | no
**Serves as final review:** yes | no

**Material findings:**
- <none or finding with violated criterion and consequence>

**Validation/evidence:**
- <command, manifest, or external archive identity and result>

**Required delta or notes:**
- <none, smallest correction, design return, missing evidence, or non-blocking note>
```

The executor owns progression after the verdict. Never modify the worktree to make validation pass, approve a different target, or infer a threat model absent from the issue.