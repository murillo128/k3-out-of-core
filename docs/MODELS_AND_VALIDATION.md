# Models, Conversion, Hardware, and Validation

This document records the model fixtures and the evidence required before each implementation phase can advance.

## 1. Development checkpoints

### Kimi-K3-0.40B

- Hugging Face ID: `inference-optimization/Kimi-K3-0.40B`
- URL: <https://huggingface.co/inference-optimization/Kimi-K3-0.40B>
- Purpose: architecture and F16 reference fixture.
- Source tensor type: F32.
- Total parameters: approximately 0.40B.
- Activated experts: 2 of 8 per token.
- Weights were initialized/trained as a tiny development fixture; it is not a quality proxy for full K3.

Preserved architecture documented by its model card:

- eight language-model layers;
- KDA layers 0–2 and 4–6;
- MLA layers 3 and 7;
- sparse MoE on layers 1–7;
- dense MLP on layer 0;
- eight routed experts;
- one shared expert;
- top-2 routed experts per token;
- latent expert projection;
- attention/MLP residual paths.

Validated local path:

```text
models/hf/Kimi-K3-0.40B
```

Validated source revision: `d853649387ffe8f48ce0198a29ac1a44205031f7`.

### Kimi-K3-0.40B-MXFP4

- Hugging Face ID: `inference-optimization/Kimi-K3-0.40B-MXFP4`
- URL: <https://huggingface.co/inference-optimization/Kimi-K3-0.40B-MXFP4>
- Purpose: MXFP4 conversion, kernel, expert-cache, and byte-layout fixture.
- Published format: W4A16 `mxfp4-pack-quantized`, group size 32.

Local path:

```text
models/hf/Kimi-K3-0.40B-MXFP4
```

Validated source revision: `ef3902c318fb8e13c3507e26055656e687fdfe38`.

## 2. Important checkpoint observation

A local safetensors key inspection produced:

```text
packed total:             203
per-expert routed:        168
outside per-expert path:   35
```

Derivation:

```text
168 = 7 MoE layers * 8 experts * 3 projections
35  = 7 MoE layers * (2 latent routed projections + 3 shared-expert projections)
```

Observed additional packed names include:

```text
routed_expert_up_proj.weight_packed
routed_expert_down_proj.weight_packed
shared_experts.gate_proj.weight_packed
shared_experts.up_proj.weight_packed
shared_experts.down_proj.weight_packed
```

This conflicts with the current model-card statement that shared experts are ignored by quantization. **The downloaded checkpoint keys are authoritative for conversion.** Preserve the exact checkpoint revision/checksum in future validation logs and report the discrepancy upstream if it persists.

## 3. Intended GGUF representations

### F16 reference

```text
Kimi-K3-0.40B-F16.gguf
```

All converted model tensors use the normal F16 path where supported.

### Hybrid MXFP4/F16 fixture

```text
Kimi-K3-0.40B-MXFP4.gguf
```

Intended representation:

- 168 per-expert routed projection tensors remain MXFP4 and are repacked into 21 GGUF expert tensors:
  - seven layers;
  - gate, up, and down groups per layer;
  - eight experts per group.
- 35 additional resident MoE tensors are lazily dequantized to F16 during conversion.
- attention, router, recurrent state parameters, residual paths, embeddings, and other non-routed tensors follow their normal conversion path.

This separation matches the runtime target: routed experts are streamable; latent/common projections and shared experts are resident.

## 4. Conversion environment

Use the `llama.cpp` environment pinned by the checked-out K3 branch.

Known requirements/workarounds from the initial session:

```text
transformers==4.57.6
tiktoken installed
```

Reason for the Transformers pin: the K3 branch converter imports `bytes_to_unicode` from the GPT-2 tokenizer module expected by that version.

The downloaded tokenizer configuration used a v5-style list in `extra_special_tokens`, while Transformers 4.57.6 expected a dictionary. The local workaround is:

1. back up `tokenizer_config.json`;
2. remove only `extra_special_tokens`;
3. preserve `additional_special_tokens` and all actual token IDs;
4. verify that the custom tokenizer still reports vocabulary size 163840;
5. record the modified file checksum.

This is a compatibility workaround, not a model-format decision. Replace it with an upstream fix when available.

