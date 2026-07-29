# Phase 4 corpus and closeout-candidate evidence

Status: `OBSERVED` — issue #10 Phase 4 technical capture, replay, and external publication passed on 2026-07-29. Its simulation evidence was regenerated after the initial Checkpoint B review found and rejected an admission-bypassing oracle; fresh Checkpoint B review is still required.

## Corpus coverage

The version-1 corpus defines seed 1, temperature 0, greedy finite-logit argmax, and context 512. Six exact prompts cover constructed prose, code, structured data, technical explanation, narrative, English, and Spanish.

| Prompt | Domain/language | Prefill tokens | Decode cap | F16 CPU observed | MXFP4 CPU observed |
|---|---|---:|---:|---:|---:|
| `prose-en-small` | prose / English | 12 | 16 | 16, cap | 16, cap |
| `code-en-small` | code / English | 16 | 128 | 39, EOG | 39, EOG |
| `structured-en-small` | structured / English | 23 | 16 | 16, cap | 16, cap |
| `technical-en-large` | technical / English | 288 | 16 | 10, EOG | 10, EOG |
| `narrative-en-large` | narrative / English | 309 | 128 | 1, EOG | 1, EOG |
| `narrative-es-large` | narrative / Spanish | 349 | 128 | 3, EOG | 3, EOG |

Both artifacts captured all six CPU cases. `prose-en-small` and `narrative-en-large` also captured CUDA, giving a small-decode and large-prefill parity subset for both artifacts. Every one of the 16 primary traces matched a second identical capture byte-for-byte. Prompt IDs agree across all artifact/backend cases, and CPU/CUDA generated IDs are exact.

CPU/CUDA route coordinates are exact. The small parity traces have identical ordered selected experts. The large prefill traces preserve exact generated output while exposing 16/2163 ordered selected-expert mismatches for F16 and 25/2163 for MXFP4; corresponding set mismatches are 10 and 18. Maximum final-weight differences on records with matching selections are reported descriptively with no invented threshold. Same-backend route/output correctness remains bound by Checkpoint A direct-readback and numerical validation.

## External raw archive

The complete raw corpus is excluded from Git and published to `murillo2000/Kimi-K3-0.40B-GGUF` on branch `phase2-observability-corpus-v1`.

- Base GGUF revision: `88de02cf8fa37f87eb06daaed370ac9c3411d5ca`.
- Immutable corpus revision: `2d838d6b4d0aca4e9af1e7d899e57ad29330c72e`.
- Member path: `phase2-observability/phase2-k3-route-corpus-v1.tar.gz`.
- Size: 323723 bytes.
- SHA-256: `6aa924a6c18bee4e2490f317ced836bcc4740c3ec63e9427a95951e79a649a5f`.
- Contents: 16 traces and four metadata/checksum members.

Two complete local recaptures produced byte-identical archives. The exact remote revision was downloaded and compared byte-for-byte. Hub metadata reports both GGUF sizes and LFS SHA-256 values unchanged from the pinned base.

## Corpus simulation

All 12 CPU traces replayed through all four Phase 3 scenarios under both LRU and Belady/MIN. The committed compact output retains per-tier requests, hits, misses, requested/served bytes, admissions, evictions, final/peak residency, prefill/decode separation, reuse-distance distributions, layer/expert skew, and theoretical-stall summaries.

For the hot-8/cold-24 scenario, F16/MXFP4 LRU backing requests are respectively 119/120 for prose, 244/242 for code, 152/152 for structured data, 113/113 for technical text, 55/55 for English narrative, and 69/70 for Spanish narrative. The exact equal-bundle, future-aware MIN reference always admits the demand and selects a victim only from current residents; it is an offline lower bound, not a production-policy selection. Fixed costs remain illustrative manifest inputs rather than measured latency claims.

## Evidence

- `phase4-corpus-capture.json`: exact prompts, token IDs, traces, checksums, repeatability, parity, and archive members.
- `phase4-corpus-publication.json`: immutable Hub revision, downloaded archive verification, and unchanged GGUF metadata.
- `phase4-corpus-simulations.json`: deterministic replay results for all 12 CPU traces.
- `corpus/phase2/prompts-v1.json`: exact committed prompt definitions and deterministic configuration.

No provider, runtime cache, I/O, prefetch, remapping, residency, or production-policy implementation was added.
