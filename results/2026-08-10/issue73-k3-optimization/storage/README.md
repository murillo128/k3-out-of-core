# Issue 73 full-K3 storage/cache screen

This packet records the mandatory A/B0/B1/C0/C1 screen and one bounded
lower-concurrency check on the selected T4 topology. Every accepted endpoint
is an unprofiled P0 fresh process over the pinned full Kimi K3 artifact, the
same 100-token prompt and 24 generated tokens, `n_ubatch=4`, eight ordinary
CUDA layers on resident GPU 0, explicit hot capacities
`0:549,1:1125,2:1125,3:1125`, a 16-GiB managed cold tier, and queue depth 64.

## P0 endpoints

| Cell | Storage/cache path | Workers | Decode tok/s | TTFT s | p50 / p95 / p99 / max s | Cold hits | Peak RSS GiB |
|---|---|---:|---:|---:|---:|---:|---:|
| A | buffered positional, async fill off | 4 | 0.244574 | 424.920 | 4.064 / 4.693 / 5.019 / 5.019 | 0 | 106.66 |
| B0 | O_DIRECT positional, async fill off | 4 | 0.294132 | 300.214 | 3.399 / 3.870 / 4.036 / 4.036 | 0 | 106.69 |
| B1 | O_DIRECT positional, async fill on | 4 | 0.293976 | 311.118 | 3.347 / 3.830 / 4.049 / 4.049 | 121 | 122.69 |
| C0 | O_DIRECT native io_uring, async fill off | 1 fallback worker | 0.193436 | 545.118 | 5.074 / 6.193 / 6.365 / 6.365 | 0 | 106.70 |
| C1 | O_DIRECT native io_uring, async fill on | 1 fallback worker | 0.189489 | 546.156 | 5.232 / 6.130 / 6.362 / 6.362 | 0 | 122.70 |
| B0W2 | O_DIRECT positional, async fill off | 2 | 0.251272 | 377.451 | 3.942 / 4.748 / 4.761 / 4.761 | 0 | 106.68 |

All six endpoints are exactly identical in prompt IDs, generated IDs, text,
and all 24 per-forward logit digests. Every direct endpoint opened all 33
sources with O_DIRECT and recorded zero buffered fallback, unsupported source,
short read, native error, I/O error, stale completion, swap, OOM, or lifecycle
fault. The 48 `direct_eof_short_reads` in each direct endpoint are valid Linux
O_DIRECT completions ending exactly at unaligned shard EOF, not storage short
reads; their aggregate aligned shortfall is 9,728 bytes.

## Selection

- `OBSERVED`: B0/A is 1.2026. O_DIRECT positional reduced total request queue
  wait from 3,975.2 to 2,236.5 seconds, improved TTFT by 124.7 seconds, and
  improved every decode tail while preserving the same logical reads and H2D.
- `OBSERVED`: B1/B0 is 0.9995. Its 12,110 completed background fills produced
  only 121 cold hits, did not reduce H2D, increased peak RSS by about 16 GiB,
  and worsened TTFT by 10.9 seconds. Async cold fill remains off.
- `OBSERVED`: C0 issued and completed all 313,320 reads through native
  io_uring with 33 registered files, a registered buffer, and zero synchronous
  fallback. Peak SQ/CQ occupancy was one and total request queue wait was
  6,803.0 seconds; C0/B0 is 0.6577. C1 produced zero cold hits from 16,575
  completed fills and C1/B0 is 0.6442.
- `OBSERVED`: reducing the winning positional path from four workers to two
  increased total request queue wait from 2,236.5 to 3,800.1 seconds and
  reduced decode TPS by 14.57%. Four workers at queue depth 64 remains the
  selected bounded concurrency point.
- `OPEN`: these single-process cells select the next host-cache experiment but
  are not the final repeated `K3_BEST` acceptance distribution.

The portable analyzer output is [matrix-summary.json](matrix-summary.json),
[selection-summary.json](selection-summary.json) records the decision fields,
and [raw-evidence-index.json](raw-evidence-index.json) binds the immutable
external evidence, including the three rejected pre-inference diagnostics.
