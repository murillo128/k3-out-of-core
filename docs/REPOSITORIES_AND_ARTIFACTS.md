# Repositories and Published Artifacts

This document records the repositories, pinned revisions, and published binary artifacts used by the K3 out-of-core project.

It is intended to let a new ChatGPT, Codex, or human session reconstruct the current baseline without relying on chat history.

## Project repository

- Repository: <https://github.com/murillo128/k3-out-of-core>
- Default branch: `main`
- Clean Phase 1 execution base: `511e87fc98cca8069fc57526fbb04b10789967eb`
- Clean Phase 1 branch: `codex/phase1-closeout-clean`
- Execution contract: GitHub issue #7, `STANDARD` profile

This repository is the source of truth for architecture, planning, validation requirements, and the exact `llama.cpp` submodule revision.

## K3-capable llama.cpp fork

- Repository: <https://github.com/murillo128/llama.cpp>
- Development branch: `k3/out-of-core`
- Phase 1 base: `84245db4c790af22135f34992689edcc11877003`
- Phase 2 input / Phase 3 execution base: `4daaaa1a4dd26d6465f84891b854b5f7ddc03020`
- Phase 3 resident-provider corrective base: `523f825d2df5efa7c9a08561e2b64861ad5594c5`
- Phase 3 optimized resident-provider head: `a120de8e2d0b552c51eacd7d701ef1dd994bc3db`
- Phase 3 branch: `codex/phase3-resident-provider`
- Phase 4 mechanism head reviewed at Checkpoint A: `8ededcb548b0d9dc6248d6ba490aecedca576bec`
- Phase 4 evidence-probe candidate: `57fe1eabbe3d0ced59096a0744efc91e286fb1c7`
- Phase 4 branch: `codex/phase4-hot-cache`
- Phase 5 Checkpoint A corrected head: `5ffed360965a1de7e2d788b8637a470183d27165`
- Phase 5 evidence-probe candidate: `26317ee1d848dd7a73f22a3666a055cad5d5cb03`
- Phase 5 branch: `codex/phase5-cold-cache`
- Phase 6 accepted storage head: `7a606dd4e11a108929f799253809a904f55feae4`
- Phase 6 branch: `codex/phase6-gguf-storage`
- Phase 7 accepted asynchronous-runtime head: `b71e40f91b1a0dab578d56ac733211453704d674`
- Phase 7 branch: `codex/phase7-async-runtime`
- Phase 8 accepted miss-execution head: `dc4d50c68378d908131b518662160fdd08f4e005`
- Phase 8 branch: `codex/phase8-miss-execution`
- Phase 9 selected-policy evidence head: `75a4ecc0fa2249e3c0c4163dd3b692c7ebf705e0`
- Phase 9 retained-default evidence head: `fd29c0f9e868e838d3641cd13eb6ceb8c1535f01`
- Phase 9 branch: `codex/phase9-cache-policy`

The `llama.cpp` gitlink in the Phase 3 review branch points to the Phase 3 resident-provider head. The Phase 1 and Phase 2 revisions remain immutable validation inputs.

Relevant behavior in this revision:

- Kimi-K3 text architecture support;
- lossless repacking of routed expert weights into GGML MXFP4;
- explicit recognition of the 35 resident packed MoE tensors in the tiny MXFP4 fixture;
- lazy dequantization of those resident tensors to F16;
- rejection of unknown packed MXFP4 tensors.

Phase 3 adds the model-owned resident expert-weight provider, typed logical/execution ID seam, request-scoped RAII leases across asynchronous submission, structural counters, and focused CPU/CUDA lifecycle tests. It does not add a cache, storage transport, prefetch, physical slots, or any change to expert residency. No GGUF or corpus artifact was republished.

Phase 4 adds one model-owned fixed-address CUDA hot pool, a preallocated transactional host directory, graph-local execution-ID remapping at the existing synchronized scheduler boundary, request-generation leases and pins, deterministic LRU validation policy, trim/surrender, and bounded diagnostics. Source routed experts remain host-resident. It does not add cold storage, demand GGUF reads, prefetch, asynchronous transport, multi-GPU, UMA, or concurrent cached submissions. No GGUF or corpus artifact was republished.

Phase 5 adds one model-owned byte-budgeted pageable cold arena, generation-checked inclusive cold/hot references, a separately bounded native pinned transfer ring with explicit pageable synchronous fallback, and source-to-cold-to-ring-to-hot promotion in bounded waves. The monolithic source remains resident and must be pageable. It does not add GGUF demand reads, dedicated streams/events, H2D/compute overlap, CPU miss execution, prefetch, multi-GPU, UMA, or concurrent cached submissions. No GGUF or corpus artifact was republished.

