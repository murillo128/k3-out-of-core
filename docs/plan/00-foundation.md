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

- Phase 1 technical exit gate: **ACCEPTED** under GitHub issue #7.
- Checkpoint C: **PASS_WITH_NOTES**
- Checkpoint C reviewed head: `4f1dcae3024bebcb932f95dfbab9ef7e5154a68c`
- Checkpoint C review: https://github.com/murillo128/k3-out-of-core/issues/7#issuecomment-5120875092
- Five earlier **FAIL / NO** attempts remain preserved. The calibrated STANDARD review accepted the technical evidence after the repository owner applied the repeated-review circuit breaker.
- Clean execution base: `511e87fc98cca8069fc57526fbb04b10789967eb`.
- Execution branch: `codex/phase1-closeout-clean`.
- Pinned `llama.cpp` commit: `84245db4c790af22135f34992689edcc11877003`.
- Published GGUF revision: `88de02cf8fa37f87eb06daaed370ac9c3411d5ca`.
- Evidence directory: `results/2026-07-29/skynet/phase1-closeout-clean/`.
- Conversion evidence confirms 21 repacked MXFP4 expert groups and 35 resident F16 dequantizations.
- Stable CPU and CUDA suites each pass 54/54; the separated external GGUF-vocabulary fixture passes 1/1 after verified Git LFS payload retrieval.
- F16 and MXFP4 CPU/CUDA inference produce exact prompt/generated IDs and pass selected-logit thresholds. Complete logs include backend placement and the known layer-3 Flash Attention CPU operation.
- An independent validator matched 81/81 stratified MXFP4 samples exactly.
- Repeated descriptive CPU/CUDA benchmarks capture load time, prompt/decode throughput, TTFT, token-latency percentiles, RSS, and CUDA VRAM.

### Tasks

#### 1.1 Pin upstream

- [x] Fetch `ggml-org/llama.cpp` PR #26185.
- [x] Record the selected exact commit SHA.
- [x] Record the upstream base commit and a complete local-diff description.
- [x] Record the policy not to automatically track a moving PR head during experiments.
- [x] Record the policy that any rebase or update requires a dedicated commit followed by a complete baseline rerun.

#### 1.2 Build environments

- [x] Create separate CPU and CUDA build directories.
- [x] Record compiler, CMake, CUDA toolkit, driver, GPU, and build flags completely.
- [x] Build the relevant tests and tools for CPU and CUDA.
- [x] Separate stable architecture/conversion tests from external-fixture tests and resolve `test-tokenizers-ggml-vocabs` with checksum-verified payloads.

#### 1.3 Normalize Python conversion environment

- [x] Pin `transformers==4.57.6` for the selected branch.
- [x] Install `tiktoken` and the conversion requirements.
- [x] Record the conversion package versions in the published conversion manifest.
- [x] Back up and patch tokenizer configuration through a reproducible project command.
- [x] Convert the tokenizer workaround into a reversible script; original and patched hashes are recorded and source snapshots remain unchanged after validation.

#### 1.4 F16 conversion

- [x] Convert `Kimi-K3-0.40B` to F16 GGUF.
- [x] Capture the complete conversion log.
- [x] Record tensor names, shapes, metadata, artifact size, and file checksum.
- [x] Validate and record tokenizer metadata, BOS/EOS, and named special-token behavior; preserve the observed HF/GGUF conflict without claiming general chat parity.

#### 1.5 MXFP4 conversion

- [x] Review and commit converter support for the observed 35 resident packed tensors.
- [x] Preserve the 168 routed expert tensors in MXFP4.
- [x] Require and observe exactly 21 repacked expert groups and 35 resident dequantizations for this checkpoint revision.
- [x] Sample source and GGUF blocks with an independent decoder and validate 81/81 scale, code, repacked-byte, and decoded-value samples exactly.
- [x] Capture the complete conversion log, artifact size, and checksum.

#### 1.6 Monolithic inference

- [x] Run F16 CPU inference.
- [x] Run hybrid MXFP4 CPU inference.
- [x] Build CUDA and run both models with `-ngl 999`.
- [x] Confirm and record actual backend placement, supported-layer offload, and CPU operations.
- [x] Recapture complete CPU inference logs in the committed evidence directory.
- [x] Capture deterministic prompt/generated token IDs and selected logits.
- [x] Resolve the perplexity/loss item for this closeout through the issue-approved full-vocabulary and selected-logit comparison; no separate quality claim is made for the tiny fixture.
- [x] Run repeated warm inference to detect persistent-state errors.

