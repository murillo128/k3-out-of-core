# Phase 13 iteration 6 — cache immutable transfer-ring lane capacity

Candidate: parent `8c32362027dcc4a91aab55f6b8e3fd2b4c565847`, nested `e324ea13d41add569ed93d76e63b18c3f005c1df`.

`OBSERVED`: persisting each device ring's immutable effective-lane capacity removes the two runtime diagnostics snapshots predicted by Iteration 5. The same seeded B trace contains 11 complete routed-layer cycles. Its post-selector-to-first-issue mean falls from 18.139 ms/layer to 0.0105 ms/layer (-99.94%), B `pre_issue_ns` falls 34.89%, and B routed-layer wall falls 18.07%. The prediction is confirmed and the production change is retained.

Tier 1 used one fresh B process first. It produced 0.294266 tok/s, 22.05% above the comparable Iteration 2 B-01 process at 0.241102 tok/s. The promising result promoted validation to Tier 2: one adjacent A/B pair produced A 0.380398 tok/s, B 0.291510 tok/s and 0.766328x scaling. This improves the selected Iteration 2 result from 0.637820x while preserving exact prompt/generated IDs, logits and routes. All storage/integrity errors, stale completions, live request references, failed cleanups and transcript drops are zero.

The A trace is reused from Iteration 2 because its runtime path and trace schema are unchanged: SHA-256 `ff620e6c19f81bbeaa956ce06546301198ad8880e25dc0d8af914494f795293e`, 1,099,909 bytes and 14 complete cycles. The fresh B trace is valid with SHA-256 `915bcfe6bdd56afa3ab11532cec1a6fff2b55e174b2d6fb58e4bffe0ca957510`, 887,113 bytes and zero CUPTI errors, drops, retained buffers or unmatched correlations. It uses selection seed 61, request 15, routed layer 11 and a 1,000-ms window.

Compilation used `-j76`; all 12 focused expert CTests passed sequentially. The targeted source changes only pool-generation metadata and does not alter expert selection, policy, routing weights, numerical order or transfer lifetime.

The 1.60x stop condition is not met. The reranked trace leaves B `pre_issue_ns` 8.367 ms/layer above A and source order contains one remaining `transfer_ring->diagnostics()` call per device for the immutable `event_capable` property. The one extra B snapshot matches that residual. Iteration 7 will persist that property for the pool generation. Prediction: B `pre_issue_ns` falls at least 6 ms/layer and one B process improves at least 3%; otherwise the change is reverted. The next-ranked residual is B post-issue transfer service at 8.967 ms/layer above A.
