# D3P 96-GiB residency-attribution investigation

Status: `DESIGN_REQUIRED`

The retained 96-GiB runtime collapses because its 95.994-GiB anonymous
ColdExpertCache and the model's 104.631-GiB hot file-backed working set cannot
co-reside on this 188.173-GiB host. Including the measured 2.931-GiB practical
host reserve and 0.885-GiB of other process residency, the pressure-free demand
is 204.441 GiB: a 16.267-GiB deficit before safety margin. The kernel resolves
that deficit by repeatedly reclaiming the model's inactive file pages. At full
cache, model/file RSS has fallen by about 18.407 GiB and the process plateaus at
about 183.2 GiB RSS with only about 1.2 GiB `MemFree`.

The physical-I/O attribution closes independently: in the new bounded 96-GiB
cell, process/NVMe reads beyond exact aligned expert `O_DIRECT` bytes were
189,617,407,200 bytes, while cgroup file-refault pages represented
189,620,428,800 bytes, a difference of only 3,021,600 bytes (0.0016%). The
existing unprofiled endpoint has the same relationship. Repeated GGUF
model-page reads, not expert I/O serialization, explain the physical-read
amplification and the exposed wall.

No runtime optimization, cache-policy change, intermediate-capacity sweep,
final D3 campaign, Checkpoint C request, or merge was performed.

## Identity and evidence use

- Accepted Checkpoint B remains project
  `4589bcb07ae81e32aff70f06008618a8b2ac9b4f`, nested
  `7930fdcf428ed1221ff070a14e62e2073cc1a803`, nested base
  `588d1b4b1ea32e10f99b0c3ce8506b703c3f3c93`, safety `YES`.
- The investigated technical target is project
  `b88d56e2db689446ef258e41f480c50acf076f1a`, nested runtime
  `fb8e271dcafd33b5ba5300bd0d69c4bee5b1e6ce`. The evidence branch started this
  investigation at parent `79acc7cb749d0c9e064f371cae47c6094b6ee59e`.
- The binary is the recorded Release/max-native CPU-only shared build:
  `phase13-mode-p-probe` SHA-256
  `3d96ce06d67302916a6c6fb3afcd975d467947bbfba80d0d69e387eb1f8ac53e`.
  It uses GCC 11.5, `-O3 -DNDEBUG`, `GGML_NATIVE=ON`, `-march=native`, OpenMP,
  32 threads, native `io_uring`, `O_DIRECT`, exact routing, and no CUDA.
- Reused endpoint truth: the unprofiled 96-GiB 0.0417579896-tok/s retained cell
  and the diagnostic 64-GiB 0.239987844-tok/s control in the parent D3P
  manifest. The earlier max-native policy profile supplies the OpenMP/policy
  cycle attribution.
- Newly executed evidence consists only of observation-heavy three-forward
  96-GiB and 64-GiB cells. Their TPS and fill times are not endpoint evidence:
  full `smaps` walks materially perturb the very page-table/reclaim path being
  measured. They supply synchronized residency, process-I/O, cgroup, VM and
  device attribution.

## Resident-memory budget

All sizes below are actual resident demand, not GGUF virtual/model size.

| Component | 64-GiB pressure-free control | 96-GiB pressure-free demand |
| --- | ---: | ---: |
| Host usable RAM | 188.173 GiB | 188.173 GiB |
| Practical fixed host/non-project reserve | 2.931 GiB | 2.931 GiB |
| ColdExpertCache payload VMA | 63.996 GiB | 95.994 GiB |
| Required resident GGUF model working set | 104.631 GiB | 104.631 GiB |
| Other process RSS | 0.885 GiB | 0.885 GiB |
| Total demand | 172.443 GiB | 204.441 GiB |
| Headroom / deficit | **+15.731 GiB** | **-16.267 GiB** |

The cache identification is exact rather than inferred from total RSS. Its
largest anonymous VMA has `Size == configured actual cache bytes + 4 KiB`; at
the terminal 64-GiB snapshot its RSS also equals the configured actual cache
bytes plus 4 KiB. Other process residency is the remainder of the pressure-free
`smaps_rollup` RSS after subtracting the cache and model mappings. The kernel
does not expose labels that split its 0.885 GiB into KV/context, graph/work,
allocator and 267.8-MiB direct-staging subcomponents, but the combined amount is
too small to close a 16.267-GiB deficit.

The model's material mapping is 106.818 GiB virtual and 104.631 GiB resident in
the pressure-free control. It is concentrated entirely in the three non-expert
mapping shards:

