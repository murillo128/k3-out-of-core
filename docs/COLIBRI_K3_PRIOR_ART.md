# Colibrì Kimi K3 prior art

Reviewed on **2026-08-01**.

This document records Colibrì v1.4.0 as external prior art and a mandatory future Kimi K3 comparison baseline. It does not authorize code import, change the active Phase 10 contract, reopen the accepted Phase 9 cache-policy decision, or make social-media summaries part of project evidence.

Use the repository status markers precisely:

- `OBSERVED`: verified from the pinned repository code or documentation;
- `SPECULATIVE`: plausible or externally summarized behavior not reproduced with a complete project-quality manifest;
- `OPEN`: a project comparison or reuse decision deferred to a controlling issue;
- `REJECTED`: incompatible with current correctness or architecture constraints.

## Sources and reviewed state

- Repository: <https://github.com/JustVugg/colibri>
- Reviewed commit: `b085b48888a88d9a1c00b151a9979774b72cdbfd`
- Release line: `v1.4.0`
- License: Apache-2.0
- K3 design record: <https://github.com/JustVugg/colibri/blob/b085b48888a88d9a1c00b151a9979774b72cdbfd/docs/kimi_k3.md>
- Main project overview: <https://github.com/JustVugg/colibri/blob/b085b48888a88d9a1c00b151a9979774b72cdbfd/README.md>

Colibrì is a vertical pure-C inference family: each supported model family has a dedicated engine while sharing storage, quantization, tokenizer, server, and hardware helpers. Its K3 path is text-only; the vision shards are not consumed.

## Correct interpretation of the public memory claim

The public summary that a 2.8T model runs with only 25 GB of RAM conflates two model-family configurations.

`OBSERVED` for the pinned K3 path:

```text
resident non-routed weights, int4       about 35 GiB
resident non-routed weights, int8       about 57 GiB
default routed-expert LRU budget          8 GiB
additional state/buffers/KV               required
```

The 25 GB low-memory statement in the general project description applies to a heavily streamed GLM-5.2 regime, not to the documented K3 resident trunk. A realistic K3 run must account separately for the resident trunk, KDA/MLA state, graph and I/O buffers, tokenizer/runtime state, and the routed-expert cache.

Therefore:

- K3 support is `OBSERVED`;
- K3 in 25 GB is `REJECTED` as a project baseline unless a reproducible K3-specific manifest demonstrates it;
- reports must not transfer RAM floors between model families.

## `OBSERVED` K3 representation and fidelity

Colibrì runs the released routed experts in their source MXFP4 representation:

```text
routed experts               896 per MoE layer
selected experts              top 16
expert payload                about 17.55 MB
expert instances              82,432
aggregate routed payload      about 1.45 TB
```

The expert path consumes the source E2M1 codes and UE8M0 scales directly. Expert bytes are not dequantized and requantized into a different persistent format. Colibrì implements scalar, AVX2, and Vulkan MXFP4 execution paths around this representation.

Everything outside the routed experts is originally BF16 and is either:

- quantized at load time to per-row int8 or int4-g64 according to configuration; or
- loaded from an optional repacked safetensors container containing the same precomputed trunk quantization.

The precise fidelity claim is therefore:

> The routed-expert QAT MXFP4 bytes are preserved; the non-routed trunk uses a separately chosen and quality-sensitive quantization policy.

Do not describe the complete runtime as unquantized or whole-model lossless.

## `OBSERVED` source-layout and repack findings

### Direct source safetensors

Colibrì reports that the six tensors comprising one routed expert are contiguous in the original safetensors shards. It uses one `pread` per complete expert bundle. Experts are not necessarily ordered by logical expert ID inside a shard, so a token's misses are sorted by file offset before parallel submission.

This is important prior art because it creates a fourth physical layout point beyond the project's current GGUF alternatives:

1. separate GGUF projection spans;
2. coalesced or sequential GGUF bundle reads;
3. a custom one-record-per-expert format such as WASTE;
4. original safetensors or spec-valid repacked safetensors with one contiguous expert read.

The original checkpoint may therefore be a stronger storage baseline than assuming safetensors necessarily requires scattered reads.

### Optional repacked safetensors

The K3 repacker:

- copies routed expert bytes without changing the MXFP4 payload;
- orders experts deterministically;
- stores the large non-routed matrices already quantized in the engine's load-time representation;
- drops the unused vision payload for the text-only runtime;
- writes spec-valid safetensors shards and a regenerated index;
- can re-read and compare every routed expert byte against the source;
- reduces startup by avoiding a full load-time trunk quantization pass.

Published approximate footprints are:

```text
source checkpoint                    about 1.56 TB
repacked int8-trunk container        about 1.50 TB
repacked int4-trunk container        about 1.48 TB
```

This is not automatically evidence that the project should abandon GGUF. It is a mandatory measured comparator for Phase 12's storage decision.

## `OBSERVED` K3 streaming mechanisms

The pinned K3 record describes:

- a bounded per-layer routed-expert LRU;
- `O_DIRECT` by default, with buffered fallback;
- sorting selected misses by backing-file offset;
- parallel reads over the active working set;
- loader threads that overlap expert `j` computation with expert `j+1` loading;
- direct MXFP4 arithmetic with an optional integer-dot activation path;
- optional multi-directory placement for splitting shards across drives;
- a Vulkan tier for permanently resident shared experts and fill-once routed experts, with transparent CPU fallback.

