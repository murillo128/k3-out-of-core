# Phase 13 structural-limit return 2

Status: `BLOCKED` pending design-authority disposition. Active measured target before this review record: parent `bf4f44bb9b55f670a7e50734f47edc5fc8da4f52`, nested `b7eb32d854ae7d6766a2f5317b50a14fff4ef3f4`.

The iterative loop has converged rather than merely stalled. Iterations 1, 2, 6 and 7 removed four measured implementation-induced serialization costs and moved the adjacent-pair scaling result from about 0.50x to 0.994512x. Iterations 3, 4 and 8 were bounded falsifications; each prevented an unsupported production optimization. Iteration 5 resolved the no-progress pause with a distinguishing comparator.

The final trace decomposition is wall-exact. A provider is 66.430 ms/layer and B provider is 66.412 ms/layer. B pre-issue, issue and post-issue deltas are -9.034, +0.052 and +8.964 ms/layer, summing to the -0.019-ms provider delta. The post-issue apparent regression is mandatory work moved by issue-ahead ordering, not duplicate B work. The complete B graph is 2.454 ms/layer; removing all of it yields a 1.027236x upper bound. The 1.60x target requires B at 42.638 ms/layer, 26.228 ms below observed B and 23.774 ms below even that impossible zero-graph bound.

The executor therefore returns the quantified structural limit required by design amendment `5225799754`. Acceptance should authorize the final five-pair matrix, immutable evidence refresh and independent final review at the exact accepted target. Rejection must identify a revised in-scope mechanism capable of removing at least 26.228 ms/layer specifically from B; another source-local micro-optimization cannot bridge the bound.

The complete machine-readable iteration ledger and bound are in `structural-limit-review.json`; per-iteration trace arithmetic and immutable trace hashes remain in `iterations/iteration-0` through `iteration-8`.
