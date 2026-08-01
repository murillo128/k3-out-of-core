# Phase 9 — Cache-policy framework and trace-driven selection

Status: **OBSERVED — final-review candidate**

This is a derived human-readable summary of issue #30 and PR #31. The authoritative machine-readable record is `phase9-manifest.json`, SHA-256 `67854066235d134984d749d8956191a6c13d09e1cc10a9805687abe299121b76`. The manifest and the later separate final-review attestation take precedence.

## Scope and revisions

- Execution profile: `STANDARD`
- Project execution base: `17a4e5be38a4820984a7bd4d3082695d8822c9ba`
- Implementation evidence head: `587a48560cbd2f80d0dd8513ad7794442580e63c`
- Nested execution base: `dc4d50c68378d908131b518662160fdd08f4e005`
- Final nested `llama.cpp` / gitlink: `fd29c0f9e868e838d3641cd13eb6ceb8c1535f01`
- Checkpoint A: comment `5148012128`, `PASS`, safety `YES`
- Checkpoint B: comment `5148752231`, `PASS`, safety `YES`
- Checkpoint C: comment `5149625334`, `PASS`, safety `YES`
- Final complete-PR review: pending

Phase 9 adds copied versioned hot/cold policy configuration, bounded model-owned policy state, canonical events, exact LRU plus deterministic LFRU/SLRU/LFU-aging candidates, optional frequency-gated hot admission, hard global/per-layer domains, byte-aware planning, native replay, and an independent Python simulator. Policy owns no slot, byte, generation, reference, publication, transport, or miss-execution mechanism.

## Selected defaults

Exact global LRU with `ALWAYS` admission is retained for both null hot and cold configurations. No semantic default switch was made. Explicit valid configurations remain available, and per-layer policy remains explicit-only.

No non-LRU pair passed every frozen gate:

- hot LFRU / cold LRU improved aggregate median token time by only about 0.0035%; its paired 95% interval included zero and worst policy CPU was about 2.13%;
- global SLRU for both tiers regressed aggregate median token time by about 1.04%, included an approximately 8% Qwen regression, and exceeded the policy-CPU gate; and
- hot SLRU / cold LFRU regressed aggregate median token time by about 0.61%, included an approximately 8.27% Qwen regression, and exceeded the policy-CPU gate.

The final statistics evidence binds 470 exact-identity artifacts: 8 screening runs, 22 warmups, and 440 measured runs across 11 comparisons. All selected-default correctness identities pass.

## Working sets, residency, and budgets

The corrected full-K3 MXFP4 top-16 one-token expert working set is exactly 25,829,572,608 bytes. Five safe exact-layout cells were fully resident with zero major faults and zero swap growth; no paging cliff was observed within the declared safe ceiling. This is physical-memory/residency evidence, not full-model inference, model quality, or physical-media throughput evidence.

Scoped cold-budget recommendations are:

| Evidence scope | Bytes | Boundary |
|---|---:|---:|
| Tiny K3 F16, two-token `CPU_FALLBACK` boundary workload | 44,040,192 | 4.0 W |
| Tiny K3 MXFP4, two-token `CPU_FALLBACK` boundary workload | 11,698,176 | 4.0 W |
| Accepted Qwen1.5-MoE F16 one-token bootstrap only | 1,678,245,888 | W + one slot |
| Full-K3 MXFP4 exact-layout residency only | 25,829,572,608 | 1.0 W |

These values preserve the recorded host headroom and are not installed as runtime auto-sizing defaults. The Qwen result is not a long-decode recommendation.

The independent WASTE replay reproduces its pinned sampled LRU/LFRU semantics without importing source. It corroborates the need to distinguish logical hits from physical residency and to preserve OS/runtime headroom, but WASTE's cache floor is not transferred across its custom 3-bit CPU/UMA representation, Apple hardware, record format, transport, and execution backend.

## Default equivalence and validation

The explicit-LRU versus null-default matrix contains 36 repeated 20-token runs in nine groups: original/split tiny F16 and MXFP4 in hot and cold modes, plus accepted Qwen cold mode. Every group has exact output identity and resolves to global LRU/ALWAYS with configuration digest `4472354929901784386`.

| Suite | Result |
|---|---:|
| Focused CPU CTest | 5/5 pass |
| Focused CUDA CTest | 5/5 pass |
| Fresh ASan+UBSan CPU CTest | 5/5 pass |
| Fresh ASLR-disabled TSan CPU CTest | 5/5 pass |
| Phase 2 evidence tests | 20/20 pass |
| Phase 8 evidence tests | 32/32 pass |
| Phase 9 evidence tests | 14/14 pass |
| Immutable accepted-head Phase 8 strict verifier | pass |
| Project/nested diff, gitlink, and clean-tree gates | pass |

Default-ASLR TSan fails before test code with the accepted `unexpected memory mapping` limitation; the ASLR-disabled native run with the established GCC/OpenMP instrumentation option is authoritative. Running the Phase 8 strict verifier at the Phase 9 head is an expected failure because Phase 9 legitimately changes the nested revision and files outside Phase 8's immutable closeout allowlist; the exact accepted Phase 8 project/nested heads re-verify successfully.

## Evidence inventory

| File | SHA-256 |
|---|---|
| `phase9-replay-online-checkpoint.json` | `be4b4697bf8401d4671a3778bb412e70a8e1b5851a66cbed4e8c8ed82b2d134c` |
| `working-sets.json` | `9f419a8a26f6f615df90551ab600df3d7a5eeca54f8aca433f047ca0ad6696c8` |
| `residency-sweep.json` | `fd4ce332ec4340fb4cd9bc192844f1373fd21e0b2b3169c6854c86d9888728a1` |
| `waste-comparison.json` | `b14310ec94453083455cca00ba2c71695fb510cda0ce634bc049f9a4b746428a` |
| `policy-statistics.json` | `cbc9163810863a915797c23c2bd12a529bfef863eebc0ab378a148ef3ed765f8` |
| `prefill-protection.json` | `9859346a2d69ad228fe443bdccae2d4669fc2ce7c0b2c503d4fad0666c1b97e0` |
| `policy-cpu-benchmark.json` | `405ff5d746ec9564dca3e55602cba2d951ede0ed2b05418c6c77f97cd8c84a3b` |
| `online-boundaries.json` | `12f7196a59c3226fdbb31de59d03a1533fa636f093a211a98ff5ec50c0afa9ff` |
| `transport-sensitivity.json` | `fc2c592aab2ea0f23fd83d8bc57843e7e262e94489fb2dc428669fbe1276ebbb` |
| `selection.json` | `fe1775a3ba5a1c2f70da173a0ce0ef98463b0bac47a2bd6604f04c271c3f905a` |
| `default-equivalence.json` | `ba094524256ec49419a6947187433c849420bdc72f009cf8d1b0562a5bf9acfd` |
| `validation.json` | `194537b6a8e68bd3ffc7112d142bd3ac7a5116eb9b33728739caa3944253a606` |

## Deferred

Phase 10 owns speculative prefetch and hot-set seeding. Multi-request fairness, UMA, multi-GPU, GDS, new expert formats, full production K3 inference/quality, and adaptive online policy remain out of scope.
