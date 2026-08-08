# Phase 13 iteration 12 — ephemeral cross-device staging rejected

Candidate base: parent `c04e5dac6b4883380cda048a322f165cd6132517`, nested candidate `3a5e68af7e3c593cb83c87e6d5270ee6b4ebd008`. Reverted without rewriting history by nested `d9fbc8bb1b8b2c395463febfe7f2f7616276ffe9`; its source tree matches the retained Iteration-11 implementation.

The candidate reserved transfer lanes in canonical order, staged bundles concurrently only across distinct per-device rings, then finalized scheduler state and H2D waves in canonical order. It fell back to the serial implementation if any device lacked sufficient lanes. Compilation used `-j76`; the four most sensitive native tests passed: provider, peer staging, hot-cache lifecycle and async I/O.

Mode-C B preserves the accepted exact identity SHA-256 `60658621b12340bc02d1fbb614142e4a17c5dd52eb529bfd4b0b2eb1a1255889`, all 24 logit digests and 1,032 route records. This rules out cross-ring data, generation and merge-order corruption in the measured path.

`REJECTED`: Mode-P Tier-1 B is `1.168476` tok/s, `-17.11%` versus the comparable retained Iteration-11 Tier-1 B (`1.409646`) and `-14.55%` versus its adjacent Tier-2 B. Creating and joining `std::async` tasks once per routed layer costs more than the overlapped host copies save. The declared `+3%` first-stage threshold fails, so no retained-candidate trace or wider matrix is authorized and the candidate is reverted.

The cross-device staging mechanism remains source-plausible, but ephemeral task construction is disproven as its implementation. Iteration 13 will test the smallest bounded refinement: one persistent auxiliary staging worker owned by the multi-device provider, with the caller staging the other ring. Prediction: remove the Iteration-12 regression, reduce B stage service at least 20%, provider wall at least 5%, and improve retained Iteration-11 B TPS at least 3%; revert on any missed performance, exactness, generation, cancellation, lifecycle or resource-bound threshold.
