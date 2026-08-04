# Foundation, observability, and provider abstraction

## Phase 0 — Repository, provenance, and baseline freeze

### Objectives

- Freeze the reference artifacts, repositories, and validation environment.
- Define reproducibility and comparison rules before implementation.

### Tasks

#### Repository setup

- Create `murillo128/k3-out-of-core`.
- Add `llama.cpp` as a pinned submodule or exact revision dependency.
- Record exact commits for:
  - upstream `llama.cpp`;
  - K3 support branch/PR;
  - converter patch;
  - prior-art forks.
- Add CI for formatting, unit tests, schema validation, and small model tests.

#### Artifact manifest

- Create a machine-readable manifest containing:
  - model repository and revision;
  - original shard hashes;
  - tokenizer/config hashes;
  - conversion command;
  - converter revision;
  - `llama.cpp` revision;
  - GGUF output hash;
  - quantization details;
  - expected file size and metadata summary.
- Preserve the original F16 and MXFP4 artifacts.
- Never overwrite converted artifacts in place.

#### Baseline environment

- Record:
  - OS, kernel, filesystem, and mount options;
  - CPU, RAM, GPU, VRAM, PCIe generation/width;
  - NVMe model, firmware, and benchmark results;
  - CUDA, compiler, CMake, and build flags;
  - NUMA topology;
  - huge-page and swap configuration.
- Add reproducible scripts for environment capture.

### Exit gate

- A clean machine can reproduce the reference builds and locate every required artifact by immutable ID.
- No benchmark or correctness comparison depends on an unrecorded local file.

---

## Phase 1 — Reproducible Kimi K3 monolithic baseline

### Objectives

- Prove that the chosen tiny K3 artifacts load and run correctly before any residency changes.
- Establish numerical and performance baselines.

### Tasks

#### Build matrix

- Build CPU and CUDA configurations from the pinned source.
- Record all CMake options and compiler versions.
- Run stable unit tests.
- Record optional external-data test failures separately.

#### Model validation

- Convert or acquire:
  - F16 GGUF;
  - hybrid MXFP4 GGUF.
- Validate:
  - metadata;
  - tensor counts and types;
  - tokenizer special tokens;
  - model size;
  - first-token generation;
  - repeated inference.

#### Correctness baseline

For fixed prompts, seeds, and runtime options, capture:

- prompt token IDs;
- generated token IDs;
- selected routing records where available;
- sampled logits or full-logit hashes;
- NaN/Inf checks;
- deterministic behavior across repeated runs.

#### Performance baseline

Measure:

- model load time;
- prompt throughput;
- decode throughput;
- TTFT;
- per-token p50/p95/p99 latency;
- peak RSS;
- peak VRAM;
- CPU utilization;
- storage read volume.

Use one warm-up, multiple measured runs, raw data, median, and dispersion.

### Exit gate

- F16 and MXFP4 artifacts run on CPU and CUDA.
- Correctness evidence is stable and reproducible.
- Baseline performance and memory results are committed.
- The tested artifact revisions and hashes are immutable.

---

## Phase 2 — Routing traces and expert storage map

### Objectives

- Capture real routing behavior.
- Prove the exact physical backing of every routed expert.
- Create the data needed for cache simulation.

### Tasks

#### Route tracing

- Add opt-in tracing at the point where selected expert IDs and final weights are available.
- Record:
  - request/run ID;
  - token position;
  - prefill/decode phase;
  - layer;
  - selected expert IDs;
  - selected weights;
  - sequence/batch information;
  - deterministic ordering.
- Keep tracing disabled by default and quantify overhead.

#### Expert storage directory

For each `(layer, expert)` record:

- source tensor names;
- gate/up/down or merged projection roles;
- source file index and identity;
- file offsets and byte lengths;
- tensor type;
- logical shape and physical strides;
- alignment;
- layout kind;
- exact file spans;
- total atomic bundle bytes.

- Support contiguous, strided, or segmented representations in the schema.
- Validate all spans against file bounds and tensor metadata.
- Reject unavailable or ambiguous file backing explicitly.

#### Trace corpus

- Create deterministic prompts covering prose, code, structured data, technical material, narrative, and English/Spanish text.
- Cover small and larger prefill plus short and longer decode within the validated context.
- Capture F16 and MXFP4 CPU traces and a representative CUDA subset.
- Commit only small fixtures, summaries, manifests, and checksums.
- Publish the complete raw corpus as an immutable external artifact.

#### Offline simulator

- Implement a GGML/CUDA-independent simulator consuming only traces and storage maps.
- Support:
  - slot and byte capacities;
  - inclusive hot/cold hierarchy;
  - LRU baseline;
  - Belady/MIN oracle lower bound;
  - hit/miss/admission/eviction accounting;
  - bytes transferred;
  - reuse distance and expert skew;
  - prefill/decode separation;
  - explicit theoretical latency/bandwidth cost models.
- Do not select a production cache policy in this phase.

### Exit gate

- Route traces are deterministic and preserve baseline outputs.
- Every routed expert maps to exact authoritative backing spans.
- The simulator reproduces hand-checkable LRU and oracle cases.
- The corpus and evidence are reproducible from pinned revisions and checksums.

---

## Phase 3 — Expert weight provider abstraction

### Objectives

- Introduce the final execution seam while all experts remain resident.
- Preserve exact default behavior and numerical results.

### Tasks

#### Provider contract

Introduce internal concepts equivalent to:

```text
ExpertKey
ExpertBundleDescriptor
ExpertSelection
ExpertGraphBinding
ExpertExecutionPlan
ExpertHandle
ExpertWeightProvider
```

- Keep original logical expert IDs distinct from future physical slot IDs.
- Treat gate/up/down weights and sidecars as one atomic expert bundle.
- Keep routing and canonical reduction outside the provider.
- Make provider state model-owned and request plans context-owned.
- Hold handles until asynchronous scheduler completion.

#### Default path

- Preserve the existing direct resident path when the provider is disabled.
- Create no provider object, plan, handle, copy, callback, allocation, or synchronization in disabled mode.
- Preserve graph topology, kernel selection, selected IDs, weights, and canonical reduction.

#### Resident provider

- Add an explicit resident-provider mode.
- Bind existing resident expert tensors without copying or remapping.
- Alias logical and execution ID tensors in resident mode.
- Prepare one bounded synchronously ready request plan per ubatch.
- Support multiple contexts, multiple models, graph reuse, repeated load/unload, cancellation, and partial initialization failure.

#### Validation

- Compare disabled and resident paths for F16/MXFP4 on CPU and CUDA.
- Require exact prompt/generated IDs and same-backend route parity.
- Apply the accepted logit comparison policy.
- Assert balanced plan/handle lifetime and no provider work on the disabled path.
- Measure disabled and resident overhead with the predeclared interleaved protocol.

### Exit gate

- The provider can reproduce the monolithic path with unchanged semantics.
- Disabled mode remains structurally zero-work.
- Ownership, lifetime, graph reuse, failure, and cleanup tests pass.
- F16/MXFP4 CPU/CUDA correctness and route parity pass.
- Performance evidence is reported against the predeclared statistical budgets; any accepted exception remains explicitly scoped and cannot weaken later gates.
- No cache, storage transport, prefetch, miss policy, or residency change is introduced.
