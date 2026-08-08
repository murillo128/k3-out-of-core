# Phase 13 iteration 8 — transport-epoch comparator and structural limit

Candidate: parent `bf4f44bb9b55f670a7e50734f47edc5fc8da4f52`, nested `b7eb32d854ae7d6766a2f5317b50a14fff4ef3f4`.

`OBSERVED`: the Tier 0 comparator falsifies async-transport epoch snapshots as the remaining post-issue cause. Across 14 complete B routed-layer cycles, both snapshot sites together consume 0.011253 ms/layer, only 0.126% of the 8.964-ms/layer B-minus-A post-issue delta and far below the declared 50% threshold. No epoch caching is implemented. The trace-only scopes are retained because their measured cost is negligible and they preserve direct attribution.

The fresh B trace is valid with SHA-256 `b85238a34e43c7ccb9098ffe3cc327d91e0b8efcb709e4c3847f21f4d1e66ff0`, 1,110,695 bytes, and zero CUPTI errors, drops, retained buffers or unmatched correlations. It uses the same seed 61/request 15/layer 11/1,000-ms selection as the reused Iteration 2 A trace, SHA-256 `ff620e6c19f81bbeaa956ce06546301198ad8880e25dc0d8af914494f795293e`. All 12 focused expert CTests pass; Tier 0 required no performance matrix.

`OBSERVED`: the exact critical path now converges on a structural single-request limit for the approved workload and design. A and B provider walls are 66.430 and 66.412 ms/layer. B pre-issue is 9.034 ms faster, issue is 0.052 ms slower, and post-issue is 8.964 ms slower; these placement deltas sum to -0.019 ms, exactly the provider-wall delta. Source order confirms that the batched B path moves mandatory victim, staging and publication work from before current-layer issue to after it; the post-issue delta is therefore not duplicate work that can be deleted.

The total traced layer walls are A 68.220 ms and B 68.866 ms. Reaching 1.60x requires B at or below 42.638 ms, a 26.228-ms/layer or 38.086% reduction. B graph wall is only 2.454 ms and contains no simultaneous GPU0/GPU1 kernel interval. Even the impossible bound that removes the complete B graph while preserving the mandatory provider path reaches only 1.027236x and remains 23.774 ms/layer above the target.

The last selected adjacent performance pair remains Iteration 7 at A 0.379537 tok/s, B 0.377454 tok/s and 0.994512x. A final five-pair matrix is intentionally not run until design authority accepts this quantified structural-limit return, as required by amendments `5225799754` and `5226296660`.
