# Current Status

Last updated: **2026-07-30**

This file is the first handoff document for a new ChatGPT or Codex session.

## Project state

- Project Phase 1 is complete and merged.
- GitHub issue #7 is closed as completed.
- PR #8 merged into `main` as `7b4d21e8793c451ce34c691f438afceabd64a841`.
- PR #9 merged into `main` as `eff5efc754919bf1a50735e27c7ad39f4d93384e`; its STANDARD materiality and repeated-review circuit-breaker rules govern later issues.
- Project Phase 2 is complete and merged through PR #11. The final reviewed project head was `c56770e9148fb94173561b7c4f2aade63cdefff7`; PR #11 merged into `main` as `d74781faec12e8552c1598084b210f784ac0a43b`, with nested `murillo128/llama.cpp` gitlink `4daaaa1a4dd26d6465f84891b854b5f7ddc03020`. The bounded F16/MXFP4 CPU corpus and CPU/CUDA subset remain reproducible, and the unchanged raw corpus is published at immutable Hub revision `2d838d6b4d0aca4e9af1e7d899e57ad29330c72e`.
- Project Phase 3 is complete and merged. Issue #13 was implemented by PR #15 and squash-merged into `main` as `6d15dab02f8129240ca83579445898be2f5f987f`. The merged nested `murillo128/llama.cpp` gitlink is `a120de8e2d0b552c51eacd7d701ef1dd994bc3db`.
- Phase 3 Checkpoint A, Checkpoint B, and the renewed final complete-PR review all returned **PASS_WITH_NOTES** with safety **YES**. The final reviewed integration head was `d8cfa06e39a87223ca97e3326d7e08e96cd64018`; the renewed final review is issue comment `5129200934`.
- Phase 3 preserves the raw standing performance result as `fail`: 22/24 original cells pass, while MXFP4 CUDA disabled-to-resident prompt throughput and TTFT exceed their unchanged confidence budgets. Design-authority comments `5128658370` and `5128726338` accept this narrow Phase-3-only result as `PASS_WITH_NOTES`. This is not a waiver for correctness, default-path performance, decode, cache, transport, misses, multi-request behavior, full-size behavior, or tail latency.
- No out-of-core cache, physical expert slots, storage transport, demand reads, prefetch policy, or asynchronous miss runtime exists yet. Those begin in Phase 4 and later phases.
- Phase 1 evidence is descriptive for the tiny K3 fixtures on `skynet`; it is not a model-quality or production-performance claim.

## Phase 3 merged baseline

- Issue: <https://github.com/murillo128/k3-out-of-core/issues/13>
- Merged PR: <https://github.com/murillo128/k3-out-of-core/pull/15>
- Execution profile: `STANDARD`
- Immutable Phase 3 execution base: `81df862da6e4ff9db005f6265470070bb5456f4c`
- Final reviewed PR head: `d8cfa06e39a87223ca97e3326d7e08e96cd64018`
- PR #15 squash merge: `6d15dab02f8129240ca83579445898be2f5f987f`
- Nested `llama.cpp` head: `a120de8e2d0b552c51eacd7d701ef1dd994bc3db`
- Standing evidence commit: `93635d7ece8fdc617291d5a036bda1c8bc2b6c77`
- Standing capture SHA-256: `23eff115b87a9e8cee101bd1c0b02f299786175e786b4b30dd4a7e66617d4970`
- Checkpoint B review: issue comment `5128960944`
- Renewed final review: issue comment `5129200934`
- Structured closeout: `complete-with-performance-notes`, raw performance gate `fail`, design disposition `accepted-with-notes`.
- Evidence: [`../results/2026-07-29/skynet/phase3-resident-provider/PHASE3.md`](../results/2026-07-29/skynet/phase3-resident-provider/PHASE3.md)

## Phase 1 merged baseline

- Issue: <https://github.com/murillo128/k3-out-of-core/issues/7>
- Merged PR: <https://github.com/murillo128/k3-out-of-core/pull/8>
- Execution profile: `STANDARD`
- Immutable execution base: `511e87fc98cca8069fc57526fbb04b10789967eb`
- Final reviewed and attested branch head: `6173b5298496a5ce4ccb456cafd7b46be0e850ed`
- PR #8 merge commit: `7b4d21e8793c451ce34c691f438afceabd64a841`
- Current policy head after PR #9: `eff5efc754919bf1a50735e27c7ad39f4d93384e`
- Evidence: [`../results/2026-07-29/skynet/phase1-closeout-clean/SUMMARY.md`](../results/2026-07-29/skynet/phase1-closeout-clean/SUMMARY.md)

The clean Phase 1 branch was created directly from the immutable base. Work from issue #3 and PR #4 was not reused, cherry-picked, amended, or resumed.

## Pinned Phase 1 inputs

- `llama.cpp`: `84245db4c790af22135f34992689edcc11877003`, exact and clean at validation.
- F16 source revision: `d853649387ffe8f48ce0198a29ac1a44205031f7`.
- MXFP4 source revision: `ef3902c318fb8e13c3507e26055656e687fdfe38`.
- Published GGUF revision: `88de02cf8fa37f87eb06daaed370ac9c3411d5ca`.
- F16 GGUF: 784318432 bytes, SHA-256 `411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7`.
- MXFP4 GGUF: 751976576 bytes, SHA-256 `0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169`.

## Validated host

```text
Host: skynet
CPU: 11th Gen Intel(R) Core(TM) i7-11700K @ 3.60GHz
RAM: 64 GiB
GPU: NVIDIA GeForce GTX 1650, 4096 MiB
Driver: 535.288.01
OS: Ubuntu 24.04.3 LTS
Kernel: 6.8.0-136-generic
```

## Review notes carried forward

- Tokenizer acceptance is limited to the fixed ordinary prompt. Named HF special tokens conflict with GGUF BOS/EOS/PAD metadata and `<|im_end|>` is outside the GGUF vocabulary.
- MXFP4 selected top-10 ID sets match at every inference step, while ordered ranks swap within the same set at steps 8 and 24.
- The benchmark's 128-token setting is a maximum cap. The fixture naturally emits EOG after 49 generated tokens; forcing post-EOG decoding would change semantics.
- VRAM is sampled device-wide telemetry and the OS page cache was not flushed.
- The Phase 3 raw performance gate and its two accepted notes must remain visible in all later performance comparisons.

## Immediate next action

Start a fresh design-authority session for **Phase 4 — persistent hot cache in accelerator memory**. Inspect the merged Phase 3 seam and `docs/plan/04-cache-and-storage.md`, then create a new self-contained GitHub issue from the current `main` head. Do not begin implementation until the Phase 4 architecture, exact base, scope, tests, evidence, checkpoints, and failure semantics are execution-ready. Do not reopen or rerun Phase 3 performance measurements as part of Phase 4 design.
