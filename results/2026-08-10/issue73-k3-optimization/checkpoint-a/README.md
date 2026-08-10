# Issue 73 Checkpoint A — real K3 bring-up and baseline

Status: `PASS_PENDING_INDEPENDENT_REVIEW`

The complete 1.561-TB Kimi K3 execution artifact now runs through the corrected
device-local expert cache on the characterized 4× RTX 4000 Ada VM. The full
100-token Mode-C compliance workload exercised all 92 routed layers, recorded
2,392 route/remap checkpoints, and completed two generated tokens without CUDA,
storage, transcript, generation, or lifecycle failures. Its token IDs and logits
digests exactly match the prefix of both production runs.

`K3_INITIAL` is frozen as the conservative T1 configuration in
[manifest.json](manifest.json): resident and expert execution on GPU 0, 64 hot
expert slots, a 16-GiB cold cache, buffered positional reads through normal Linux
page cache/readahead, four storage workers, and 24 generated tokens. This is an
initial out-of-core baseline, not an optimized result.

## Baseline result

Both fresh-process P0 runs produced exactly the same 24 token IDs, text, and
per-token logits digests.

| Run | Decode tok/s | TTFT | Decode p50 | Decode p95 | Decode max |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.190680 | 453.77 s | 5.230 s | 5.889 s | 6.118 s |
| 2 | 0.187774 | 457.78 s | 5.319 s | 5.958 s | 6.041 s |
| Pooled | 0.189216 | — | 5.230 s | 5.958 s | 6.118 s |

The 1.53% run-to-run TPS spread is retained rather than selecting the faster
run. Each process moved 2.497 TB of logical expert data to GPU 0 and caused about
3.50 TB of guest-visible block reads. The 64-slot cache had no hot reuse in this
window, making capacity/topology optimization the next causal step. Peak process
RSS was 106.1 GiB; GPU-0 memory peaked at 10.9 GiB; swap and OOM counters stayed
zero. All queues, operations, pins, references, and transfer events drained.

The P0 classification follows the issue's post-bring-up measurement amendment:
production runtime semantics, no Mode-C transcript machinery, and only the
one-second external resource sampler. The compliance run's throughput-like
number is diagnostic and is not used to rank performance.

## Evidence

- [K3_INITIAL machine-readable summary](k3-initial-summary.json)
- [Checkpoint manifest](manifest.json)
- [Raw evidence index](raw-evidence-index.json)

The checksum-addressed raw archive contains host/topology identity, the complete
33-file model manifest, conversion log, full compliance JSON, both production
workloads and resource streams, and focused bring-up logs. It is published at the
immutable release URL recorded in the raw evidence index.

After independent review, the next stage derives T1 `MAX_SAFE`, runs the requested
matched `CPU_CONTROL` / `GPU_HOT_0` / `GPU_HOT_MAX` causality control, and then
screens T1 through T4 before storage/cache tuning.
