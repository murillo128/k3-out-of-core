# Phase 1 closeout summary

Status: **ACCEPTED** for the Phase 1 technical evidence; **PENDING** independent Checkpoint C attestation.

This directory is the clean STANDARD-profile execution of GitHub issue #7 from immutable base `511e87fc98cca8069fc57526fbb04b10789967eb` on branch `codex/phase1-closeout-clean`. It does not reuse work from issue #3 or PR #4 and contains no Phase 2 or out-of-core runtime implementation.

## Immutable inputs

- `llama.cpp`: `84245db4c790af22135f34992689edcc11877003`, exact and clean.
- F16 source: `d853649387ffe8f48ce0198a29ac1a44205031f7`.
- MXFP4 source: `ef3902c318fb8e13c3507e26055656e687fdfe38`.
- Published GGUF revision: `88de02cf8fa37f87eb06daaed370ac9c3411d5ca`.
- F16 GGUF SHA-256: `411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7`.
- MXFP4 GGUF SHA-256: `0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169`.
- Host: `skynet`, Intel Core i7-11700K, 64 GiB RAM, NVIDIA GeForce GTX 1650 4 GiB, driver 535.288.01.

## Bounded phase results

1. Environment and inputs: exact revisions, complete file hashes, build flags, host hardware, and clean submodule state captured.
2. Test matrix: stable CPU and CUDA suites each passed 54/54; the external GGUF-vocabulary fixture passed 1/1 on both builds after verified Git LFS payload retrieval.
3. Tokenizer: both models and both tokenizer paths produced prompt IDs `[18805, 308, 799, 5624, 12524]`. The model/HF named special-token IDs differ from GGUF BOS/EOS/PAD metadata and `<|im_end|>` is outside the 163840-token GGUF vocabulary; this is explicitly recorded and is not generalized into a chat-template parity claim.
4. MXFP4 integrity: 81 stratified samples covered three layers, projections, experts, and block positions. Scale bytes, code bytes, repacked bytes, and decoded values matched exactly; maximum absolute error was 0.0.
5. Monolithic inference: F16 and MXFP4 each produced identical CPU/CUDA prompt and 32-token generated IDs. Selected top-10 logit ID sets matched at every step and all declared thresholds passed. MXFP4 ordered ranks swapped within the same set at steps 8 and 24. CUDA assigned layers 0-8 and all 21 type-39 expert tensors to CUDA0; layer 3 Flash Attention compute remained on CPU because the CUDA backend lacks support for that operation.
6. Benchmarks: four fresh-process combinations each used one model load, one discarded warmup, and five measured context-recreated runs. All 24 successful inferences produced the same 49 generated IDs and natural terminal EOG ID 163585; the Phase 5 32-token sequence is an exact prefix. The requested 128-token setting is treated as a maximum cap because the model naturally terminates at 49 tokens.

## Descriptive benchmark results

| Model | Backend | Load s | Prompt tok/s mean | Decode tok/s mean | TTFT p50 s | Decode p50/p95/p99 s | Peak RSS bytes | Peak device VRAM bytes |
|---|---|---:|---:|---:|---:|---|---:|---:|
| F16 | CPU | 0.191140 | 417.883 | 97.458 | 0.011932 | 0.010248 / 0.010341 / 0.010650 | 911753216 | n/a |
| F16 | CUDA | 0.239766 | 618.652 | 160.284 | 0.008080 | 0.006232 / 0.006284 / 0.006606 | 1049235456 | 871432192 |
| MXFP4 | CPU | 0.191723 | 435.112 | 99.575 | 0.011484 | 0.010021 / 0.010146 / 0.010441 | 879845376 | n/a |
| MXFP4 | CUDA | 0.233851 | 632.336 | 163.015 | 0.007908 | 0.006102 / 0.006197 / 0.007413 | 1016848384 | 879820800 |

These measurements describe this tiny fixture and host only; they do not establish out-of-core performance.

## Review and gate state

- Checkpoint A: **PASS_WITH_NOTES**, safety gate **YES**.
- Checkpoint B: **PASS_WITH_NOTES**, safety gate **YES**.
- Checkpoint C: **PENDING** until an independent reviewer evaluates the committed benchmark and closeout state.
- The first Checkpoint C attempt at `3867da790b9b299b925cc562cbfdc7a5985c7da6` returned **FAIL**, safety gate **NO**, because strict verification did not reject incomplete cross-document attestation. The bounded corrective delta strengthens this gate before re-review.
- The second Checkpoint C attempt at `44177bcee0d0b8d367f7c7272e21b3f75f99fd50` returned **FAIL**, safety gate **NO**, after finding that a failed ancestor, coexisting PENDING status, and placeholder link could still pass. The next correction binds strict closeout to the exact attestation parent and externally verified issue #7 review comment.
- The third Checkpoint C attempt at `1fc662f83ead68d48242376b4ab0820f787f7fbd` returned **FAIL**, safety gate **NO**, after finding removable failure history, ambiguous duplicate fields, and merge-attestation acceptance. The latest correction uses canonical field parsing, externally verifies all failed attempts, and requires a single-parent attestation; fresh re-review is pending.
- The fourth Checkpoint C attempt at `a2dba4bbe1fe5d39a3667f64fee6cba6673bd5c7` returned **FAIL**, safety gate **NO**, after finding suffix/comment/list contradictions and case-sensitive URL discovery. The latest correction fully anchors fields and rejects every extra label-bearing line; fresh re-review is pending.
- `evidence.sha256` authenticates every primary artifact in this directory except itself and the derived verifier report.
- `scripts/phase1/verify_closeout.py --allow-pending-checkpoint-c` is the pre-review non-circular gate. Strict mode must fail while Checkpoint C is pending and must pass before final PR review.

Project Phase 2 and all out-of-core runtime work remain explicitly out of scope.
