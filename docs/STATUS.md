# Current Status

Last updated: **2026-07-29**

This file is the first handoff document for a new ChatGPT or Codex session.

## Project state

- Project Phase 1 is complete and merged.
- GitHub issue #7 is closed as completed.
- PR #8 merged into `main` as `7b4d21e8793c451ce34c691f438afceabd64a841`.
- PR #9 merged into `main` as `eff5efc754919bf1a50735e27c7ad39f4d93384e`; its STANDARD materiality and repeated-review circuit-breaker rules now govern future issues.
- Checkpoints A, B, and C returned **PASS_WITH_NOTES** with safety gate **YES**.
- The final complete-PR review returned **PASS_WITH_NOTES** with safety to merge **YES**.
- No out-of-core runtime implementation exists. Project Phase 2 has not begun.
- Phase 1 evidence is descriptive for the tiny K3 fixtures on `skynet`; it is not a model-quality or production-performance claim.

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

## Phase 1 evidence

- Stable CPU and CUDA tests: 54/54 passed in each build.
- External tokenizer GGUF fixture: 1/1 passed in each build after verified Git LFS payload retrieval.
- Fixed ordinary prompt tokenizer parity: exact IDs `[18805, 308, 799, 5624, 12524]` across both source models and both GGUFs.
- Special-token conflicts are explicitly documented; general chat-template/end-of-message parity is not claimed.
- MXFP4 integrity: 81/81 stratified samples matched scale bytes, code bytes, repacked bytes, and decoded values exactly.
- F16 and MXFP4 monolithic CPU/CUDA inference: exact 32-token generation parity and accepted selected-logit thresholds.
- CUDA placement: layers 0-8 and all 21 type-39 routed-expert tensors assigned to CUDA0; the unsupported layer-3 Flash Attention operation remained on CPU and is logged.
- Repeated benchmark: one discarded warmup plus five measured runs per model/backend combination, with model retained and context recreated. All runs terminated naturally at the same 49-token sequence and EOG ID 163585.
- Prompt/decode throughput, TTFT, per-token p50/p95/p99 latency, load time, RSS, and CUDA VRAM are committed in `benchmarks.json` and summarized in `SUMMARY.md`.
- Strict closeout verifier passed with zero errors and all 18 evidence checksums passed.

## Review notes carried forward

- Tokenizer acceptance is limited to the fixed ordinary prompt. Named HF special tokens conflict with GGUF BOS/EOS/PAD metadata and `<|im_end|>` is outside the GGUF vocabulary.
- No converter decoder or repacker helper is called by the independent MXFP4 validator. Its metadata reader has a transitive helper import, so the recorded `gguf_quantization_helpers_imported: false` wording is conservative rather than a correctness failure.
- MXFP4 selected top-10 ID sets match at every inference step, while ordered ranks swap within the same set at steps 8 and 24.
- The benchmark's 128-token setting is a maximum cap. The fixture naturally emits EOG after 49 generated tokens; forcing post-EOG decoding would change semantics.
- VRAM is sampled device-wide telemetry and the OS page cache was not flushed.
- Five earlier Checkpoint C failures remain preserved as workflow history; they found no material technical defect.

## Immediate next action

Design a new, bounded GitHub issue for **Project Phase 2 — routing and expert-layout observability**.

The design session must inspect the exact pinned `llama.cpp` code before declaring the issue `EXECUTION_READY`. It should resolve:

- the exact router seam exposing selected expert IDs and routing weights;
- deterministic trace semantics for layer, token/batch position, request or sequence, and prefill versus decode;
- a compact versioned trace format and trace-disabled overhead requirement;
- the loader seam for authoritative GGUF file identity, offset, byte size, type, logical shape, stride, and alignment;
- `ExpertStorageEntry` semantics for atomic gate/up/down expert bundles;
- how implementation should be split into reviewable issues or phases.

Do not implement cache, provider, NVMe I/O, prefetch, expert remapping, or residency changes as part of the Phase 2 design issue unless a later approved implementation contract explicitly permits them.
