# Issue 69 Delta D2c: constrained-memory physical-SSD qualification

Delta D passes in the K3-representative regime with the Checkpoint C runtime
unchanged. The selected path remains buffered positional `pread` with four I/O
workers, direct promotion to pinned memory/H2D, and `cold_admissions = 0`.

The earlier abundant-RAM Delta D result remains valid only for its published
~297 GiB host regime. D2c adds whole-workload 64 and 96 GiB cgroup limits,
including file/page-cache and anonymous memory, with swap fixed at zero. Every
completed constrained run reported zero swap, zero OOM events, exact generated
output, and zero terminal resource state.

## Exact inputs and bounds

- Accepted Checkpoint C parent: `5faf1a32df3ef0516a821af79ae50977e73ff9b9`
- Selected runtime/nested revision: `968e138cab375cbca4406b82b756671eb103ea3e`
- D2c evidence harness revision: `26caaab5b4d1a1d19e0409679858b932ac0fed1e`
- Model backing store: existing persistent ext4 filesystem on `/dev/vda1`
- Model/workload/output identity: unchanged DeepSeek-V4-Flash `UD-Q2_K_XL`
- CUDA graphs disabled, prefetch off, queue/ring and workload dimensions unchanged
- Explicit workers screened only at the authorized `1, 2, 4, 8` values

The existing verified SSD shards were reused. Nothing was downloaded and no
model artifact is present in the evidence archive.

## Buffered-I/O memory points

The 64/96 GiB points stayed below the ~97 GB model working set. Normal buffered
S0 peaked at 58.08/58.11 GB cgroup memory and retained about 40.33/40.37 GB of
file cache. The 16 GiB explicit fill used the same total cap; it did not receive
extra effective RAM outside the cgroup.

| Total cap / S0 screen | Decode tok/s | TPS vs normal | Process/block physical result | Logical result |
| --- | ---: | ---: | --- | --- |
| 64 GiB normal buffered | 1.439626 | reference | reference | 5,920 reads / 48.225 GB |
| 64 GiB `FADV_RANDOM` | 1.419059 | -1.43% | +0.03% / +0.22% reads | unchanged |
| 64 GiB buffered + 16 GiB fill | 1.324328 | -8.01% | +0.10% / +0.29% reads | -15.73% requests |
| 96 GiB normal buffered | 1.433495 | reference | reference | 5,920 reads / 48.225 GB |
| 96 GiB `FADV_RANDOM` | 1.458893 | +1.77% | +0.01% / +0.21% reads | unchanged |
| 96 GiB buffered + 16 GiB fill | 1.330400 | -7.19% | -0.10% / -0.09% reads | -15.54% requests |

The isolated `FADV_RANDOM` speed change reversed sign across memory points and
never reduced physical reads, so normal buffered I/O remained selected. The
writable device `read_ahead_kb=0` control was stopped after 11m31s without a
completed S0 result—12.30× the normal whole-run elapsed—and the original 128 KiB
setting was verified restored. It was not promoted to S1/A1.

## `O_DIRECT` architecture control and cold-fill decision

The ext4 `O_DIRECT` path required independent aligned staging per positional
worker; a first shared-staging experiment produced non-finite output and was
rejected. The corrected bounded comparator screened workers 2/4/8 and selected
eight for the control. This code is experimental evidence only and is absent
from the selected nested tree.

Three fresh interleaved 64 GiB pairs produced:

| Topology | Buffered direct promotion | O_DIRECT direct | O_DIRECT + 16 GiB fill | Fill vs O_DIRECT block reads | Fill vs buffered block reads |
| --- | ---: | ---: | ---: | ---: | ---: |
| S0 | 1.438873 | 1.304225 | 1.370875 | -9.16% | +24.35% |
| S1 | 1.668426 | — | 1.664238 | — | +12.79% |
| A1 | 1.554110 | 1.594087 | 1.682585 | -7.14% | -0.73% |

The control answers the architecture question: without Linux page cache, 16 GiB
async fill converted reuse into real physical-read savings and improved the
matched O_DIRECT path by 5.11% (S0) and 5.55% (A1). It still failed the selected
end-to-end gate. Against the best buffered path, S0 regressed 4.73% while reading
24.35% more physical bytes; S1 was flat (-0.25%) while reading 12.79% more; A1
improved 8.27% but physical reads were effectively flat (-0.73%).

The authorized 32 GiB S0 follow-up showed no capacity curve: block reads were
0.35% higher than 16 GiB, cold hits fell 3.32%, and decode regressed 28.10%.
Therefore 64 GiB was not tested. The exact experimental source patch is retained
in raw evidence, but its final focused suite also exposed a segfault in
`test-expert-miss-policy` and an invalid free in `test-hot-expert-cache`. Because
the candidate already failed selection, it was not hardened; it was removed in
full. The restored selected runtime passes the exact focused suite 12/12.

## Final D3 attribution

The final selected S0/A1 captures ran inside the 64 GiB whole-cgroup limit. The
Perfetto/CUPTI service unions overlap and must not be added.

| 1 s window | Provider p50/p95/p99 | Storage union | H2D union / storage overlap | Exposed storage | Useful kernels | GPU idle | Remaining host gap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| S0 | 17.198/19.922/20.494 ms | 577.444 ms | 172.487/110.810 ms | 466.634 ms | GPU0 30.862 ms | GPU0 970.054 ms | 120.442 ms |
| A1 | 13.934/19.757/20.634 ms | 553.091 ms | 99.305/44.015 ms | 509.076 ms | GPU0/GPU1 33.917/7.186 ms | GPU0/GPU1 966.977/993.708 ms | 184.762 ms |

The bounded full-stack scheduler trace reports no actual data loss. Inference
wake-to-run p95 was 75.8 µs (S0) and 44.1 µs (A1); storage-worker p95 was 74.2
and 49.6 µs. Main-thread runnable time was 3.03%/1.94% of the window. Seven S0
main-thread migrations were observed, but tails remained sub-millisecond;
scheduler latency was not material and no affinity comparator was authorized.

Decode-relevant 99 Hz DWARF profiles attribute 97.91% (S0) and 98.39% (A1) of
storage-worker samples to file-read/page-cache service. Whole-process file-read
attribution is 52.83% and 32.95%. The dominant remaining critical path is exposed
physical-SSD/ext4/page-cache service, not scheduler wake/run latency. The
manifest records perf-stat counters, trace verification, output/resource state,
and all machine-readable comparisons.

## Raw evidence and exit

- Manifest: `manifest.json`
- Release: <https://github.com/murillo128/k3-out-of-core/releases/tag/issue69-delta-d2c-final-physical-ssd-v1>
- Asset: `issue69-delta-d2c-final-v1.tar.zst`
- Size: `13817221` bytes
- SHA-256: `3763616c6bc65a7d57ce9585058262b52c1484ae3abfee42e753b3a663b2f807`

The archive contains 208 checksum-addressed regular files: bounded screens,
24-run confirmation, failed and passing focused-suite logs, experimental source
patch, selected Perfetto/CUPTI and scheduler traces, raw perf data, and analyzer
inputs. An independent extraction replay passed all embedded SHA-256 checks.

The #44 handoff must distinguish abundant-RAM buffered caching from this fixed
out-of-core total-memory regime, and must run native `io_uring` as an E2E
comparator on the next capable discrete-CUDA host. PRs #70 and nested #3 remain
draft/unmerged pending a fresh exact-target independent review.
