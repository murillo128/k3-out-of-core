# Phase 13 iteration 5 — B-only selector attribution comparator

Candidate: parent `838dc08d52f0741c8dbd4ed8c35f0a1ccefb0dec`, nested `c0d6b95141c9b80e1f9400f6a88944f7d9df85c2`.

`OBSERVED`: the no-progress comparator resolves the ambiguous selector interval without changing runtime semantics. Across 9 complete B routed-layer cycles, mandatory policy admission occupies 5.928 ms/layer and 97.069% of the complete 6.107-ms device-constrained selector. Candidate construction/coherence validation is only 0.122 ms/layer and physical-feasibility accounting is 0.037 ms/layer. The comparator hypothesis is confirmed by its relative-share criterion, but it also proves that the selector is not the largest remaining pre-issue dependency.

The newly isolated interval after the final selector and before the first adjacent current-layer enqueue is 18.139 ms/layer, with a narrow 17.934–18.348-ms range across the selected cycles. Source order begins this interval with exactly one `transfer_ring->diagnostics()` snapshot per device. Iteration 1 had exposed approximately 9–10 ms for each such snapshot, and Iteration 2 improved B materially by reducing the call count from once per miss to once per device per layer. The remaining two snapshots quantitatively match the new gap.

This is a Tier 0 instrumentation-only iteration under adaptive-validation amendment `5226296660`. It required no performance matrix. The A runtime path and trace schema needed by this query are unchanged, so the checksum-addressed Iteration 2 A trace (`ff620e6c19f81bbeaa956ce06546301198ad8880e25dc0d8af914494f795293e`, 14 cycles) is reused. The fresh B trace is valid with SHA-256 `b30db3998ce7e70e595a392f832c362f373b086dfaf5051cae3de1db4c1e1df5`, exact logical selection seed 61/request 15/layer 11/window 1000 ms, and unchanged exact workload identity. Compilation used `-j76`; all 12 focused expert CTests passed.

The trace-only scopes are retained as low-volume attribution telemetry. They resolve the mandatory no-progress review; no falsified-production-fix count is carried forward.

Iteration 6 has one B-only production hypothesis: effective lane capacity is immutable for a transfer-ring/pool generation, so persist it once and remove both per-layer diagnostics snapshots. Prediction: the post-selector-to-first-issue gap falls by at least 80%, B `pre_issue_ns` falls by at least 15 ms/layer, and one fresh B process improves decode TPS by at least 3%. Per Tier 1, run focused tests, one B process and the same B trace first; reuse A unless the candidate becomes promising and needs Tier 2 confirmation.