| Shard | Virtual | Pressure-free RSS | Sampled RSS after reclaim begins |
| --- | ---: | ---: | ---: |
| 31 | 17.202 GiB | 17.202 GiB | 15.636 GiB |
| 32 | 46.531 GiB | 46.531 GiB | 46.531 GiB |
| 33 | 43.084 GiB | 40.897 GiB | 32.149 GiB |
| Total | 106.818 GiB | 104.631 GiB | 94.316 GiB |

Expert shards 1–30 have only 4-KiB control mappings and zero RSS because expert
payloads use the explicit storage path. Thus the 1.56-TB model's virtual/storage
size is not being confused with process RSS. At this sampled onset, shard 33
has the largest resident loss. By first-full, aggregate process file RSS is
about 86.224 GiB, 18.407 GiB below the pressure-free model requirement; exact
per-shard refault counts are not available from this kernel.

## Lifecycle and pressure knee

The probe exposes no external fill-complete event. The preregistered full-smaps
walks extended the profiled 96-GiB fill from the unprofiled 76.4 seconds to
185.5 seconds, so their original elapsed-time names are corrected below using
the completed result and lightweight series. This does not affect the
unprofiled endpoint.

| 96-GiB lifecycle point | Cache/anon residency | Model/file RSS | `MemFree` | Reclaim observation |
| --- | ---: | ---: | ---: | --- |
| Earliest practical setup/fill snapshot | cache 1.31 GiB | model 8.58 GiB | ~98 GiB | no scan/steal |
| Pre-pressure natural fill | cache ~69.5–71.5 GiB | model 104.63 GiB | 8.7 GiB | no scan/steal |
| First background reclaim | cache ~77.6 GiB | file 104.20 GiB | 0.99 GiB | 117,440 pages scanned and stolen |
| First direct reclaim | cache ~87.1 GiB | file ~94.4 GiB | ~1.3 GiB | 5,665,664 total scans; 3,712 direct |
| Actual first-full | cache 95.994 GiB | file 86.224 GiB | 1.17 GiB | 18,092,258 refaults; 17,925,799 scans |
| End of bounded post-fill work | cache 95.994 GiB | file 84.91 GiB | ~1.3 GiB | 46,294,050 refaults; 46,480,233 scans; 46,461,105 steals |

The practical knee is therefore bounded directly by the natural fill: kswapd
reclaim begins near **78 GiB of physically resident cache payload**, and direct
reclaim is visible by about **87 GiB**. At full cache, `active_file` remains
essentially constant at 35.75 GiB while `inactive_file` falls from the
pressure-free control's 69.48 GiB to about 51.04 GiB. The 18.44-GiB inactive
file loss matches the 18.41-GiB model RSS loss. This continuous-fill knee and
the 64-GiB anchor make an 80/88-GiB rerun unnecessary.

The full bounded 96-GiB interval records 293,760 direct-reclaim scans and the
same number of direct steals; most reclaim is performed by kswapd, but direct
reclaim is also present on inference threads. Swap and OOM counters remain
zero. The 64-GiB control retains the full 104.631-GiB model set, has about
14.1 GiB `MemFree` at terminal capture, zero scan/steal delta, zero process
major faults in the new cell, and only one file refault page after fill.

## Explicit expert I/O versus model/refault I/O

For the trustworthy unprofiled endpoint cells, exact aligned expert bytes are
subtracted from per-NVMe physical reads:

| Cell | Expert `O_DIRECT` | Non-expert/model reads | NVMe physical | Split | Process major faults |
| --- | ---: | ---: | ---: | ---: | ---: |
| 96 GiB | 149.604 GB | 201.559 GB | 351.162 GB | 42.6% / 57.4% | 786,558 |
| 64 GiB | 121.631 GB | 20.605 GB | 142.236 GB | 85.5% / 14.5% | 9 |

The endpoint 96-GiB non-expert delta is within 5.6 MB of
`49,210,080 * 4096` file-refault bytes. The 64-GiB non-expert delta is its
one-time fill/model population; the new control records one 4-KiB refault and
no scan/steal after first-full.

The new profiled 96-GiB cell independently records process `read_bytes` equal
to the `nvme0n1` read delta (330,727,307,264 bytes). Subtracting exact expert
aligned bytes (141,109,900,064) leaves 189,617,407,200 bytes; cgroup
`workingset_refault_file` accounts for 189,620,428,800 bytes. Over its bounded
post-fill interval, inferred model reads are about 38.47 GB per forward while
expert reads are about 10.80 GB per forward. This is fault/refault-driven GGUF
traffic, not hidden expert fallback: buffered and synchronous expert fallbacks
remain zero.

## Causal bucket classification

