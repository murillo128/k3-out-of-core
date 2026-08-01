# DwarfStar native-MXFP4 SSD-streaming prior art

Reviewed on **2026-08-01**.

This document records DwarfStar's emerging native-MXFP4 DeepSeek-V4-Flash path as external prior art and a future comparison baseline. It does not authorize code import, change the K3 phase order, or make an external throughput claim part of project evidence.

Use the repository status markers precisely:

- `OBSERVED`: verified from repository code, metadata, or published artifacts;
- `SPECULATIVE`: plausible or externally claimed behavior that is not yet reproduced with a complete project-quality manifest;
- `OPEN`: a project decision or comparison deferred to a controlling issue.

## Sources and reviewed state

### Runtime

- Repository: <https://github.com/antirez/ds4>
- Branch: `ds4f-mxfp4`
- License: MIT, retaining the ds4 and GGML copyright notices.
- Branch state at review time: 18 commits ahead of `main`; treat the branch as moving and pin an exact head before any reproduction.

Mechanism commits inspected:

- `725b084db394fdbcc4198894f15c405fd47463d0` — `Add lossless DeepSeek MXFP4 GGUF conversion`
- `7bec128aaa13a86198bcbdcb88284f53ce14e7fd` — `Add portable Metal MXFP4 expert inference`

### Model artifacts

Relevant published repositories include:

- <https://huggingface.co/antirez/deepseek-v4-gguf>
- <https://huggingface.co/anemll/DSv4-Flash-MXFP4-native-flash>

The model artifact used for a project comparison must be pinned by immutable repository revision, exact filenames, file sizes, and SHA-256 values. Do not assume that similarly named 156 GB packages have identical dense-tensor precision, layout, or storage semantics.

## `OBSERVED` conversion mechanism

Commit `725b084db394fdbcc4198894f15c405fd47463d0` adds a direct MXFP4 repack path for routed experts:

- source weights must be packed `I8` codes with `F8_E8M0` scales;
- the converter changes the nibble ordering required by the GGUF MXFP4 block layout;
- it does not dequantize expert values through floating point;
- the original E8M0 scale byte is copied unchanged;
- every source code and scale is checked after the layout transform;
- expert shapes and packed sizes are validated before output.

The correct interpretation of `lossless` is therefore:

> The released routed-expert MXFP4 codes and scales are preserved exactly through a layout-only repack.

It is not a claim that every tensor in every associated GGUF is byte-identical to the source checkpoint. Dense, shared, attention, output, or control tensors may use a separate conservative conversion policy. A comparison must describe those tensors independently.

## `OBSERVED` inference and streaming mechanism

Commit `7bec128aaa13a86198bcbdcb88284f53ce14e7fd` adds a Metal-native MXFP4 path including:

- direct MXFP4 matrix-vector and matrix-matrix kernels;
- selected-expert slot kernels for the six routed experts used by DeepSeek V4 Flash;
- fused gate/up and down-result paths;
- compatibility with the existing SSD-streaming expert slot cache;
- reuse of selected experts loaded during prefill to seed the streaming decode cache;
- focused CPU and Metal MXFP4 tests.

The reviewed branch documents an SSD-streaming design in which:

- non-routed weights remain resident;
- routed experts occupy a bounded in-memory cache;
- cache misses load experts from the model backing store;
- the automatic budget reserves memory for non-routed weights, context/KV state, graph scratch, activations, and two routed layers used by overlapped streaming prefill;
- hot-expert preload remains enabled for normal use, with explicit cold-start controls for measurements;
- the requested cache budget may be reduced to stay below the backend's recommended physical-memory working set.

These are directly relevant to this project's physical-residency, cache-headroom, prefill-seeding, and exact-expert-layout work.

## External performance claim

The project owner supplied the following DwarfStar author claim:

> The `ds4f-mxfp4` branch can run a lossless MXFP4 DeepSeek V4 Flash GGUF, including SSD streaming on 128 GB systems, at more than 20 tokens/second.

Disposition: **`SPECULATIVE` pending reproduction**.

The inspected code and artifact structure make the claim technically plausible, but no complete benchmark record was located during this review that binds the headline number to all of the following:

```text
exact DwarfStar branch head
exact model revision and file hashes
machine and memory configuration
SSD model and measured real-record I/O
context and KV configuration
prompt and generated length
cold or warm cache state
expert-cache budget and preload state
hit rate and bytes read per token
ordinary target decode or DSpark/speculative decode
prefill and decode command line
p50/p95/p99 token latency
```

