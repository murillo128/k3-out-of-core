# Issue 73 ordinary-model VRAM allocation screen

This packet records the owner-amended Stage-1 screen from issue comment
`5242479238`. Completed topology, storage, and host-cache evidence is reused.
V0 is the accepted B0/T4 endpoint. V1 moves GPU0 VRAM from expert-hot capacity
to ordinary model layers while keeping experts on GPUs 1–3. V3 tests the
extreme CPU-expert path with maximum mechanism-safe ordinary residency. V2 is
not run because V1 fails the amendment's exact-logit gate.

## Capacity boundaries

V1 uses `Resident={0}`, `Expert={1,2,3}`. Two fresh L16 capacity processes
completed cleanly at a 1,204-MiB GPU0 floor; the harness-valid process used the
normal prompt plus one decode interval. The runtime reported 16 of 94 layers
offloaded and an 18,540.59-MiB CUDA0 model buffer. Fresh L17 reported a
19,761.89-MiB model buffer, reached 2 MiB free, and aborted with CUDA OOM on
the first compute graph. V1 `MAX_SAFE` is therefore exactly 16 ordinary CUDA
layers for the 1-GiB reserve.

V3 uses `CPU_FALLBACK` with the existing minimum valid 64-slot configuration;
it records zero hot admissions, hot hits, or expert H2D, so persistent expert
residency is effectively disabled. L16 nevertheless reached only 138 MiB free
under the actual CPU-fallback execution footprint and was terminated at the
reserve gate. L15 completed at a 1,362-MiB floor with a 17,319.29-MiB CUDA0
model buffer. V3 `MAX_SAFE` is 15 ordinary CUDA layers.

## Stage-1 endpoints

| Cell | Workload | Ordinary layers | GPU0 expert slots | Decode tok/s | TTFT s | p50 / p95 / p99 / max s | GPU0 floor MiB |
|---|---|---:|---:|---:|---:|---:|---:|
| V0/B0 | normal 100-token prompt, 24 output | 8 | 549 | 0.294132 | 300.214 | 3.399 / 3.870 / 4.036 / 4.036 | 1,030 |
| V1 | normal 100-token prompt, 24 output | 16 | 0 | 0.305327 | 331.096 | 3.273 / 3.731 / 3.982 / 3.982 | 1,204 |
| V3SHORT | explicit non-decision-driving short prompt, 5 output | 15 | effectively 0 | 0.022048 | 531.739 | 43.869 / 47.955 / 47.955 / 47.955 | 1,362 |

V1/V0 decode TPS is 1.0381, but V1 worsens TTFT by 30.9 seconds. Removing
GPU0's 549 expert slots lowers aggregate expert capacity from 3,924 to 3,375,
lowers hot hit rate from 26.60% to 11.79%, and increases logical expert
storage/H2D from 1,832,636,252,160 to 2,202,181,632,000 bytes (+20.16%). Guest
block reads rise from 1,832,796,758,528 to 2,202,377,615,872 bytes. Host-staged
peer bytes rise slightly from 7,823,886,336 to 7,866,206,208, and provider H2D
join service rises from 72.73 to 88.06 seconds. V1 remains swap/OOM/storage/
lifecycle clean and keeps the 1-GiB device floors.

## Correctness gate

V1 preserves the exact 24 generated IDs and text, but all 24 per-forward
logit FNV64 digests differ from V0. HMAX7391 on the same nested revision and
eight ordinary layers remains logit-exact with V0, ruling out revision drift.
The divergence is induced by changing ordinary placement from 8 to 16 CUDA
layers. The issue amendment explicitly keeps exact output/logit requirements
fixed, and there is no approved numerical exception. V1 is therefore a
rejected performance-positive/correctness-negative cell and cannot authorize
V2 or `K3_BEST` eligibility.

The analyzer was corrected so production identity includes per-forward logit
digests. [v0-v1-analysis.json](v0-v1-analysis.json) now fails closed with
different logit-inclusive identities. This validation correction is committed
at `004ac29` and protects later repeated acceptance runs.

## V3 early diagnostic

The prior matched normal-prompt CPU control required about 34 minutes of
prefill and decoded at 0.021028 tok/s. The amendment therefore permits a short
prompt for non-decision-driving screening. The committed
`scripts/issue73/v3-short-prompt.txt` plus five generated tokens yielded four
real decode intervals at 0.022048 tok/s, only 7.50% of accepted V0 and 7.22% of
V1. This is far below the amendment's 50% early-stop threshold, so a normal
prompt rerun is not warranted. V3 performed zero expert H2D/hot admissions,
read 608,556,662,784 logical expert bytes, caused 1,124,741,878,272 guest block
bytes and 116,266 major faults, and remained swap/OOM/storage/lifecycle clean.

## Decision

- `OBSERVED`: allocating eight more ordinary layers produces a single-run
  3.81% decode improvement, but loses expert locality, increases expert I/O by
  20.16%, worsens TTFT, and changes every forward's logit digest.
- `REJECTED`: V1 is not an admissible endpoint under the unchanged exact-logit
  contract. V2 is skipped because its gate requires a valid V1.
- `REJECTED`: V3 is an order of magnitude below the GPU-expert paths and does
  not graduate from its explicitly short diagnostic.
- `ACCEPTED`: classify Stage 1 as `ORDINARY_RESIDENCY_NEGATIVE` for the
  currently authorized semantics. Do not implement Stage-2 multi-resident
  placement in issue #73. Return to the original profiling/optimization
  sequence with V0/B0/T4 unchanged.

[selection-summary.json](selection-summary.json) records the decision fields,
[v3-short-analysis.json](v3-short-analysis.json) records the bounded diagnostic,
and [raw-evidence-index.json](raw-evidence-index.json) binds the external raw
evidence and rejected boundary probes.
