---
name: design-github-issue
description: Define a self-contained execution-ready GitHub issue that resolves material decisions, gives a fresh executor every fact needed to implement safely, and avoids duplicating workflow history or derived metadata.
---

# Design a GitHub Execution Issue

## Responsibility

Use this skill before non-trivial implementation starts, or when execution returns because a material design or validation decision is unresolved.

The design authority owns:

- the observable outcome;
- material architectural and validation decisions;
- the complete phase-specific context needed to execute safely;
- scope, invariants, exclusions, failure semantics, and acceptance criteria;
- risk-based review checkpoints;
- the issue's initial readiness and any design-authority state transition.

It does not implement code, operate branches, publish commits, or perform independent review.

## Assume a fresh, less-capable executor

Design the issue for an executor that:

- has no access to the design session's reasoning;
- may be less capable than the design authority at resolving ambiguity;
- should not need to reconstruct material facts from prior issues, PR histories, or broad repository reading;
- must be able to distinguish required behavior from examples, observations, alternatives, and future work.

The issue must therefore contain every **phase-specific fact, decision, constraint, and acceptance rule** required for correct implementation. Links are supporting references, not substitutes for material instructions.

A self-contained issue is not an archive. Include the current technical contract in full; omit historical narration and generic workflow already owned elsewhere.

## Load material design context

Start with:

1. `AGENTS.md`;
2. the request, roadmap epic, or existing controlling issue.

Then inspect only what is needed to settle the phase:

- exact plan and decision sections;
- relevant source seams, APIs, ownership, state machines, and tests;
- the prior accepted manifest or baseline behavior;
- required hardware, model, artifact, and nested-repository inputs;
- overlapping current work and superseded attempts when their findings constrain the design.

Prefer authoritative current outcomes over complete historical issue or PR traversal. Expand history only when a material finding, rejected mechanism, or provenance boundary affects the new contract.

## The issue is the executor's complete contract

Include enough concrete detail that implementation does not depend on unstated inference. Depending on the phase, this may require:

- current limitation and observable goal;
- accepted baseline behavior and defaults that must remain unchanged;
- exact relevant repository and nested inputs when reproducibility or compatibility depends on them;
- inspected implementation seams, ownership and lifetime boundaries, data shapes, states, identifiers, and error mapping;
- resolved API/configuration semantics and invalid combinations;
- ordering, concurrency, cancellation, teardown, and failure behavior;
- required telemetry and resource bounds;
- permitted implementation scope and explicit exclusions;
- stable commands, targets, fixtures, hardware, and artifact identities needed for validation;
- objective acceptance criteria and material review risks;
- prior negative evidence or superseded attempts when they prohibit repeating a known-invalid mechanism.

Use precise names, paths, values, examples, and equations where they remove ambiguity. Summarize the relevant content of linked documents rather than expecting the executor to infer the contract by reading them wholesale.

Do not copy:

- generic Git, publication, review, label, or reporting procedure owned by skills;
- chronological phase histories, complete review transcripts, or commit ledgers;
- complete command output or machine-readable evidence already stored as artifacts;
- routine GitHub metadata visible on the issue or PR;
- the same rule into multiple sections merely for emphasis.

Exact commits are required when reproducibility, a nested dependency, accepted evidence, branch ownership, or review identity depends on them. Do not repeat routine heads merely for bookkeeping.

## Readiness

The controlling issue's current workflow state is the single authoritative state label. Use exactly one of:

- `execution-ready`: no material design or validation decision remains;
- `design-required`: a material decision remains unresolved;
- `investigation-required`: bounded evidence is needed before choosing a design;
- `blocked`: a required external capability is unavailable with no practical alternative;
- `in-progress`: execution has started;
- `completed`: the accepted terminal outcome has been reached and the issue is closing or closed.

At issue publication, set exactly one state label through `codex-github-operations`. The issue body may record **Initial state** so a fresh executor understands the publication context, but that field is historical and is not updated for routine transitions. The label alone answers the current-state question.

When design authority changes the technical contract, update the issue body or add a material amendment comment as appropriate, then change the state label if progression changes. Do not create a comment whose only purpose is to announce a state transition, and do not repeat a mutable `State:` field in amendment comments.

## Design method

### 1. Define the observable outcome

State what must become true, why it matters, the current limitation, and the boundary of the requested change.

### 2. Resolve material unknowns

Resolve questions that can change behavior, compatibility, architecture, ownership, lifetime, numerical semantics, backend support, failure handling, validation, licensing, or upstream strategy.

Use these classifications only where they clarify a real decision:

- `OBSERVED`
- `ACCEPTED`
- `OPEN`
- `SPECULATIVE`
- `REJECTED`
- `BLOCKED`

Do not turn `OPEN` or `SPECULATIVE` items into implementation requirements. Record durable cross-phase architecture in `docs/DECISIONS.md`; keep phase-local choices in the issue.

When implementation may reuse third-party code or a prior fork, resolve the provenance boundary before execution. Record the source repository/URL, exact revision when material, license, smallest reusable unit, and required attribution; identify ownership/lifetime adaptations needed by this architecture and require isolated validation plus comparison with the unmodified baseline. Do not authorize wholesale copying merely because prior art exists.

### 3. Bound implementation without under-specifying it

