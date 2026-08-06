# Phase 12.5 Checkpoint C — causal bottleneck report

`SUPPORTED_BOTTLENECK_ATTRIBUTION` with provider captures at `d4665e95f6bdb8ed2647c5228d7e311357411b0b` / `90347c79c8348ecf2199a419ee4112ea18238d66` and corrected control captures at `f589f6782009480257abbcf844a3c8db3499a221` / `d67525814502370a554ca851a0057bf4b8a735f8`.

`OBSERVED`: both selected positional repeats are exact and pass the active-trace gate. Traced throughput is 0.1587 and 0.1595 token/s. Non-overlapping critical-path accounting assigns 63.218% and 63.448% to storage intervals, 15.864-16.064% to scheduler intervals, and 18.741-18.769% to provider residual. Storage queue-wait p95 is 952-957 ms while operation-service-wall p95 is 3.62-3.67 ms. GPU busy time is about 2.94% and storage/GPU overlap about 1.9%.

`OBSERVED`: the buffered native-io_uring case uses zero synchronous fallback but falls to 0.1007 token/s. Storage service p95 becomes 821 ms, scheduler plus storage consume 83.46% of token wall, and storage/GPU overlap is effectively zero. The 64 GiB cold case reduces misses from 5,963 to 4,427 and improves traced throughput to 0.1948 token/s, but peak RSS rises to 67,913,992 KiB and storage remains 55.61% of token wall. Fit and CPU-MoE controls have zero provider-storage rows and p95 critical-path rows of 0.637 s and 0.888 s. Both adjacent controls preserve exact generated text, all 24 token IDs, and all 24 whole-logit identities with zero non-finite logits.

`INFERENCE`: storage-request queue lifetime is the dominant selected-path bottleneck; provider/scheduler serialization and insufficient overlap are second. GPU execution, synchronization, CPU scheduling, and physical read-service time do not explain the provider slowdown on this host. Buffered io_uring is slower here. Larger cold capacity helps but does not remove the bottleneck shape and is not a justified default.

`BLOCKED`: none. All six selected raws are below 2 GiB, all compressed forms are below 1 GiB, total raw plus compressed size is 6,209,341,375 bytes, and every required loss/drop/error counter is zero. Five attempts rejected by the fixed 1 ms clock gate and two superseded incomplete-identity controls are listed in `excluded-attempts.json`.

This evidence comes from virtio/ext4 with a warm page cache and does not weaken or replace Phase 12 physical-NVMe, cold-state, direct-I/O, full-size, statistical, or storage-layout gates.