The reported host-specific storage result is:

```text
buffered expert reads       about 1.8 GB/s
O_DIRECT parallel reads     about 6.3 GB/s
measured drive ceiling      about 7.1 GB/s
decode before changes       about 21 s/token
decode after changes        about 9.4 s/token
```

The resulting generation rate is roughly 0.1 token/s on that host. This is a feasibility and mechanism result, not interactive performance.

The comparison must separate four effects that a headline can otherwise conflate:

1. expert layout and read amplification;
2. request ordering and queue depth;
3. direct versus buffered I/O API behavior;
4. overlap with CPU or GPU expert arithmetic.

`io_uring` must earn its value against this simpler `pread`/`O_DIRECT` pipeline rather than being treated as an advantage by construction.

## `OBSERVED` chunked-prefill result

Colibrì's K3 prefill processes a single request in layer-major chunks while preserving sequential state updates. At a documented chunk size of 32 tokens it reports:

```text
unique-expert deduplication        about 2.7x
expert bytes per prefill token     25.8 GB -> 9.6 GB
prefill time per token              5.3 s -> 2.0 s
output equivalence                 bit-identical in the reported comparison
```

This is distinct from multi-request batching. It is a mandatory single-request baseline for Phase 13 before attributing later gains to cross-request coalescing.

## Validation evidence and caveats

The K3 record includes:

- an independent NumPy layer-stack reference;
- real-checkpoint layer comparisons across the K3 layer types;
- tokenizer cross-checks against the checkpoint's tiktoken behavior;
- exact multi-turn token-ID comparisons for the documented XTML chat encoding;
- Vulkan-versus-CPU MXFP4 kernel comparisons.

A project reproduction must still bind exact commands, prompts, model revision, hardware, memory configuration, and output hashes. The external validation does not replace this project's monolithic GGUF correctness oracle.

Important non-equivalent or optional modes:

- the K3 runtime is text-only;
- `K3_IDOT` changes activation arithmetic and requires a quality/correctness A/B;
- `K3_TOPP` drops selected experts and changes router semantics, so comparable baseline runs must keep it disabled;
- Vulkan placement is not evidence for CUDA performance;
- general Colibrì claims about learned pinning or one-layer-ahead prediction must not be attributed to K3 unless K3-specific traces and A/B results are supplied.

## Relationship to WASTE

Colibrì and WASTE represent complementary K3 baselines:

| Baseline | Routed representation | Approximate model footprint | Published decode regime |
|---|---|---:|---:|
| WASTE | custom 3-bit residual vector quantization | 982 GiB | 0.45–0.62 tok/s |
| Colibrì | source MXFP4 experts preserved | 1.48–1.56 TB | about 0.1 tok/s on the documented host |
| This project | GGUF MXFP4 experts preserved | about 1.45 TB expert store plus trunk | `OPEN` full-size hardware evidence |

WASTE is currently the stronger published throughput baseline. Colibrì is the more directly comparable fidelity and source-layout baseline. Phase 12 must explain differences in expert precision, bytes/token, storage bandwidth, arithmetic kernels, cache size, and hardware rather than ranking raw tokens/second alone.

## Candidate reuse after isolated review

Subject to Apache-2.0 review and attribution, independently test the smallest useful ideas:

1. detect and validate contiguous source expert bundles;
2. order same-token reads by physical file offset without changing logical consumption order;
3. compare raw safetensors, repacked safetensors, GGUF, and an optional expert record format;
4. preserve source MXFP4 bytes while changing only physical organization;
5. use full-byte verification for repacked expert stores;
6. compare direct I/O using ordinary parallel reads against `io_uring` on the same layout;
7. evaluate independent multi-NVMe sharding by measured bandwidth and controller topology;
8. use single-request chunked prefill as a deduplication baseline;
9. compare a consumer Vulkan hot tier with CPU fallback when hardware is available.

Do not transplant Colibrì's one-engine-per-family runtime, global model assumptions, tokenizer/frontend, or lifetime model into the provider. Preserve this project's generic descriptors, ownership, cancellation, policy/transport separation, canonical accumulation, CUDA/UMA goals, and evidence contracts.

## Plan implications

The normative additions are in [`plan/12-colibri-comparison.md`](plan/12-colibri-comparison.md).

Key dispositions:

- Phase 10: no scope change. K3-specific learned-pinning or predictive-prefetch evidence was not found at the pinned revision.
- Phase 12: add source/repacked safetensors, offset-ordered reads, ordinary direct-I/O, Colibrì full-size execution, and multi-NVMe as explicit comparators.
- Phase 12.5: include file offset, backing shard/drive, and read-order identity in the benchmark trace where applicable.
- Phase 13: add bit-exact single-request chunked prefill before cross-request batching.
- Phase 9: retain the accepted global LRU default; if full-size Colibrì-style per-layer evidence materially reverses the result, return to design authority rather than changing policy silently.

## Current disposition

- Colibrì K3 engine and v1.4.0 support: **`OBSERVED` external prior art**.
- Source routed-expert MXFP4 preservation: **`OBSERVED`**.
- K3 operation in 25 GB RAM: **`REJECTED` as an unsupported conflation**.
- Published K3 performance: **`OBSERVED` only for the documented host and configuration class**.
- Reusing isolated code: **`OPEN`**, subject to tests, Apache-2.0 attribution, and a controlling issue.
- Replacing the project's runtime architecture with Colibrì: **`REJECTED`**.