Phase 6 adds immutable split-aware GGUF source identity and spans, model-owned positional handles, deferred routed tensors, synchronous storage-to-cold population, atomic integrity-checked publication, deterministic eviction/reread, and cancellation/retry. Phase 7 adds the model-owned bounded asynchronous transport and scheduler, direct-I/O opt-in with visible fallback, priority and single-flight state, dedicated native transfer events, exact-generation cancellation/unload drain, FlightId-based overlap accounting, and cached-only physical remap placement. Phase 8 adds explicit miss-execution policy selection, CPU fallback, mixed CPU/GPU execution, canonical completion, deterministic explicit AUTO evaluation, bounded optional background promotion, complete production-path probes, and descriptor-only bootstrap sizing before the final bounded scheduler reservation. Phase 9 adds copied versioned cache-policy configuration, separate bounded hot/cold policy ownership, canonical events, exact LRU plus deterministic LFRU/SLRU/LFU-aging and optional hot admission, global/per-layer domains, native and independent replay, and physical-residency evidence. It retains global LRU/ALWAYS as the null default. It does not add speculative prefetch, runtime auto-sizing, adaptive policy, multi-request concurrency, multi-GPU, UMA, or a new expert format.

The Phase 7 final-review-candidate evidence is under `results/2026-07-31/skynet/phase7-async-runtime/`. Its authoritative record is `phase7-manifest.json` (`phase7-manifest-v1`), with bounded inputs in `checkpoint-b-final-correction.json`, `checkpoint-b-placement-correction.json`, `runtime-matrix.json`, and `validation-results.json`. The generated 218-part F16/MXFP4 splits are local reproducibility inputs whose complete path, size, SHA-256, source-model identity, and exact nested tool head are recorded in `runtime-matrix.json`; no split binary is committed or published.

The Phase 8 final-review-candidate evidence is under `results/2026-07-31/skynet/phase8-miss-execution/`. Its authoritative record is `phase8-manifest.json` (`phase8-manifest-v1`), binding Checkpoints A, B, and C; the exact project capture/evidence heads and nested gitlink; the six evidence inputs; all closeout scripts, schemas, tests, and source-of-truth documents; and the mandatory final-review attestation. No model, split, full-size store, or generated binary is committed or published.

The Phase 9 frozen selection evidence is under `results/2026-07-31/skynet/phase9-cache-policy/`. It binds immutable Phase 2 replay, online agreement, exact working sets, residency and transport sweeps, WASTE normalization, prefill protection, 470-artifact final statistics, policy CPU cost, and the fixed-rule global LRU retention. The non-circular `phase9-manifest.json` becomes the authoritative technical record after Phase 9.5 closeout; it binds the exact implementation evidence head and artifact identities without containing its own hash or final-review attestation. No model, split, sparse payload, raw benchmark binary, or complete full-K3 checkpoint is committed or published.

Phase 8 uses `Qwen/Qwen1.5-MoE-A2.7B-Chat` only as the authorized larger public MoE F16 bootstrap input. The immutable source revision is `ec052fda178e241c7c443468d2fa1db6618996be`. The local config is 920 bytes with SHA-256 `b3e9fc1ad643d12f6536dee80a66c5ea045b571856165a598252720b6f2c553e`; the local tokenizer is 7,028,015 bytes with SHA-256 `f7c9b2dba4a296b1aa76c16a34b8225c0c118978400d4bb66bff0902d702f5b8`. It was converted by `convert_hf_to_gguf.py` at nested head `a885ff7750a4e73901b7f378e7dc45880a7d1536` to a local 28,644,015,616-byte F16 GGUF with SHA-256 `fbdb28cef42831732a3e2193a70b1f50840d26d894d88bc8617c24d34d72867f`, then exercised at runtime head `dc4d50c68378d908131b518662160fdd08f4e005`. The manifest records the exact paths, command, revisions, sizes, and checksums.

Phase 8 also derives an exact-layout full-K3 MXFP4 descriptor from `moonshotai/Kimi-K3` revision `9f62e4e9fffbd0a83ddd60e1c209d828994b3569`; its 7,006-byte config has SHA-256 `9710e121a58d03ac92c8d6da287a19541994319afbbe6d6202af001ffd379213`. The generated local sparse store has logical size 1,446,456,066,048 bytes and 167,936 allocated bytes. Its deterministic seed, dimensions, offsets, and sampled-span SHA-256 values are recorded in `synthetic-store.json`; the sparse payload itself is disposable and is not a published full checkpoint.

