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
- Phase 3 resident-provider head: `523f825d2df5efa7c9a08561e2b64861ad5594c5`
- Phase 3 branch: `codex/phase3-resident-provider`

The `llama.cpp` gitlink in the Phase 3 review branch points to the Phase 3 resident-provider head. The Phase 1 and Phase 2 revisions remain immutable validation inputs.

Relevant behavior in this revision:

- Kimi-K3 text architecture support;
- lossless repacking of routed expert weights into GGML MXFP4;
- explicit recognition of the 35 resident packed MoE tensors in the tiny MXFP4 fixture;
- lazy dequantization of those resident tensors to F16;
- rejection of unknown packed MXFP4 tensors.

Phase 3 adds the model-owned resident expert-weight provider, typed logical/execution ID seam, request-scoped RAII leases across asynchronous submission, structural counters, and focused CPU/CUDA lifecycle tests. It does not add a cache, storage transport, prefetch, physical slots, or any change to expert residency. No GGUF or corpus artifact was republished.

The single standing Phase 3 performance capture approved in issue comment `5127588494` failed 3 of 24 gated metric cells. It is committed as negative evidence; no GGUF, corpus, nested implementation revision, or published artifact changed as a result.

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

## Update policy

When any source model, converter, GGUF, or repository revision changes:

1. create a new conversion manifest;
2. rerun conversion integrity checks;
3. rerun CPU and CUDA baselines;
4. publish new immutable GGUF artifacts;
5. record the new Hugging Face revision, sizes, and SHA-256 values here;
6. update the `llama.cpp` submodule only after validation;
7. never replace a recorded baseline silently.