## 5. Conversion commands

Run from the K3-capable `llama.cpp` checkout with its virtual environment active.

### F16

```bash
rm -f ../models/Kimi-K3-0.40B-F16.gguf

python convert_hf_to_gguf.py \
  ../models/Kimi-K3-0.40B \
  --outfile ../models/Kimi-K3-0.40B-F16.gguf \
  --outtype f16 \
  |& tee /tmp/kimi-k3-0.40b-f16-convert.log
```

### Hybrid MXFP4

The converter must contain the reviewed K3 fixture support that:

- recognizes the 168 routed expert tensors;
- rejects unknown packed tensors;
- lazily dequantizes the known 35 resident tensors to F16;
- retains the routed experts in GGML MXFP4 layout.

```bash
rm -f ../models/Kimi-K3-0.40B-MXFP4.gguf

python convert_hf_to_gguf.py \
  ../models/Kimi-K3-0.40B-MXFP4 \
  --outfile ../models/Kimi-K3-0.40B-MXFP4.gguf \
  --outtype auto \
  |& tee /tmp/kimi-k3-0.40b-mxfp4-convert.log
```

Expected conversion evidence:

```bash
grep -c 'repacked 8 experts to MXFP4' \
  /tmp/kimi-k3-0.40b-mxfp4-convert.log
# expected: 21

grep -c 'resident tensor will be dequantized to F16' \
  /tmp/kimi-k3-0.40b-mxfp4-convert.log
# expected: 35
```

These counts are necessary but not sufficient. Also validate tensor names, shapes, file metadata, and inference.

## 6. Revision and checksum manifest

Before any performance or correctness result is accepted, create a machine-readable manifest containing:

```text
Date and hostname
OS and kernel
CPU, RAM, GPU, driver, CUDA toolkit
NVMe device and filesystem
llama.cpp repository URL
llama.cpp commit SHA
K3 PR head/base SHAs
converter diff SHA
Python version
transformers/tiktoken/torch/safetensors versions
HF repository revisions
SHA-256 for config/tokenizer/model files
SHA-256 for generated GGUF files
build CMake options
```

Issue #7 captured these fields in machine-readable environment, input, test, inference, and benchmark artifacts under `results/2026-07-29/skynet/phase1-closeout-clean/`. The closeout verifier checks the immutable revisions and hashes.

## 7. Development and target hardware

### Current validated development system

Captured during the clean Phase 1 execution:

```text
Host: skynet
CPU: 11th Gen Intel(R) Core(TM) i7-11700K @ 3.60GHz
RAM: 64 GiB
GPU: NVIDIA GeForce GTX 1650, 4096 MiB VRAM
NVIDIA driver: 535.288.01
OS: Ubuntu 24.04.3 LTS
Kernel: 6.8.0-136-generic
```

Storage-specific facts remain open for later disk-backed phases:

```text
[OPEN] exact NVMe model, firmware, link width, filesystem, mount options
[OPEN] measured sequential and random read bandwidth/latency
[OPEN] available PCIe topology and negotiated GPU link
```

This machine is suitable for discrete-GPU cache and PCIe testing. It is not a valid UMA proxy.

### Target UMA system: NVIDIA DGX Spark

Official specifications relevant to this project:

```text
Architecture: NVIDIA Grace Blackwell / GB10
CPU: 20-core Arm
Memory: 128 GB coherent unified LPDDR5x
Memory bandwidth: 273 GB/s
Storage: 1 TB or 4 TB NVMe M.2 depending configuration
CUDA/Tensor cores: Blackwell generation / fifth-generation Tensor Cores
```

Official references:

- <https://docs.nvidia.com/dgx/dgx-spark/hardware.html>
- <https://www.nvidia.com/en-eu/support/dgx-spark/>

The 128 GB capacity does not make the full K3 checkpoint resident. It changes cold-to-hot promotion from a physical PCIe copy into a residency/readiness operation over coherent memory.

## 7.1 Phase 1 validation record

The clean STANDARD-profile execution for issue #7 established the monolithic baseline before any residency change:

