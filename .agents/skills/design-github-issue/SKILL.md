---
name: design-github-issue
description: Use when a normal ChatGPT session must turn a non-trivial repository request into an approved, implementation-ready GitHub issue before Codex execution.
---

# Design an Implementation-Ready GitHub Issue

Use this skill in the design-authority ChatGPT session that runs before the `spec-driven-codex-loop` execution skill.

The output is not merely an issue description. It is the complete, durable contract that allows Codex to execute one bounded phase at a time without inventing architecture, scope, validation, or completion criteria.

## Core rule

Do not create an execution-ready issue until the design is sufficiently precise to implement and independently review.

A larger issue body does not compensate for unresolved decisions. When a material architectural, behavioral, numerical, performance, ownership, dependency, or validation question remains open, resolve it first or create a design/investigation issue instead of pretending the task is ready for implementation.

## Role boundary

The design-authority session may:

- inspect the repository, issues, pull requests, history, and external references;
- clarify the goal and current behavior;
- investigate evidence needed to choose a design;
- compare approaches and recommend one;
- update or propose updates to source-of-truth documents;
- define implementation phases and validation gates;
- choose the recommended model capability class for each phase;
- create or update the GitHub issue after the contract is approved.

It must not:

- start implementation while designing the issue;
- silently convert an `OPEN` or `SPECULATIVE` decision into an accepted one;
- hide uncertainty inside vague implementation instructions;
- prescribe files or APIs without first inspecting the repository;
- assume tests, benchmarks, hardware, models, or dependencies are available without verifying them;
- create an execution-ready issue whose success cannot be observed objectively.

## Optional helper skills

This skill is self-contained. It must still work when no external skill pack is installed.

When the current ChatGPT or Codex harness exposes relevant Superpowers skills, compose them as helpers:

| Task shape | Preferred helper sequence |
|---|---|
| New feature, architecture, or behavior change | `brainstorming` → `writing-plans` → `verification-before-completion` |
| Bug, test failure, or unexpected behavior | `systematic-debugging` → `writing-plans` → `verification-before-completion` |
| Performance regression or optimization | `systematic-debugging` for evidence/root cause → `brainstorming` for design alternatives when needed → `writing-plans` → `verification-before-completion` |
| Refactor or migration with accepted behavior | brief `brainstorming` scope check → `writing-plans` → `verification-before-completion` |
| Skill creation or modification | `writing-skills`, when available, plus repository review and validation |

Helper skills improve discovery and decomposition; they do not replace this repository's issue contract.

Precedence is:

1. user instructions;
2. committed repository source-of-truth and `AGENTS.md`;
3. this skill;
4. optional helper skills.

If a helper skill conflicts with the repository workflow, adapt the helper rather than weakening the repository workflow.

## Issue readiness states

Classify the result explicitly:

- `EXECUTION_READY`: design, scope, phases, validation, and review gates are complete.
- `DESIGN_REQUIRED`: implementation is blocked by unresolved design or product decisions.
- `INVESTIGATION_REQUIRED`: evidence is insufficient to define the correct change.
- `BLOCKED`: required access, dependency, hardware, model, data, or authority is unavailable.

Only `EXECUTION_READY` issues may enter the `spec-driven-codex-loop` implementation workflow.

## Workflow

### 1. Restate the requested outcome

Write a one-paragraph problem statement that separates:

- the user-visible or engineering outcome;
- the motivation;
- the current limitation;
- the boundary of the requested change.

Do not begin with a proposed implementation. First establish what must become true.

### 2. Classify the work

Classify the request as one or more of:

- feature;
- defect;
- refactor;
- migration;
- performance investigation;
- performance optimization;
- research/prototype;
- documentation/source-of-truth change;
- infrastructure/tooling.

The classification determines the required evidence:

- defects require reproduction and root-cause evidence;
- performance work requires a baseline, measurement protocol, and target metrics;
- migrations require compatibility and rollback rules;
- architecture changes require alternatives and explicit decision records;
- research tasks require hypotheses and stop conditions rather than pretending the outcome is known.

### 3. Inspect repository context before asking design questions

Read `AGENTS.md` and its required reading order first. Then inspect the relevant subset of:

- `README.md`;
- `docs/STATUS.md`;
- `docs/DECISIONS.md`;
- `PLAN.md` and the active phase files;
- `docs/MODELS_AND_VALIDATION.md`;
- `docs/PRIOR_ART.md`;
- relevant source, tests, scripts, fixtures, and configuration;
- recent commits that touched the area;
- open or recently closed issues and PRs that overlap the request;
- pinned upstream repositories, branches, commits, model revisions, licenses, and hardware assumptions.

Use the GitHub connector for repository facts. Use authoritative upstream sources for external technical facts. Distinguish observed repository state from assumptions.

Do not ask the user for information already available in the repository or earlier conversation.

### 4. Build a fact and decision ledger

Before choosing a design, classify important statements using the repository markers:

- `OBSERVED`: directly supported by code, tests, measurements, logs, or committed evidence;
- `ACCEPTED`: an approved design or constraint in the source of truth;
- `OPEN`: a decision still requiring resolution;
- `SPECULATIVE`: a hypothesis not yet supported by sufficient evidence;
- `REJECTED`: an approach explicitly ruled out;
- `BLOCKED`: cannot be resolved with current access or evidence.

