# Phase 13 iteration 1 — issue-ahead storage comparator

Candidate: parent `262196eb1d18dedb3e25512348e595752f4bb721`, nested `c6f94c953be5af564cafa7786ccf99d33afe0b26`.

`OBSERVED`: the single requested change worked on its exact trace bucket. B's current-layer demand issue span fell by 99.779% relative to Iteration 0, from 72.432 to 0.160 ms/layer. The two-pair screen preserved the exact output identity across four fresh processes and measured A at 0.379615 tok/s, B at 0.186601 tok/s and A→B at 0.491552×. Focused validation passed, with no stale/error/cancellation/resource residue in the normal hardware runs.

The predicted TPS gain did not occur. B is 2.2% below the prior 0.190823-tok/s candidate, below the amendment's mandatory-revert threshold of 3%. The selected B trace also worsened from 147.618 to 159.481 ms/layer. Wall moved out of `issue_span_ns`: B's post-issue bucket increased by 348.7%, and pre-plus-post provider gaps now exceed A by 89.934 ms/layer. CUDA graph time and peer-copy occupancy remain secondary; sampled GPU0/GPU1 kernel overlap is still zero.

The candidate is retained because it removes the measured issue-order defect, has no correctness/resource regression, stays inside the 3% TPS revert boundary and exposes a precise next dependency. In representative B layers, repeated approximately 9–10-ms gaps follow each host-ready→H2D scheduler transition before the next hot-slot victim marker. The next production call after that transition is `ring->diagnostics()` solely to reread immutable effective lane capacity, even though capacity was already queried per device before staging.

Iteration 2 has one primary hypothesis: snapshot effective lane capacity once per device before the miss loop and remove the per-miss diagnostics reacquisition. The prediction is at least a 20% reduction in B `post_issue_ns` and at least a 3% B-TPS improvement, with A within 3%. It will be reverted if both targeted trace and TPS changes are below 3%, or if any correctness/resource gate regresses.
