# Phase 3 resident-provider evidence

Status: `OBSERVED` corrective prerequisites pass; post-optimization standing capture pending.

This directory contains the committed issue #13 evidence for project base `81df862da6e4ff9db005f6265470070bb5456f4c`, nested base `4daaaa1a4dd26d6465f84891b854b5f7ddc03020`, corrective nested base `523f825d2df5efa7c9a08561e2b64861ad5594c5`, and optimized candidate `a120de8e2d0b552c51eacd7d701ef1dd994bc3db`. It reuses the Phase 2 manifest and published corpus revision `2d838d6b4d0aca4e9af1e7d899e57ad29330c72e` without modifying or republishing the raw corpus.

## Corrective administrative fast path

Design-authority comment `5127774849` governs this bounded correction. `provider-parity-post-optimization.json`, `lifecycle-and-failures-post-optimization.json`, `provider-admin-fast-path.json`, and `corrective-prerequisites.json` all pass at published project evidence commit `3532655f9c11bac214269d5ab75f8dabf0f3c004`.

The focused diagnostic executes the same F16 CPU work against corrective base and candidate: two contexts, two nonempty ubatches, 154 binds, and 7 routed layers. The base records 14 acquisitions/releases. The candidate records 2, exactly one per ubatch, plus 7 registrations/full validations, 147 fast-path hits, and binding-vector capacity 8 in both contexts. The repeated CPU/CUDA F16/MXFP4 parity and lifecycle/failure matrices pass, including 20 CPU cycles, 10 CUDA cycles, concurrent registry publication, cached-path fault injection, binding retry, empty bindings, asynchronous destruction, and ASan/UBSan.

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

## Historical performance

`provider-overhead.json` is the single complete standing capture approved prospectively in issue comment `5127588494`. It contains all raw warmups, 160 measured process runs, adjacent pair assignments, complete non-gated telemetry on both sides, and 24 gated analyses. Its result stands without retry, pooling, or cross-attempt selection: 21 cells pass and 3 fail, so the Phase 3 performance gate is not satisfied.

The two earlier complete corrected attempts remain committed as historical failed evidence. The former composed report and its selection tool were rejected by independent review and are not used by the standing result.

Those three files are immutable, non-authoritative history for the corrective disposition. Exactly one complete v2 capture, `provider-overhead-post-optimization.json`, is approved under rule `single-complete-post-optimization-capture-v2`. It preserves the original budgets and protocol and has not started at this handoff.

| Artifact/backend | Comparison | Decode upper/budget | Prompt upper/budget | TTFT upper/budget | Result |
|---|---|---:|---:|---:|---|
| F16 CPU | base → disabled | 0.141563% / 1.377163% | 0.560787% / 3.485397% | 0.647356% / 3.485397% | PASS |
| F16 CPU | disabled → resident | 0.074206% / 1.377163% | 5.019635% / 3.485397% | 5.630880% / 3.485397% | FAIL |
| F16 CUDA | base → disabled | 0.022837% / 0.988906% | 0.529993% / 10.027158% | 0.538483% / 10.027158% | PASS |
| F16 CUDA | disabled → resident | 0.031008% / 0.988906% | 0.632931% / 10.027158% | 0.641046% / 10.027158% | PASS |
| MXFP4 CPU | base → disabled | 0.035942% / 2.127630% | 2.471109% / 10.531247% | 2.388641% / 10.531247% | PASS |
| MXFP4 CPU | disabled → resident | 0.076023% / 2.127630% | 2.495478% / 10.531247% | 2.582258% / 10.531247% | PASS |
| MXFP4 CUDA | base → disabled | 0.232094% / 0.988906% | 0.351071% / 2.400604% | 0.354338% / 2.400604% | PASS |
| MXFP4 CUDA | disabled → resident | 0.144048% / 0.988906% | 2.153824% / 2.400604% | 2.411496% / 2.400604% | FAIL |

Each bound is the paired mean slowdown plus the one-sided 95% Student-t critical value with 9 degrees of freedom. Negative slowdowns, where present in raw pairs, mean the candidate observation was faster; they are not clamped.

## Review and scope

Checkpoint A first found a material failed-binding graph-reuse defect. The bounded correction was reviewed again at project head `0a16a7e4b0e383ea43706d740abc19924c82cdf5` and nested head `d9d20e1b616a25ba5d0ec8ad12ef408a83ae227b`; the fresh verdict was `PASS_WITH_NOTES`, safety `YES` ([issue comment](https://github.com/murillo128/k3-out-of-core/issues/13#issuecomment-5124005466)).

The first Checkpoint B review accepted the runtime, parity, lifecycle, scope, lineage, and independent gate calculations but found that the old pinned-base probe omitted required non-gated telemetry. A correction added a provider-free baseline telemetry probe, explicit unavailable provider counters, and strict raw-key verification. A fresh re-review then rejected outcome-conditioned selection across two corrected attempts. The repository owner approved one prospective standing capture, the verifier was hardened before execution, and that capture failed the performance gate. Checkpoint B and final review therefore remain pending.

No cache, storage transport, prefetch, physical slot, I/O, residency change, or backend-kernel provider logic is present. The tiny fixtures validate the integration seam and lifecycle only; they do not validate full-size or out-of-core performance.
