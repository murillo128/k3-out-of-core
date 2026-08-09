# Issue 69 Delta D1 — unchanged runtime on local persistent SSD

Status: **PASS** for the bounded D1 characterization. The accepted Checkpoint-C
runtime is unchanged at nested `968e138cab375cbca4406b82b756671eb103ea3e`.
The exact accepted DeepSeek-V4-Flash UD-Q2_K_XL shards were copied from tmpfs to
the host's persistent ext4 model directory on `/dev/vda1`; every authoritative
process used positional `pread` and started after `sync` plus Linux
`drop_caches=3`.

## Worker screen and confirmation

The one-process screen covered only explicit worker counts 1, 2, 4, and 8 for
S0, S1, and A1. Four workers was the smallest useful count: versus two workers,
screen decode improved by 14.5% (S0), 13.8% (S1), and 8.9% (A1). Eight workers
was flat for S0, improved S1 by only 1.7%, and regressed A1 by 2.0%.

Three fresh interleaved worker-4 confirmations produced:

| cell | decode geometric mean | logical backing bytes | process physical reads | block physical reads |
|---|---:|---:|---:|---:|
| S0 | 1.435 tok/s | 44.91 GiB | 37.55 GiB | 37.63 GiB |
| S1 | 1.678 tok/s | 41.67 GiB | 37.72 GiB | 37.78 GiB |
| A1 | 1.612 tok/s | 33.78 GiB | 37.71 GiB | 37.80 GiB |

Relative to the accepted tmpfs result, physical-SSD decode retained 76.8% (S0),
70.3% (S1), and 67.3% (A1). A1 retained only 96.1% of S1 throughput despite
issuing 18.9% fewer logical backing bytes, so its logical-byte advantage did not
become a physical-SSD throughput advantage.

All cells preserved the exact generated-output identity, used their requested
effective worker count, recorded zero cold hits/admissions and zero resident cold
keys, and closed with every bounded resource counter at zero.

## Bounded Perfetto/CUPTI attribution

| cell | provider p50 / p95 | storage active | exposed storage without H2D/kernel | storage→H2D overlap | useful kernels |
|---|---:|---:|---:|---:|---:|
| S0 | 16.64 / 20.09 ms | 577.85 ms | 460.23 ms | 117.62 ms | GPU0 31.66 ms |
| A1 | 13.52 / 18.45 ms | 574.02 ms | 528.95 ms | 45.07 ms | GPU0 35.66 ms; GPU1 7.67 ms |

Each row covers an approximately 1.001-second bounded decode window. Transfer
staging remained absent. The trace therefore records an **OBSERVED** material
storage-facing opportunity for D2, while leaving open whether an explicit host
cold copy can beat the kernel page cache plus direct `pread` path end to end.

## Evidence

- [Machine-readable manifest](manifest.json)
- Immutable raw release: `issue69-delta-d1-physical-ssd-v1`
- Raw asset size: 7,460,644 bytes
- Raw asset SHA-256: `8ec3a16697c811b1a323e7f41725e5c7d8348754a130c44149a928cb67974be4`

The next bounded action is D2: quantify reusable repeated demands and the
16/32/64-GiB no-admission controls before deciding whether the authorized
asynchronous opportunistic cold fill warrants implementation.