- stable tests: CPU 54/54 and CUDA 54/54;
- external GGUF-vocabulary fixture: CPU 1/1 and CUDA 1/1;
- fixed prompt IDs: `[18805, 308, 799, 5624, 12524]` across both source tokenizers and both GGUF tokenizers;
- MXFP4 integrity: 81/81 stratified samples matched exactly, including scale bytes, codes, repacked bytes, and decoded values;
- deterministic F16 and MXFP4 CPU/CUDA generation: exact 32-token IDs with accepted selected-logit thresholds;
- repeated benchmark: one discarded warmup and five measured context-recreated runs per model/backend combination;
- natural termination: all benchmark inferences generated the same 49-token sequence and EOG ID 163585, with the 32-token inference sequence as an exact prefix.

CUDA placed layers 0-8 and all 21 GGUF MXFP4 routed-expert tensors on CUDA0. The layer-3 Flash Attention operation remained on CPU because the CUDA backend reported it unsupported; this is an understood operation-level placement, not silent model-layer fallback.

The tokenizer evidence records a real metadata conflict: named HF special tokens use IDs 163584/163585/163839 while GGUF BOS/EOS/PAD metadata uses 1/2/0, and `<|im_end|>` is outside the 163840-token GGUF vocabulary. Therefore only ordinary fixed-prompt parity is accepted; general chat-template or end-of-message parity is not claimed.

See [`../results/2026-07-29/skynet/phase1-closeout-clean/SUMMARY.md`](../results/2026-07-29/skynet/phase1-closeout-clean/SUMMARY.md) for exact revisions, measured values, review notes, and checksum-verifiable evidence.

## 7.2 Phase 2 observability and simulation record

Issue #10 uses project base `c0ef5d08c6efb8d1f7a08a62109feb1a488c72fa` and nested `llama.cpp` base `84245db4c790af22135f34992689edcc11877003`. The accepted nested implementation head is `4daaaa1a4dd26d6465f84891b854b5f7ddc03020`: route observation is at `92c4627e19219134ed42e24aa84a1514bf3dffa3`, followed by loader-owned storage metadata.

Checkpoint A re-review covered project head `43216235b6e74914afdb1b76918557675bf7e0b1` and returned `PASS_WITH_NOTES`, safety `YES`. It independently reproduced trace-enabled timing, exact observer accounting, direct-readback parity, storage maps, and the focused tests.

The bounded corpus configuration is seed 1, temperature 0, greedy finite-logit argmax, and context 512. It contains constructed prose, code, structured-data, technical, narrative, English, and Spanish prompts. Exact prefill sizes are 12, 16, 23, 288, 309, and 349 tokens. Decode caps are 16 or 128; natural EOG is preserved and observed counts are recorded. Both artifacts have all six CPU traces; both have a representative small/large CUDA subset. All 16 traces reproduced byte-for-byte on a second identical run, and CPU/CUDA prompt and generated IDs are exact.

The large CPU/CUDA prefill subsets expose small internal route differences despite exact generated IDs: F16 has 16 ordered top-2 mismatches in 2163 records and MXFP4 has 25. The short subsets have none. This is `OBSERVED` backend numerical behavior, not evidence of observer disagreement: same-backend direct readback and repeated trace correctness passed Checkpoint A. No unapproved cross-backend route-weight threshold is asserted.

The raw archive is published at Hub revision `2d838d6b4d0aca4e9af1e7d899e57ad29330c72e`, size 323723 bytes, SHA-256 `6aa924a6c18bee4e2490f317ced836bcc4740c3ec63e9427a95951e79a649a5f`. The repository commits only prompt definitions, checksums, decoded summaries, simulator output, and the one permitted minimal real trace fixture.

The Phase 3 simulator is independent of GGML/CUDA and reports inclusive hot/cold LRU plus an equal-bundle perfect-future Belady/MIN offline lower bound. Canonical MIN always admits a fitting demand and selects its replacement victim only from current residents; the `A B A` capacity-one discriminator records three misses and three admissions. Its latency/bandwidth inputs are explicitly illustrative and serial with no overlap; they are not production predictions. Twenty focused Phase 2 tests pass. The corrected Checkpoint B re-review at project head `961e2f44413ec2031497dcc1474e8e79b828e6cb` returned `PASS_WITH_NOTES`, safety `YES`. Complete evidence is under `results/2026-07-29/skynet/phase2-observability/`.

