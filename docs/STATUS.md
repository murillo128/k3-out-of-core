# Current Status

Last updated: **2026-07-29**

This file is the first handoff document for a new ChatGPT or Codex session.

## Project state

- Phase 1 technical evidence is complete under GitHub issue #7's STANDARD profile.
- Checkpoints A and B returned **PASS_WITH_NOTES** with safety gate **YES**.
- Checkpoint C: **PENDING** independent review of the committed benchmark and closeout state.
- Three Checkpoint C attempts returned **FAIL / NO** on verifier traceability ambiguities; the benchmark itself passed every review.
- The latest bounded correction is committed in the current branch. It uses canonical field parsing, externally verifies failed review history, and requires a single-parent attestation. Fresh Checkpoint C re-review is the pending action.
- No out-of-core runtime implementation exists. Project Phase 2 has not begun.
- The Phase 1 evidence is descriptive for the tiny K3 fixtures on `skynet`; it is not a quality or production-performance claim.

## Clean execution contract

- Issue: <https://github.com/murillo128/k3-out-of-core/issues/7>
- Draft PR: <https://github.com/murillo128/k3-out-of-core/pull/8>
- Profile: `STANDARD`
- Immutable execution base: `511e87fc98cca8069fc57526fbb04b10789967eb`
- Branch: `codex/phase1-closeout-clean`
- Evidence: [`../results/2026-07-29/skynet/phase1-closeout-clean/SUMMARY.md`](../results/2026-07-29/skynet/phase1-closeout-clean/SUMMARY.md)

This branch was created directly from the immutable base. Work from issue #3 and PR #4 was not reused, cherry-picked, amended, or resumed.

## Pinned inputs

- `llama.cpp`: `84245db4c790af22135f34992689edcc11877003`, exact and clean.
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

## Review notes carried forward

- Checkpoint A limits the tokenizer result to the fixed ordinary prompt. Named HF special tokens conflict with GGUF BOS/EOS/PAD metadata and `<|im_end|>` is outside the GGUF vocabulary.
- Checkpoint B confirmed that no converter decoder or repacker helper is called by the independent MXFP4 validator. Its metadata reader has a transitive helper import, making the recorded `gguf_quantization_helpers_imported: false` wording conservative rather than a correctness failure.
- MXFP4 selected top-10 ID sets match at every inference step, while ordered ranks swap within the same set at steps 8 and 24.
- The benchmark's 128-token setting is a maximum cap. The fixture naturally emits EOG after 49 generated tokens; forcing post-EOG decoding would change semantics.

## Immediate next action

Complete Checkpoint C, record its verdict in the committed checkpoint artifact, run the strict closeout verifier, and request the required separate final review of the complete PR and issue history. Do not begin project Phase 2 or any out-of-core runtime implementation.

## Session handoff rule

At the end of every implementation session:

1. update this file with completed work and exact commit SHAs;
2. update `PLAN.md` checkboxes and gate evidence;
3. record durable architectural changes in `docs/DECISIONS.md` only when a decision actually changes;
4. update model commands/evidence in `docs/MODELS_AND_VALIDATION.md`;
5. update repository/artifact records and the machine-readable manifest when revisions or artifacts change;
6. commit before starting a new session.
