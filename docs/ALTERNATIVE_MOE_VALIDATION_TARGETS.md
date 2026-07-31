# Alternative MoE Validation Targets

Reviewed on **2026-08-01**.

This document records external model candidates that can validate whether the K3 expert-residency runtime generalizes to useful open-weight models. It is a research and validation input, not a change to the committed phase order or K3 acceptance criteria.

Use the repository status markers precisely:

- `OBSERVED`: published model metadata, artifact sizes, or externally reported behavior with a cited source;
- `SPECULATIVE`: a project hypothesis that still requires reproducible local evidence;
- `OPEN`: a decision intentionally deferred to the controlling issue for the relevant phase.

## Scope and invariants

- Kimi K3 remains the architecture stress target and full-size scaling target.
- The tiny K3 F16/MXFP4 fixtures remain the correctness oracle for the current implementation phases.
- No alternative model is authorized to change Phase 9 or Phase 10 scope.
- Supporting another architecture must preserve the existing provider, storage, cache, transport, miss-policy, cancellation, and telemetry contracts rather than introducing a model-specific parallel runtime.
- External throughput claims are leads only. They are not project evidence until exact model revision, GGUF files, command line, runtime commit, hardware, context, KV configuration, prompt, and measurement logs are captured.

## Candidate roles

| Candidate | Published scale | Intended project role | Current disposition |
|---|---:|---|---|
| Kimi K3 | 2.78T total, 104B activated | Extreme full-size NVMe/UMA scaling and architecture target | `ACCEPTED` primary target |
| Qwen3-Coder-Next | about 80B total, 3B activated | Resident consumer-hardware baseline and provider-portability check | `OPEN` after current K3 policy work |
| DeepSeek-V4-Flash | 284B total, 13B activated | Practical out-of-core quality/capacity comparator on 64–128 GB hosts | `OPEN` Phase-12 candidate |

## Qwen3-Coder-Next

### `OBSERVED` model facts

Primary model and report:

- <https://huggingface.co/Qwen/Qwen3-Coder-Next>
- <https://arxiv.org/abs/2603.00729>

Published characteristics:

```text
total parameters       about 80B
activated parameters   about 3B per token
specialization         coding agents
license                Apache-2.0
```

The small activated set makes Qwen3-Coder-Next a strong baseline for a discrete GPU with limited VRAM. Suitable quantizations can be fully resident across 64 GB host RAM and a 12 GB GPU, so it is primarily a RAM/PCIe/CPU-miss benchmark rather than an NVMe benchmark.

### External performance leads

- Unsloth Bluesky post supplied by the project owner: <https://bsky.app/profile/unsloth.ai/post/3mrxfgg6hek2h>
- `llama.cpp` consumer-hardware performance discussion: <https://github.com/ggml-org/llama.cpp/issues/19480>
- `llama.cpp` GPU/runtime performance discussion: <https://github.com/ggml-org/llama.cpp/issues/19345>

These sources show that placement and graph configuration materially affect observed performance. In particular, `--fit`, `--n-cpu-moe`, CUDA graph behavior, KV configuration, host-memory bandwidth, and which common operations remain on the GPU can dominate a simple model-size comparison.

The Bluesky result is retained as a benchmark-discovery lead only. Do not copy its headline throughput into project claims without the underlying reproducible command and logs.

### Required comparison

A future Qwen validation must compare the project runtime against an unmodified, pinned `llama.cpp` baseline using at least:

```text
automatic --fit placement
explicit --n-cpu-moe placement where supported
identical GGUF and context/KV settings
single-request decode
controlled prefill
warm and cold runs
```

A project speedup is not established merely by beating a naive layer-offload configuration. The baseline must first represent current best-known `llama.cpp` placement for the tested host.

### Project value hypothesis

`SPECULATIVE` potential gains are:

- persistent dynamic expert residency instead of static layer placement;
- GPU execution for hot experts with bounded CPU execution for misses;
- asynchronous RAM-to-GPU promotion;
- protected decode hot sets;
- cross-request demand coalescing after concurrency support exists.

Because the whole quantized model can be physically resident, Qwen is not expected to demonstrate the main NVMe value proposition. It is valuable as a portability, regression, and practical-consumer baseline.

## DeepSeek-V4-Flash

### `OBSERVED` official model facts

Primary model:

- <https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash>
- Technical report identifier: `arxiv:2606.19348`

Published characteristics:

```text
total parameters       284B
activated parameters   13B per token
context length         up to 1M tokens
expert precision       FP4
most other parameters  FP8
license                MIT
```

The instruct checkpoint already uses mixed FP4 expert and FP8 non-expert precision. Consequently, quantizing the GGUF globally from Q8 to Q4 saves much less capacity than a conventional BF16 model because the dominant expert payload is already low precision.

### `OBSERVED` Unsloth GGUF size ladder

Source:

- Guide: <https://unsloth.ai/docs/models/deepseek-v4>
- Artifacts: <https://huggingface.co/unsloth/DeepSeek-V4-Flash-GGUF>

Observed published sizes on 2026-07-31:

| Unsloth quant | Published size |
|---|---:|
| `UD-IQ1_S` | 82.5 GB |
| `UD-IQ1_M` | 86.9 GB |
| `UD-IQ2_XXS` | 90.9 GB |
| `UD-IQ2_M` | 90.9 GB |
| `UD-Q2_K_XL` | 96.8 GB |
| `UD-IQ3_XXS` | 103 GB |
| `UD-IQ3_S` | 117 GB |
| `UD-Q3_K_M` | 129 GB |
| `UD-Q3_K_XL` | 129 GB |
| `UD-IQ4_XS` | 138 GB |
| `UD-IQ4_NL` | 138 GB |
| `UD-Q4_K_XL` | 155 GB |
| `UD-Q8_K_XL` | 162 GB |

