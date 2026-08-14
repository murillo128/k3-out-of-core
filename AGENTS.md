# AGENTS.md

Bootstrap instructions for coding agents working in this repository.

## Authority

Implement the K3 out-of-core runtime from committed architecture and the active controlling issue. Chat history is provisional until recorded in an authoritative source.

Authority is scoped by concern:

- tests and captured evidence establish observed behavior;
- `docs/DECISIONS.md` establishes accepted architecture;
- `PLAN.md` and `docs/plan/` establish technical sequence and exit gates, while `docs/MODELS_AND_VALIDATION.md` establishes model and validation requirements;
- the controlling issue establishes the bounded phase contract within those durable constraints;
- pull requests, checks, reviews, manifests, and Git history preserve implementation and reproducible evidence; epic #39 owns roadmap status only.

When authoritative sources materially conflict, stop and document the conflict rather than silently choosing one.

Use these decision markers exactly in design notes: `ACCEPTED`, `OPEN`, `SPECULATIVE`, `REJECTED`, `OBSERVED`, and `BLOCKED`. Never present an `OPEN` or `SPECULATIVE` item as decided.

## Progressive context

For non-trivial work, load only:

1. this file;
2. the controlling GitHub issue;
3. the workflow skill for the active role;
4. exact repository documents, source, tests, manifests, evidence, or dependency state needed by the current outcome.

Do not preload repository documentation, skills, issue/PR history, or result directories. Reuse previously inspected inputs while their identity is unchanged.

Read epic #39 only to select the next phase, resolve roadmap dependencies, or update roadmap status. Once a controlling issue exists, it and its PR are the active execution context. Do not encode phase-specific status in this file or duplicate it into durable repository documents.

Trivial non-functional edits may skip the controlling-issue workflow unless the user explicitly requests it; repository-wide safety rules still apply.

## Role routing

Load skills lazily:

- design: `.agents/skills/design-github-issue/SKILL.md`;
- implementation: `.agents/skills/spec-driven-codex-loop/SKILL.md`;
- Git/GitHub mutation: `.agents/skills/codex-github-operations/SKILL.md`;
- independent review: `.agents/skills/codex-independent-review/SKILL.md`;
- non-trivial native build/test: `.agents/skills/native-build-test/SKILL.md`;
- profiling/performance tuning: `.agents/skills/profile-performance-tuning/SKILL.md`.

`AGENTS.md` owns repository-wide authority, invariants, and routing. Skills own procedures. Issues own phase-specific scope, commands, inputs, and gates. Do not duplicate rules between them.

`STANDARD` is the default execution profile. `HIGH_ASSURANCE` is opt-in and must be explicit.

## Durable project invariants

- Keep storage, cache mechanism, policy, transport, and execution responsibilities separated.
- Preserve the applicable monolithic/baseline path for correctness and A/B comparison when changing execution behavior.
- Execution changes must fail closed on non-finite results absent from the baseline, invalid IDs or generations, missing or mismatched expert payload/metadata, lifetime or cancellation failures, asynchronous completion-order nondeterminism, unexplained tokenization/EOS changes, and unbounded memory growth.
- When persistent/cache/generation state is affected, validation must exercise repeated warm reuse when that failure mode can apply.
- Decision-driving performance claims must bind exact revisions and configuration and report end-to-end throughput, latency tails, and material resource/I/O effects; hit rate or component throughput alone is not acceptance.
- Do not silently downgrade unsupported configurations or claim hardware/runtime support from compilation alone.
- Do not commit generated model weights, GGUFs, large traces, benchmark binaries, secrets, or prohibited artifacts.
- Do not import third-party code without license and attribution review.
- Avoid unrelated cleanup and formatting.
- Do not force-push or rewrite shared history without explicit user approval.
- Direct commits to the default branch require explicit user instruction; otherwise use a feature branch and pull request.

Architecture-specific constraints and accepted design decisions belong in `docs/DECISIONS.md`; technical sequence and exit gates belong in `PLAN.md` and `docs/plan/`; model and validation requirements belong in `docs/MODELS_AND_VALIDATION.md`. Load the relevant sections only when the active contract or outcome needs them.

Do not update durable repository documents merely to mirror GitHub execution status. Change them only when the durable content owned by that document changes.

## Nested `llama.cpp`

Use `llama.cpp/AGENTS.md` and `llama.cpp/CONTRIBUTING.md` for technical conventions and any actual contribution intended for `ggml-org/llama.cpp`.

Do not develop against an unrecorded moving upstream or dependency head. Pin exact revisions when they are execution or evidence inputs, and treat an update as an explicit technical delta.

The nested restrictions on AI-authored commit messages, automated commits, pushes, comments, and pull requests apply when an action targets `ggml-org/llama.cpp` or prepares or communicates an upstream submission. They do not govern internal K3 development in `murillo128/llama.cpp` or parent-repository commits that update its gitlink.

The repository owner gives standing human authorization for agents executing an approved controlling issue to write commit messages, commit, push, and create or update branches and pull requests under `murillo128` without requesting approval again for each individual action. Do not pause or refuse solely because the nested upstream contribution guard detects agent-assisted work. This fork-local authorization does not bypass the controlling issue, project workflow skills, tests, review gates, licensing rules, or the technical portions of the nested guidance.

Fork-local commits and pull requests do not require upstream-only AI disclosure or `Assisted-by` trailers unless the controlling issue or repository owner explicitly requires them.

If an action targets `ggml-org/llama.cpp`, prepares material for an upstream submission, or communicates with upstream maintainers, stop and follow the current upstream `AGENTS.md`, `CONTRIBUTING.md`, and disclosure policy.
