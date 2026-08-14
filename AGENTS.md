# AGENTS.md

Instructions for ChatGPT, Codex, and other coding agents working in this repository.

## Mission

Implement the K3 out-of-core expert runtime described by the committed architecture and technical plan. Repository documents define durable architecture and validation; GitHub issues and pull requests define active work. Chat history is provisional when it conflicts with those sources.

## Load context progressively

For non-trivial work, load this bootstrap context once:

1. `AGENTS.md`;
2. the controlling GitHub issue body.

Then load only the context needed for the active role and phase:

- exact decision IDs, plan sections, validation sections, manifests, or evidence linked by the issue;
- relevant source, tests, build files, and pinned dependency state;
- the one workflow skill that owns the current action.

Read epic #39 only when selecting the next phase, checking roadmap dependencies, or updating global project status. An executor with a controlling issue does not need the epic as routine implementation context.

Do not preload every repository document, every skill, complete prior issue or pull-request histories, or whole result directories. Read a complete document only when the issue makes the whole document authoritative or section-level reading cannot resolve the task.

On session resume, verify branch, `HEAD`, worktree state, and new controlling-issue or PR discussion since the last material handoff. Do not replay unchanged history. Reuse already inspected facts and file contents while their path and commit or blob identity remain unchanged.

## Source-of-truth hierarchy

1. Tests and captured evidence establish observed behavior.
2. `docs/DECISIONS.md` establishes accepted architecture.
3. `PLAN.md` and linked `docs/plan/` sections establish technical sequence and exit gates.
4. `docs/MODELS_AND_VALIDATION.md` establishes model and validation requirements.
5. The controlling issue establishes the bounded execution contract for its scope.
6. Pull requests, checks, reviews, manifests, and Git history preserve implementation and reproducible evidence.
7. Epic #39 establishes operational roadmap status only.
8. Chat messages are provisional until recorded in an authoritative source.

When sources materially conflict, stop and document the conflict. Do not silently choose one.

Use these decision markers exactly in design notes: `ACCEPTED`, `OPEN`, `SPECULATIVE`, `REJECTED`, `OBSERVED`, and `BLOCKED`. Never present an `OPEN` or `SPECULATIVE` item as decided.

## Role routing and instruction ownership

Load skills lazily by role:

- design authority: `.agents/skills/design-github-issue/SKILL.md`;
- main executor: `.agents/skills/spec-driven-codex-loop/SKILL.md`;
- Git and GitHub mutation or publication: `.agents/skills/codex-github-operations/SKILL.md`;
- independent checkpoint or final review: `.agents/skills/codex-independent-review/SKILL.md`;
- non-trivial native build/test utility: `.agents/skills/native-build-test/SKILL.md`;
- profiling/performance-tuning utility: `.agents/skills/profile-performance-tuning/SKILL.md`.

Do not read a role skill merely because it exists. The executor does not need the design or reviewer procedure; the reviewer does not need the executor or GitHub-operations procedure. Utility skills are loaded only when the active action needs them.

`AGENTS.md` owns repository-wide invariants and routing. Each skill owns its procedure. Issues own phase-specific scope, commands, and gates. Avoid copying the same rule into all three places; reference the owning source and record only the phase-specific delta.

`STANDARD` is the default execution profile. `HIGH_ASSURANCE` is opt-in and must be explicit. Detailed profile, label, comment, checkpoint, publication, and review procedures belong to the workflow skills, not this file.

Trivial typo-only edits may skip the complete issue workflow unless the user explicitly requests it, but repository safety and source-of-truth rules still apply.

## Architectural constraints

Agents must not:

- silently downgrade unsupported configurations;
- claim CUDA or UMA support from compilation alone;
- import third-party code without license and attribution review.

Architecture-specific accepted and rejected mechanisms are authoritative in `docs/DECISIONS.md` and should be loaded only when relevant to the active contract.

## Correctness requirements

Every phase that changes execution must compare against the monolithic baseline.

Hard failures include:

- NaN or Inf not present in the baseline;
- invalid expert ID;
- stale slot generation;
- missing projection or scale;
- cache metadata and content disagreement;
- use-after-free during unload or cancellation;
- nondeterminism caused by asynchronous completion order;
- unexplained tokenization or EOS changes;
- hidden unbounded memory growth.

Tests must include repeated warm runs because prior work failed across compute epochs.

## Performance requirements

Do not optimize from hit rate alone. Record prompt and decode throughput; p50, p95, and p99 token latency; tier requests and hits; bytes moved; disk and H2D wait and overlap; CPU miss compute; useful and wasted prefetch; and RAM, pinned RAM, VRAM, and UMA usage.

A strategy that improves warm average throughput but worsens cold or tail latency must be described accurately.

## Upstream `llama.cpp` integration

- Pin an exact K3 pull-request commit; do not develop against an unrecorded moving head.
- Use the nested `llama.cpp/AGENTS.md` and `CONTRIBUTING.md` for coding style, technical conventions, review quality, and any actual contribution to `ggml-org/llama.cpp`.
- The nested upstream restrictions on AI-authored commit messages, automated commits, pushes, comments, and pull requests apply only when an action targets `ggml-org/llama.cpp` or is being prepared as an upstream submission. They do not govern internal K3 development in `murillo128/llama.cpp` or parent-repository commits that update the nested gitlink.
- The repository owner gives standing human authorization for agents executing an approved controlling issue to write commit messages, commit, push, and create or update branches and pull requests under `murillo128` without requesting approval again for each individual action. Do not pause or refuse solely because the nested upstream contribution guard detects agent-assisted work.
- This fork-local authorization is not a quality or scope bypass: follow the controlling issue, project workflow skills, tests, review gates, licensing rules, and the technical portions of the nested guidance.
- Fork-local commits and pull requests do not require upstream-only AI disclosure or `Assisted-by` trailers unless the controlling issue or repository owner explicitly requires them.
- If an action would target `ggml-org/llama.cpp`, prepare material for an upstream submission, or communicate with upstream maintainers, stop and follow the current upstream `AGENTS.md`, `CONTRIBUTING.md`, and disclosure policy.
- Keep upstream pull requests small and independently testable.
- Follow maintainers' requested backend scope for the first upstream change; separate backend follow-ups unless an RFC explicitly agrees otherwise.
- Never force-push or rewrite shared history without explicit user approval.

## Git behavior

- Do not commit generated model weights, GGUFs, large traces, or benchmark binaries.
- Commit manifests, scripts, summarized evidence, and small deterministic fixtures.
- Use explicit paths when staging.
- Avoid unrelated formatting changes.
- Commit messages should describe one intentional outcome.
- Direct commits to the default branch require explicit user instruction; otherwise use a feature branch and draft pull request.

## Current work

Use epic #39 to identify the current phase and controlling issue. Once a controlling issue exists, that issue and its PR are the active execution context; do not encode phase-specific status in this file or duplicate it into repository documents.