The issue must never phrase `OPEN` or `SPECULATIVE` items as implementation requirements.

### 5. Resolve material unknowns

Ask only questions whose answers materially alter scope, architecture, compatibility, validation, or risk.

Prefer one focused question at a time during collaborative design. Offer concrete alternatives when the decision space is known. Avoid asking abstract questions such as "What should the design be?"

A material unknown includes:

- expected external behavior;
- compatibility requirements;
- acceptable numerical or performance tolerance;
- hardware/backend support;
- ownership and lifetime rules;
- failure and fallback behavior;
- storage or file-format constraints;
- security, privacy, or licensing constraints;
- required observability;
- merge/upstream strategy.

Minor details may use clearly marked assumptions or placeholders. Material decisions may not.

### 6. Investigate before prescribing fixes

For defects, failures, regressions, or unexpected performance:

1. capture the exact symptom and reproduction conditions;
2. inspect errors, logs, failing tests, recent changes, and relevant data flow;
3. separate root-cause evidence from hypotheses;
4. identify what additional instrumentation or experiment is needed;
5. only then define a fix phase.

When root cause is not established, produce an `INVESTIGATION_REQUIRED` issue whose deliverable is evidence and a design decision, not an implementation issue containing a guessed fix.

### 7. Compare viable approaches

When more than one plausible design exists, present two or three serious alternatives. For each, evaluate:

- correctness;
- architectural fit;
- complexity;
- observability and testability;
- performance implications;
- migration and rollback cost;
- upstreamability;
- dependency and licensing risk;
- interaction with later plan phases.

Recommend one approach and explain why. Record rejected alternatives and the conditions that would justify reopening them.

Do not manufacture alternatives for a mechanical task whose architecture is already accepted.

### 8. Commit durable design decisions before execution

If the task changes accepted architecture, invariants, model policy, validation policy, or the implementation plan, the durable repository documents must be updated before or as an explicit first phase of the implementation issue.

Prefer a separate design/source-of-truth PR before implementation when:

- the decision affects multiple future tasks;
- the design requires substantial review;
- implementation should not begin until the decision is merged;
- the issue would otherwise become the only place where architecture is defined.

The execution issue must link the exact committed document or design PR and state whether implementation is blocked on its merge.

### 9. Define bounded phases

Decompose the work into the smallest independently verifiable outcomes that still produce meaningful progress.

Each phase must have:

- one primary outcome;
- explicit inputs and permitted scope;
- expected files or artifacts;
- exact validation commands where knowable;
- objective success criteria;
- independent review checks;
- explicit exclusions;
- a recommended model capability class and rationale;
- a commit boundary or explicitly documented no-code result.

A phase is too large when:

- it spans unrelated subsystems;
- it mixes design discovery with broad implementation;
- it cannot be reviewed without understanding several later phases;
- a failure would not reveal which decision or edit caused it;
- it requires multiple architectural outcomes in one commit.

### 10. Select model capability per phase

Assign capability classes rather than relying only on product names:

- `TOP_REASONING`: architecture, ambiguous root cause, concurrency/lifetime, numerical correctness, difficult performance reasoning, cross-cutting design, final review.
- `STRONG_CODING`: bounded implementation requiring repository-wide understanding or non-trivial tests.
- `FAST_CODING`: mechanical edits, repetitive migrations, straightforward test additions, cleanup under exact instructions.
- `LIGHTWEIGHT`: formatting, generated documentation synchronization, metadata, or other low-risk deterministic work.

For every phase, include:

- recommended capability class;
- reason it is needed;
- risk of using a weaker class;
- whether substitution must block or merely be recorded.

Do not require a specific transient model name unless the environment guarantees it.

### 11. Design validation before implementation

Validation must prove the requested outcome, not merely that the code compiles.

Define as applicable:

- baseline revision and configuration;
- reproduction command;
- unit, integration, regression, and repeated-run tests;
- negative and failure-path tests;
- numerical comparison method and tolerance;
- performance benchmark protocol and metrics;
- memory/resource limits;
- telemetry or artifact paths;
- hardware, model, dataset, dependency, and environment identifiers;
- exact distinction between required, optional, and unavailable checks.

For performance work, include both average and tail behavior where relevant, and distinguish cold, warm, and steady-state measurements.

For tasks that cannot be fully validated in the executor's environment, define what may be validated locally, what requires external hardware, and who accepts the residual risk.

### 12. Define review and restart gates

Every phase must define what the independent reviewer verifies.

The full issue must state when to:

- proceed;
- fix a small delta;
- return to design;
- split the issue;
- abandon the attempt and restart from a clean branch.

Require restart or redesign when the implementation demonstrates that the approved architecture, decomposition, validation strategy, or source-of-truth is materially wrong. Do not normalize a chain of compensating patches.

### 13. Search for overlapping issues before creation

Before creating a new issue:

- search open and recently closed issues for the same outcome;
- inspect linked PRs and prior attempts;
- update an existing issue when it already owns the work;
- create a new issue only when it has a distinct contract;
- link superseded or dependent issues explicitly.

