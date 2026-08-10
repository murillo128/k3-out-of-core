# Issue 73 full-K3 topology screen

This packet records the mandatory T1/T2/T3/T4 topology screen and the
owner-requested T2B dedicated-expert control. Every endpoint is an unprofiled
P0 fresh process over the pinned full Kimi K3 artifact, the same 100-token
prompt and 24 generated tokens, `n_ubatch=4`, eight ordinary CUDA layers on
resident GPU 0, a 16-GiB managed cold tier, buffered positional reads, four
storage workers, queue depth 64, and deterministic argmax sampling.

## Explicit reserve-safe capacities

`OBSERVED`: resident/shared GPU 0 is `MAX_SAFE=549` whole-expert slots. Each
expert-only GPU is `MAX_SAFE=1125` slots. Selected full runs preserved a
1,080,033,280-byte floor on GPU 0 and 1,075,838,976 bytes on each remote GPU
against the 1,073,741,824-byte reserve. The one-slot-over candidates 550 and
1126 were rejected after an observed reserve breach. UUID/BDF identities and
the topology-specific reuse of these exact capacities are recorded in
[capacity-summary.json](capacity-summary.json).

## P0 endpoints

| Cell | Roles (`Resident={0}`) | Decode tok/s | TTFT s | p50 / p95 / p99 / max s | Hot hit rate | Logical storage / H2D TB | Host-staged GB | Guest block TB |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| T1 | `Expert={0}` | 0.185998 | 473.484 | 5.309 / 6.061 / 6.267 / 6.267 | 0.00% | 2.497 | 0.000 | 3.501 |
| T2B | `Expert={1}` | 0.204776 | 463.431 | 4.905 / 5.424 / 5.620 / 5.620 | 0.00% | 2.497 | 2.608 | 2.929 |
| T2 | `Expert={0,1}` | 0.227587 | 451.141 | 4.387 / 5.041 / 5.102 / 5.102 | 4.26% | 2.390 | 2.608 | 2.926 |
| T3 | `Expert={1,2,3}` | 0.233610 | 507.218 | 4.390 / 4.893 / 5.021 / 5.021 | 11.80% | 2.202 | 7.824 | 2.895 |
| T4 | `Expert={0,1,2,3}` | 0.244574 | 424.920 | 4.064 / 4.693 / 5.019 / 5.019 | 26.60% | 1.833 | 7.824 | 2.334 |

All five endpoints are exactly identical in prompt IDs, generated IDs, text,
and per-forward logit digests. Every endpoint has zero swap/OOM, zero storage
or lifecycle error, and bounded terminal queues, references, and cache pins.

## Selection

- `OBSERVED`: T2B/T1 is 1.1010. Keeping the resident GPU out of routed-expert
  execution helps even though the single 1125-slot expert pool has zero hits,
  but T2B does not beat T2.
- `OBSERVED`: T2/T1 is 1.2236 and T3/T2 is 1.0265. Added expert-device
  parallelism and aggregate hot capacity reduce the decode tail and storage,
  while T3's larger TTFT leaves its overall endpoint less decisive.
- `OBSERVED`: T4/T1 is 1.3149, T4/T3 is 1.0469, and T4/T2 is 1.0746. T4 has
  the best decode TPS, TTFT, median latency, hot-hit rate, and storage/H2D
  volume, so it is the selected topology for the storage/API shortlist.
- `OPEN`: these single-process topology cells select the next experiment but
  are not the final repeated `K3_BEST` acceptance distribution.

The portable endpoint source is [matrix-summary.json](matrix-summary.json),
and [raw-evidence-index.json](raw-evidence-index.json) binds the immutable
external capacity, workload, and resource evidence.
