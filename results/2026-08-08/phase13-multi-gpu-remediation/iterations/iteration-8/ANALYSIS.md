# Phase 13 iteration 8 — transport-epoch comparator; structural conclusion rejected

Candidate: parent `bf4f44bb9b55f670a7e50734f47edc5fc8da4f52`, nested `b7eb32d854ae7d6766a2f5317b50a14fff4ef3f4`.

`OBSERVED`: the Tier 0 comparator falsifies async-transport epoch snapshots as the remaining post-issue cause. Across 14 complete B routed-layer cycles, both snapshot sites together consume 0.011253 ms/layer, only 0.126% of the 8.964-ms/layer B-minus-A post-issue delta and far below the declared 50% threshold. No epoch caching is implemented. The trace-only scopes are retained because their measured cost is negligible and they preserve direct attribution.

The fresh B trace is valid with SHA-256 `b85238a34e43c7ccb9098ffe3cc327d91e0b8efcb709e4c3847f21f4d1e66ff0`, 1,110,695 bytes, and zero CUPTI errors, drops, retained buffers or unmatched correlations. It uses the same seed 61/request 15/layer 11/1,000-ms selection as the reused Iteration 2 A trace, SHA-256 `ff620e6c19f81bbeaa956ce06546301198ad8880e25dc0d8af914494f795293e`. All 12 focused expert CTests pass; Tier 0 required no performance matrix.

`REJECTED`: the structural-limit interpretation of this trace is invalid under design-authority amendment `5226377041`. A and B provider walls of 66.430 and 66.412 ms/layer include about 50.9/51.9 ms/layer of compliance-only full cache-policy state hashing. The relative transport-epoch comparator remains useful, but the common provider wall cannot be treated as mandatory production work.

The historical arithmetic — A 68.220 ms/layer, B 68.866 ms/layer and a nominal zero-B-graph bound of 1.027236x — is retained only to make the rejected conclusion auditable. It is not a Mode-P roofline and must not support a stop decision, final review or merge.

The Iteration-7 pair at A 0.379537 tok/s, B 0.377454 tok/s and 0.994512x is likewise compliance-contaminated historical context. Execution continues with the required administrative-overhead audit, explicit Mode C/P switch, dual-mode qualification and a fresh Mode-P causal trace before any new roofline conclusion.
