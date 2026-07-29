# Foundation and monolithic baseline

## Phase 0 — Repository and evidence discipline

### Objectives

- Establish this repository as the authoritative project record.
- Ensure every benchmark can be reproduced from committed metadata.
- Prevent architecture drift across ChatGPT and Codex sessions.

### Tasks

- [x] Clone `murillo128/k3-out-of-core` into the development workspace.
- [ ] Add scripts for environment, hardware, model, and build manifests.
- [x] Define a results directory convention:

  ```text
  results/YYYY-MM-DD/<host>/<experiment-id>/
  ```

- [x] Define a machine-readable result structure containing configuration, revisions, metrics, and checksums.
- [ ] Add a decision-update checklist to pull requests.
- [ ] Add CI for Markdown links, formatting, and any scripts introduced here.
- [ ] Select a project license before importing third-party code.

### Exit gate

- [x] A new agent can identify the current phase, exact upstream revision, local patches, models, commands, and outstanding decisions without relying on chat history.

---

## Phase 1 — Reproducible K3 monolithic baseline

### Objectives

- Prove that the tiny K3 F16 and MXFP4 checkpoints convert and execute in the selected `llama.cpp` revision.
- Establish CPU and CUDA correctness/performance baselines before changing residency.

### Current evidence

- Project evidence commit: `750df633509d84893fed6c8cdebffafacf2636f0`.
- Pinned `llama.cpp` commit: `84245db4c790af22135f34992689edcc11877003`.
- Published GGUF revision: `88de02cf8fa37f87eb06daaed370ac9c3411d5ca`.
- Evidence directory: `results/2026-07-29/skynet/phase1/`.
- Conversion evidence confirms 21 repacked MXFP4 expert groups and 35 resident F16 dequantizations.
- CPU inference was observed successfully for both artifacts, but the committed CPU logs contain only the expected no-GPU warning and must be recaptured before the Phase 1 exit gate is closed.
- CUDA logs record successful F16 and MXFP4 execution with `-ngl 999`, identical smoke-test continuation, and exit code 0; explicit backend placement is not present in those logs.
- CPU and CUDA CTest runs passed 54 of 55 tests. `test-tokenizers-ggml-vocabs` failed because checked-out fixture files were Git LFS pointer text rather than GGUF payloads; the fixture-dependent test remains unresolved.

### Tasks

#### 1.1 Pin upstream

- [x] Fetch `ggml-org/llama.cpp` PR #26185.
- [x] Record the selected exact commit SHA.
- [ ] Record the upstream base commit and a complete local-diff description.
- [x] Record the policy not to automatically track a moving PR head during experiments.
- [x] Record the policy that any rebase or update requires a dedicated commit followed by a complete baseline rerun.

#### 1.2 Build environments

- [x] Create separate CPU and CUDA build directories.
- [ ] Record compiler, CMake, CUDA toolkit, driver, GPU, and build flags completely.
- [x] Build the relevant tests and tools for CPU and CUDA.
- [ ] Separate stable architecture/conversion tests from network or external-fixture tests and resolve or explicitly quarantine `test-tokenizers-ggml-vocabs`.

#### 1.3 Normalize Python conversion environment

- [x] Pin `transformers==4.57.6` for the selected branch.
- [x] Install `tiktoken` and the conversion requirements.
- [x] Record the conversion package versions in the published conversion manifest.
- [ ] Back up and patch tokenizer configuration through a reproducible project command.
- [ ] Convert the tokenizer workaround into a script or committed patch; do not leave manual edits undocumented.

#### 1.4 F16 conversion

- [x] Convert `Kimi-K3-0.40B` to F16 GGUF.
- [x] Capture the complete conversion log.
- [x] Record tensor names, shapes, metadata, artifact size, and file checksum.
- [ ] Validate and record tokenizer metadata, BOS/EOS, and end-of-message behavior.

#### 1.5 MXFP4 conversion

- [x] Review and commit converter support for the observed 35 resident packed tensors.
- [x] Preserve the 168 routed expert tensors in MXFP4.
- [x] Require and observe exactly 21 repacked expert groups and 35 resident dequantizations for this checkpoint revision.
- [ ] Sample source and GGUF blocks and validate MXFP4 repacking byte-for-byte or through a trusted dequantization comparison.
- [x] Capture the complete conversion log, artifact size, and checksum.

#### 1.6 Monolithic inference

- [x] Run F16 CPU inference.
- [x] Run hybrid MXFP4 CPU inference.
- [x] Build CUDA and run both models with `-ngl 999`.
- [ ] Confirm and record actual backend placement, supported-layer offload, and any CPU fallback nodes.
- [ ] Recapture complete CPU inference logs in the committed evidence directory.
- [ ] Capture deterministic prompt/generated token IDs and selected logits.
- [ ] Run a perplexity/loss corpus suitable for the toy model.
- [ ] Run repeated warm inference to detect persistent-state errors.

#### 1.7 Baseline benchmark

- [ ] Measure prompt and decode throughput separately with repeated runs and a declared aggregation method.
- [ ] Record CPU/GPU memory usage.
- [ ] Record per-token latency distribution.
- [ ] Record model-load time.

### Exit gate

- [x] Both GGUF files are reproducibly generated and checksum-verified.
- [x] CPU inference works for both artifacts.
- [ ] CUDA backend placement is understood and either works fully or has a committed blocking issue.
- [ ] No unexplained tensor drop, NaN, invalid expert ID, tokenizer mismatch, or unresolved required test remains.
- [ ] Complete baseline evidence, including usable CPU logs and required correctness comparisons, is committed.

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