Define the smallest coherent outcome, permitted subsystem or files, explicit exclusions, and invariants. Split work only when a failure would otherwise obscure which design or edit caused it.

Avoid exhaustive file allowlists when subsystem boundaries and review are clearer, but include exact files or seams when a less-capable executor could otherwise modify the wrong layer.

### 4. Define validation that proves the outcome

Specify only material validation, but specify it concretely:

- native build or test targets;
- correctness, repeated-run, failure-path, numerical, concurrency, lifetime, or performance checks;
- required environment and external artifacts;
- objective pass/fail criteria;
- the authoritative technical evidence artifact, when needed.

When execution changes persistent/cache/generation state across compute epochs, include repeated warm-run validation unless the design establishes that the historical cross-epoch failure mode cannot apply. The issue should define enough repetition and state reuse to exercise that risk rather than relying only on a cold first run.

Prefer repository-native targets. Use exact commands when their arguments or environment are part of what is being proven; otherwise identify the target and required result without freezing replaceable invocation syntax.

### 5. Keep technical evidence independent from workflow

For new phases, a machine-readable technical manifest should contain technical and reproducibility data only, such as:

- project and nested implementation revisions;
- input identities and hashes;
- environment and configuration;
- commands, results, artifacts, metrics, gates, and limitations.

Do not require it to contain branch names, issue or PR numbers, labels, comment IDs, review verdicts, merge identity, or closeout status unless one is itself a technical input to the software being tested. Review is an external attestation over an immutable target and manifest.

When raw evidence is large or highly repetitive, require an immutable external archive with checksums and keep in Git only the manifest, bounded summaries, schemas, reproduction tooling, small fixtures, and archive index. Do not externalize artifacts needed for ordinary deterministic tests.

### 6. Add risk-based checkpoints

Under `STANDARD`, add independent checkpoints only for distinct material risks such as architecture, ownership or lifetime, persistent state, numerical behavior, concurrency, backend execution, broad refactoring, or decision-driving performance evidence.

Use `HIGH_ASSURANCE` only when explicitly justified; issue size alone is not a reason.

A checkpoint defines:

- the covered outcome and exact target semantics;
- material risks and acceptance criteria;
- evidence to inspect or reproduce;
- what would make progression unsafe.

When the last checkpoint can inspect the complete final PR diff, immutable final technical evidence, and all remaining acceptance criteria, declare it **final-capable**. A passing review of that exact unchanged target also serves as the final PR review; do not require a duplicate review.

Any later change to code, tests, technical evidence, manifest, dependencies, configuration, or technical claims invalidates final-capable status and requires review of the changed target. Changes only to labels, issue/PR prose, roadmap state, or other workflow metadata do not.

### 7. Define nested publication boundaries

When implementation lives primarily in nested `llama.cpp`, permit coherent nested commits without requiring a parent gitlink commit for every nested step.

Require the parent gitlink to be updated at:

- a checkpoint that needs an exact parent+nested review target;
- the final integration candidate;
- another explicit recovery or compatibility boundary.

The issue may require more frequent updates only when parent-side code or evidence genuinely depends on each nested revision.

### 8. Define restart semantics

Distinguish:

- local implementation defect: correct a bounded delta;
- design defect: return to `design-required`;
- evidence gap: return to `investigation-required`;
- replaceable tool failure: use another transport or leave a handoff;
- real blocker: no safe practical continuation exists.

Under `STANDARD`, two consecutive review failures for substantially the same validation, attestation, parser, documentation-sync, or bookkeeping mechanism trigger design review before a third corrective cycle. This never waives a continuing material defect.

### 9. Check overlap

Inspect only plausibly overlapping open issues, PRs, branches, and recent attempts. Link superseded work and summarize its material constraint instead of copying its history.

## Execution-ready check

Before marking the issue `execution-ready`, confirm:

- a fresh executor can implement without access to design-session reasoning;
- the observable outcome and terminology are unambiguous;
- all material facts and decisions are present in the issue;
- linked sources supplement rather than replace the contract;
- scope, invariants, failure behavior, and acceptance are clear;
- required inputs and validation capabilities are identified;
- checkpoints match distinct risks and avoid duplicate final review;
- technical evidence is independent from GitHub workflow metadata;
- nested and external-evidence publication boundaries are explicit when applicable;
- `execution-ready` is the issue's only state label.

## Issue structure

Use the sections that carry phase-specific information:

```markdown
# <Outcome-oriented title>

## Readiness
**Initial state:** execution-ready | design-required | investigation-required | blocked
**Profile:** STANDARD | HIGH_ASSURANCE

## Goal and current limitation
<Observable outcome, why it matters, and current behavior.>

## Authoritative baseline and inputs
<All material baseline facts, revisions, manifests, artifacts, and defaults.>

## Resolved technical contract
<APIs, ownership, state, ordering, failure semantics, bounds, and concrete seams.>

## Scope
### In scope
### Out of scope
### Invariants

## Validation and evidence
<Required targets, cases, environment, artifacts, and objective gates.>

## Checkpoints
<Distinct risk checkpoints; mark the last one final-capable when applicable.>

## Delivery
<One coherent PR, parent/nested publication boundaries, evidence retention, and observable completion.>
```

Add or split sections freely when technical completeness requires it. Do not impose an arbitrary line limit on a genuinely complex phase.