# Foundation and monolithic baseline

## Phase 0 — Repository and evidence discipline

### Objectives

- Establish this repository as the authoritative project record.
- Ensure every benchmark can be reproduced from committed metadata.
- Prevent architecture drift across ChatGPT and Codex sessions.

### Tasks

- [ ] Clone `murillo128/k3-out-of-core` into the development workspace.
- [ ] Add scripts for environment, hardware, model, and build manifests.
- [ ] Define a results directory convention, for example:

  ```text
  results/YYYY-MM-DD/<host>/<experiment-id>/
  ```

- [ ] Define machine-readable result schema containing configuration, revisions, metrics, and checksums.
- [ ] Add a decision-update checklist to pull requests.
- [ ] Add CI for Markdown links, formatting, and any scripts introduced here.
- [ ] Select a project license before importing third-party code.

### Exit gate

- A new agent can identify the current phase, exact upstream revision, local patches, models, commands, and outstanding decisions without relying on chat history.

---

## Phase 1 — Reproducible K3 monolithic baseline

### Objectives

- Prove that the tiny K3 F16 and MXFP4 checkpoints convert and execute in the selected `llama.cpp` revision.
- Establish CPU and CUDA correctness/performance baselines before changing residency.

### Tasks

#### 1.1 Pin upstream

- [ ] Fetch `ggml-org/llama.cpp` PR #26185.
- [ ] Record the selected exact commit SHA.
- [ ] Record base commit, branch, and any local diff.
- [ ] Do not automatically track a moving PR head during experiments.
- [ ] Rebase or update only in a dedicated commit followed by complete baseline rerun.

#### 1.2 Build environments

- [ ] Create separate CPU and CUDA build directories.
- [ ] Record compiler, CMake, CUDA toolkit, driver, and flags.
- [ ] Build relevant tests and tools.
- [ ] Run architecture/conversion tests that do not depend on moving external fixtures separately from network fixture tests.

#### 1.3 Normalize Python conversion environment

- [ ] Pin `transformers==4.57.6` for the selected branch.
- [ ] Install `tiktoken` and all conversion requirements.
- [ ] Record exact package lock or `pip freeze` subset.
- [ ] Back up and patch tokenizer configuration reproducibly.
- [ ] Convert the workaround into a script or committed patch; do not leave manual edits undocumented.

#### 1.4 F16 conversion

- [ ] Convert `Kimi-K3-0.40B` to F16 GGUF.
- [ ] Capture complete conversion log.
- [ ] Record tensor count, names, shapes, metadata, and file checksum.
- [ ] Validate tokenizer metadata, BOS/EOS, and end-of-message behavior.

#### 1.5 MXFP4 conversion

- [ ] Review and commit converter support for the observed 35 resident packed tensors.
- [ ] Preserve the 168 routed expert tensors in MXFP4.
- [ ] Require exactly 21 repacked expert groups and 35 resident dequantizations for this checkpoint revision.
- [ ] Sample source and GGUF blocks and validate MXFP4 repacking byte-for-byte or through a trusted dequantization comparison.
- [ ] Capture complete conversion log and checksum.

#### 1.6 Monolithic inference

- [ ] Run F16 CPU inference.
- [ ] Run hybrid MXFP4 CPU inference.
- [ ] Build CUDA and run both models with all supported layers offloaded.
- [ ] Record backend placement and identify any CPU fallback nodes.
- [ ] Capture deterministic token IDs and selected logits.
- [ ] Run a perplexity/loss corpus suitable for the toy model.
- [ ] Run repeated warm inference to detect persistent-state errors.

#### 1.7 Baseline benchmark

- [ ] Measure prompt and decode throughput separately.
- [ ] Record CPU/GPU memory usage.
- [ ] Record per-token latency distribution.
- [ ] Record model-load time.

### Exit gate

- Both GGUF files are reproducibly generated.
- CPU inference works for both.
- CUDA behavior is understood and either works or has a committed blocking issue.
- No unexplained tensor drop, NaN, invalid expert ID, or tokenizer mismatch remains.
- Baseline evidence is committed.

---

## Phase 2 — Routing and expert-layout observability

### Objectives

- Expose the actual expert selections and physical storage spans without changing computation.
- Produce traces for cache simulation and exact storage metadata for out-of-core reads.

### Tasks

#### 2.1 Router trace

- [ ] Add an opt-in trace hook at the selected-expert ID tensor.
- [ ] Record model, layer, token/batch position, selected IDs, routing weights, request/sequence ID, and phase (prefill/decode).
- [ ] Use a compact binary or structured format with a version.
- [ ] Ensure trace-disabled overhead is negligible.
- [ ] Ensure trace order is deterministic.

#### 2.2 Expert storage map

- [ ] Extend the GGUF/model loader to expose file identity, file offset, byte size, type, logical shape, row stride, and alignment for each routed-expert projection.
- [ ] Define `ExpertStorageEntry` containing gate/up/down spans.
- [ ] Validate every span against GGUF metadata and file bounds.
- [ ] Do not infer offsets through virtual-memory mappings.

#### 2.3 Offline simulator

- [ ] Implement trace replay independent of GGML/CUDA.
- [ ] Model hot and cold capacities in bytes and expert slots.
- [ ] Implement LRU baseline.
- [ ] Implement perfect-oracle lower bound.
- [ ] Report tier hit rates, bytes, evictions, reuse distances, and theoretical stalls.
- [ ] Separate prefill and decode analysis.

#### 2.4 Trace corpus

- [ ] Capture multiple prompts and domains.
- [ ] Capture short and long decode.
- [ ] Capture small and large prefill.
- [ ] Preserve raw traces and summarized results with model/checkpoint revisions.

### Exit gate

- Every routed expert can be mapped to exact backing-file spans.
- Real K3 traces can be replayed offline.
- No inference result changes with tracing enabled.

---

## Phase 3 — Provider abstraction with resident parity

### Objectives

- Introduce the final integration seam without changing residency or performance behavior.

### Tasks

#### 3.1 Interfaces

Define and review concepts equivalent to:

```text
ExpertKey
ExpertBundleDescriptor
ExpertSelection
ExpertExecutionPlan
ExpertHandle
ExpertWeightProvider
ExpertStorage
ExpertTransport
CachePolicy
```

- [ ] Keep C ABI boundaries minimal where CPU and CUDA compilation units cannot link directly.
- [ ] Define ownership and lifetime explicitly.
- [ ] Define cancellation and error propagation.
- [ ] Define per-model and per-device context; prohibit global singleton model state.

#### 3.2 Resident provider

- [ ] Implement pass-through provider for a fully resident model.
- [ ] Always remap or always preserve IDs according to one invariant; avoid paths where ID meaning changes ambiguously.
- [ ] Add zero-capacity/disabled path with negligible overhead.

#### 3.3 Lifecycle

- [ ] Model creation, provider initialization, request use, model unload, device reset, and failure cleanup.
- [ ] Multiple model contexts in one process.
- [ ] Repeated load/unload stress tests.

### Exit gate

- Resident-provider output matches the original path.
- Default-path performance regression is within the predeclared noise budget.
- Interfaces are reviewed against discrete, UMA, disk, multi-request, and multi-GPU requirements.

---
