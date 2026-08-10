# Issue 73 — full-K3 architecture baseline closeout

Status: `PASS_PENDING_INDEPENDENT_REVIEW`

Phase 13.5 establishes a reproducible full-Kimi-K3 baseline for the current
out-of-core architecture on 4× RTX 4000 Ada and characterizes its dominant
memory/feed-path limitations. The historical B0/T4 `K3_BEST` label is retained
inside the campaign evidence, but the final classification is
`K3_ARCH_BASELINE`: it is the best configuration found inside this bounded
architecture and scope, not a claim about the ultimate performance attainable
with different placement, execution, cache/admission, or model-representation
designs.

Owner amendment
[#73 comment 5244964890](https://github.com/murillo128/k3-out-of-core/issues/73#issuecomment-5244964890)
reduced the remaining closeout to a short reproducibility confirmation, reuse
of the accepted bounded trace, and preservation of comparator notes. It
explicitly deferred new high-detail profiling, expensive Mode-C replay, and the
same-host Colibrì / `kimi-k3-in-c` campaign. The five-run confirmation command
was already in flight when the amendment arrived; it completed normally and
exceeds the amended two-process requirement.

## Frozen configuration

The baseline uses resident GPU 0, expert roles
`0:549,1:1125,2:1125,3:1125`, eight ordinary CUDA layers, `n_ubatch=4`, global
LRU/always admission, a 16-GiB cold tier with asynchronous fill off, positional
O_DIRECT, four storage workers, QD64, 128-MiB per-device promotion rings, and
the promote-and-GPU miss path. It runs one request with speculative decoding
and CUDA graphs disabled.

Exact execution revisions are parent `0f49c22fc41d12323fe21e8e8e3fab3df838ce01`
and nested runtime `763a425e3a59c6af14554218b9e9cf77804918c1`.
The model is the pinned 33-file, 1,561,157,859,104-byte
`moonshotai/Kimi-K3@9f62e4e9fffbd0a83ddd60e1c209d828994b3569`
artifact; its execution-identity manifest SHA-256 is
`58b14d13a602944e1134fc753b2cc819a84a31290aee9c1479264a66dbb5efe2`.

## Reproducibility envelope

The five fresh P0 processes each generated 256 tokens. All five produced
exactly the same generated IDs, text, and all per-forward logits digests.

| Run | Decode tok/s |
| --- | ---: |
| 1 | 0.289490 |
| 2 | 0.287566 |
| 3 | 0.286344 |
| 4 | 0.283170 |
| 5 | 0.291315 |
| Pooled | **0.287550** |

The range is 2.83% of pooled TPS. Pooled decode p50/p95/p99/max is
3.461/3.981/4.254/4.415 seconds; TTFT p50/p95 is 274.039/283.870 seconds.
This is 51.97% above the pooled 0.189216 tok/s `K3_INITIAL` result.

The earlier 24-token B0 selection endpoint was 0.294132 tok/s. It and the
longer 256-token confirmation have different workload lengths, so this packet
retains both without treating their difference as a matched regression.

All five confirmation processes opened all 33 sources with O_DIRECT and had
zero buffered fallback, storage short reads, I/O errors, stale completions,
terminal queued/in-flight work, live transfer events, swap, or OOM kills.
Maximum process HWM was 111,878,096 KiB. Minimum free VRAM remained
1,030/1,026/1,026/1,026 MiB.

## Optimization and causal result

The retained ladder is 0.189216 tok/s `K3_INITIAL`, 0.244574 tok/s after the
T4 four-device expert topology, and 0.294132 tok/s after selecting four-worker
positional O_DIRECT. The host-cache, ordinary-VRAM, and eight-worker screens
did not yield an admissible further step.

The accepted bounded P-TRACE remains the final attribution. Its 52 complete
routed-layer intervals are lossless, but its +7.19% perturbation makes its TPS
attribution-only. Provider work is 63.2% of routed-layer wall and post-issue
provider dependency is 53.7%. Storage service occupies 47.4% and H2D scopes
14.2% of the accounted wall; 88.3% of H2D-union time overlaps storage. QD64
provides 128-operation capacity while the endpoint peaks at 84, so the queue
depth is not the active bound.

The final confirmation moves 19.895 GB of logical expert storage and H2D per
generated token, with 0.088 GB of host-staged cross-device traffic and no CUDA
peer traffic. The hot-cache hit rate is 40.00%. Mean sampled GPU utilization
is only 8.40/6.14/6.08/6.23%, consistent with a feed-path-limited baseline
rather than a compute ceiling.

Important negative results remain part of the conclusion:

- H96/HMAX managed-cold capacity displaced ordinary mapped weights, caused
  millions of major faults and extra guest reads, and collapsed TPS.
- Native `io_uring` completed correctly but was slower than positional
  O_DIRECT on this guest virtio-SCSI path.
- V1 ordinary placement screened +3.81% but changed every logit digest; V3
  CPU-expert execution reached only 0.022048 tok/s.
- Eight workers reduced queue wait but did not reproduce an endpoint gain.

## Correctness and deferred comparison

Checkpoint A's full-K3 Mode-C run already exercised all 92 routed layers and
2,392 route/remap checkpoints with exact production-prefix IDs/logits and clean
transcript/lifecycle state. The only later retained runtime change bounds the
default-off CUPTI capture allocation. Exact output/logits in the accepted trace
and all five final frozen-revision processes satisfy the amendment's reuse gate;
no additional expensive Mode-C replay is needed.

The intended comparison targets remain `JustVugg/colibri` and
`FareedKhan-dev/kimi-k3-in-c`. No checkout, materialization, or result is
claimed. The owner amendment defers them to a dedicated benchmark that can pin
commits, licenses, representations, semantic coverage, and same-host resource
use after the expected architecture changes.

## Reproduction and evidence

The confirmation command was:

```text
python3 scripts/issue73/run_matrix.py \
  --probe llama.cpp/build-cuda/bin/phase9-cache-policy-probe \
  --model /var/lib/k3/issue73/model/kimi-k3-bf16-00001-of-00033.gguf \
  --artifact-identity-manifest /var/lib/k3/issue73/evidence/max-safe-artifact-identity.json \
  --output-dir /var/lib/k3/issue73/runs/final/best-p0-256-01 \
  --case K3_BEST_P0 --roles 0:549,1:1125,2:1125,3:1125 \
  --n-gpu-layers 8 --runs 5 --n-ubatch 4 --max-generate 256 \
  --cold-bytes 17179869184 --ring-bytes 134217728 \
  --peer-staging-bytes 134217728 --io-workers 4 --queue-depth 64 \
  --transport DIRECT_IO_POSITIONAL --runtime-mode PRODUCTION_PERFORMANCE \
  --miss-policy PROMOTE_AND_GPU --measurement-tier P0 --sample-period 0.5 \
  --block-stat /sys/block/sda/stat
```

[summary.json](summary.json) is the compact machine-readable closeout.
[raw-evidence-index.json](raw-evidence-index.json) binds the immutable final
archive, including the decision-driving campaign raw data, accepted P-TRACE,
and final confirmation. Checkpoint A's separately published archive preserves
the large compliance transcript. The model artifact and host remain retained
for downstream issue #77 as required.
