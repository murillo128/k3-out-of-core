# Phase 2 authoritative expert-storage evidence

Status: `OBSERVED` — issue #10 implementation phases 1 and 2 passed validation on 2026-07-29.

## Revisions and artifacts

- Project execution base: `c0ef5d08c6efb8d1f7a08a62109feb1a488c72fa`.
- `llama.cpp` execution base: `84245db4c790af22135f34992689edcc11877003`.
- Route-observer commit: `92c4627e19219134ed42e24aa84a1514bf3dffa3`.
- Authoritative storage-metadata commit: `4daaaa1a4dd26d6465f84891b854b5f7ddc03020`.
- Published GGUF revision: `88de02cf8fa37f87eb06daaed370ac9c3411d5ca`.
- F16 GGUF: 784318432 bytes, SHA-256 `411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7`.
- MXFP4 GGUF: 751976576 bytes, SHA-256 `0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169`.

## Authoritative loader metadata

The model now owns a lifetime-stable copy of every path-loaded source-file identity and size plus every source tensor's file index, whole-tensor offset and byte size, GGML type, logical shape, physical byte strides, and GGUF alignment. Runtime layout changes, extra backend transforms, and repacking are reported independently.

Explicitly ordered split paths use the same representation. Anonymous `FILE *` and user-supplied tensor models returned `LLAMA_MODEL_STORAGE_ERROR_NO_FILE_BACKING_METADATA`; no mapping, pointer, descriptor, `/proc`, or virtual-address inference is used.

## Storage maps and byte reconstruction

The versioned schema and both maps passed JSON Schema validation. Each artifact contains 56 `(layer, expert_id)` entries and 168 gate/up/down projections. Across both artifacts, validation covered 112 entries, 336 projections, and 42 source tensors.

Every pinned projection is one contiguous expert slice. For each routed source tensor, the eight expert spans concatenate in expert order to the exact whole-tensor byte range read from the GGUF. Bounds, file size, file identity, alignment, atomic bundle size, and byte reconstruction all passed.

The F16 routed tensors used the ordinary CPU buffer with no reported transform or repack. The MXFP4 routed tensors selected `CPU_REPACK`: all 21 report a backend transform and repack while retaining unchanged source type, shape, strides, and spans. This separation prevents runtime layout from being mistaken for backing-file layout.

Synthetic tests cover contiguous, regular strided, and irregular segmented span representations, including rejection of logical gaps and layout misclassification.

## Regression validation

- CPU and CUDA `llama` library builds passed at the exact storage-metadata commit.
- The complete F16/MXFP4 CPU/CUDA numerical gate passed with finite full-vocabulary logits, exact generated IDs, and accepted CPU/CUDA selected-logit comparisons.
- The complete route-observer matrix passed again at the storage-metadata commit, including repeated trace bytes, direct-readback parity, disabled-path behavior, failure modes, and deterministic routing statistics.
- Python route and storage-layout tests passed; both repositories passed whitespace checks.

Machine-readable evidence is in `phase2-storage-validation.json`, `phase2-route-regression.json`, `phase2-numerical/inference.json`, and the two `phase2-*-expert-storage-map-v1.json` files.
