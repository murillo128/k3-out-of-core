# Issue #56 — integrity-remediation decision

Disposition: `SELECT_DEFAULT_OFF`.

The production default now uses explicit integrity mode `none`. It performs zero transport or provider digest traversals and reports `not_checked`; the full canonical two-sided FNV path remains available unchanged as the explicit `fnv64_end_to_end` development/evidence mode.

Five interleaved fresh-process pairs confirmed 0.158918 to 0.396604 token/s, a paired improvement of 149.67% (95% CI 142.40% to 156.95%). Mean p95 fell from 7.474 s to 2.809 s, 62.25% lower (95% CI 58.95% to 65.54%). The pooled 115-token distributions were 6.106/8.466/8.540/8.606 s versus 2.477/2.819/3.189/3.195 s for p50/p95/p99/max.

The strict traces measure 111.929 s in the transport FNV pass and 113.292 s in the provider FNV pass, each over 5,963 complete logical bundles and 65,788,575,744 bytes. The hash-disabled trace has zero digest scopes, requests, bytes, comparisons, or successful-check reports and 5,963 explicit `not_checked` completions.

Transport hashing was the queue-lifetime cause: post-queue service fell from 132.315 s to 23.626 s and aggregate queue wait from 1,370.402 s to 224.433 s, while actual operation/`pread64` service moved from 20.119 s to 23.353 s. Provider hashing was a second 113.292 s CPU traversal, but it overlapped storage progress and cannot be added naively to end-to-end wall savings. Non-overlapping provider residual remains about 39.9 s and is now the largest critical-path share (46.45%), followed by storage (27.92%), scheduler/admission (15.16%), and transfer (9.61%).

Four positional workers with integrity disabled were tested separately. They reduced queue accounting by about 73%, but improved throughput by only 1.74% and 0.41% and p95 by only 1.44% and 2.47% in the reciprocal pairs, while staging and CPU cost rose. The worker code was fully reverted. Transfer batching is not justified because transfer-lane and H2D concurrency remain one and no evidence shows two host-ready demanded bundles entering one wave.

All five pairs and both traces preserve the exact generated IDs, text, finite-logit identities, and 1,032 route records under identity SHA-256 `6d2d3c5cb735d4df1bb1f67993bcf745ab49a5a57ff1096c4a61c9a70f2e3dac`. Short reads, I/O errors, stale completions, cancellation faults, cleanup faults, trace loss, swap, cgroup pressure, and OOM are zero. RSS, the 67,173,120-byte pinned ring, 4,286,284,800-byte hot expert pool, CUDA allocation inputs, model bytes, routing, arithmetic, cache/policy settings, and Phase 9/10 defaults are unchanged.

The immutable release contains only the accepted FNV baseline trace, accepted integrity-disabled trace, their compressed forms, and the evidence index. Prior v1/v2 assets are referenced rather than duplicated. Exact hashes, commands, trace attribution, tests, risk notes, and the rejected worker screen are recorded in `evidence-index.json`.
