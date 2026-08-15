# Prior Art and Reuse Plan

Reviewed through **2026-08-15**. Statuses can change; verify them before basing implementation work on a branch.

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
| WASTE | Active external implementation | Full 2.78T K3 streamed from NVMe on 64 GB, custom 3-bit experts, measured cache/prefetch limits | Use as external full-size baseline; reuse methodology and isolated ideas after review |
| Colibrì v1.4.0 | Active external implementation | Full text K3 with source MXFP4 experts, direct/repacked safetensors, direct I/O, CPU/Vulkan tiers, and chunked prefill | Use as primary high-fidelity K3 layout/execution baseline; reuse isolated ideas after review |
| Cache-Conditional Experts | TMLR 2025 | Training-free cache-aware expert membership selection with a quality/locality frontier | Direct Phase 13.6 prior art; compare against hard-regret-bounded routing |
| MoE-ERAS | MLArchSys 2024 | Expert-residency-aware selection balancing performance and accuracy | Historical residency-aware routing prior art |
| Local Routing Consistency | arXiv 2025 | Measures how naturally cacheable/offload-friendly different MoE routers are | Reuse methodology; do not transfer other-model locality assumptions to K3 |
| ReMoE | ICML 2026 | Router fine-tuning to increase temporal expert reuse under memory constraints | Adjacent training-based prior art; compare quality/locality methodology |
| PipeNetwork Kimi-K3 REAP domain overlap | External K3 experiment | Per-source K3 saliency/top-N overlap and targeted pruning expose domain-conditioned expert structure | Use as K3-specific specialization prior; methodology only until project-code license is clarified |
| CommitteeAudit / Standing Committee | ACL 2026 | Domain-invariant routed-expert core plus task-specific periphery in other MoE models | Adapt core/periphery diagnostics to K3; do not transfer model-specific conclusions |

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

## 10. WASTE — Weight-Aware Streaming Tensor Engine

- Repository: <https://github.com/sqliteai/waste>
- Article: <https://marcobambini.substack.com/p/the-waste-inference-engine>
- Reviewed branch: `main`.
- Pinned reviewed commit: `c4d45c5914d1d15643d201855128938e8fb1698a`.
- License: Apache-2.0, copyright SQLite Cloud, Inc.
- Observed state: active external implementation and published full-K3 proof point.

### Architecture

WASTE is a standalone C11 inference engine with no third-party runtime dependencies. Its K3 path uses:

- a resident trunk plus disk-backed routed experts;
- a custom `.waste` container with one aligned record containing gate/up/down data for each expert;
- page-cache bypass through platform direct-I/O facilities;
- bounded reader threads and issue-ahead for all experts already selected in a layer;
- a bounded RAM expert cache;
- three-stage residual vector quantization at 3.00 bits per expert weight;
- direct table-based expert computation without materializing a conventional dense matrix;
- 4- and 8-bit resident trunk tensors;
- a compressed/absorbed attention-state representation for K3.

This is a distinct design point from this project. WASTE executes the streamed experts on CPU/UMA from its custom representation. This project retains GGUF/MXFP4 compatibility and targets explicit NVMe/RAM/pinned/VRAM tiers, discrete CUDA, CPU fallback, coherent CUDA UMA, and multi-GPU.

### `OBSERVED` full-K3 result

At the pinned commit, WASTE reports the complete 2.78T Kimi K3 checkpoint as:

```text
container                    982 GiB
resident trunk               about 27.28 GB
minimum RAM to open at 4K    29.05 GB
practical tested RAM         64 GB
selected experts/token       16 x 92 layers = 1472 records
expert working set/token     about 17.0 GB
expert record                about 11.83 MB
measured decode              0.49–0.54 tok/s
hardware                     Apple M5 Pro, 64 GB, internal NVMe
```

Its real-record disk benchmark reports about 10.73 GB/s with one random-read thread and about 12.79 GB/s from two threads onward. Adding asynchronous issue-ahead and overlapping expert reads with arithmetic improved K3 by roughly 1.6x while preserving identical hit/miss counts. A later profile still attributed about 54.8% of the decode step to expert I/O and 27.2% to expert arithmetic.

These numbers demonstrate feasibility and provide a serious external baseline. They are not direct performance predictions for this project because the hardware, quantization, expert bytes, kernels, container, cache hierarchy, and execution backend differ.

