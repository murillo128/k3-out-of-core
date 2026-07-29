# Phase 3 offline cache-simulation evidence

Status: `OBSERVED` — issue #10 Phase 3 implementation and validation passed on 2026-07-29.

## Scope and semantics

The project-side simulator imports neither GGML nor CUDA. It converts each selected `(layer, expert_id)` in a completed route trace into one request for the storage map's atomic gate/up/down bundle, preserving canonical record and consumed top-k order.

The initial hot/cold hierarchy is inclusive: every hot resident is also cold-resident, and a cold eviction invalidates the matching hot entry. LRU is explicitly a deterministic test baseline, not a production-policy decision. The perfect-future Belady/MIN result is explicitly an offline lower bound and is accepted only when all referenced bundles are equal-sized. Both pinned K3 maps meet that condition: every F16 bundle is 786432 bytes and every MXFP4 bundle is 208896 bytes. Unequal-size synthetic fixtures exercise byte-constrained LRU and explicitly reject a false exact-MIN claim.

Requests cascade from hot to cold to backing store. Per-tier requested and served bytes, hits, misses, admissions, evictions, reuse distance, layer/expert skew, and final/peak residency are deterministic. Cache state carries from prefill into decode while every metric is also attributed separately by phase.

The theoretical cost model is supplied by the versioned simulation manifest. Its fixed latency and bandwidth values are illustrative inputs, not measured `skynet` constants or production-latency predictions. Version 1 uses explicit serial, no-overlap accounting.

## Hand-checkable validation

Reference cases cover capacity zero, capacity one, below the working set, the exact working set, and a full hot working set. An `A B C A B C` trace with capacity two produces six LRU backing requests and four Belady/MIN backing requests. The distinguishing `A B A` capacity-one case produces three MIN misses and three admissions, proving that the demanded bundle is admitted and the replacement victim is selected only from current residents. Unequal 2/3/2-byte objects under a four-byte capacity validate multiple-eviction accounting. A two-request prefill/decode case validates phase carry-over, requested/served bytes, and exact supplied-cost arithmetic.

All 20 Phase 2 unit tests passed, including eight simulator tests plus dependency inspection. Both version-1 JSON schemas passed Draft 2020-12 validation.

## Real-trace reference replay

The committed F16 CPU fixture contains 252 route records and 504 atomic expert requests: 70 prefill and 434 decode. It references 53 of the 56 mapped expert bundles. Input identities are checksum-bound in the output:

- trace SHA-256: `1952895f05d7778fa9382e86b9dcaddf1549b330fe5aa034c5418479435111da`;
- F16 storage-map SHA-256: `42af6acd1d1f2ea77aaa9750b28141670ce69009ba06b5ddca3f42c696b51151`;
- simulation-manifest SHA-256: `a08d803db9eeac54aff099eab27a4d3a66fec3eb657be65c469a74aa6e606228`;
- deterministic output SHA-256: `ca4ed1c7a7f2d87caf17e1144c22124795e3607e1f187c64d203f02d4dd8f9de`.

| Scenario | Policy | Hot hits | Cold hits | Backing requests | Cold evictions |
|---|---|---:|---:|---:|---:|
| No cache | LRU | 0 | 0 | 504 | 0 |
| One expert | LRU | 5 | 0 | 499 | 498 |
| One expert | Belady/MIN | 5 | 0 | 499 | 498 |
| Hot 8 / cold 24 | LRU | 34 | 239 | 231 | 207 |
| Hot 8 / cold 24 | Belady/MIN | 212 | 166 | 126 | 102 |
| Full working set | LRU | 451 | 0 | 53 | 0 |
| Full working set | Belady/MIN | 451 | 0 | 53 | 0 |

The real trace's finite distinct-bundle reuse distance has p50 21, p95 43, and p99 49. These tiny-fixture results demonstrate deterministic accounting only; they do not select a production policy or predict full-size K3 behavior.

## Validation commands

```text
python3 -m unittest discover -s tests/phase2 -p 'test_*.py' -v
python3 scripts/phase2/run_cache_simulation.py --trace ... --storage-map ... --manifest ... --output ...
Draft202012Validator(...).validate(...)
cmp committed-output independently-regenerated-output
git diff --check
git -C llama.cpp diff --check
```