#### 1.7 Baseline benchmark

- [x] Measure prompt and decode throughput separately with repeated runs and a declared aggregation method.
- [x] Record CPU/GPU memory usage.
- [x] Record per-token latency distribution.
- [x] Record model-load time.

### Exit gate

- [x] Both GGUF files are reproducibly generated and checksum-verified.
- [x] CPU inference works for both artifacts.
- [x] CUDA backend placement is understood and works within the recorded operation support boundary.
- [x] No unexplained tensor drop, NaN, invalid expert ID, tokenizer conflict, or unresolved required test remains.
- [x] Complete baseline evidence, including usable CPU logs and required correctness comparisons, is committed.

---

## Phase 2 — Routing and expert-layout observability

### Objectives

- Expose the actual expert selections and physical storage spans without changing computation.
- Produce traces for cache simulation and exact storage metadata for out-of-core reads.

### Tasks

#### 2.1 Router trace

- [x] Add an opt-in trace hook at the selected-expert ID tensor.
- [x] Record model, layer, token/batch position, selected IDs, routing weights, request/sequence ID, and phase (prefill/decode).
- [x] Use a compact binary or structured format with a version.
- [x] Ensure trace-disabled overhead is negligible.
- [x] Ensure trace order is deterministic.

#### 2.2 Expert storage map

- [x] Extend the GGUF/model loader to expose file identity, file offset, byte size, type, logical shape, row stride, and alignment for each routed-expert projection.
- [x] Define `ExpertStorageEntry` containing gate/up/down spans.
- [x] Validate every span against GGUF metadata and file bounds.
- [x] Do not infer offsets through virtual-memory mappings.

#### 2.3 Offline simulator

- [x] Implement trace replay independent of GGML/CUDA.
- [x] Model hot and cold capacities in bytes and expert slots.
- [x] Implement LRU baseline.
- [x] Implement perfect-oracle lower bound.
- [x] Report tier hit rates, bytes, evictions, reuse distances, and theoretical stalls.
- [x] Separate prefill and decode analysis.

#### 2.4 Trace corpus

- [x] Capture multiple prompts and domains.
- [x] Capture short and long decode.
- [x] Capture small and large prefill.
- [x] Preserve raw traces and summarized results with model/checkpoint revisions.

### Exit gate

- [x] Every routed expert can be mapped to exact backing-file spans.
- [x] Real K3 traces can be replayed offline.
- [x] No inference result changes with tracing enabled.

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

- [x] Keep C ABI boundaries minimal where CPU and CUDA compilation units cannot link directly.
- [x] Define ownership and lifetime explicitly.
- [x] Define cancellation and error propagation.
- [x] Define per-model and per-device context; prohibit global singleton model state.

#### 3.2 Resident provider

- [x] Implement pass-through provider for a fully resident model.
- [x] Preserve original model IDs in the resident provider and distinguish logical IDs from future physical execution slots.
- [x] Add a disabled path with structural zero work and performance within the predeclared gate.

#### 3.3 Lifecycle

- [x] Model creation, provider initialization, request use, model unload, backend recreation, and failure cleanup.
- [x] Multiple model contexts and mixed-mode F16/MXFP4 models in one process.
- [x] Repeated load/unload stress tests.

### Exit gate

- [x] Resident-provider output matches the original path.
- [x] Default-path performance regression is within the predeclared noise budget.
- [ ] Resident-provider performance regression is within the predeclared noise budget.
- [x] Interfaces are reviewed against discrete, UMA, disk, multi-request, and multi-GPU requirements.

Status: ACCEPTED WITH NOTES — the immutable post-optimization capture passed 22 of 24 original cells and failed the MXFP4 CUDA disabled-versus-resident prompt-throughput and TTFT confidence bounds. Design-authority comments `5128658370` and `5128726338` accept the Phase 3 technical exit for project progression without marking the unchecked raw gate as passed. No further Phase 3 measurement is authorized; Checkpoint B and final review remain pending.

---