The Phase 4 standing evidence is under `results/2026-07-30/skynet/phase4-hot-cache/`. `hot-cache-parity.json` has SHA-256 `d11ff31d762ed0ebcfb8b3a940b8ceb78925386e4e8c925c7070912d96bab4fb`; `lifecycle-and-failures.json` has SHA-256 `f39ebf5e2512377960d299e948e1fb21d65e3b52d1b99b9bdf75877c8f715d1a`. These artifacts reuse the existing immutable F16/MXFP4 GGUFs and Phase 3 manifest; no binary model artifact changed.

The original standing Phase 3 capture approved in issue comment `5127588494` failed 3 of 24 gated metric cells and remains immutable history. The one post-optimization v2 capture authorized by comment `5127774849` is published at project evidence commit `93635d7ece8fdc617291d5a036bda1c8bc2b6c77` as `results/2026-07-29/skynet/phase3-resident-provider/provider-overhead-post-optimization.json`, SHA-256 `23eff115b87a9e8cee101bd1c0b02f299786175e786b4b30dd4a7e66617d4970`. It passes 22/24 cells and remains a raw performance-gate failure. Comments `5128658370` and `5128726338` accept the Phase 3 technical exit with narrow performance notes; no GGUF, corpus, nested implementation revision, budget, or raw evidence changed as a result.

The submodule configuration is:

```ini
[submodule "llama.cpp"]
    path = llama.cpp
    url = https://github.com/murillo128/llama.cpp.git
    branch = k3/out-of-core
```

## Upstream reference

- Upstream repository: <https://github.com/ggml-org/llama.cpp>
- Kimi-K3 text support PR: <https://github.com/ggml-org/llama.cpp/pull/26185>
- PR head observed when the project baseline was created: `cf67f0d24511864d2d3da0769108fd6fc16d00d1`

The project does not automatically follow a moving upstream PR head. Any rebase or update must happen in a dedicated commit followed by a complete baseline rerun.

## Source model repositories

### F16 reference source

- Repository: <https://huggingface.co/inference-optimization/Kimi-K3-0.40B>
- Hugging Face ID: `inference-optimization/Kimi-K3-0.40B`
- Purpose: architecture and F16 reference fixture.

### MXFP4 source

- Repository: <https://huggingface.co/inference-optimization/Kimi-K3-0.40B-MXFP4>
- Hugging Face ID: `inference-optimization/Kimi-K3-0.40B-MXFP4`
- Purpose: compressed-tensors MXFP4 conversion and MoE layout fixture.

The exact source revisions used for publication are recorded in the published `conversion-manifest.json` described below.

Validated source revisions for the clean Phase 1 execution:

- F16: `d853649387ffe8f48ce0198a29ac1a44205031f7`.
- MXFP4: `ef3902c318fb8e13c3507e26055656e687fdfe38`.

## Published GGUF repository

- Repository: <https://huggingface.co/murillo2000/Kimi-K3-0.40B-GGUF>
- Hugging Face ID: `murillo2000/Kimi-K3-0.40B-GGUF`
- Verified revision: `88de02cf8fa37f87eb06daaed370ac9c3411d5ca`
- Publication date: `2026-07-29`

Published files:

| File | Size in bytes | SHA-256 |
|---|---:|---|
| `Kimi-K3-0.40B-F16.gguf` | 784318432 | `411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7` |
| `Kimi-K3-0.40B-MXFP4.gguf` | 751976576 | `0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169` |
| `README.md` | 1877 | Hub model card |
| `conversion-manifest.json` | 1474 | Reproducibility manifest |

The Hub also contains the normal `.gitattributes` file used for large-file storage.

Verification performed against the Hub API confirmed that both remote GGUF sizes and remote LFS SHA-256 values exactly match the local publication manifest.

### Phase 2 route-corpus publication

Issue #10 published the complete raw real-trace corpus on a dedicated branch descended directly from the verified GGUF revision. The GGUF payloads were not replaced or modified.

- Branch: `phase2-observability-corpus-v1`.
- Immutable revision: `2d838d6b4d0aca4e9af1e7d899e57ad29330c72e`.
- Direct base revision: `88de02cf8fa37f87eb06daaed370ac9c3411d5ca`.
- Archive: `phase2-observability/phase2-k3-route-corpus-v1.tar.gz`.
- Archive size: 323723 bytes.
- Archive SHA-256 / remote LFS SHA-256: `6aa924a6c18bee4e2490f317ced836bcc4740c3ec63e9427a95951e79a649a5f`.
- Archive contents: 16 raw traces plus `README.md`, exact prompt definitions, a corpus index, and member checksums.