### Cache findings

WASTE identifies one token's expert working set as a critical cache scale. In its sweep predating read-ahead:

```text
17.32 GB expert cache    13% hit    0.32 tok/s
29.32 GB expert cache    37% hit    0.04 tok/s
```

The larger logical cache was much slower because the host began paging/compressing cache pages. Its automatic 64 GB configuration therefore selects about a 46 GB total budget with roughly 17.5 GB of expert cache instead of filling nominal RAM. A purgeable-cache experiment made gross oversubscription degrade less catastrophically but destroyed useful hits at the normal operating point.

Lessons retained for this project:

- cache hit rate alone is not a valid optimization objective;
- logical hits must be distinguished from physically resident hits and faulted/degraded hits;
- budget sweeps should include whole token-working-set boundaries and explicit OS/runtime headroom;
- page faults, swap/compression, RSS, physical residency, hit service time, throughput, and tails are required evidence;
- WASTE's observed cache floor is a baseline to reproduce or refute per model, representation, hardware, and policy, not a universal invariant.

Phase 9 independently reproduced WASTE's sampled LRU/LFRU semantics for replay only; no WASTE source was imported. Its evidence agrees that physical residency and headroom, rather than logical hit rate alone, control safe cache sizing, but does not transfer WASTE's approximately 17 GB floor to GGUF/MXFP4 on this discrete-GPU host. The corrected full-K3 MXFP4 top-16 working set is 25,829,572,608 bytes, and the safe exact-layout sweep observed no paging cliff. Global LRU/ALWAYS remains this runtime's default because no non-LRU pair passed the fixed online/statistical gates, not because WASTE selected or implied it.

### Prefetch and routing findings

WASTE distinguishes exact current-layer issue-ahead from prediction. Once routing has selected all experts for a layer, issuing those reads ahead is exact and produced the measured overlap gain.

Its pinned cross-layer experiment over a 214-token mixed trace reported recall@16 approximately as:

```text
random experts                    1.8%
static per-layer hot experts     20.5%
previous token's same-layer set  29.5%
layer-to-next-layer predictor    29.0%
in-sample fitted ceiling         49.7%
measured break-even              about 60%
```

On that bandwidth-saturated CPU/UMA engine, cross-layer prediction did not beat the previous-token baseline and would increase total reads. This is a strong negative result for WASTE, not a universal rejection of prediction behind PCIe or on a different compute/storage balance.

WASTE also measured a relatively flat selected-expert routing distribution: ranks 9–16 carried about 33.3% of K3 routing mass, and the lower half carried about 32% on Kimi-Linear. Therefore a cheap low-weight tail could not be assumed for partial-record precision or selective stage loading.

Required implications:

- Phase 10 must compare learned predictors against random, static-hot, and previous-token baselines;
- break-even must be re-derived from this runtime's actual hidden latency, bytes, bandwidth, transfer path, and pollution cost;
- exact issue-ahead must not be reported as predictive prefetch;
- Phase 12 must measure routing-weight distributions before considering partial expert records or per-activation precision.

### Batching finding

WASTE measured that grouping tokens reduced marginal expert reads by about 70–76% while leaving per-token expert computation essentially unchanged. Its measured batching ceiling therefore came from the remaining compute and did not multiply independently with I/O overlap.

Phase 13 must record unique expert records separately from token-expert compute pairs and decompose batching gains into I/O deduplication, transfer avoidance, compute utilization, and scheduling.

### Storage-format comparison

WASTE's custom record layout achieves one aligned read per expert and computes directly from its 3-bit representation. This is relevant to Phase 12's GGUF decision, but it does not by itself justify a new format:

- GGUF must first be measured using real expert spans and split-file behavior;
- comparisons must normalize bytes/expert, bytes/token, read count, amplification, quality, conversion footprint, and kernel cost;
- direct code or format reuse requires an Apache-2.0 attribution and license review;
- no WASTE code has been imported into this project at this review point.

### Candidate reuse after isolated review

Potentially reusable methodology or small units include:

1. real-record disk benchmark methodology;
2. working-set and memory-budget sweep methodology;
3. route-ID/route-weight trace capture and simple predictor baselines;
4. cache telemetry that exposes evictions, bytes, and physical-memory failure modes;
5. one-record-per-expert layout as a Phase 12 comparator;
6. bounded reader-queue tests and byte-identical synchronous/read-ahead checks.

