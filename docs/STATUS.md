# Current Status

Last updated: **2026-07-29**

This file is the first handoff document for a new ChatGPT or Codex session.

## Project state

- Repository created to serve as the cross-session source of truth.
- Goal, architecture, decisions, prior art, validation model, and full implementation plan have been documented.
- No out-of-core runtime implementation exists yet.
- No upstream PR has been opened from this repository.

## Upstream K3 foundation

- Repository: `ggml-org/llama.cpp`
- PR: <https://github.com/ggml-org/llama.cpp/pull/26185>
- Title: `model: add Kimi-K3 text model`
- Observed state: open, non-draft, not merged.
- Observed head: `cf67f0d24511864d2d3da0769108fd6fc16d00d1`
- Observed on: 2026-07-29.

The PR implements K3 text architecture and MXFP4 expert repacking. Vision support is outside the current project scope.

## Local model assets

Downloaded under the local `k3-streaming-lab` workspace:

```text
Kimi-K3-0.40B
Kimi-K3-0.40B-MXFP4
```

Hugging Face IDs:

```text
inference-optimization/Kimi-K3-0.40B
inference-optimization/Kimi-K3-0.40B-MXFP4
```

## Conversion findings

Confirmed from local inspection:

```text
203 packed tensors total
168 per-expert routed tensors
35 additional resident MoE tensors
```

Known environment fixes:

- install `tiktoken`;
- use `transformers==4.57.6` for the current converter branch;
- remove the incompatible `extra_special_tokens` list from local tokenizer configuration after backup;
- extend the K3 converter to dequantize the known 35 resident packed tensors to F16 while preserving the 168 routed tensors as MXFP4.

## Not yet confirmed

The following must not be stated as completed until evidence is committed:

- successful F16 GGUF conversion;
- successful hybrid MXFP4/F16 GGUF conversion;
- expected conversion counts of 21 and 35 in final logs;
- successful CPU inference;
- successful CUDA inference;
- CPU/CUDA token or logit parity;
- routing trace capture;
- any performance number;
- any out-of-core cache code.

## Immediate next action

Execute **Phase 1** in `PLAN.md`:

1. clone this repository locally alongside the `llama.cpp` checkout;
2. record exact environment and checkpoint revisions;
3. pin the current K3 PR commit intentionally;
4. make tokenizer/converter fixes explicit patches under version control;
5. convert and validate the F16 model;
6. convert and validate the hybrid MXFP4 model;
7. commit manifests and reproducible logs or summarized evidence.

## Session handoff rule

At the end of every implementation session:

1. update this file with completed work and exact commit SHAs;
2. update `PLAN.md` checkboxes and gate evidence;
3. add or amend decisions in `docs/DECISIONS.md`;
4. update model commands/evidence in `docs/MODELS_AND_VALIDATION.md`;
5. commit before starting a new session.
