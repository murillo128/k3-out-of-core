# Prior Art and Reuse Plan

Reviewed on **2026-07-29**. Statuses can change; verify them before basing implementation work on a branch.

The central conclusion is that the proposed architecture is not novel in isolation. Nearly identical systems have been designed and prototyped. The opportunity is to combine the strongest ideas behind a clean provider abstraction, avoid documented lifetime and synchronization failures, and adapt the result to Kimi-K3 MXFP4 and UMA.

## Summary

| Work | Current observed status | Main contribution | Reuse decision |
|---|---|---|---|
| `llama.cpp` issue #20757 | Closed as completed | Exact VRAM/RAM/SSD hierarchy, slots, SLRU | Reuse architecture and policy rationale |
| `llama.cpp` PRs #21609/#21614/#21620 | Closed, not merged | Early persistent-cache attempts | Mine patches/tests; do not adopt |
| `llama.cpp` PR #23170 | Closed, not merged | Resident-copy optimization and crucial lifetime failure | Reuse telemetry and negative result |
| `llama.cpp` PR #24524 / discussion #24528 | Closed, not merged | Hybrid GPU-hit/CPU-miss execution and backend API | Reuse API concepts and miss policy |
| `llama.cpp` PR #21067 | Open | Async tensor prefetch and transfer overlap | Reuse pinned-buffer/stream/event patterns |
| Lidenburg `moe-expert-caching` branch | External PoC | VRAM + RAM + NVMe, `io_uring`, direct I/O | Port isolated Linux I/O and telemetry ideas |
| vLLM RFC #38256 / PR #37190 | Open | Clean `ExpertWeightProvider`, fixed slots, persistent mapping, LFRU | Reuse software architecture |
| tinyserve | External implementation | Independent cache behavior and benchmark evidence | Reuse tests/policy ideas after license review |
| MoE-Infinity | Active external project | Activation tracing, activation-aware caching/prefetch | Reuse research ideas, not runtime integration |

## 1. `llama.cpp` issue #20757

- URL: <https://github.com/ggml-org/llama.cpp/issues/20757>
- Title: *Two-tier GPU+RAM expert cache for MoE offload (pluggable eviction policy)*
- Observed state: closed, marked completed.

### Relevant design

The issue proposes almost the same hierarchy as this project:

```text
GPU persistent expert-slot cache
CPU pinned backing cache
SSD/mmap backing store
```

Key ideas:

- fixed-address accelerator slots;
- persistent `expert_id -> slot_id` mapping;
- remapping selected IDs before `MUL_MAT_ID`;
- SLRU with probationary and protected segments;
- frequency-gated admission to protect decode hot sets from prefill pollution;
- readahead and page release for the SSD tier;
- eventual double buffering.

### What to reuse

- problem decomposition;
- slot-directory semantics;
- SLRU and admission-filter definitions;
- separate prefill/decode evaluation;
- proposed integration points around selective expert copies.

### What not to assume

- benchmark results are workload- and implementation-specific;
- pinning all CPU experts is acceptable;
- OS page cache is sufficient for the final disk tier.

## 2. Early `llama.cpp` cache PRs

### PR #21609

- URL: <https://github.com/ggml-org/llama.cpp/pull/21609>
- Title: *N-slot LFRU cache with FATE prefetch for MoE weight offloading*
- Observed state: closed, not merged, withdrawn.

### PR #21614

- URL: <https://github.com/ggml-org/llama.cpp/pull/21614>
- Title: *persistent expert cache for --n-cpu-moe*
- Observed state: closed, not merged, withdrawn.

### PR #21620

- URL: <https://github.com/ggml-org/llama.cpp/pull/21620>
- Title: *dedup expert cache for MoE CPU offload*
- Observed state: closed, not merged, withdrawn.

### Reuse plan

Before implementing the equivalent subsystem, inspect these patches for:

- selected-ID extraction;
- expert-range copy logic;
- remapping strategy;
- cache invalidation tests;
- FATE/prefetch hooks;
- reasons for withdrawal in comments and later branches.