Until those fields are captured, do not quote `>20 tok/s` as an accepted project baseline or infer the same performance on CUDA, DGX Spark, Linux `io_uring`, or a discrete NVIDIA GPU.

## Platform scope

The inspected native-MXFP4 inference commit is Metal-specific. DwarfStar as a whole also has CUDA and DGX Spark support, but this review does not establish that the new `ds4f-mxfp4` SSD-streaming path has equivalent CUDA kernels, storage behavior, or performance.

Required distinctions in future reports:

- Metal unified memory versus CUDA coherent UMA versus discrete CUDA;
- DwarfStar's platform storage API versus this project's Linux `io_uring` transport;
- target-only decode versus DSpark effective tokens/second;
- resident execution versus SSD-streaming execution;
- preloaded/warm hot set versus a controlled cold start.

## Why this matters to `k3-out-of-core`

DwarfStar materially raises the external baseline for DeepSeek V4 Flash:

1. It demonstrates a layout-only path that preserves the source routed-expert MXFP4 representation instead of requantizing those experts to IQ2/Q2/Q4.
2. It demonstrates that a vertical runtime can combine direct low-precision expert kernels, bounded expert slots, SSD misses, overlap, and prefill-derived cache seeding.
3. The reported performance suggests that a roughly 156 GB high-fidelity package may remain interactive on a 128 GB unified-memory machine when routing locality and storage overlap are exploited.
4. It makes a simple comparison against an ultra-low-bit resident quant insufficient. A future DeepSeek evaluation must include a native-MXFP4 SSD-streaming baseline.

This does not transfer directly to K3. K3 activates far more parameters per token, has a much larger full checkpoint, and has a different working-set and routing regime. DwarfStar is evidence that the design class can work well for a favorable MoE; it is not evidence that K3 will achieve comparable throughput.

## Mandatory future comparison

When DeepSeek-V4-Flash is activated as a project validation target, compare at least:

1. a pinned current `llama.cpp` native-MXFP4 baseline;
2. DwarfStar `ds4f-mxfp4` target-only SSD streaming;
3. a lower-bit resident or nearly resident Unsloth/DwarfStar configuration;
4. this project's higher-fidelity explicit-residency path on the same hardware where practical.

The comparison must normalize or expose:

- exact expert and dense tensor representations;
- model bytes and resident bytes;
- expert bytes per token;
- storage reads and amplification;
- cache budget, hit rate, physical residency, and page pressure;
- exposed versus overlapped storage time;
- kernel and backend compute time;
- prompt throughput, TTFT, decode throughput, and latency tails;
- deterministic output and approved quality evaluations;
- speculative-decoding acceptance and overhead when enabled.

A winning result is not merely the highest headline tokens/second. The project question is whether explicit storage and residency can preserve materially more model fidelity at a useful and stable speed.

## Candidate ideas for isolated study

Review and independently test, subject to the MIT/GGML notices, the smallest useful concepts:

- exact MXFP4 code/scale repack verification;
- selected-slot MXFP4 kernel interfaces;
- layer-major or one-record-per-expert storage as a measured comparator;
- seeding the decode cache from experts already consumed during prefill;
- cache autofit that reserves complete routed-layer prefill headroom;
- cold/warm benchmark controls and expert-byte telemetry.

Do not transplant DwarfStar's vertical runtime, global assumptions, model-specific graph, or platform-specific lifetime model into the provider. Preserve this project's generic expert descriptors, ownership, cancellation, policy/transport separation, deterministic merge semantics, Linux/CUDA and UMA transports, and reproducible evidence contracts.

## Phase placement

- Phase 10 may use DwarfStar's prefill-seeding behavior as a static/hot-set baseline, without changing the approved K3 predictor scope.
- Phase 11 should treat DwarfStar Spark/CUDA results as separate evidence only after exact backend support is verified.
- Phase 12 should include DwarfStar native-MXFP4 SSD streaming as the principal external DeepSeek quality-versus-residency baseline when complete inference is in scope.
- Phase 12.5 must supply the token-level event and metric schema used for any authoritative cross-runtime performance campaign.

## Current disposition

- DwarfStar native-MXFP4 conversion and Metal execution: **`OBSERVED` external prior art**.
- `>20 tok/s` with SSD streaming on a 128 GB system: **`SPECULATIVE` pending reproduction**.
- Importing code: **`OPEN`**, allowed only as isolated reviewed units with tests, attribution, and a controlling issue.
- Replacing the project's runtime architecture with DwarfStar: **`REJECTED`**.
