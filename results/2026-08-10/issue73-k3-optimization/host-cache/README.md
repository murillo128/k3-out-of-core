# Issue 73 full-K3 host-cache screen

This packet records the bounded managed-cold cache screen on the selected B0
storage path and T4 topology. Every endpoint is an unprofiled P0 fresh process
over the pinned full Kimi K3 artifact, the same 100-token prompt and 24
generated tokens, `n_ubatch=4`, eight ordinary CUDA layers on resident GPU 0,
explicit hot capacities `0:549,1:1125,2:1125,3:1125`, four positional
O_DIRECT workers, queue depth 64, and the production promote-and-GPU miss
path. B0 is the no-fill storage control; H64, H96, and HMAX7391 enable
asynchronous cold fill at progressively larger bounded capacities.

## P0 endpoints

| Cell | Managed cold | Slots | Decode tok/s | TTFT s | p50 / p95 / p99 / max s | Cold hits | Logical expert reads TB | Guest block reads TB | Peak RSS GiB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B0 | 16.0 GiB, fill off | 979 | 0.294132 | 300.214 | 3.399 / 3.870 / 4.036 / 4.036 | 0 | 1.833 | 1.833 | 106.69 |
| H64 | 64.0 GiB | 3,916 | 0.294755 | 309.133 | 3.361 / 3.930 / 4.143 / 4.143 | 13,613 | 1.594 | 1.594 | 170.68 |
| H96 | 96.0 GiB | 5,874 | 0.024641 | 1,043.765 | 40.223 / 43.096 / 43.757 / 43.757 | 17,778 | 1.521 | 2.975 | 185.75 |
| HMAX7391 | 120.785 GiB | 7,391 | 0.018839 | 1,106.755 | 53.075 / 56.048 / 60.867 / 60.867 | 17,715 | 1.522 | 4.301 | 185.78 |

All four endpoints are exactly identical in prompt IDs, generated IDs, text,
and all 24 per-forward logit digests. Every endpoint opened all 33 sources with
O_DIRECT and recorded zero buffered fallback, unsupported source, storage
short read, native error, I/O error, stale completion, swap, OOM, or lifecycle
fault. Logical H2D remained 1,832,636,252,160 bytes in every endpoint; managed
cold hits reduce disk reads but do not change expert routing or accumulation.

## Capacity boundary and host accounting

The declared OS/runtime reserve is 64 GiB, or 67,108,864 KiB of host
`MemAvailable`. H96 reached a measured 93,121,208 KiB minimum. With the exact
17,136-KiB cache-slot stride, that observation predicts 7,391 slots as the
largest capacity preserving the reserve. A fresh full HMAX7391 process measured
67,113,844 KiB, only 4,980 KiB above the reserve. Slot 7,392 would consume a
further 17,136 KiB and therefore predicts 67,096,708 KiB, 12,156 KiB below the
reserve. It is rejected analytically and was not run.

At HMAX7391, the managed cold allocation was 129,691,828,224 bytes (120.785
GiB). Runtime-declared pinned or registered staging was 625,541,120 bytes
(four 122,830,848-byte device rings plus 134,217,728 bytes of peer staging).
The sampler observed maxima of 131,985,121,280 bytes cgroup anonymous memory,
113,757,880,320 bytes cgroup file memory, 113,248,272,384 bytes mapped file
memory, and 194,807,688 KiB process high-water RSS. The minimum free-VRAM
floors remained 1,030/1,026/1,026/1,026 MiB. Swap, cgroup memory high/max/OOM
events, `VmPin`, and host `Mlocked` were all zero; the runtime counters above
are the authoritative accounting for CUDA-registered staging.

## Selection

- `OBSERVED`: H64 reduced logical expert storage by 13.03% but H64/B0 decode
  TPS was only 1.0021, within a single-run noise band, while TTFT worsened by
  8.9 seconds and peak RSS increased by about 64 GiB.
- `OBSERVED`: H96 and HMAX7391 reduced logical expert storage by 17.02% and
  16.96%, but physical guest block reads rose to 1.96x and 2.83x their logical
  expert reads. They incurred 15,779,721 and 9,746,762 major faults and fell to
  8.38% and 6.40% of B0 decode TPS. The larger managed cache displaces the
  ordinary mapped-weight working set and amplifies refault I/O.
- `OBSERVED`: 7,391 slots is the exact measured MAX_SAFE capacity for the
  declared 64-GiB reserve on this host; capacity safety does not imply useful
  performance.
- `ACCEPTED`: retain B0 with asynchronous cold fill off as the host-cache
  selection. It is genuinely out-of-core, preserves the baseline execution
  path, and avoids the measured page-cache collision.
- `OPEN`: B0 remains the current selection for profiling and bounded causal
  optimization. Final `K3_BEST` requires repeated fresh-process acceptance.

The portable analyzer output is [matrix-summary.json](matrix-summary.json),
[selection-summary.json](selection-summary.json) records the decision and
capacity fields, and [raw-evidence-index.json](raw-evidence-index.json) binds
the immutable external evidence.