Do not copy the runtime wholesale. Preserve this project's provider, ownership, cancellation, generation, GGUF, CPU/CUDA, and evidence contracts.

## 11. Colibrì v1.4.0 — source-MXFP4 K3 streaming

- Repository: <https://github.com/JustVugg/colibri>
- Pinned reviewed commit: `b085b48888a88d9a1c00b151a9979774b72cdbfd`.
- License: Apache-2.0.
- Detailed review: [`COLIBRI_K3_PRIOR_ART.md`](COLIBRI_K3_PRIOR_ART.md).
- Observed state: active external implementation with a text-only full-K3 engine.

Colibrì provides the closest published fidelity and physical-layout baseline for this project:

- source routed-expert MXFP4 codes and scales are executed without persistent requantization;
- the original safetensors layout is reported to place each complete expert bundle contiguously, enabling one `pread` per expert;
- an optional spec-valid safetensors repack keeps expert payloads byte-identical while ordering experts and prequantizing the resident trunk;
- K3 uses a bounded per-layer LRU, offset-ordered parallel direct reads, compute/read overlap, optional multi-drive placement, and CPU/Vulkan tiers;
- single-request chunked prefill reports about 2.7x unique-expert deduplication while preserving the documented output comparison.

The public 25 GB memory headline is not accepted for K3: the pinned K3 document reports about 35 GiB for the int4 resident trunk before state, buffers, and expert cache. The low-memory figure belongs to a different streamed model-family regime.

The reported K3 decode improvement from roughly 21 seconds/token to 9.4 seconds/token is host-specific. WASTE remains the stronger published throughput baseline, while Colibrì is the stronger direct source-MXFP4 and safetensors-layout baseline.

Required implications are normative in [`plan/12-colibri-comparison.md`](plan/12-colibri-comparison.md):

- Phase 10 is not expanded because K3-specific learned-pinning or predictive-lookahead evidence was not found at the pinned revision;
- Phase 12 must compare original/repacked safetensors, GGUF, WASTE, ordinary parallel direct reads, `io_uring`, offset ordering, and multi-NVMe where available;
- Phase 12.5 must retain backing-file offset, submission order, and drive identity where available;
- Phase 13 must establish a bit-exact single-request chunked-prefill baseline before cross-request batching;
- any full-size evidence that materially reverses Phase 9's global-LRU decision must return to design authority instead of silently changing the default.

Reuse only isolated, independently tested mechanisms. Do not transplant Colibrì's one-engine-per-family runtime or model-specific lifetime assumptions into the provider.

## 12. Kimi-K3 support PR

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

## 13. Cache-aware / locality-aware MoE routing

Phase 13.6 should treat the following work as explicit prior art. These papers establish that expert residency/reuse can be incorporated into MoE routing and that there is a real quality/locality frontier; they do **not** remove the need for the Kimi K3-specific experiment because #77 uses a different bounded policy and measures the resulting real route evolution on full K3.

### 13.1 Mixture of Cache-Conditional Experts for Efficient Mobile Device Inference

Andrii Skliar et al., arXiv:2412.00099, published in TMLR (06/2025):
<https://arxiv.org/abs/2412.00099>

Directly relevant prior art. The paper introduces training-free cache-aware routing for memory-constrained MoE inference, explicitly trading router preference for cached-expert reuse. It evaluates language modeling, MMLU and GSM8K and reports up to 2× on-device speedup. The key conceptual overlap with #77 is that cache state may affect **expert membership selection** while the underlying model/router remains otherwise unchanged.

Important distinction for #77: rather than applying an unconstrained cache prior/reranking bias, Phase 13.6 uses an explicit deterministic substitution policy with a **hard per-swap router-score regret bound** and a hard `max_swaps` bound. The exact top-16 remains the reference decision and final expert weights continue to come from the original unbiased probabilities.

### 13.2 MoE-ERAS: Expert Residency Aware Selection

Abhimanyu Rajeshkumar Bambhaniya, Sashankh Chengavalli Kumar, Tushar Krishna, MLArchSys 2024:
<https://openreview.net/forum?id=o43eHjPEMO>

Earlier residency-aware routing work that explicitly selects experts considering both performance and accuracy and presents a speedup/quality trade-off. It is useful as historical evidence that expert residency can legitimately be part of the selection objective for offloaded MoEs.

