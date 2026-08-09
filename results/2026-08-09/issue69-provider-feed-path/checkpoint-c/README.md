# Issue 69 Checkpoint C — final-capable feed-path evidence

Status: `PASS` (exact-target final independent review pending)

The unchanged nested candidate
`968e138cab375cbca4406b82b756671eb103ea3e` passes every technical gate in
issue #69. The fresh comparison baseline is
`9cc4300f4c090456a43718b68da9298c19d8fddf`, the exact pre-feed-path target
that already implements the mandated explicit two-worker setting. Both probes
were built as Release CUDA/Perfetto binaries for SM86 on the same 2×RTX 3090
host.

## Acceptance result

Five fresh pairs were interleaved per topology, with odd pairs running
baseline-first and even pairs final-first. The 95% S0 interval is a two-sided
paired Student-t interval over log throughput ratios.

| Cell | Baseline tok/s | Final tok/s | Final / baseline | Gate |
| --- | ---: | ---: | ---: | --- |
| S0 | 1.5430 | 1.8688 | 1.2111 | pass; 95% interval [1.1521, 1.2731] |
| S1 | 1.3807 | 2.3887 | 1.7300 | pass |
| A1 | 1.4544 | 2.3961 | 1.6475 | pass |

All 30 paired processes preserve the accepted generated and topology-specific
numerical identities, use exactly two storage workers, and close with zero live
reads, requests, references, pins, or events. Every A1 final process reads
36,272,472,064 backing bytes, below S1's 44,741,623,808 bytes.

The matched S0 Perfetto routed-layer p50 falls from 18.024496 ms to
12.526748 ms, a final/baseline ratio of 0.6950 and a 30.5% reduction. The final
trace has no transfer-stage slices. It records miss-rich layers where H2D starts
before the final storage read completes, while the compliance telemetry records
different-flight storage/H2D overlap without dropped records.

## Correctness and structure

Fresh Mode-C S0/S1/D1/A1 processes are exact across topologies. Every cell uses
two workers, performs direct storage-to-pinned-lane promotion with zero durable
cold admissions and zero `transfer_ring::stage()` bytes, and reports direct
bytes equal to H2D bytes. Hot experts remain valid without cold references,
production remap dynamic allocations remain zero, and every bounded terminal
count returns to zero.

The focused native expert suite passes 12/12, including direct read/H2D failure,
cancellation, stale generation, multi-flight drain, exact worker validation,
and the local plus two-GPU mixed cold-ready/direct-storage overlap regressions.

## Capacity and CPU controls

| Cold budget | S0 distinct keys | A1 distinct keys | Measured backing bytes |
| --- | ---: | ---: | ---: |
| 16 GiB | 268 | 1,573 | S0 48,225,058,816; A1 36,272,472,064 |
| 32 GiB | 268 | 1,573 | S0 48,225,058,816; A1 36,272,472,064 |
| 64 GiB | 268 | 1,573 | S0 48,225,058,816; A1 36,272,472,064 |
| full working set | 11,008 | 11,008 | 0 in both measured decode windows |

Normal `PROMOTE_AND_GPU` cold misses intentionally bypass durable cold
admission, so the finite-budget controls expose additive hot residency without
mandatory duplicate cold copies. The explicit full prewarm holds all 11,008
keys and proves the cold-RAM transfer floor: exactly zero measured storage
requests and bytes, at least 88,661,220 KiB host memory available, and no swap.

After the structural change, the largest individual pure cache-bookkeeping
function is `llm_cold_expert_cache::validate_invariants` at 1.33% of S0
process on-CPU samples; policy selection is 0.68%. Both are below the issue's 5%
threshold, so LRU/ALWAYS remains unchanged. D-007 now records the accepted
independent/reclaimable-tier semantics while preserving immutable GGUF authority
and no writeback.

The complete machine-readable result is in [manifest.json](manifest.json).
Raw workloads, resource samples, `perf.data`, trace files, and CTest output are
published in the immutable
[`issue69-checkpoint-c-final-v1`](https://github.com/murillo128/k3-out-of-core/releases/tag/issue69-checkpoint-c-final-v1)
release. Asset: `issue69-checkpoint-c-final-v1.tar.zst`, 13,752,475 bytes,
SHA-256 `3db3256273d72978742349c1139c709c742b60d2222e71544f4859b0a7b69457`.
