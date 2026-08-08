# Phase 13 iteration 7 — cache immutable transfer-ring event capability

Candidate: parent `859fff131322cf375fd77b88c04789e7d74bb101`, nested `56a7b3ae2969304f64006c033567ee22781cfca5`.

`OBSERVED`: persisting each ring's immutable event-capable property removes the final runtime transfer-ring diagnostics snapshots. The B trace has 14 complete routed-layer cycles. B `pre_issue_ns` falls 51.14% from Iteration 6 and changes from 8.367 ms/layer slower than A to 8.955 ms/layer faster. B routed-layer wall falls 18.89%. The prediction is confirmed and the production change is retained.

Tier 1 used one fresh B process first and produced 0.377645 tok/s, 28.34% above Iteration 6 B at 0.294266 tok/s. The single Tier 2 adjacent A/B confirmation reports A 0.379537 tok/s, B 0.377454 tok/s and 0.994512x scaling, up from 0.766328x. Prompt/generated IDs, logits and routes remain exact. All storage/integrity errors, stale completions, live request references, failed cleanups and transcript drops are zero.

The unchanged A trace is reused from Iteration 2: SHA-256 `ff620e6c19f81bbeaa956ce06546301198ad8880e25dc0d8af914494f795293e`, 1,099,909 bytes and 14 complete cycles. The fresh B trace is valid with SHA-256 `12b462a23f8123cff04a6d4749eaf652d150150fc274c7cb022935a9cdabc0fa`, 1,096,532 bytes, 14 selected cycles, and zero CUPTI errors, drops, retained buffers or unmatched correlations. It uses selection seed 61, request 15, routed layer 11 and a 1,000-ms window.

Compilation used `-j76`; all 12 focused expert CTests passed sequentially. The change stores initialization metadata for the same pool generation and clears it at surrender; it does not alter expert selection, policy, routing weights, numerical order or transfer lifetime.

The 1.60x stop condition is still not met. The material provider residual has moved completely after current-layer issue: B post-issue wall is 9.384 ms/layer above A while pre-issue is faster. B-minus-A stage and H2D-scope unions are 2.377 and 2.461 ms/layer, but those nested scopes do not explain the full wall residual. Source order reads async-transport diagnostics twice per miss after all enqueues, including directly inside staging. Iteration 8 is a Tier 0 trace comparator only: measure those epoch snapshots and cache nothing unless they explain at least 50% of the post-issue delta. No performance matrix is authorized for that instrumentation step.
