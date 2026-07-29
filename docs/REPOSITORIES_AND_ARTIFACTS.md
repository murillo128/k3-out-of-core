# Repositories and Published Artifacts

This document records the repositories, pinned revisions, and published binary artifacts used by the K3 out-of-core project.

It is intended to let a new ChatGPT, Codex, or human session reconstruct the current baseline without relying on chat history.

## Project repository

- Repository: <https://github.com/murillo128/k3-out-of-core>
- Default branch: `main`
- Baseline commit: `daca2b6a566411bd97fcb42308a2a75c5d2c2055`
- Baseline commit message: `Pin llama.cpp resident MXFP4 conversion`

This repository is the source of truth for architecture, planning, validation requirements, and the exact `llama.cpp` submodule revision.

## K3-capable llama.cpp fork

- Repository: <https://github.com/murillo128/llama.cpp>
- Development branch: `k3/out-of-core`
- Pinned commit: `84245db4c790af22135f34992689edcc11877003`
- Commit message: `kimi-k3: dequantize resident MXFP4 tensors`

The `llama.cpp` gitlink in `k3-out-of-core` points to this exact commit.

Relevant behavior in this revision:

- Kimi-K3 text architecture support;
- lossless repacking of routed expert weights into GGML MXFP4;
- explicit recognition of the 35 resident packed MoE tensors in the tiny MXFP4 fixture;
- lazy dequantization of those resident tensors to F16;
- rejection of unknown packed MXFP4 tensors.

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

## Artifact representation

### `Kimi-K3-0.40B-F16.gguf`

- File type reported by `llama.cpp`: `F16`.
- Used as the monolithic numerical and execution reference.

### `Kimi-K3-0.40B-MXFP4.gguf`

- File type reported by `llama.cpp`: `MXFP4 MoE`.
- 168 routed per-expert source tensors remain MXFP4.
- They are repacked into 21 GGUF expert tensors: seven MoE layers times gate/up/down.
- 35 resident MoE tensors are dequantized to F16 during conversion.

## CPU smoke-test baseline

Both published GGUF files loaded and executed successfully using the CPU build of commit `84245db4c790af22135f34992689edcc11877003`.

Prompt:

```text
According to all known laws
```

Observed deterministic continuation in the initial smoke test:

```text
the start.
```

No `error`, `failed`, `nan`, `inf`, `unsupported`, or `fallback` messages were found in the captured CPU logs.

Initial single-run measurements on `skynet` were:

| Artifact | Prompt throughput | Generation throughput |
|---|---:|---:|
| F16 | 1371.5 tokens/s | 130.4 tokens/s |
| MXFP4/F16 | 1439.6 tokens/s | 133.1 tokens/s |

These numbers are smoke-test observations, not accepted performance benchmarks. Repeated runs, memory measurements, token/logit comparisons, and CUDA validation are still required before closing Phase 1.

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