Difference from #77: Phase 13.6 is specifically interested in **near-tie bounded substitution** in Kimi K3's 896→16 router, with exact-route controls, cache-state contemporaneity, downstream route evolution, and semantic-drift instrumentation.

### 13.3 Not All Models Suit Expert Offloading: On Local Routing Consistency of Mixture-of-Expert Models

Jingcong Liang et al., arXiv:2505.16056:
<https://arxiv.org/abs/2505.16056>

Studies local routing consistency across 20 MoE LLMs and introduces metrics such as Segment Routing Best Performance and Segment Cache Best Hit Rate. Its main relevance is methodological: the amount of naturally exploitable expert locality varies materially between models, so results from DeepSeek/Qwen/Mixtral should not be assumed to transfer to Kimi K3.

For #77 this supports measuring K3's own real route streams and cache-capacity frontier rather than extrapolating from another MoE architecture.

### 13.4 ReMoE: Boosting Expert Reuse through Router Fine-Tuning in Memory-Constrained MoE LLM Inference

Xiongwei Zhu et al., arXiv:2605.27081, accepted at ICML 2026:
<https://arxiv.org/abs/2605.27081>

Adjacent but important prior art. ReMoE fine-tunes the router to favor recently selected experts and increase temporal expert reuse. The paper reports ~26% improved expert reuse while maintaining downstream task performance, plus real-system gains under vLLM offloading and llama.cpp/Jetson evaluation.

Difference from #77: ReMoE changes the router through training; Phase 13.6 is **training-free** and changes membership only at inference time under explicit regret/swap bounds. ReMoE nevertheless strengthens the premise that routing locality is an optimization dimension worth targeting and that quality must be measured as part of the locality frontier.

### Phase 13.6 positioning relative to prior art

The novelty/value of #77 should therefore be stated narrowly rather than as the first cache-aware MoE routing idea. The K3-specific questions are:

- whether a very fine-grained **896-expert / top-16** router exposes enough near-tie slack to improve locality;
- whether a **hard-regret-bounded**, deterministic, training-free substitution policy gives a better-controlled quality/locality frontier than cache-prior reranking;
- how intentional swaps alter the **real subsequent K3 route stream**, rather than only an offline replay of the original route;
- how perturbations propagate through **local MoE output → hidden states → induced routing divergence → logits/NLL**;
- how the frontier behaves under genuinely out-of-core local-NVMe execution and project-relevant cache capacities;
- where the practical knee and the upper-bound/stress region (`max_swaps` through 16) occur.

These works should be cited in the final #77 interpretation and used to compare methodology/results, while preserving the preregistered K3 experiment and avoiding retrofitting the policy to reproduce prior-art outcomes.

## 14. Expert specialization, domain overlap, and standing-committee structure

