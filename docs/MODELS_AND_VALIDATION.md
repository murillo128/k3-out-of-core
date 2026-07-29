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

Local path used in the initial session:

```text
../models/Kimi-K3-0.40B
```

### Kimi-K3-0.40B-MXFP4

- Hugging Face ID: `inference-optimization/Kimi-K3-0.40B-MXFP4`
- URL: <https://huggingface.co/inference-optimization/Kimi-K3-0.40B-MXFP4>
- Purpose: MXFP4 conversion, kernel, expert-cache, and byte-layout fixture.
- Published format: W4A16 `mxfp4-pack-quantized`, group size 32.

Local path:

```text
../models/Kimi-K3-0.40B-MXFP4
```

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

The first implementation task should add a script that emits this manifest rather than relying on handwritten logs.

## 7. Development and target hardware

### Current development system

Known from project context:

```text
Host: skynet
CPU: AMD Ryzen 9 3900X
RAM: 64 GB
GPU: NVIDIA RTX 4070 Ti, 12 GB VRAM
OS: Ubuntu/Linux
```

Open hardware facts to capture before benchmarking:

```text
[OPEN] exact Ubuntu release and kernel
[OPEN] NVIDIA driver and CUDA toolkit
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
