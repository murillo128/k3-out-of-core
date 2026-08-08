# Phase 13 iteration 11 — bounded positional-read concurrency retained

Candidate: parent `560eef0e71f60cb4697863bf1430c53a6fe5543d`, nested `6f7f87822b7bfdbf5d271d367a5e3d1b730ef6ca`.

`OBSERVED`: the forced-positional transport now derives a bounded worker count from the expert-device count only for buffered positional reads. A uses one worker and B/B′ use two. Direct-I/O and `io_uring` retain the single-submitter path; storage layout, caches, routing, policies, H2D transport and canonical merge order are unchanged. Diagnostics expose the effective worker count. A deterministic native test proves that two deferred requests enter the read override concurrently, then complete and drain; it passed five repeated runs.

Native compilation used `-j76`. All 12 focused expert CTests pass sequentially after rebuilding every header-dependent target. The earlier two failures were stale test executables compiled against the prior aggregate layout; rebuilding them removed both failures without a source correction. Mode-C B preserves the accepted full identity SHA-256 `60658621b12340bc02d1fbb614142e4a17c5dd52eb529bfd4b0b2eb1a1255889`, all 24 logit digests and 1,032 routes with effective worker count 2.

`OBSERVED`: Mode-P Tier 1 B is `1.409646` tok/s, `+20.24%` over Iteration 9 B. The adjacent Tier-2 pair is A `1.399151`, B `1.367420` tok/s and `0.977322x`: A changes only `+1.24%`, while B improves `+16.64%`. Generated output remains exact. The required capacity comparator B′ is `1.232470` tok/s (`0.880870x` versus the adjacent A); it improves similarly but remains slower than B, so the candidate gain is not caused by doubled hot capacity.

The fresh exact Mode-P trace pair is valid: A SHA-256 `790a74a9e9f8ece55d38996a21fdb1b90e80a2b8d5c03adf18af142690e7f6db` (3,915,036 bytes, 58 cycles) and B `ef655e1b15c11ab7e5b75330dc4fbe93ccb2958377feddf6c938ecdb07d056a0` (3,980,057 bytes, 55 cycles). B storage service falls from `6.224936` to `4.627005 ms/layer` (`-25.67%`), provider wall from `15.817585` to `14.226300 ms/layer` (`-10.06%`), and B layer wall `-10.82%`. All declared retention thresholds are met.

The remaining B-minus-A wall is `0.878 ms/layer`. B provider is now `1.013 ms/layer` faster than A, but B graph wall remains `1.891 ms/layer` slower with zero simultaneous device-kernel overlap. Staging itself remains about `6.261 ms/layer` and changed only `-0.69%`; source shows the already-independent per-device transfer rings are staged serially after all reads finish.

Iteration 12 will stage already-reserved bundles concurrently across the two device rings, while preserving order within each ring and canonical graph merge order. Prediction: B stage service -25%, provider wall -8%, B Mode-P TPS +3%, A within 3%; revert on any performance, exactness, transfer-generation, cancellation, lifecycle or resource-bound failure. The 1.60x stop condition is not met and no structural stop or final campaign is authorized.
