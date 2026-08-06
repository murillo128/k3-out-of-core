# Phase 12.5 Perfetto evidence

## Checkpoint A — instrumentation and no-op correctness

`OBSERVED`: the optional `LLAMA_PERFETTO` build path is default-OFF. The OFF library has no Perfetto/CUPTI dependency or dynamic symbol, while the ON build links the selected CUDA toolkit CUPTI and the vendored official Perfetto v50.1 SDK.

The focused provider, cache, scheduler, storage, async-I/O, transfer, UMA configuration/no-work, and tracing tests pass in both builds (12/12). The host cannot initialize the Phase 11 UMA runtime arena on its discrete RTX 3090; the dedicated runtime test rejects identically in both builds and no UMA emulation was introduced.

With the ON build and no active external session, trace macros do not evaluate arguments, no tracing owner is initialized, no trace file is created, and all CUPTI activity/drop counters remain zero.

The direct event schema covers the selected route keys; hot lookup, hit, miss, victim, eviction, admission, pin, and unpin transitions; cold victim, eviction, and admission transitions; hot-cache, cold-cache, and transfer-lane occupancy counters; and graph reserve. Hot transitions cover demand, background join/promotion, predictive, cold-backed, trim, surrender, teardown, and failure-cleanup paths. Cold transitions cover replacement, retire, trim, and surrender. Occupancy is derived from actual ready/pinned slot state rather than admissions-minus-evictions bookkeeping. A focused Phase 10 run exercised two predictive admissions, one useful prediction, and one wasted/removal path without a runtime failure.

A 16 MiB system trace probe retained 8,388,600 bytes of record capacity and peaked at 9,437,176 bytes across that capacity plus all active CUPTI buffers, with 250 records and no errors, drops, unknown timestamps, or unmatched correlations. The same allocation accounting enforces the configured 256 MiB hard maximum.

The exact-target 20-run tiny-fixture ABBA comparison produced the same exact generated/logit/route identity. Paired median shift was -0.48% TTFT, -2.05% decode latency, and +2.11% decode throughput; no metric regressed beyond the 1% gate. The adjacent one-token DeepSeek provider confirmation was exact and the ON/no-session latency improved 0.74%, passing the 2% gate.

Machine-readable evidence is in `checkpoint-a.json`. Checkpoint B full-stack traces and SQL verification are intentionally not claimed by this checkpoint.
