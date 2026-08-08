# Phase 13 multi-GPU result

Disposition: `BLOCKED_PERFETTO_CUPTI`. The multi-GPU mechanism, exact-output campaign and resource/lifetime checks pass, but issue #61 cannot be closed as `SUPPORTED_MULTI_GPU` because no qualifying adjacent A/B Perfetto/CUPTI trace pair could be captured within the accepted bounded trace configuration.

The mandatory five fresh-process A/B pairs preserve the same output, routes and logits across all ten processes. The single-GPU control reaches 0.376838 decode tok/s; the two-GPU `HOST_STAGED` cell reaches 0.249522 tok/s. Speedup is 0.662145, efficiency is 0.331073, and the paired bootstrap 95% interval is [0.657935, 0.667040], so the classification is `SCALING_NEGATIVE`.

The two-GPU cell raises aggregate hot-hit rate by 5.6828 percentage points, which triggers the capacity-matched comparator. Three A/B-prime pairs with 134 slots per GPU reach 0.513077x speedup, with a 95% interval of [0.509905, 0.515217]. This excludes doubled hot capacity as the cause of the negative result. The observed overhead is consistent with fine-grained host-staged activation/result traffic, synchronization and device-local physical-feasibility constraints dominating the small routed compute subsets: B transfers 961,658,880 host-staged bytes and records 215,970 feasibility skips across five processes while P2P is unavailable in both directions.

The fixture transport is explicitly `POSITIONAL` in every cell (`FIXTURE_TRANSPORT_HELD_CONSTANT`). The backing path is the same `tmpfs` mount for all cells; Phase 13 has no NVMe requirement. Storage bytes/operations are identical within each A/B matrix and all I/O error, short-read, stale-completion, cancellation and cleanup counters remain zero.

Final validation at nested commit `bf877f8bde80850b64403096ce043940d2f7f567` rebuilds the focused targets with 76 parallel jobs, passes 11/11 focused CTests, passes a real two-GPU `compute-sanitizer` run with zero errors/leaks, and rejects a request for three devices when only two exist. The one independent final-capable review was not started because issue #61 requires the selected trace to be frozen first.