| Bucket | Classification | Evidence |
| --- | --- | --- |
| ColdExpertCache residency | **PRIMARY** | Exact 95.994-GiB anonymous VMA creates a 16.267-GiB pressure-free deficit; reclaim begins as it grows through ~78 GiB. |
| Model mmap/page-fault I/O | **PRIMARY** | Model RSS falls ~18.4 GiB; 57.4% of endpoint physical reads are non-expert and match file-refault bytes. |
| Expert backing I/O | **SECONDARY** | 64 GiB moves more expert bytes per forward (13.08 GB versus 10.23 GB) yet is 5.75x faster. |
| Other anonymous runtime memory | **NON-MATERIAL** | Combined pressure-free remainder is 0.885 GiB. |
| Useful CPU/model compute | **SECONDARY / IRREDUCIBLE HERE** | Pressure-free 64-GiB execution completes near 4.17 s/forward with unchanged arithmetic/routes. |
| Scheduler/cache-policy administration | **NON-MATERIAL** | Retained profile puts cache policy at 0.31% of main-thread weighted cycles. |
| Kernel I/O overlap | **NON-MATERIAL DEFECT** | Existing evidence reaches 16 expert requests, 48 operations and SQ/CQ 16/15 with native `io_uring`. |
| OpenMP wait/spin | **SYMPTOM** | It dominates 96-GiB sampled cycles while workers wait around graph work; when reclaim disappears at 64 GiB, total forward wall falls to 4.13 s. Exact faulting-thread stacks remain unavailable. |

The 96-GiB endpoint averages 23.948 s/forward. The hard floor requires at most
5.0 s/forward: a 4.790x speedup and recovery of about 18.948 s/forward. The
pressure-free control averages 4.167 s/forward and has p50 4.134 s, leaving
about 0.83 s/forward margin. The measured p50 recoverable interval is
18.298 s. Thus eliminating the residency/refault loop has an observed upper
envelope of about 0.240 tok/s; policy, copies, or queue administration cannot
plausibly recover the required 79.1% of wall.

## Recommended next architecture mechanism

**Preferred mechanism family: decouple the fixed 96-GiB logical cache capacity
from anonymous physical residency.** Keep policy order, logical occupancy,
generations, final cache state and fixed slot identities, but add an explicit
`NONRESIDENT/RESTORING` payload state for unreferenced slots and rehydrate them
through the existing storage/scheduler/transport path before `HOST_READY`.
Bound physical cache payload to at most about 76 GiB on this host, below the
observed ~78-GiB reclaim onset; this releases at least 20 GiB and restores
positive pressure margin without silently changing the required logical
96-GiB capacity.

Its upper-bound benefit is the measured 18.298-s p50 / 18.948-s average gap and
the 0.240-tok/s pressure-free envelope. The realized benefit will be lower by
the rehydration cost of logically cached but physically nonresident experts,
which must be measured before acceptance. The design risks are substantial and
therefore require design authority: cache-hit/telemetry meaning, generation and
publication transitions, `cpu_execution` lifetime preventing decommit, exact
failure/cancellation cleanup, deterministic final state, unload safety, and
bounded added expert reads. A regular file-backed/reclaimable slot mapping is a
possible realization but has additional dirty-writeback, `O_DIRECT` coherence,
and storage-layout risks; it should not be introduced as a second mechanism
unless the simpler explicit sparse-residency state proves infeasible.

The only semantics-neutral alternative is at least about 20 GiB of additional
practical RAM. Shrinking the model mapping by the required >=16.3 GiB has no
evident lossless target: the pressure-free control references essentially its
entire 104.631-GiB resident set every run, and quantization/model-format changes
remain outside #89.

## Falsified hypotheses and limitations

- Insufficient `io_uring` concurrency, cache-policy scanning, copies, or a large
  unlabeled heap cannot explain the gap.
- The 1.56-TB storage/virtual model size is not resident; only 104.631 GiB of
  model GGUF mappings is resident pressure-free.
- OpenMP tuning alone cannot recover the gap; wait/spin tracks the fault-stalled
  graph rather than supplying 18 seconds of independent useful work.
- Sequential mmap advice reduced refault traffic but did not make the working
  set fit.
- Tracefs events are permission-blocked; host and cgroup memory PSI files are
  unavailable. The sampled major-fault `perf record` window landed before the
  delayed pressure knee and contains no fault samples, so exact per-thread and
  per-shard fault stacks are not claimed. `/proc` VMA residency, process I/O,
  cgroup refault/scan/steal and NVMe counters nonetheless close the byte-level
  attribution.
- Full `smaps` scans are non-atomic over a moving fill and perturb timing.
  Endpoint throughput remains exclusively the prior clean unprofiled evidence.

Large raw captures and every small snapshot remain host-local under
`/mnt/nvme1/issue89/d3p-max-native/residency-attribution`; the compact checksum
binding is in [raw-evidence-index.json](raw-evidence-index.json), and structured
conclusions are in [manifest.json](manifest.json).