Do not cherry-pick them wholesale. They predate later lifetime conclusions and current `llama.cpp` scheduler changes.

## 3. `llama.cpp` PR #23170 — temporary-buffer lifetime failure

- URL: <https://github.com/ggml-org/llama.cpp/pull/23170>
- Title: *treat experts as cache residents during MoE offloading*
- Observed state: closed, not merged.

### Approach

The patch tracked which expert regions were already copied into a scheduler staging tensor and skipped copies for presumed residents.

### Failure and lesson

The resident bitset could survive longer than the temporary graph allocation contents. When epoch invalidation was relaxed, cache hits appeared but perplexity produced NaNs. With safe invalidation, useful cross-epoch residency disappeared.

This establishes a non-negotiable requirement:

> Persistent cache metadata requires persistent cache-owned buffers. A stable pointer to graph-temporary memory is not sufficient.

### What to reuse

- hit/miss and copied-byte telemetry;
- structural invalidation conditions;
- perplexity checks that caught stale data;
- test cases spanning compute epochs.

### What not to reuse

- the scheduler staging tensor as cache storage;
- metadata lifetime tied only to pointer identity.

## 4. `llama.cpp` PR #24524 and discussion #24528

- PR: <https://github.com/ggml-org/llama.cpp/pull/24524>
- Discussion: <https://github.com/ggml-org/llama.cpp/discussions/24528>
- Title: *CUDA adaptive VRAM caching of CPU-resident experts*
- Observed PR state: closed, not merged.

### Important architectural difference

Rather than moving the entire `MUL_MAT_ID` operation to the GPU and forcing misses through H2D, the design executes:

- cache hits on GPU;
- misses on CPU;
- both concurrently where possible;
- canonical result collection afterward.

This structurally limits worst-case regressions from misses on discrete GPUs.

### Reusable API concepts

The PR introduced a backend bridge with operations equivalent to:

```text
begin
plan
dispatch
collect
redirect/hand-off
invalidate
trim
node timing
```

This is useful inspiration for the C/C++ boundary between CPU GGML code and CUDA implementation.

### Reuse plan

- preserve a small provider/backend interface;
- support CPU miss execution as an explicit policy;
- retain VRAM trim/surrender under allocation pressure;
- retain model-unload invalidation;
- reuse fused gate/up and down-result handoff concepts only after K3 graph inspection.

### Do not copy directly

The PR was large, modified many backend areas, and was closed as unreviewable under project contribution constraints. Reimplement in small components with independent tests.

## 5. `llama.cpp` PR #21067 — asynchronous prefetch

- URL: <https://github.com/ggml-org/llama.cpp/pull/21067>
- Title: *allow prefetching tensor overrides*
- Observed state: open on 2026-07-29.

### Relevant work

- overlaps current-layer compute with future weight transfer;
- uses CUDA transfer scheduling and backend events;
- highlights that pageable mmap-backed sources can serialize transfers;
- provides tensor-override prefetch hooks.

### Reuse plan

Inspect and adapt:

- pinned-source allocation;
- stream/event lifecycle;
- cross-layer dependency handling;
- bounded transfer buffers;
- CLI/config patterns where suitable.

The PR does not solve dynamic expert prediction for small decode batches. It is transport prior art, not the cache design itself.

## 6. Lidenburg `llama.cpp` fork

- Repository: <https://github.com/Lidenburg/llama.cpp>
- Branch: `moe-expert-caching`
- Compared branch observed as two commits ahead of its base at review time.

### Implemented ideas

The branch contains a large proof of concept with:

- accelerator cache slots;
- RAM staging/cache slots;
- host-buffer registration;
- `io_uring`;
- `O_DIRECT` and aligned reads;
- disk/host/GPU hit statistics;
- LFU/LFU-aging variants;
- file-offset based expert reads;
- multi-slot transfer rings.

### High-value reusable pieces

Port only after line-by-line review:

1. direct-I/O alignment calculations;
2. `io_uring` submission/completion lifecycle;
3. transfer-ring accounting;
4. bidirectional slot mappings;
5. wait-time and tier-hit telemetry;
6. error paths for registration and disk reads.

