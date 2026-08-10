# Issue 73 T1 capacity and matched controls

This packet records the T1 `MAX_SAFE` result and the owner-requested
`CPU_CONTROL` / `GPU_HOT_0` / `GPU_HOT_MAX` causal comparison. All cells use
the pinned full Kimi K3 artifact, the same 100-token prompt, `n_ubatch=4`,
eight ordinary CUDA layers on resident device 0, a 16-GiB managed cold tier,
buffered positional reads, four storage workers, queue depth 64, one request,
and 24 generated tokens.

## T1 capacity

`OBSERVED`: the exact reserve-safe boundary is 549 whole-expert slots on
`GPU-cc10d10a-cb7c-d71e-6f7e-b9ef0f8fc8fc` (`00000000:00:02.0`). The
selected run requested and received 549 slots, completed all 24 tokens with
the exact `K3_INITIAL` IDs and logits, and sampled 1,080,033,280 bytes free
against the 1,073,741,824-byte floor. Candidate 550 was rejected immediately
after an observed reserve breach.

## P0 endpoints

| Cell | Decode tok/s | TTFT s | p50 / p95 / p99 / max s | Hot hits | H2D GiB/token | Block GiB/token | Peak RSS GiB |
|---|---:|---:|---:|---:|---:|---:|---:|
| CPU_CONTROL | 0.021028 | 2053.944 | 47.466 / 49.027 / 50.402 / 50.402 | 0 | 0.000 | 188.486 | 122.346 |
| GPU_HOT_0 | 0.189216 | 453.768 | 5.230 / 5.958 / 6.118 / 6.118 | 0 | 96.888 | 135.764 | 106.108 |
| GPU_HOT_MAX | 0.185998 | 473.484 | 5.309 / 6.061 / 6.267 / 6.267 | 0 | 96.888 | 135.862 | 106.098 |

`GPU_HOT_0` reuses the two accepted 64-slot `K3_INITIAL` processes. That is
the minimum valid capacity for this microbatch, and both processes had zero
hot hits, so the tier was effectively disabled without changing the graph.

## Causal result

- `OBSERVED`: `GPU_HOT_0 / CPU_CONTROL = 8.9984`. GPU expert compute is
  strongly beneficial even when every use pays storage plus H2D promotion.
- `OBSERVED`: `GPU_HOT_MAX / GPU_HOT_0 = 0.9830`. The extra 485 persistent
  slots produced zero hits, avoided zero H2D bytes, and was 1.70% slower in
  this single repeat. T1's reuse distance exceeds its safe VRAM capacity, so
  this is not evidence of a residency benefit.
- `OBSERVED`: `GPU_HOT_MAX / CPU_CONTROL = 8.8454`.
- `OBSERVED`: both GPU cells are exactly identical in prompt IDs, generated
  IDs, text, and logits. CPU and GPU selected the same first six generated
  tokens, then diverged at token 7; their logit digests differ from the first
  forward. The later autoregressive route streams are therefore not strictly
  matched, and the CPU ratios carry that explicit numerical/workload caveat.
- `OBSERVED`: CPU fallback performed zero H2D, zero hot admissions, and zero
  hot hits. It also had zero cold hits at the 979-slot managed-cold capacity,
  read 2.497 TB logically, and caused 4.857 TB of guest block reads.
- `OPEN`: exposed storage, H2D, and routed-expert compute time remain for the
  matched P-TRACE attribution step. Aggregate service counters are not
  presented as exposed critical-path stall.

The portable sources are [matched-controls.json](matched-controls.json),
[matrix-summary.json](matrix-summary.json),
[max-safe-summary.json](max-safe-summary.json), and
[raw-evidence-index.json](raw-evidence-index.json).

