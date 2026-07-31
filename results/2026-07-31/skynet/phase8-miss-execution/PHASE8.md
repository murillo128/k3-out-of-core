# Phase 8 — Discrete-GPU Miss Execution

This is a derived human-readable summary of issue #26 and PR #27. The authoritative machine-readable record is `phase8-manifest.json`; its exact revisions, checksums, gates, `closeout_state`, and `final_review` object take precedence.

## Scope and revisions

- Profile: `STANDARD`
- Branch: `codex/phase8-miss-execution`
- Project execution base: `5fe0bda6965da7d2b0f85dd14b97427a7b60f161`
- Phase 8.4 standing-evidence capture: `eb3d0093157da7757036882dc81b37dd622bbf46`
- Final nested `llama.cpp` / gitlink: `dc4d50c68378d908131b518662160fdd08f4e005`
- Checkpoint A: comment `5141694340`, `PASS`, safety `YES`
- Checkpoint B: comment `5144721775`, `PASS`, safety `YES`
- Checkpoint C: comment `5146173479`, `PASS_WITH_NOTES`, safety `YES`, no required delta

Phase 8 retains `PROMOTE_AND_GPU` as the stable default. `CPU_FALLBACK` and deterministic AUTO v1 are explicit. AUTO uses only a versioned caller snapshot, fails closed to GPU for missing or invalid operands, and chooses GPU on ties. Background promotion is explicit and default-off. The runtime does not learn, explore, or change policy online.

## Standing results

- The fail-closed production-path Checkpoint B probe passes all 25 cases.
- Original and generated 218-part split K3 F16/MXFP4 cases each pass ten cold processes and two warm captures with the complete native policy matrix.
- The authorized larger public MoE F16 case passes bootstrap and policy coverage at immutable Qwen source revision `ec052fda178e241c7c443468d2fa1db6618996be`.
- Descriptor-only PP/TG discovery reports two graphs, zero scheduler reserve calls, and zero backend allocation before bounded hot/cold/ring initialization and final `COLD_CACHE` reservation.
- Ten controlled hybrid runs record positive CPU work, positive GPU work, and positive overlap: 562 us minimum, 722 us p50, and 802 us p95/p99.
- The deterministic 300-cell matrix covers five policy/regime identities across decode/prefill, three hot ratios, two reuse classes, and background promotion off/on. CPU-favorable, GPU-favorable, and tie checks pass.
- The exact-layout full-K3 MXFP4 sparse store is 1,446,456,066,048 logical bytes with 167,936 allocated bytes. Its layout is derived from immutable `moonshotai/Kimi-K3` config revision `9f62e4e9fffbd0a83ddd60e1c209d828994b3569` and deterministic sampled spans pass.
- Device-wide telemetry records 3,661 MiB peak used and 242 MiB minimum free.

The exact-size sparse workload is controlled mechanism/crossover evidence. It is not full-model inference, a model-quality result, or a physical-media throughput measurement. The Qwen case is a bootstrap-scale public-MoE path check, not a K3 quality or performance proxy. The OS page cache was not flushed.

## Validation

- Focused CPU suite: 5/5
- Focused CUDA suite: 5/5
- ASan+UBSan suite: 5/5
- Accepted ASLR-disabled TSan suite: 5/5
- Phase 8 evidence unit suite: 32/32
- Immutable Phase 7 authoritative verifier: pass
- Project and nested scope/diff checks: pass
- Nested worktree and exact project gitlink: clean and equal

Default-ASLR TSan may fail before test code because of the accepted shadow-memory mapping limitation; the ASLR-disabled native suite is authoritative. Checkpoint C carries the unchanged callback-free test-fixture teardown race as a non-material note; the corrected production/model paths pass the required sanitizer, lifetime, and cleanup coverage.

## Evidence files

| File | SHA-256 | Purpose |
|---|---|---|
| `checkpoint-b-probe.json` | `2598f3194e1c79c4b73a5825e2c706bca6f3bd8f4b4046351032d969a945f335` | 25-case fail-closed production-path probe |
| `miss-policy-parity.json` | `6f36f4374c1fd14cafd0ef0171774a1ec953d695faa2e85b0b1f02608e4a3732` | K3 and larger public MoE parity, bootstrap, repetitions, VRAM |
| `hybrid-overlap.json` | `c86d1da7f627a6569bffadc5a7caafa8257fe818c74d8b098b9cf8810a0363b3` | Controlled mixed CPU/GPU overlap |
| `miss-policy-benchmarks.json` | `a7284cf84c623e6cf8720a593cff59e9a2bd46187956069600008c6cf7da439d` | 300-cell deterministic policy matrix and tails |
| `synthetic-store.json` | `74e3230b37a23b1c1b0259f663d3f24cb9fb55c06ac650fda12f25f16826c7bc` | Full-K3 exact-layout sparse-store descriptor |
| `validation-results.json` | `6231eb75788deb6931660c86945e3147edbb1884f6b7453f9014635073734ffa` | Native, sanitizer, regression, lifetime, cleanup, and diff validation |

## Deferred

Phase 9 cache-policy selection, Phase 10 speculative prefetch, multi-request fairness, UMA, multi-GPU, GDS, and full production K3 quality/performance remain out of scope.