### Known design debt to avoid

- roughly 1,800 lines inserted into `ggml-backend.cpp`;
- global static state;
- compile-time and hard-coded capacities;
- Linux-specific `/proc/self/maps` discovery;
- assumptions about model, request, device, and tensor ordering;
- policy, transport, storage, and statistics intertwined;
- N+1 prefetch disabled in the branch after poor observed utility.

The fork is a reference implementation and test oracle, not the base branch.

## 7. vLLM RFC #38256 and PR #37190

- RFC: <https://github.com/vllm-project/vllm/issues/38256>
- PR: <https://github.com/vllm-project/vllm/pull/37190>
- Observed state: both open on 2026-07-29.

### Strongest reusable idea: provider architecture

The RFC models residency as a weight provider:

```text
ExpertWeightProvider
├── FullGPUProvider
└── CachedWeightProvider
    ├── fixed-address GPU slot manager
    ├── persistent GPU mapping tensor
    ├── LFRU eviction
    └── pinned CPU backing store
```

The kernel sees GPU buffers and remapped IDs, not storage decisions.

### Reuse plan

Adopt the pattern, translated to GGML:

- provider default path with negligible overhead;
- fixed-address slot buffers;
- persistent mapping updated in place;
- quantization-specific registration of the tensors comprising an expert;
- unique-expert deduplication for batched prefill;
- explicit unsupported-configuration errors;
- provider logic outside CUDA graph capture.

### Differences from this project

- the current vLLM work starts with CPU-pinned backing, not a completed disk tier;
- it is Python/PyTorch-oriented;
- current PR limitations include synchronous H2D, single GPU, and restricted formats;
- this project needs GGUF spans, CPU GGML fallback, MXFP4, explicit NVMe, and UMA.

## 8. tinyserve

- Repository: <https://github.com/e1n00r/tinyserve>

The `llama.cpp` and vLLM proposals cite tinyserve as an independent implementation of fixed GPU slots, CPU backing, cache policies, tracing, and temporal prediction.

### Reuse plan

After reviewing license and code:

- port policy unit-test cases;
- port trace-analysis metrics;
- compare LFRU versus SLRU/admission behavior;
- reuse cold-start and mixed prefill/decode benchmark methodology;
- do not transplant PyTorch runtime integration into GGML.

## 9. MoE-Infinity

- Repository: <https://github.com/EfficientMoE/MoE-Infinity>
- Paper: *MoE-Infinity: Efficient MoE Inference on Personal Machines with Sparsity-Aware Expert Cache*.

### Relevant ideas

- expert activation tracing;
- activation-aware cache management;
- activation-aware prefetch;
- host offload for resource-constrained accelerators;
- OS-level optimizations and multi-GPU research.

### Reuse plan

- study trace representation and predictor inputs;
- reuse evaluation concepts for domain shifts and long contexts;
- compare its activation-aware policies with K3 traces;
- do not use it as the runtime base because it targets Hugging Face/PyTorch and a different execution stack.

## 10. Kimi-K3 support PR

- URL: <https://github.com/ggml-org/llama.cpp/pull/26185>
- Title: *model: add Kimi-K3 text model*
- Observed state: open, non-draft.
- Observed head on 2026-07-29: `cf67f0d24511864d2d3da0769108fd6fc16d00d1`.

The PR supplies the architecture and conversion foundation:

- hybrid KDA and MLA;
- cross-layer residual attention;
- latent MoE;
- SiTU activation;
- MLA output gate;
- full-rank KDA gate;
- MXFP4 expert repacking.

This project must pin an exact K3 PR commit for each validation run. Do not merge out-of-core work with ongoing model-support changes until the baseline is stable.

## Reuse checklist before importing code

For every imported fragment:

1. identify repository, branch, commit, and license;
2. document the exact behavior being reused;
3. write an isolated test before integration;
4. remove global/model-specific assumptions;
5. adapt ownership to the provider design;
6. verify multi-model and unload lifetime;
7. verify thread and device safety;
8. benchmark against a clean baseline;
9. preserve attribution where required;
10. record the decision and commit in this document.
