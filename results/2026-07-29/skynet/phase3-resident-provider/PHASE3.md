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

`provider-overhead.json` contains the selected raw warmups, all 160 measured process runs, adjacent pair assignments, complete non-gated telemetry on both sides, and 24 independent gated analyses. Both complete corrected attempts are also committed. Each attempt had a different five-token F16 CPU prompt comparison exceed the fixed noise gate; the composed report selects the first passing full ABBA capture independently for each comparison and records source file hashes without altering a raw sample or analysis. All fixed issue #13 gates pass in the composed independent-comparison report.

| Artifact/backend | Comparison | Decode upper/budget | Prompt upper/budget | TTFT upper/budget |
|---|---|---:|---:|---:|
| F16 CPU | base → disabled | 0.090093% / 1.377163% | 2.114660% / 3.485397% | 2.190516% / 3.485397% |
| F16 CPU | disabled → resident | 0.045391% / 1.377163% | 1.363539% / 3.485397% | 1.430330% / 3.485397% |
| F16 CUDA | base → disabled | 0.068484% / 0.988906% | -0.125802% / 10.027158% | -0.123317% / 10.027158% |
| F16 CUDA | disabled → resident | 0.068374% / 0.988906% | 0.290797% / 10.027158% | 0.289900% / 10.027158% |
| MXFP4 CPU | base → disabled | 0.265514% / 2.127630% | 0.210516% / 10.531247% | 0.177412% / 10.531247% |
| MXFP4 CPU | disabled → resident | -0.007106% / 2.127630% | 1.864607% / 10.531247% | 1.923751% / 10.531247% |
| MXFP4 CUDA | base → disabled | 0.093985% / 0.988906% | -0.064509% / 2.400604% | -0.061868% / 2.400604% |
| MXFP4 CUDA | disabled → resident | 0.043793% / 0.988906% | 0.315100% / 2.400604% | 0.332806% / 2.400604% |

Each bound is the paired mean slowdown plus the one-sided 95% Student-t critical value with 9 degrees of freedom. Negative slowdowns, where present in raw pairs, mean the candidate observation was faster; they are not clamped.

## Review and scope

Checkpoint A first found a material failed-binding graph-reuse defect. The bounded correction was reviewed again at project head `0a16a7e4b0e383ea43706d740abc19924c82cdf5` and nested head `d9d20e1b616a25ba5d0ec8ad12ef408a83ae227b`; the fresh verdict was `PASS_WITH_NOTES`, safety `YES` ([issue comment](https://github.com/murillo128/k3-out-of-core/issues/13#issuecomment-5124005466)).

The first Checkpoint B review accepted the runtime, parity, lifecycle, scope, lineage, and independent gate calculations but found that the old pinned-base probe omitted required non-gated telemetry. The correction adds a provider-free baseline telemetry probe, explicit unavailable provider counters, strict raw-key verification, two focused verifier cases, and fresh ABBA captures. The failed review and both corrected raw capture attempts remain preserved.

No cache, storage transport, prefetch, physical slot, I/O, residency change, or backend-kernel provider logic is present. The tiny fixtures validate the integration seam and lifecycle only; they do not validate full-size or out-of-core performance.
