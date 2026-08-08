# Phase 13 structural-limit return 2 — rejected

Status: `REJECTED` by design-authority amendment `5226377041`. Measured target: parent `bf4f44bb9b55f670a7e50734f47edc5fc8da4f52`, nested `b7eb32d854ae7d6766a2f5317b50a14fff4ef3f4`.

Iterations 1, 2, 6 and 7 still removed four measured implementation-induced serialization costs. Iterations 3, 4 and 8 remain useful bounded falsifications. However, the Iteration-8 structural interpretation is invalid because the traced common provider wall includes the compliance-only `hash_state()` cost exposed in Iteration 4.

The decomposition is wall-exact only for Mode C. Its A/B provider walls of 66.430/66.412 ms/layer contain about 50.9/51.9 ms/layer of full state hashing. Consequently, the nominal 1.027236x zero-B-graph bound is not a production upper bound. The arithmetic remains recorded for audit but cannot justify stopping the optimization loop.

Execution continues under amendment `5226377041`: classify the administrative hot path, preserve full attestation in Mode C, disable compliance-only work in explicit Mode P, validate exact behavior with matched observation settings, then recapture the bounded trace and rebuild the roofline from Mode-P evidence.

The complete machine-readable iteration ledger and bound are in `structural-limit-review.json`; per-iteration trace arithmetic and immutable trace hashes remain in `iterations/iteration-0` through `iteration-8`.
