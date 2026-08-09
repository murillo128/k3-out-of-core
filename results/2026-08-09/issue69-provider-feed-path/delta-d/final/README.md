# Issue 69 Delta D: physical local-SSD validation

Delta D passes with the Checkpoint C runtime unchanged. The selected path remains
direct SSD-to-pinned promotion followed by H2D, with `cold_admissions = 0`.
The bounded 16 GiB async cold-fill candidate reduced logical provider reads but
did not materially reduce physical reads and regressed end-to-end decode, so it
was rejected and fully removed before final validation.

## Exact inputs

- Accepted Checkpoint C parent: `5faf1a32df3ef0516a821af79ae50977e73ff9b9`
- Runtime/nested revision: `968e138cab375cbca4406b82b756671eb103ea3e`
- Delta D evidence harness revision: `200a4c6b7a4d7001a7ec070ea48e144cee881aaa`
- Model backing store: the existing persistent ext4 filesystem on `/dev/vda1`
- Transport: positional `pread`, four explicit I/O workers
- Workload/output identity: unchanged from the accepted issue 69 workload

The already verified DeepSeek-V4-Flash UD-Q2_K_XL shards were reused; no model
was downloaded. The D1 worker screen covered only 1, 2, 4, and 8 workers and
selected four. Fresh/interleaved physical-SSD confirmation geometric means were
1.434519 tok/s (S0), 1.678145 tok/s (S1), and 1.611892 tok/s (A1). The complete
D1 result was published before any candidate runtime work.

## D2 decision

The exact D1 request sequences contain real reuse. At a 16 GiB exact-LRU upper
bound, S0 could avoid 1,701 reads/12.931 GiB, S1 1,230/9.364 GiB, and A1
749/5.680 GiB. A 32 GiB bound captures almost all repeated requests. However,
unchanged direct-promotion controls at 32 and 64 GiB recorded zero cold hits,
admissions, and resident keys, proving those configured budgets are inert in the
selected no-admission path.

That opportunity justified one bounded opt-in async-fill implementation at
16 GiB. Its fill ran best-effort from the demand pinned lane, never made H2D wait,
allowed demand to bypass a joined fill, bounded active fill state, and drained on
failure/cancellation/teardown. An initial lifecycle failure was corrected and
covered before measurement. The final candidate and its tests are preserved as
a source patch in raw evidence; all candidate code was then reverted.

| Topology | Direct tok/s | Async-fill tok/s | Logical bytes reduced | Block bytes reduced | Process physical bytes reduced |
| --- | ---: | ---: | ---: | ---: | ---: |
| S0 | 1.422956 | 1.314540 | 15.690% | 0.036% | 0.041% |
| A1 | 1.583237 | 1.502032 | 10.579% | 0.049% | 0.048% |

Generated and numerical output hashes matched within each comparison, and every
terminal resource counter was zero. Because physical reads were effectively flat
while decode regressed 7.6% on S0 and 5.1% on A1, the Delta D selection gate
failed. Candidate 32/64 GiB runs were therefore not authorized or performed.

## D3 critical-path evidence

The selected S0 and A1 traces are valid Perfetto/CUPTI captures from OS-cold
physical-SSD runs. Service unions overlap and must not be added.

| 1 s window | Provider p50/p95 | Storage union | H2D union | Storage to H2D overlap | Exposed storage outside H2D/kernel | Useful kernels | GPU idle | Remaining dependency/host gap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| S0 | 17.042/20.593 ms | 587.435 ms | 169.298 ms | 111.314 ms | 476.121 ms | GPU0 30.500 ms | GPU0 970.467 ms | 120.346 ms |
| A1 | 13.912/19.102 ms | 584.816 ms | 103.535 ms | 45.479 ms | 539.337 ms | GPU0/GPU1 36.156/7.675 ms | GPU0/GPU1 964.736/993.216 ms | 179.069 ms |

The decode-relevant 99 Hz DWARF profiles independently attribute 99.315% (S0)
and 98.301% (A1) of storage-worker samples to ext4/page-cache read service.
Whole-process file-read attribution is 53.349% and 32.990%, respectively. The
manifest includes the perf-stat counters, bounded FlameGraphs, trace hashes,
block-device deltas, provider wall, output identities, and terminal state. Decode
rates under profiling are instrumentation observations, not selection results.

The dominant remaining critical path is exposed physical-storage/page-cache
service, with scheduler/policy bookkeeping more visible in A1. No CUDA-graph,
LRU, topology, batching, or storage-format redesign was attempted.

## Validation and raw evidence

The final selected nested source passes the focused native suite 12/12. The
probe is rebuilt from and hashes to the accepted nested source, and the nested
worktree is clean.

- Manifest: `manifest.json`
- Release: <https://github.com/murillo128/k3-out-of-core/releases/tag/issue69-delta-d-final-physical-ssd-v1>
- Asset: `issue69-delta-d-final-physical-ssd-v1.tar.zst`
- Size: `9008086` bytes
- SHA-256: `b2d276be94662cd7716de8a747df5df595ef1282ad89e1bb1b00aaf24a3f36c5`

The archive contains D2 controls/candidate evidence and rejected source patch,
the failed first trace attempt, successful replacement traces, raw perf data,
rendered FlameGraphs, analyzer input, and focused CTest log. It contains no GGUF
or model files. Merge remains blocked pending a fresh independent review of the
exact published parent and nested targets.
