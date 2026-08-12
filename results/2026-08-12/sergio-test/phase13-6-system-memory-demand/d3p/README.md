# Phase 13.6 D3P CPU production-performance recovery

> **Superseded production status:** this report remains the immutable pre-autofit
> 96-GiB design return. The later accepted common system-memory autofit and
> passing final CPU D3 are recorded in [autofit/README.md](autofit/README.md)
> and [autofit/manifest.json](autofit/manifest.json).

Status: `DESIGN_REQUIRED_NO_ACCEPTABLE_K3_CPU_BEST`

Checkpoint B remains the accepted correctness/concurrency floor at project
`4589bcb07ae81e32aff70f06008618a8b2ac9b4f`, nested runtime
`7930fdcf428ed1221ff070a14e62e2073cc1a803`, and nested base
`588d1b4b1ea32e10f99b0c3ce8506b703c3f3c93`.

D3P retained two coherent runtime improvements at technical project
`b88d56e2db689446ef258e41f480c50acf076f1a` and nested runtime
`fb8e271dcafd33b5ba5300bd0d69c4bee5b1e6ce`, but no 96-GiB CPU production
candidate met the mandatory 0.20 tok/s floor. The best trustworthy max-native
96-GiB endpoint was 0.041758 tok/s, 20.88% of the floor. Therefore no
`K3_CPU_BEST` was frozen, and the final D3 campaign, full conformance pass,
Checkpoint C, merge, issue closure, and restart of the dependent CPU campaign
were not performed.

## Optimized build envelope

Decision-driving cells used matched Release, shared-library, CPU-only builds
with GCC 11.5, `-O3 -DNDEBUG`, `GGML_NATIVE=ON`, `-march=native`, OpenMP on,
LTO off, and Perfetto off. The OCI guest exposes an AMD EPYC 9J14 with AVX-512
F/DQ/CD/BW/VL, IFMA, VBMI/VBMI2, VNNI, BF16, BITALG and VPOPCNTDQ. The runtime
loaded the single shared `ggml-cpu` backend from its own build tree, used all 32
allowed logical CPUs on the single exposed NUMA node, and used 32 inference
threads without explicit affinity pinning.

The accepted Checkpoint-B and policy-candidate build fingerprints match in every
material field except the intentional source delta. See
[max-native-build-fingerprint.json](max-native-build-fingerprint.json) and
[max-native-final-build-fingerprint.json](max-native-final-build-fingerprint.json).
Earlier AVX2/static results are retained in the raw archive only as historical
correctness/diagnostic evidence; they do not select the performance winner or
participate in the 0.20 tok/s gate. The earlier mixed native/shared cell remains
quarantined because it changed multiple build dimensions and model work.

## Trustworthy max-native endpoint

All cells below used exact routing, native `io_uring` plus `O_DIRECT`, natural
empty-cache fill, Mode-P, no profiler in the timed path, 32 threads, and no
buffered or synchronous fallback.

| 96-GiB cell | Forwards | Fill | Decode | p50 | p95 | Physical/logical reads |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Checkpoint-B source | 8 | 155.483 s | 0.032431 tok/s | 30.541 s | 32.546 s | 4.545× |
| Linear policy validation | 8 | 100.329 s | 0.036497 tok/s | 27.056 s | 31.312 s | 4.300× |
| Sequential deferred mapping | 4 | 76.373 s | 0.041758 tok/s | 22.432 s | 26.150 s | 2.347× |

The first two rows are an exact matched A/B: identical output IDs, fill and
decode requests, hits/misses, backing loads/bytes, asynchronous operations,
final cache digest, and terminal resources. Linear candidate validation improves
TPS by 12.54%, reduces fill time by 35.47%, and reduces p50 by 11.41%.

The final mapping-advice row is an independently valid unprofiled endpoint from
the source-identical pre-commit tree. Against the exact four-forward profiled
control it preserves all logical work while reducing physical reads from
528.817 GB to 351.162 GB, file refaults from 92.75 million to 49.21 million,
and page steals from 92.94 million to 54.47 million. Its throughput ratio is not
used as a clean A/B claim because the control was profiled. Its 0.041758 tok/s
endpoint is valid for the hard gate and fails it by 79.12%.

## Causal attribution

- **H1 — I/O overlap:** real concurrency is present. BATCHED reaches 16 active
  expert requests, 48 active read operations, and SQ/CQ high-water marks 16/15.
  All reads remain native direct `io_uring`; queue depth is not the limiting
  administrative defect.
