# Phase 1 route-observer evidence

Status: `OBSERVED` — Phase 1 validation passed on 2026-07-29.

## Revisions and artifacts

- Project execution base: `c0ef5d08c6efb8d1f7a08a62109feb1a488c72fa`.
- `llama.cpp` base: `84245db4c790af22135f34992689edcc11877003`.
- `llama.cpp` route-observer commit: `92c4627e19219134ed42e24aa84a1514bf3dffa3`.
- Published GGUF revision: `88de02cf8fa37f87eb06daaed370ac9c3411d5ca`.
- F16 GGUF: 784318432 bytes, SHA-256 `411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7`.
- MXFP4 GGUF: 751976576 bytes, SHA-256 `0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169`.

## Route-observer result

F16/MXFP4 on CPU/CUDA passed all of the following:

- exact Phase 1 prompt and 32 generated token IDs;
- byte-identical repeated traces on the same backend;
- bit-identical logits for trace enabled, trace disabled, repeated, and test-only direct-readback runs;
- exact selected-ID and final-weight equality with the test-only direct tensor readback;
- 252 canonical records spanning routed layers 1–7;
- finite non-negative top-2 weights summing to one within `1e-5`;
- 32 traced ubatches, 224 layer callbacks, 4032 copied bytes, and exactly 32 explicit synchronizations;
- 30 graph reuses in each warm context;
- zero trace callbacks, copies, synchronizations, or failures when disabled;
- explicit rejection of mixed phase (`-2`), missing annotation, and latched sink failure;
- rejection of every incomplete failure trace by the version-1 reader.

The machine-readable result is `phase1-route-validation.json`. The one permitted real test fixture is `../../../../tests/fixtures/phase2/k3-f16-cpu-route-v1.bin`, 19477 bytes with SHA-256 `1952895f05d7778fa9382e86b9dcaddf1549b330fe5aa034c5418479435111da`.

## Numerical parity

The unchanged Phase 1 gate passed all four artifact/backend runs. Full-vocabulary logits were finite, generated IDs were stable, and CPU/CUDA selected-logit comparisons remained within the accepted thresholds. The exact report and logs are under `phase1-numerical/`.

## Trace-disabled overhead

The immutable base and candidate used matching compiler and GGML configurations. Each artifact/backend combination used one warmup per binary and ten context-recreated measured runs in ABBA process order.

| Combination | Metric | One-sided 95% slowdown upper bound | Derived budget | Result |
|---|---:|---:|---:|---:|
| F16 CPU | decode | 0.130231% | 1.377163% | PASS |
| F16 CPU | prompt | 2.133507% | 3.485397% | PASS |
| F16 CUDA | decode | 0.022895% | 0.988906% | PASS |
| F16 CUDA | prompt | 2.460321% | 10.027158% | PASS |
| MXFP4 CPU | decode | 0.308376% | 2.127630% | PASS |
| MXFP4 CPU | prompt | 3.602612% | 10.531247% | PASS |
| MXFP4 CUDA | decode | 0.013883% | 0.988906% | PASS |
| MXFP4 CUDA | prompt | 1.055423% | 2.400604% | PASS |

The refreshed control coefficient of variation is authoritative for the larger derived prompt budgets. The complete samples, paired slowdowns, confidence calculation, binary hashes, and matching build configuration are in `phase1-disabled-overhead.json`.

## Validation commands

```text
cmake --build llama.cpp/build-cpu --target llama -j4
cmake --build llama.cpp/build-cuda --target llama -j4
python3 -m unittest tests.phase2.test_route_trace -v
python3 scripts/phase2/capture_route_observer.py ...
.venv-k3/bin/python scripts/phase2/capture_numerical_parity.py ...
python3 scripts/phase2/measure_disabled_overhead.py ...
git diff --check
git -C llama.cpp diff --check
```

The system Python environment did not provide `pytest`; the same Python tests were executed through `unittest`. `clang-format` was unavailable, so formatting was inspected manually and both diff checks were used.
