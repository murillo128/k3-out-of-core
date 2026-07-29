# Phase 3 resident-provider evidence

Status: `OBSERVED` pass candidate for Checkpoint B.

This directory contains the committed issue #13 evidence for project base `81df862da6e4ff9db005f6265470070bb5456f4c`, nested base `4daaaa1a4dd26d6465f84891b854b5f7ddc03020`, and candidate nested head `523f825d2df5efa7c9a08561e2b64861ad5594c5`. It reuses the Phase 2 manifest and published corpus revision `2d838d6b4d0aca4e9af1e7d899e57ad29330c72e` without modifying or republishing the raw corpus.

## Correctness and structure

`provider-parity.json` records four passing artifact/backend combinations. Every combination has exact prompt and 32-token generated IDs, full-logit equality between same-backend paths, exact 252-record route traces, stable graph reuse, and identical disabled/resident graph operation hashes and node counts.

| Artifact | Backend | GGML nodes | Reused graphs | Resident bindings | Resident leases |
|---|---|---:|---:|---:|---:|
| F16 | CPU | 1234 | 30 | 7 | 224/224 released |
| F16 | CUDA | 1238 | 30 | 7 | 224/224 released |
| MXFP4 | CPU | 1234 | 30 | 7 | 224/224 released |
| MXFP4 | CUDA | 1238 | 30 | 7 | 224/224 released |

Every disabled provider counter is zero. The resident path records no provider allocation, callback, tensor copy, or synchronization. CPU and CUDA retain their existing kernels and physical placement; the provider adds no GGML node and does not alter logical IDs, routing weights, or ordered reduction.

## Lifecycle and failure behavior

`lifecycle-and-failures.json` records:

- 20 passing CPU and 10 passing CUDA model load/decode/unload cycles, alternating F16 and MXFP4;
- shared resident-model contexts, interleaved contexts, and mixed-mode F16/MXFP4 models in one process;
- graph-binding, plan-allocation, preparation, cancellation, descriptor/key, and partial-initialization failures;
- CPU abort while leases are held, asynchronous context destruction, and backend recreation;
- exact acquired/released lease balance; and
- a passing focused ASan/UBSan run.

## Performance

`provider-overhead.json` contains raw warmups, all 160 measured process runs, adjacent pair assignments, non-gated telemetry, and 24 independent gated analyses. All fixed issue #13 gates pass.

| Artifact/backend | Comparison | Decode upper/budget | Prompt upper/budget | TTFT upper/budget |
|---|---|---:|---:|---:|
| F16 CPU | base → disabled | 0.488352% / 1.377163% | 1.369634% / 3.485397% | 1.452182% / 3.485397% |
| F16 CPU | disabled → resident | 0.051256% / 1.377163% | 1.539688% / 3.485397% | 1.633756% / 3.485397% |
| F16 CUDA | base → disabled | 0.214561% / 0.988906% | 0.695666% / 10.027158% | 0.702803% / 10.027158% |
| F16 CUDA | disabled → resident | 0.177365% / 0.988906% | 0.795167% / 10.027158% | 0.808911% / 10.027158% |
| MXFP4 CPU | base → disabled | 0.861980% / 2.127630% | 5.952744% / 10.531247% | 6.687194% / 10.531247% |
| MXFP4 CPU | disabled → resident | 0.609795% / 2.127630% | 3.199574% / 10.531247% | 3.405738% / 10.531247% |
| MXFP4 CUDA | base → disabled | 0.158636% / 0.988906% | 0.101331% / 2.400604% | 0.105271% / 2.400604% |
| MXFP4 CUDA | disabled → resident | 0.056709% / 0.988906% | 0.121881% / 2.400604% | 0.123395% / 2.400604% |

Each bound is the paired mean slowdown plus the one-sided 95% Student-t critical value with 9 degrees of freedom. Negative slowdowns, where present in raw pairs, mean the candidate observation was faster; they are not clamped.

## Review and scope

Checkpoint A first found a material failed-binding graph-reuse defect. The bounded correction was reviewed again at project head `0a16a7e4b0e383ea43706d740abc19924c82cdf5` and nested head `d9d20e1b616a25ba5d0ec8ad12ef408a83ae227b`; the fresh verdict was `PASS_WITH_NOTES`, safety `YES` ([issue comment](https://github.com/murillo128/k3-out-of-core/issues/13#issuecomment-5124005466)).

No cache, storage transport, prefetch, physical slot, I/O, residency change, or backend-kernel provider logic is present. The tiny fixtures validate the integration seam and lifecycle only; they do not validate full-size or out-of-core performance.