- **H2 — reclaim/refault:** confirmed dominant. At 96 GiB the process reaches
  roughly 184 GiB RSS. The best endpoint still incurs 786,558 process major
  faults, 49.21 million host file refaults and 54.47 million page steals. A
  diagnostic-only 64-GiB cell reaches 0.239988 tok/s and p50 4.134 s despite
  more expert misses per forward; it has nine process major faults, 27 file-page
  steals, and 1.170× physical/logical reads. The smaller capacity is not eligible
  for the fixed 96-GiB gate, but it proves the causal bottleneck and demonstrates
  that the max-native CPU/I/O substrate can exceed 0.20 tok/s when the resident
  working set fits.
- **H3 — cache-policy administration:** confirmed and reduced. Replacing three
  quadratic duplicate-candidate scans with one preallocated bitmap preserves
  policy semantics and gives the matched improvement above. In the subsequent
  max-native profile, policy selection falls to 0.31% of main-thread weighted
  cycles and is no longer a material bucket.
- **H4 — CPU compute:** present but not the 96-GiB gap. The pressure-free
  64-GiB cell completes a forward in about 4.17 s while moving 13.08 GB of expert
  backing bytes per forward. Router quantization, expert quantization/arithmetic,
  routes, weights and model format were unchanged.
- **H5 — synchronization/copies/bookkeeping:** max-native profiling attributes
  95.9% of process weighted cycles to OpenMP wait/spin sites while useful BF16
  and MXFP4 dot leaves account for 1.64% and 1.36%; `memmove` accounts for
  0.22%. These waits correlate with the main thread's mmap fault stalls. The
  sequential VMA advice reduces their backing-read amplification but cannot
  make the 96-GiB resident set fit.

Perfetto is unavailable in this CPU build: current repository CMake requires
CUDA/CUPTI when `LLAMA_PERFETTO=ON`. No Perfetto evidence is claimed. The bounded
profile instead used `perf stat`, a 60-second 49-Hz DWARF `perf record`,
project-pinned FlameGraph revision `41fee1f99f9276008b7cd112fca19dc3ea84ac32`,
VM/cgroup counters, per-NVMe counters and external resource sampling. `perf stat`
recorded 22.84 CPUs, 4.23 trillion cycles, 394.9 billion instructions (IPC 0.09),
2.79 million page faults and 256,365 major faults in 60.02 seconds.

## Bounded optimization decisions

1. **Retained — linear candidate validation**
   (`8f7cd23816b96f451e95487f1213e2de60c8ccce`). Focused policy tests pass; the
   max-native 512-MiB SERIAL/BATCHED smoke and 96-GiB matched A/B have exact
   semantic/work/cache/resource equality.
2. **Rejected historical experiment — passive OpenMP waiting.** It was tested
   under the superseded AVX2 compatibility envelope and is not decision-driving.
   It added configuration surface without a material smoke improvement.
3. **Retained — sequential advice for no-prefetch deferred mappings**
   (`fb8e271dcafd33b5ba5300bd0d69c4bee5b1e6ce`). It changes only Linux VMA access
   advice, not tensor bytes, ColdExpertCache capacity or replacement semantics,
   and materially reduces physical reads/refaults. The focused policy test and
   max-native 512-MiB equality smoke pass.

The three-delta D3P budget is exhausted. No known correctness, lifetime,
generation, cancellation or terminal-resource defect is being waived.

## Required design return

The remaining 96-GiB gap is a physical residency mismatch. The measured p50
interval between the final 96-GiB cell and the pressure-free 64-GiB control is
18.30 seconds per forward even though the smaller cache performs more expert
backing work. Meeting the fixed 96-GiB floor now requires one of the following
scope changes:

- amend the final capacity to a measured residency-safe point;
- provide more physical RAM on the decision host; or
- authorize and constrain a resident-footprint/residency or storage mechanism
  that prevents the non-expert mmap working set from competing with the full
  96-GiB anonymous expert cache.

Current-layer expert residency protection is not proposed because the D3P
contract explicitly excludes it. Cache replacement semantics, storage layout,
quantization, routes, arithmetic and model format were not changed.

Physical coherent-UMA and discrete-CUDA qualification remain deferred to their
separate issues and are not the cause of this CPU design return.

The machine-readable result and checksum-addressed host-local evidence index are
in [manifest.json](manifest.json).
