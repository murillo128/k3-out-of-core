# Phase 13.6P system-memory autofit and final CPU D3

Status: `K3_CPU_BEST_FROZEN_FINAL_D3_PASS_CHECKPOINT_C_REQUIRED`

This result supersedes the earlier D3P conclusion that no acceptable CPU
candidate existed. That earlier conclusion remains valid for the fixed 96-GiB
stress configuration. The superseding issue-89 design amendment changed the
production decision capacity to the shared `expert_cold_cache_bytes = 0`
system-memory autofit; it did not invalidate Checkpoint B or the 64/96-GiB
residency attribution.

## Exact target

- accepted Checkpoint-B floor: parent
  `4589bcb07ae81e32aff70f06008618a8b2ac9b4f`, nested
  `7930fdcf428ed1221ff070a14e62e2073cc1a803`, safety `YES`;
- technical parent: `5f88dfc5b32ddc540d0ec98e88dd66ae12f1ee99`;
- preregistration parent: `10a1cac5f6e0ba18a5c33fa68fa3fcbf4739251f`;
- nested runtime: `a702c36b4ec50db5b5f653d5177eb4d732eeaaa9`;
- nested base: `588d1b4b1ea32e10f99b0c3ce8506b703c3f3c93`;
- production binary SHA-256:
  `000315703c3c3e24d589a48661746bd1c17e4d557e770fbb952b335ed5aec786`.

The build is CPU-only GCC 11.5 Release `-O3 -DNDEBUG`, `GGML_NATIVE=ON`,
resolved `-march=native`, shared GGML, OpenMP, LTO off, 32 inference threads,
one exposed NUMA node, native `io_uring`, and `O_DIRECT`. All endpoint cells use
unprofiled Mode-P with exact routing and natural empty-cache fill. See
[build-fingerprint.json](build-fingerprint.json).

## Shared autofit result

The accepted common budget resolver selected `45,535,150,080` bytes, or 2,595
whole experts, in every one of the six final fresh processes. Its decomposition
was:

| Budget component | Bytes |
| --- | ---: |
| effective host/cgroup limit | 202,049,667,072 |
| measured non-pool commitment | 118,028,777,856 |
| limiting headroom | 84,020,889,216 |
| system reserve | 20,204,966,708 |
| runtime reserve | 17,179,869,184 |
| formula safe pool | 46,623,080,448 |
| admission hysteresis | 1,073,741,824 |
| selected admission-safe pool | 45,535,150,080 |

The measured non-pool term includes the complete 114,694,715,776-byte model
file-mapping obligation rather than treating evictable current RSS as sufficient
headroom. The arena is fixed after first-context resolution; each later request
samples the same bounded pressure authority without resizing or changing cache
policy.

The fresh eight-forward BATCHED screening cell reached `0.221595055 tok/s`,
p50/p95 `4.264/5.945 s`, and first-full `14.257 s`. Peak RSS was
155,711,976 KiB and minimum MemAvailable was 148,251,540 KiB. It recorded zero
major faults, file refaults, scans, steals, swap, OOM, pressure rejection, or
fallback. Fill plus decode moved 168,047,307,616 exact aligned expert bytes
versus 168,047,325,696 physical NVMe bytes, an 18,080-byte residual.

## Final D3 result

Three interleaved fresh-process pairs ran in preregistered order
`SERIAL/BATCHED`, `BATCHED/SERIAL`, `SERIAL/BATCHED`, with 64 measured forwards
per cell.

| Pair | SERIAL tok/s | BATCHED tok/s | B/S ratio |
| ---: | ---: | ---: | ---: |
| 1 | 0.196515 | 0.238142 | 1.21183 |
| 2 | 0.197338 | 0.236939 | 1.20068 |
| 3 | 0.195381 | 0.231850 | 1.18666 |

The BATCHED median is `0.236939065 tok/s`; the minimum is `0.231850342 tok/s`.
Both satisfy the `>=0.20 tok/s` hard gate. The paired TPS geometric mean is
`1.199678` with 95% CI `[1.168734, 1.231440]`, so D3 classifies
`CPU_PERF_POSITIVE`. The p95 latency ratio is `0.831955` with 95% CI
`[0.790948, 0.875089]`. Peak-RSS ratio is `1.000004` with 95% CI
`[0.999989, 1.000018]`.

All six cells had exactly 46,081 measured hits, 48,127 misses/loads, and
844,497,174,528 logical backing bytes; identical token hash
`1cbef80136d5b71e`; identical final cache digest `9c47e06dd4e64b4d`;
zero terminal references; and zero buffered/synchronous fallback. SERIAL issue
depth was exactly one request/three operations with SQ/CQ 3/3. BATCHED reached
16 requests/48 operations and SQ/CQ 16/15 or 16/16, proving real kernel issue
width.

Across the campaign, maximum peak RSS was 155,714,472 KiB and minimum sampled
MemAvailable was 147,916,672 KiB. No cell scanned or stole pages, swapped,
rejected pressure, or observed an OOM. Five cells had only 21,408 bytes of
physical-read residual. The first SERIAL cell had a bounded 6.44-MB residual,
25 host/process major faults and 1,468 file-refault pages, still with zero scan
or steal; it did not create sustained pressure or affect exact work.

## Final local conformance

The unchanged target passed all 10 locally applicable focused Release tests:
weight provider, hot/cold caches, storage, async I/O, scheduler, transfer ring,
UMA logic, resident demand, and cache-aware-routing regression. Post-freeze
stress passed resident demand 20/20 and async I/O 20/20. The exact max-native
512-MiB SERIAL/BATCHED smoke also passed output/work/cache/bytes/resource
equality and 1/3 versus 16/48 concurrency.

ASan+UBSan and TSan configurations were retried at the exact nested source.
They remain toolchain-blocked at link time because
`/usr/lib64/libasan.so.6.0.0` and `/usr/lib64/libtsan.so.0.0.0` are absent.
The focused repeated Release stress above is the nearest valid substitute; no
sanitizer pass is claimed.

Physical coherent-UMA qualification is `DEFERRED -> #92`. Discrete-CUDA
qualification is `DEFERRED -> #93`. These accepted validation splits do not
block issue #89 or the CPU substrate. PRs #91 and #7 remain draft pending the
required final-capable Checkpoint C review.

The machine-readable result is [manifest.json](manifest.json). Large and raw
host captures remain under `/mnt/nvme1/issue89`; their immutable paths, sizes,
and SHA-256 values are in [raw-evidence-index.json](raw-evidence-index.json).
