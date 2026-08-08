# Phase 13 no-progress attribution review 1

Active published heads after both explicit reverts: parent `64e33a63a76d7b15ba2f551c35da87933262ef89`, nested `85ec861073c6b0b5450a4b8b21c9455e3aab53b5`. The nested production tree is back to the retained Iteration 2 implementation; later commits preserve only auditable experiment/revert history.

`OBSERVED`: the prior attribution model overclaimed the selector's whole-directory validation as the cause of B pre-issue wall. Iteration 3 removed that work but did not shrink the target interval or improve B TPS. Iteration 4 then directly measured full policy-state hashing: its unioned B-minus-A increment is only 0.949 ms/layer, 2.306% of the provider pre-plus-post delta. Neither mechanism is eligible for another production fix.

The corrected retained budget is more constraining than the current 0.638× headline suggests. Iteration 2 measures A at 68.220 ms/layer and B at 102.902 ms/layer. A 1.60× layer-rate target requires B at or below 42.638 ms/layer, a 60.265-ms or 58.565% reduction. Removing all 34.682 ms of observed B-extra wall merely makes B equal to A (1.00×); it cannot reach 1.60×. Reaching the objective therefore requires B to overlap or eliminate substantial work that A also pays, not just remove a two-device regression.

The latest instrumented trace leaves 40.210 ms/layer of B-minus-A provider residual after hash attribution. GPU graph work is secondary: sampled simultaneous GPU0/GPU1 kernel overlap is zero, but the entire B graph is only a few milliseconds per layer. Even ideal removal of the observed B-extra wall plus the complete B graph would remain close to 1×, not 1.6×. This strongly suggests a single-request algorithmic amortization ceiling, but `structural_limit_proven` remains false while the large provider residual is unattributed.

The no-progress pause has therefore changed the next action from “optimize the selector” to “distinguish the residual.” Source order isolates a B-only interval between request-pin release and the first adjacent enqueue. The smallest valid comparator is trace-only subscopes inside `select_hot_slot_for_device_locked()` for:

- global directory/policy coherence validation plus owner candidate construction;
- mandatory policy admission/decision;
- physical-feasibility accounting.

No production semantics change is authorized by this review. Under adaptive-validation amendment `5226296660`, this comparator needs the focused build/tests and one fresh B trace only; A's retained path is unchanged and its compatible trace may be reused. Optimization resumes only if a subscope accounts for a dominant share. If the residual remains distributed/common host work, the next deliverable is a quantitative structural-limit return to design authority rather than a third speculative fix.