Phase 13.6P-G (#102) exposes a new K3-specific question that is distinct from both static router geometry (#75) and expert substitutability (#81): whether **actual hidden-state-conditioned expert activation** is organized primarily by domain/family, by a shared cross-domain core plus specialized periphery, or by a mixed layer-dependent regime.

The two works below provide complementary priors. Neither result is transferred into K3 as an assumption.

### 14.1 PipeNetwork `kimi-k3-mlx` — K3 domain-conditioned REAP saliency

- Repository: <https://github.com/PipeNetwork/kimi-k3-mlx>
- Pinned inspected commit: `20a4fb101ce81380ab8af0036743d49e7256c521`.
- Relevant files: `README.md`, `scripts/reap_calibrate.py`, `scripts/reap_overlap.py`, `scripts/reap_subset.py`.
- Code provenance note: no root `LICENSE` file was present at the pinned revision when inspected. Treat this entry as **methodology/evidence prior art only** until the license and attribution boundary for PipeNetwork-authored code is resolved; do not copy scripts into this project merely from this reference.

The project applies REAP-style expert saliency to Kimi K3. Its calibration path tags tokens by source corpus and accumulates a per-source, per-layer, per-expert score based on router gate and expert-output magnitude. `reap_overlap.py` then normalizes each source/layer and compares top-N salient-expert sets.

For its published top-242 comparison over K3's 896 routed experts, the random independent-set overlap reference is about `242/896 = 27%`. At the pinned revision the README reports examples including:

```text
code-python <-> code-multi    57.2%
lang-de <-> lang-es           59.3%
lang-de <-> web-en            56.5%
chinese <-> lang-ja           42.8%
chinese <-> code-python       17.8%   # below random-set expectation
```

The same project also reports targeted-pruning demonstrations in which changing the calibration-domain mixture changes retained capability. The authors explicitly bound that demonstration: the quoted generation comparison uses one prompt per domain and 24 greedy tokens and is not a rigorous domain evaluation by itself; the repository therefore adds held-out source-bucketed perplexity as the stronger evaluation path.

#### Relevance and non-equivalence to this project

This is the closest K3-specific external evidence found for domain-conditioned expert structure, but its observable is **REAP saliency**, not the production top-16 demand stream and not cache reuse distance. A high-saliency expert need not have the same frequency/rank behavior as an actually selected expert under #102's free-generation traces.

Use the work to motivate:

- per-family expert-set overlap normalized against a random `N/896` reference;
- source/family-specific versus shared expert mass;
- sensitivity across fixed N rather than cherry-picking one top-set size;
- held-out or leave-one-family-out validation before claiming specialization.

Do not numerically pool its overlap percentages with #102. Corpus, metric, normalization, pruning objective, route horizon and runtime are different.

### 14.2 CommitteeAudit — domain-invariant Standing Committee

Yan Wang, Yitao Xu, Nanhan Shen, Jinyan Su, Jimin Huang, and Zining Zhu, *The Illusion of Specialization: Unveiling the Domain-Invariant "Standing Committee" in Mixture-of-Experts Models*, ACL 2026:

- ACL Anthology: <https://aclanthology.org/2026.acl-long.665/>
- arXiv: <https://arxiv.org/abs/2601.03425>
- Code referenced by the paper: <https://github.com/The-FinAI/CommitteeAudit>

The paper introduces **COMMITTEEAUDIT**, a post-hoc group-level analysis of MoE routing. Across three representative MoE models and MMLU domains, it reports a compact coalition of routed experts that captures a majority of routing mass across otherwise different domains, with a more domain-specific periphery.

The important prior for this project is structural, not functional:

```text
shared routed core / "standing committee"
        +
more workload-specific peripheral experts
```

This provides a direct competing hypothesis to a purely domain-specialized interpretation of the PipeNetwork-style overlap result.

#### Adaptation boundary for K3

CommitteeAudit analyzes routing-weight profiles. #102 should reproduce that construction only if its instrumentation can expose the required complete routing-weight information without changing runtime/model semantics. If the available observer provides selected top-16/top-M routes only, the K3 result must be labeled a **top-k CommitteeAudit-inspired analysis**, not an exact reproduction.

Do not transfer the paper's qualitative functional labels (for example reasoning/syntax versus domain knowledge) to K3 from routing frequency alone. #102 can establish structural recurrence, overlap, core/periphery working sets and cache behavior; functional attribution requires separate intervention/quality evidence.

### 14.3 Positioning against #75, #81, and #102

These questions are deliberately distinct:

```text
#75 static router geometry:
    do router vectors themselves form broad stable expert families?

#81 substitutability:
    when expert i is actually competitive, which nearby alternatives are low-regret
    and semantically safe substitutes?

#102 dynamic specialization/core-periphery:
    which experts are actually selected across semantic families and lengths,
    and how does that organization determine cache locality / backing loads?
```

#75's negative result for broad static geometric clusters does **not** preclude a dynamic standing committee, because actual selection is hidden-state conditioned. Conversely, repeatedly selecting the same expert across domains does not imply that expert is substitutable with another; #81 owns that question.

The accepted #102 post-hoc amendment therefore uses EXACT observer captures, family overlap/fingerprint analysis, leave-one-family-out standing-committee construction, core-versus-periphery reuse/cache decomposition, and a replay-only `COMMITTEE_PIN_COUNTERFACTUAL`. Any positive pinning result remains nonphysical and cannot change the production cache policy inside #102; a later bounded issue and physical validation would be required.

Possible valid K3 outcomes include:

```text
MOSTLY_SHARED
CORE_PERIPHERY
DOMAIN_SPECIALIZED
MIXED_BY_LAYER
NO_STABLE_STRUCTURE
```

A negative or heterogeneous result is evidence, not a reason to redefine the family set, committee threshold, or routing policy.

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