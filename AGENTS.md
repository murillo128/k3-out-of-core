# AGENTS.md

Bootstrap instructions for coding agents working in this repository.

## Authority

Implement the K3 out-of-core runtime from committed architecture and the active controlling issue. Repository documents define durable architecture and validation; GitHub issues define bounded active work; tests and captured evidence establish observed behavior. Chat history is provisional when it conflicts with those sources.

When authoritative sources materially conflict, stop and document the conflict rather than silently choosing one.

Use these decision markers exactly in design notes: `ACCEPTED`, `OPEN`, `SPECULATIVE`, `REJECTED`, `OBSERVED`, and `BLOCKED`. Never present an `OPEN` or `SPECULATIVE` item as decided.

## Progressive context

For non-trivial work, load only:

1. this file;
2. the controlling GitHub issue;
3. the workflow skill for the current role;
4. exact repository documents, source, tests, manifests, evidence, or dependency state needed by the current outcome.

Do not preload repository documentation, skills, issue/PR history, or result directories. Reuse previously inspected inputs while their identity is unchanged.

Read epic #39 only to select the next phase, resolve roadmap dependencies, or update global roadmap status. Once a controlling issue exists, it is the active execution contract.

## Role routing

Load skills lazily:

- design: `.agents/skills/design-github-issue/SKILL.md`;
- implementation: `.agents/skills/spec-driven-codex-loop/SKILL.md`;
- Git/GitHub mutation: `.agents/skills/codex-github-operations/SKILL.md`;
- independent review: `.agents/skills/codex-independent-review/SKILL.md`;
- non-trivial native build/test: `.agents/skills/native-build-test/SKILL.md`;
- profiling/performance tuning: `.agents/skills/profile-performance-tuning/SKILL.md`.

`AGENTS.md` owns only repository-wide invariants and routing. Skills own procedures. Issues own phase-specific scope, commands, inputs, and gates. Do not duplicate rules between them.

`STANDARD` is the default execution profile. `HIGH_ASSURANCE` is opt-in and must be explicit.

## Durable project invariants

- Keep storage, cache mechanism, policy, transport, and execution responsibilities separated.
- Preserve the monolithic/baseline execution path for correctness and A/B comparison when changing execution behavior.
- Do not silently downgrade unsupported configurations or claim hardware/runtime support from compilation alone.
- Do not commit generated model weights, GGUFs, large traces, benchmark binaries, secrets, or prohibited artifacts.
- Do not import third-party code without license and attribution review.
- Avoid unrelated cleanup and formatting.
- Do not force-push or rewrite shared history without explicit user approval.
- Direct commits to the default branch require explicit user instruction; otherwise use a feature branch and pull request.

Architecture-specific constraints and accepted design decisions belong in `docs/DECISIONS.md`; technical sequence and exit gates belong in `PLAN.md` and `docs/plan/`; model and validation requirements belong in `docs/MODELS_AND_VALIDATION.md`. Load the relevant sections when the controlling issue requires them instead of carrying their contents in this bootstrap file.

## Nested `llama.cpp`

Use `llama.cpp/AGENTS.md` and `llama.cpp/CONTRIBUTING.md` for technical conventions and any actual contribution intended for `ggml-org/llama.cpp`.

Their upstream contribution restrictions apply when targeting upstream or preparing/communicating an upstream submission. Internal development in `murillo128/llama.cpp` and parent-repository gitlink updates follow this repository's controlling issue and workflow skills. The repository owner authorizes agents executing an approved controlling issue to commit, push, and manage branches/PRs under `murillo128` without per-action approval.

If an action targets `ggml-org/llama.cpp` or communicates with upstream maintainers, stop and follow the current upstream contribution and disclosure policy.

## Current work

Use epic #39 to identify the current phase and controlling issue only when necessary. Do not encode phase-specific status in this file.