### 14. Review the draft issue against the quality gate

An `EXECUTION_READY` issue must answer all of these:

- What exact outcome must become true?
- Why is it needed now?
- What is the observed current state?
- Which committed documents and files govern the work?
- Which design is accepted, and which alternatives were rejected?
- What is explicitly in scope?
- What is explicitly out of scope?
- Which invariants must not change?
- What assumptions remain, and how are they marked?
- What are the ordered bounded phases?
- What artifacts must each phase produce?
- Which model capability should execute each phase, and why?
- Which exact validations prove each phase?
- What does the independent reviewer inspect?
- What blocks progression?
- What triggers redesign or restart?
- What is the final acceptance criterion?
- Which source-of-truth documents must be updated?
- Is the issue understandable without private chat context?
- Could a fresh Codex session execute Phase 1 without guessing?

If any answer is materially missing, do not label the issue `EXECUTION_READY`.

### 15. Obtain approval and create/update the issue

Present the final design and issue contract for user approval before creating an execution issue when material choices were made during the session.

After approval:

- create or update the GitHub issue using the connector;
- preserve the approved wording and boundaries;
- add appropriate links to source-of-truth files, dependencies, prior issues, and PRs;
- report the issue number and readiness state;
- do not begin implementation in the same design-authority session unless the user explicitly changes the role and workflow.

## Canonical issue template

Use this structure, removing sections only when they are genuinely inapplicable:

```markdown
# <Outcome-oriented title>

## Readiness

**State:** EXECUTION_READY | DESIGN_REQUIRED | INVESTIGATION_REQUIRED | BLOCKED
**Design authority:** <session/person>
**Target repository/base:** `<repo>` / `<branch-or-commit>`

## Goal

<Observable outcome that must become true.>

## Motivation

<Why the change is needed and why now.>

## Current state and evidence

- **OBSERVED:** <current behavior, reproduction, measurement, or repository fact>
- **OBSERVED:** <relevant code/test/document state>

## Source of truth

- `AGENTS.md`
- `<design/decision/plan document>`
- `<relevant source/test path>`
- `<upstream repository and pinned revision>`

## Accepted design

- **ACCEPTED:** <decision>
- **ACCEPTED:** <invariant>

### Rejected alternatives

- **REJECTED:** <alternative> — <reason and reopening condition>

## Scope

### In scope

- <bounded requirement>

### Out of scope

- <explicit exclusion>

## Constraints and invariants

- <behavior, architecture, compatibility, numerical, resource, or licensing rule>

## Assumptions and open questions

- **OPEN:** <must be resolved before execution, or none>
- **SPECULATIVE:** <hypothesis assigned to an investigation phase, or none>
- **BLOCKED:** <missing dependency/access, or none>

## Dependencies and prerequisites

- <issue, PR, commit, model, hardware, data, or permission>

## Execution phases

### Phase 1 — <bounded outcome>

**Status:** NOT_STARTED
**Recommended model class:** TOP_REASONING | STRONG_CODING | FAST_CODING | LIGHTWEIGHT
**Rationale:** <why>
**Weak-model risk:** <risk or low>

**Inputs**
- `<path/revision/artifact>`

**Instructions**
1. <bounded action>
2. <bounded action>

**Expected deliverables**
- `<file/artifact/behavior/evidence>`

**Validation**
- `<exact command>`
- Success means: <observable criteria>

**Independent review checks**
- <scope/architecture check>
- <artifact check>
- <validation/evidence check>
- <unexpected-change check>

**Out of scope for this phase**
- <explicit exclusion>

## Final validation

- `<full command or external validation procedure>`
- Success means: <complete acceptance criteria>

## Required source-of-truth updates

- `<path>` — <required update>

## Failure, rollback, and restart policy

- Fix a reviewed delta when: <condition>
- Return to design when: <condition>
- Restart from a clean branch when: <condition>
- Preserve evidence by: <issue/PR/branch rule>

## Final acceptance criteria

- [ ] Every phase has a result commit or documented no-code result.
- [ ] Every phase has an independent review verdict.
- [ ] Required tests and measurements pass with recorded evidence.
- [ ] No explicit out-of-scope behavior was introduced.
- [ ] Source-of-truth documents are current.
- [ ] Final external PR review approves the complete diff and issue history.

## Handoff to Codex

Execute using `.agents/skills/spec-driven-codex-loop/SKILL.md`.
Start with Phase 1 only. Do not infer missing design decisions from chat history.
```

## Completion output

When the issue is created or updated, report:

- issue number and title;
- readiness state;
- key accepted design choice;
- number of phases;
- first-phase outcome;
- blockers or prerequisites;
- whether any source-of-truth PR must merge first.

Do not claim the issue is implementation-ready without checking the complete quality gate against the final GitHub body.

## Provenance

This workflow is repository-specific and self-contained. Its optional helper-skill composition is informed by the MIT-licensed `obra/superpowers` methodology, especially its brainstorming, plan-writing, systematic-debugging, verification, and skill-authoring practices. No external skill installation is required to execute this skill.