## 7.3 Phase 3 resident-provider validation record

Issue #13 uses immutable project base `81df862da6e4ff9db005f6265470070bb5456f4c` and nested base `4daaaa1a4dd26d6465f84891b854b5f7ddc03020`. Design-authority comment `5127774849` defines the corrective base as project head `bb9a7778b207c248646c46083c03bdef5076c5bf` and nested head `523f825d2df5efa7c9a08561e2b64861ad5594c5`. The bounded resident-provider administrative fast path is published at nested head `a120de8e2d0b552c51eacd7d701ef1dd994bc3db`.

The correctness matrix covers F16 and MXFP4 on CPU and CUDA. Each combination compares an isolated nested-base binary with the candidate disabled path, then compares disabled and resident modes. The fixed prompt IDs, 32 generated IDs, full-vocabulary logits, same-backend route records, final consumed weights, graph operation hash/node count, graph reuse, and provider counters are exact. The disabled path records zero provider objects, bindings, plans, handles, allocations, callbacks, copies, and synchronizations. The resident provider records balanced request leases and no provider allocation, tensor copy, callback, or synchronization.

Lifecycle validation includes two contexts sharing one resident model, mixed-mode F16/MXFP4 models in one process, interleaved requests, asynchronous context destruction, CPU abort while handles are held, graph-binding and plan failures, cancellation, invalid descriptors and logical keys, partial initialization, 20 CPU load/decode/unload cycles, 10 CUDA cycles, and an ASan/UBSan focused run. All acquired handles are released exactly once.

The corrective evidence repeats the complete matrix and adds a focused F16 CPU comparison against exact nested corrective base `523f825d2df5efa7c9a08561e2b64861ad5594c5`. For the same two contexts and two nonempty ubatches, the base records 14 acquisitions/releases while the optimized candidate records 2. Both execute 154 bindings; the candidate performs 7 first registrations/full validations and 147 registered fast-path bindings. Each graph retains 7 bindings in a pre-reserved capacity of 8. All 12 corrective prerequisite checks pass before performance capture, and the historical evidence identities remain unchanged.

The performance protocol runs two independent comparisons for each model/backend combination: isolated base versus candidate disabled, and candidate disabled versus resident. Each side receives one discarded warmup and ten measurements in `ABBA` order. Adjacent observations form ten pairs. Throughput slowdown is `1 - candidate/base`; latency slowdown is `candidate/base - 1`; the gate is the paired mean plus the one-sided 95% Student-t critical value for 9 degrees of freedom. Decode throughput, prompt throughput, and TTFT are gated independently against the fixed issue #13 budgets. A measurement-only probe compiled against the pinned baseline records the same load time, token-latency p50/p95/p99, RSS, CUDA memory, and graph-reuse telemetry without using any provider API. Baseline provider counters are explicit JSON `null` values marked unavailable; candidate counters are reported numerically.

The first prospectively approved standing capture completed at `2026-07-30T06:53:38.449543+00:00` and failed 3 of 24 gated metric cells. It and the two earlier corrected attempts remain immutable historical evidence; their samples were not selected, pooled, overwritten, or composed into the corrective disposition. The one complete post-optimization v2 capture authorized by design-authority comment `5127774849` completed at `2026-07-30T08:42:09.806186+00:00` against exact nested candidate `a120de8e2d0b552c51eacd7d701ef1dd994bc3db`. Twenty-two of 24 cells pass. MXFP4 CUDA disabled-versus-resident prompt throughput has a 3.989153% upper bound and TTFT has a 4.386548% upper bound, both above the unchanged 2.400604% budget. Every baseline-versus-disabled comparison and every other resident comparison passes. This result stands without retry; Phase 3 returns to design authority. These are control-derived noise gates for the tiny fixtures, not production regression tolerances.

Exact closeout commands are:

```bash
cmake --build llama.cpp/build-cpu --target llama test-expert-weight-provider -j4
cmake --build llama.cpp/build-cuda --target llama test-expert-weight-provider -j4
ctest --test-dir llama.cpp/build-cpu --output-on-failure -R expert-weight-provider
ctest --test-dir llama.cpp/build-cuda --output-on-failure -R expert-weight-provider
python3 -m unittest discover -s tests/phase2 -p 'test_*.py' -v
python3 -m unittest discover -s tests/phase3 -p 'test_*.py' -v
python3 scripts/phase3/capture_provider_parity.py --cpu-build llama.cpp/build-cpu --cuda-build llama.cpp/build-cuda --f16 models/gguf/Kimi-K3-0.40B-F16.gguf --mxfp4 models/gguf/Kimi-K3-0.40B-MXFP4.gguf --phase2-manifest results/2026-07-29/skynet/phase2-observability/phase2-manifest.json --output-root results/2026-07-29/skynet/phase3-resident-provider --output-name provider-parity-post-optimization.json
python3 scripts/phase3/run_provider_lifecycle.py --cpu-build llama.cpp/build-cpu --cuda-build llama.cpp/build-cuda --f16 models/gguf/Kimi-K3-0.40B-F16.gguf --mxfp4 models/gguf/Kimi-K3-0.40B-MXFP4.gguf --cpu-load-cycles 20 --cuda-load-cycles 10 --output results/2026-07-29/skynet/phase3-resident-provider/lifecycle-and-failures-post-optimization.json
python3 scripts/phase3/measure_provider_admin.py --cpu-build llama.cpp/build-cpu --f16 models/gguf/Kimi-K3-0.40B-F16.gguf --output results/2026-07-29/skynet/phase3-resident-provider/provider-admin-fast-path.json
python3 scripts/phase3/verify_corrective_prerequisites.py --parity results/2026-07-29/skynet/phase3-resident-provider/provider-parity-post-optimization.json --lifecycle results/2026-07-29/skynet/phase3-resident-provider/lifecycle-and-failures-post-optimization.json --administration results/2026-07-29/skynet/phase3-resident-provider/provider-admin-fast-path.json --output results/2026-07-29/skynet/phase3-resident-provider/corrective-prerequisites.json
python3 scripts/phase3/measure_provider_overhead.py --baseline-ref 4daaaa1a4dd26d6465f84891b854b5f7ddc03020 --candidate-ref a120de8e2d0b552c51eacd7d701ef1dd994bc3db --cpu-build llama.cpp/build-cpu --cuda-build llama.cpp/build-cuda --f16 models/gguf/Kimi-K3-0.40B-F16.gguf --mxfp4 models/gguf/Kimi-K3-0.40B-MXFP4.gguf --pairs 10 --order ABBA --post-optimization-standing-capture --output results/2026-07-29/skynet/phase3-resident-provider/provider-overhead-post-optimization.json
python3 scripts/phase3/build_phase3_manifest.py --project-root . --results-root results/2026-07-29/skynet/phase3-resident-provider --output results/2026-07-29/skynet/phase3-resident-provider/phase3-manifest.json
python3 scripts/phase3/verify_phase3.py --project-root . --manifest results/2026-07-29/skynet/phase3-resident-provider/phase3-manifest.json --models-dir models/gguf --strict
```

Complete machine-readable evidence is under `results/2026-07-29/skynet/phase3-resident-provider/`. It reuses the immutable Phase 2 manifest and published raw-corpus revision by checksum; it does not recapture or republish that corpus. These results validate the tiny fixtures and resident integration seam only, not an out-of-core cache or production full-size performance.

## 8. Validation levels

### Level A — conversion integrity

Required:

- converter completes without warnings that imply dropped required tensors;
- expected tensor counts and names;
- GGUF opens with metadata tools;
- no unexpected F32 expansion of routed experts;
- MXFP4 bytes and scales round-trip against source tensors on sampled blocks;
- tokenizer IDs, BOS, EOS, and end-of-message behavior are recorded.

### Level B — monolithic inference

Run both models through CPU and CUDA where supported:

```text
F16 CPU
F16 CUDA
Hybrid MXFP4 CPU
Hybrid MXFP4 CUDA
```

The tiny model card uses the prompt:

```text
According to all known laws
```

and the trained fixture commonly continues with the FitnessGram Pacer Test text. That output is a smoke test, not a sufficient correctness oracle.

Required evidence:

- deterministic prompt token IDs;
- generated token IDs with temperature 0 and fixed seed;
- per-token logits or selected logit slices;
- no fallback to unsupported operators without being logged;
- CPU/CUDA performance and memory measurements.