The exact published revision was downloaded again and compared byte-for-byte with the local deterministic archive. Hub file metadata at that revision reports the original F16 and MXFP4 sizes and LFS SHA-256 values unchanged. Complete member identities are recorded in `results/2026-07-29/skynet/phase2-observability/phase4-corpus-capture.json`; publication verification is in `phase4-corpus-publication.json` in the same directory.

## Artifact representation

### `Kimi-K3-0.40B-F16.gguf`

- File type reported by `llama.cpp`: `F16`.
- Used as the monolithic numerical and execution reference.

### `Kimi-K3-0.40B-MXFP4.gguf`

- File type reported by `llama.cpp`: `MXFP4 MoE`.
- 168 routed per-expert source tensors remain MXFP4.
- They are repacked into 21 GGUF expert tensors: seven MoE layers times gate/up/down.
- 35 resident MoE tensors are dequantized to F16 during conversion.

## Phase 1 validated monolithic baseline

The clean issue #7 execution is recorded at `results/2026-07-29/skynet/phase1-closeout-clean/`. Both published artifacts passed stable CPU/CUDA tests, deterministic inference, selected-logit comparison, complete placement capture, repeated warm execution, memory capture, and descriptive repeated benchmarks.

The independent MXFP4 validator matched all 81 stratified source/GGUF samples exactly. The fixed prompt token IDs are identical across the F16 and MXFP4 source and GGUF paths. Special-token metadata conflicts remain explicitly recorded, so this does not imply general chat-template parity.

The benchmark retained one model per model/backend process, discarded one warmup, recreated the context for five measured runs, and recorded load time, prompt/decode throughput, TTFT, per-token latency percentiles, RSS, and CUDA VRAM. All runs naturally terminated at the same 49-token sequence and EOG ID 163585. These are descriptive measurements for the tiny fixture and validated host, not out-of-core results.

Checkpoint A and Checkpoint B both returned `PASS_WITH_NOTES` with safety gate `YES`. Five earlier Checkpoint C attempts returned `FAIL / NO` on verifier traceability ambiguities; the benchmark itself passed each review.

The second attempt found a failed ancestor, coexisting stale lines, and a placeholder comment domain. The third found removable failure history, ambiguous duplicate fields, contradictory external fields, and merge-attestation acceptance.

The fourth attempt found unanchored suffix contradictions, uppercase placeholder URLs, and extra malformed label lines.

The fifth attempt found plain and alternatively styled contradictory external labels were not counted. The repository owner applied the STANDARD repeated-review circuit breaker, classifying those adversarial representation findings as non-material.

Checkpoint C: **PASS_WITH_NOTES**

Checkpoint C reviewed head: `4f1dcae3024bebcb932f95dfbab9ef7e5154a68c`

Checkpoint C review: https://github.com/murillo128/k3-out-of-core/issues/7#issuecomment-5120875092

The calibrated review accepted the complete technical evidence with safety gate `YES`. Notes carried forward are older README prose and the disclosed limitation that VRAM telemetry is sampled device-wide rather than process-attributed continuous peak telemetry.

## Downloading the published baseline

From the project root:

```bash
mkdir -p models/gguf

hf download murillo2000/Kimi-K3-0.40B-GGUF \
  Kimi-K3-0.40B-F16.gguf \
  Kimi-K3-0.40B-MXFP4.gguf \
  conversion-manifest.json \
  --local-dir models/gguf
```

Verify the binaries:

```bash
printf '%s  %s\n' \
  411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7 \
  models/gguf/Kimi-K3-0.40B-F16.gguf \
  0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169 \
  models/gguf/Kimi-K3-0.40B-MXFP4.gguf \
  | sha256sum --check
```

## Phase 6 generated split lineage

Issue #22 generates 218 one-tensor splits for each immutable F16 and MXFP4 GGUF with `llama-gguf-split --split --split-max-tensors 1`. The split binaries are deleted after capture; exact path, size, SHA-256, source model identity, command, and nested tool head are recorded in the Phase 6 manifest. A native read-only observer selects a populated cold bundle, and the standing capture independently reads its declared spans and requires exact byte-count and SHA-256 equality. The selected F16 and MXFP4 split bundles each span three distinct files. The original GGUFs and Hub revisions are unchanged.

The standing capture filesystem and backing-device identity are recorded explicitly. Timings are descriptive and are not labeled NVMe performance unless the manifest's `physically_on_nvme` field is true.

## Update policy

When any source model, converter, GGUF, or repository revision changes:

1. create a new conversion manifest;
2. rerun conversion integrity checks;
3. rerun CPU and CUDA baselines;
4. publish new immutable GGUF artifacts;
5. record the new Hugging Face revision, sizes, and SHA-256 values here;
6. update the `llama.cpp` submodule only after validation;
7. never replace a recorded baseline silently.
