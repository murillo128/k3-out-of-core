# Current Status

Last updated: **2026-07-29**

This file is the first handoff document for a new ChatGPT or Codex session.

## Project state

- Repository created to serve as the cross-session source of truth.
- Goal, architecture, decisions, prior art, validation model, and full implementation plan have been documented.
- The tiny Kimi-K3 F16 and hybrid MXFP4 GGUF fixtures have been converted, published, checksum-verified, and smoke-tested on CPU.
- No out-of-core runtime implementation exists yet.
- No upstream PR has been opened from this repository.
- Phase 1 remains open because CUDA validation, token/logit comparison, repeated runs, and baseline memory/performance evidence are still pending.

## Repository baseline

### Project repository

- Repository: <https://github.com/murillo128/k3-out-of-core>
- Branch: `main`
- Commit that pinned the validated `llama.cpp` converter baseline: `daca2b6a566411bd97fcb42308a2a75c5d2c2055`

### K3-capable llama.cpp fork

- Repository: <https://github.com/murillo128/llama.cpp>
- Branch: `k3/out-of-core`
- Pinned commit: `84245db4c790af22135f34992689edcc11877003`
- Commit message: `kimi-k3: dequantize resident MXFP4 tensors`

The `llama.cpp` submodule in this repository points to that exact commit.

See [`REPOSITORIES_AND_ARTIFACTS.md`](REPOSITORIES_AND_ARTIFACTS.md) for the complete repository and artifact record.

## Upstream K3 foundation

- Repository: `ggml-org/llama.cpp`
- PR: <https://github.com/ggml-org/llama.cpp/pull/26185>
- Title: `model: add Kimi-K3 text model`
- Observed state when the baseline was created: open, non-draft, not merged.
- Observed head: `cf67f0d24511864d2d3da0769108fd6fc16d00d1`
- Observed on: 2026-07-29.

The project does not automatically track a moving PR head. Any upstream update requires a dedicated commit and a complete baseline rerun.

## Source model assets

Hugging Face IDs:

```text
inference-optimization/Kimi-K3-0.40B
inference-optimization/Kimi-K3-0.40B-MXFP4
```

Local project layout:

```text
models/hf/Kimi-K3-0.40B
models/hf/Kimi-K3-0.40B-MXFP4
models/gguf/Kimi-K3-0.40B-F16.gguf
models/gguf/Kimi-K3-0.40B-MXFP4.gguf
```

The source checkpoints and generated GGUF files are not stored in GitHub.

## Published GGUF artifacts

- Repository: <https://huggingface.co/murillo2000/Kimi-K3-0.40B-GGUF>
- Hugging Face ID: `murillo2000/Kimi-K3-0.40B-GGUF`
- Verified revision: `88de02cf8fa37f87eb06daaed370ac9c3411d5ca`

| Artifact | Size | SHA-256 |
|---|---:|---|
| `Kimi-K3-0.40B-F16.gguf` | 784318432 bytes | `411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7` |
| `Kimi-K3-0.40B-MXFP4.gguf` | 751976576 bytes | `0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169` |

Hub API verification confirmed that remote sizes and remote LFS SHA-256 values exactly match the local publication manifest.

Machine-readable project record:

- [`../manifests/kimi-k3-0.40b-phase1.json`](../manifests/kimi-k3-0.40b-phase1.json)

The published Hugging Face repository also contains its own `conversion-manifest.json`, including the exact source revisions and conversion environment.

## Conversion findings

Confirmed from source inspection and final conversion logs:

```text
203 packed tensors total
168 per-expert routed tensors
35 additional resident MoE tensors
21 repacked GGUF MXFP4 expert groups
35 resident tensors dequantized to F16
```

Converter behavior at the pinned `llama.cpp` commit:

- preserves the 168 routed per-expert tensors in MXFP4;
- repacks them into 21 GGUF expert tensors;
- lazily dequantizes the 35 known resident packed tensors to F16;
- aborts on unknown packed MXFP4 tensor names.

Known environment requirements/workarounds:

- install `tiktoken`;
- use `transformers==4.57.6` for the current converter branch;
- remove the incompatible `extra_special_tokens` list from local tokenizer configuration after backup;
- preserve `additional_special_tokens` and validate vocabulary size 163840.

## CPU smoke-test result

Both GGUF files loaded and generated successfully with the CPU build of commit `84245db4c790af22135f34992689edcc11877003`.

Prompt:

```text
According to all known laws
```

Observed continuation for both fixtures:

```text
the start.
```

No `error`, `failed`, `nan`, `inf`, `unsupported`, or `fallback` messages were detected in the captured CPU logs.

Initial single-run observations on `skynet`:

| Artifact | Prompt throughput | Generation throughput |
|---|---:|---:|
| F16 | 1371.5 tokens/s | 130.4 tokens/s |
| MXFP4/F16 | 1439.6 tokens/s | 133.1 tokens/s |

These values are smoke-test observations, not accepted benchmark results.

## Pending Phase 1 validation

The following must not yet be stated as completed:

- CUDA inference for F16;
- CUDA inference for hybrid MXFP4/F16;
- explicit backend placement and CPU fallback analysis;
- deterministic prompt and generated token ID capture;
- selected logit comparison between reference paths;
- repeated warm-run validation;
- memory measurements and repeated performance benchmarks;
- perplexity or equivalent loss comparison;
- sampled source-to-GGUF MXFP4 byte/dequantization validation;
- routing trace capture;
- any out-of-core cache implementation.

## Immediate next action

Continue **Phase 1** in `PLAN.md`:

1. build the CUDA configuration at the pinned `llama.cpp` commit;
2. run both published GGUF fixtures with full supported offload;
3. record backend placement and any CPU fallback nodes;
4. capture deterministic tokens and selected logits;
5. run repeated CPU and CUDA measurements with memory telemetry;
6. commit summarized evidence and update Phase 1 checkboxes;
7. do not begin out-of-core residency changes until the monolithic exit gate is satisfied.

## Session handoff rule

At the end of every implementation session:

1. update this file with completed work and exact commit SHAs;
2. update `PLAN.md` checkboxes and gate evidence;
3. add or amend decisions in `docs/DECISIONS.md`;
4. update model commands/evidence in `docs/MODELS_AND_VALIDATION.md`;
5. update `docs/REPOSITORIES_AND_ARTIFACTS.md` and the machine-readable manifest when revisions or artifacts change;
6. commit before starting a new session.