Unsloth describes `UD-Q8_K_XL` as the lossless/full-precision GGUF reference and notes that it is only 7 GB larger than `UD-Q4_K_XL`. This is an external claim about its conversion and quantization pipeline; a project validation must still pin the exact repository revision and verify file hashes and output behavior.

### Why this model is relevant

DeepSeek-V4-Flash creates a direct and useful comparison between two deployment strategies:

1. **ultra-low-bit resident or nearly resident execution** using an approximately 82–103 GB GGUF; and
2. **higher-precision out-of-core execution** using the 155–162 GB GGUF with explicit expert residency.

This supports a concrete project question:

> Can explicit NVMe/RAM/VRAM or UMA expert residency preserve a higher-precision checkpoint at usable speed, instead of achieving capacity only through 1–3-bit quantization?

The answer is `OPEN`. Artifact size alone establishes neither quality nor throughput.

### Capacity hypotheses

The following are derived capacity hypotheses, not measured performance:

#### Discrete 12 GB GPU plus 64 GB RAM

- Nominal RAM plus VRAM must not be treated as a fully available 76 GB weight budget.
- The operating system, CUDA runtime, graph workspaces, pinned rings, KV/recurrent state, and context allocations require explicit headroom.
- Even `UD-IQ1_S` is therefore expected to require either NVMe participation or unsafe oversubscription at useful contexts.
- The 155–162 GB variants are strong tests of explicit RAM/NVMe caching, but their speed and quality trade-off must be measured rather than estimated in acceptance criteria.

#### DGX Spark with 128 GB coherent memory

- The 103 GB and 117 GB variants may fit by capacity, but only after measured CUDA/runtime/KV and operating-system headroom is reserved.
- The 129 GB variant is not safely resident merely because its file size is close to nominal memory.
- The 155–162 GB variants remain out-of-core and are suitable comparators for the Phase-11 UMA path.
- Coherent memory removes a duplicate host-to-device copy; it does not remove the need to control physical residency or NVMe misses.

### Quality requirements

Do not equate a smaller GGUF with an acceptable product configuration. Every activated DeepSeek comparison must include:

- deterministic same-runtime output comparison where possible;
- selected logits or full-logit hashes on bounded fixtures;
- perplexity or an approved model-quality proxy;
- coding/agentic evaluations appropriate to the intended use;
- chat-template, multi-turn, tool-call, EOS, and reasoning-mode validation;
- exact context and KV format;
- a statement separating model-quality loss from runtime numerical differences.

The higher-precision out-of-core path is useful only if it preserves materially more quality or capability than the faster resident low-bit path.

### Runtime requirements

A future DeepSeek port must:

- use a pinned `llama.cpp` revision with current DeepSeek-V4 model and chat-template fixes;
- describe the model's expert bundle without hard-coding K3 tensor names or topology;
- preserve resident attention, routing, shared/common parameters, and required state;
- keep dynamic expert preparation outside CUDA graph capture;
- compare `PROMOTE_AND_GPU`, `CPU_FALLBACK`, and `AUTO` using measured costs;
- record expert bytes per token separately from attention, KV, and residual work;
- preserve deterministic merge and canonical accumulation semantics;
- treat speculative decoding, including the official DSpark-attached variant, as a separate optimization after baseline correctness.

Official DSpark checkpoint reference:

- <https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-DSpark>

## Activation decision and phase placement

### Qwen3-Coder-Next

`OPEN`: activate only through a bounded issue after the K3 cache-policy baseline is stable. It should prove provider portability and establish a strong current-runtime baseline, not interrupt K3 Phase 9 or Phase 10.

### DeepSeek-V4-Flash

`OPEN`: evaluate as a Phase-12 cross-model comparator after the K3 synthetic exact-size store and physical scaling gates are available. Earlier research may inspect metadata and GGUF layout, but no implementation claim should bypass the K3 phase sequence.

A Phase-12 controlling issue should decide whether the comparison is limited to trace/layout analysis or includes complete single-request inference, based on available storage, RAM, hardware time, and pinned upstream support. Multi-request and multi-GPU comparisons remain Phase 13 and Phase 14 work.

## Minimum evidence manifest for an alternative model

Before accepting any alternative-model result, record:

```text
model repository and immutable revision
license
all GGUF filenames, sizes, and SHA-256 values
converter/quantizer repository and revision
llama.cpp project and nested commit SHAs
CPU, RAM topology and measured bandwidth
GPU, VRAM, driver, CUDA and negotiated PCIe link
NVMe model, filesystem, mount options and measured real-record I/O
OS and kernel
exact command line and environment variables
context, batch, parallelism and KV configuration
resident trunk, RAM cache, pinned ring and VRAM cache budgets
prompt and generated-token hashes
prompt throughput, TTFT and decode throughput
token-latency p50/p95/p99
RSS, locked bytes, page faults, swap/compression and physical residency
tier hits, bytes moved, I/O wait, H2D wait and overlap
quality evaluation and comparison baseline
```

## Conclusions

- Qwen3-Coder-Next is the best near-term consumer-hardware baseline, but it mostly tests RAM/PCIe placement because suitable quantizations can be resident.
- DeepSeek-V4-Flash is the strongest currently identified practical out-of-core comparator for 64–128 GB machines because its published GGUFs span approximately 82.5–162 GB while activating only 13B parameters.
- The most valuable DeepSeek experiment is not a headline tokens/second race. It is a controlled quality-versus-residency comparison between a lower-bit resident configuration and a higher-precision explicit out-of-core configuration.
- K3 remains the project architecture and full-size scaling target until a committed decision explicitly changes that scope.