### Level C — provider no-op parity

Introduce `ExpertWeightProvider` while all experts remain resident.

Exit condition:

- provider-disabled and resident-provider paths match the baseline within the agreed tolerance;
- no measurable default-path regression beyond the defined noise budget;
- unload/reload and multiple model contexts are clean.

### Level D — forced hot-cache behavior

Use deliberately tiny capacities to force deterministic hits and evictions.

Required cases:

- capacity zero;
- capacity one expert;
- capacity below top-k;
- capacity exactly top-k;
- capacity all experts;
- repeated expert hit;
- alternating experts;
- pinned expert cannot be evicted;
- simultaneous gate/up/down lifecycle;
- model unload during no in-flight work;
- cancellation/error cleanup.

Compare logits/tokens with the monolithic baseline.

### Level E — cold RAM cache

Required cases:

- hot miss / cold hit;
- hot and cold eviction;
- inclusive-cache invariant;
- bounded pinned-ring usage;
- pageable-to-pinned staging correctness;
- overlapping H2D and compute;
- CPU fallback and GPU promotion produce identical logical outputs.

### Level F — NVMe backing

Required cases:

- cold start;
- repeated disk miss becomes cold/hot hit;
- aligned and unaligned GGUF spans;
- end-of-file boundaries;
- short reads and I/O errors;
- direct-I/O unsupported fallback;
- queue saturation and bounded memory;
- demand priority over speculative prefetch;
- cancellation and model unload with in-flight I/O.

### Level G — trace replay and policy evaluation

Capture real routing traces for:

- short decode;
- long decode;
- small prefill;
- large prefill;
- mixed prefill/decode;
- multiple domains;
- multiple concurrent requests when supported.

Replay traces offline against candidate policies. Report:

```text
hot hit rate
cold hit rate
disk miss rate
bytes per generated token
promotion count
eviction count
wasted prefetch bytes
prefetch precision and recall
reuse distance distribution
per-layer expert skew
p50/p95/p99 simulated stall time
```

### Level H — UMA

On DGX Spark:

- verify CUDA access to chosen shared-memory allocation;
- measure first-touch/page-fault behavior;
- compare explicit prefetch/advice strategies;
- verify no duplicate hot/cold physical copy;
- force a cache smaller than the model fixture even when it fits;
- compare with discrete-GPU semantics using the same trace;
- validate NVMe-to-shared-memory overlap with CUDA compute.

## 9. Correctness policy

The project must define numeric tolerances per backend and quantization before accepting performance results.

Minimum rules:

1. compare exact prompt tokenization;
2. compare monolithic and provider logits at selected layers/tokens;
3. compare final token IDs with deterministic generation;
4. run perplexity or equivalent corpus loss tests;
5. preserve canonical top-k reduction order;
6. treat NaN, Inf, invalid expert IDs, and stale-slot reads as hard failures;
7. run cache metadata invariants under sanitizers where possible;
8. run repeated warm sessions to catch stale persistent state.

## 10. Performance metrics

Every benchmark must report at least:

```text
prompt tokens/s
decode tokens/s
time to first token
per-token p50/p95/p99 latency
GPU compute time
CPU expert compute time
H2D bytes and time
NVMe bytes, queue depth, and wait time
hot/cold/disk hit counts
prefetch issued/useful/wasted
cache occupancy and evictions
host RAM, pinned RAM, VRAM, and unified-memory usage
```

Do not report only average tokens/s; out-of-core viability depends on tail latency and miss behavior.

## 11. Full-size physical benchmark

The tiny model is insufficient to measure full K3 I/O. Build a synthetic expert store whose spans exactly match the full checkpoint tensor metadata and MXFP4 byte layout.

The generator must derive sizes from source metadata rather than hard-coded estimates. It should support:

- configurable layer/expert counts;
- real full-K3 expert dimensions;
- deterministic byte contents and checksums;
- GGUF-like three-span layout;
- optional contiguous expert-bundle layout for comparison;
- controlled routing traces;
- corruption/short-read injection.

This benchmark validates NVMe, cache, and transfer design before acquiring or converting the full checkpoint.
