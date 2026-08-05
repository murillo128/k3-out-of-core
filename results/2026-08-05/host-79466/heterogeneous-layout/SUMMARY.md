# Heterogeneous expert layout validation

Issue #49 has a positive final-capable result at measured parent revision `4e5418483db5c39afcce698fa7c42e00a107f8ef` and nested revision `87f6fdbb04db24078d4d5b9bdc5cd0502e17290c`.

The exact DeepSeek V4 UD-Q3_K_XL artifact seals three deterministic routed-expert layout classes: the common 41-layer class plus distinct layer 26 and layer 42 classes. Full canonical descriptor bytes determine dense class IDs; digests are telemetry only. One class-independent hot-slot namespace, cold-slot namespace, transfer ring, and global LRU/ALWAYS policy serve every class without class banks or hidden partitions.

Checkpoint A passed CPU Debug, CUDA Release, and CPU ASan focused suites at 8/8 each. The real provider and compact-versus-universal CUDA probes passed Compute Sanitizer with zero errors or leaked bytes. Identical real source bytes for layers 0, 26, and 42 were executed through up, gate, and down projections: all nine same-kernel comparisons were finite and bit-exact, with zero raw error and intact padding guards.

The final 4,096-context, 128-batch/microbatch matrix ran three processes each for current `--fit`, explicit `--cpu-moe`, and the universal provider, plus a fourth provider process for direct PSS sampling. All four provider processes produced identical eight-token output, logits hashes, all 344 route records, cache decisions, byte counts, tier geometry, and terminal state. The CPU-expert comparison produced the same eight token IDs; its route weight bits first diverged at layer 1 and selected IDs at layer 3 because it uses a different expert backend. All eight layer-0 route records match exactly, and the decisive same-kernel layout comparison remains 9/9 bit-exact.

The accepted provider cell used 268 hot slots (`4,286,284,800` bytes), 335 cold slots (`17,145,139,200` bytes), and four transfer lanes (`67,173,120` pinned bytes). It retained at least `12,466 MiB` free VRAM and `137,690,427,392` available RAM bytes, with zero major faults, swap, cgroup pressure/OOM events, short reads, I/O errors, cleanup failures, or dropped trace records. Peak RSS was `17,649,484 KiB`; directly sampled peak PSS was `17,604,560 KiB`. Filesystem availability never fell below `59,390,513,152` bytes, preserving the required 55 GiB reserve.

Nearest-rank provider decode latency across 21 decoded tokens was p50 `6.098 s`, p95 `8.419 s`, p99/max `8.460 s`, at `0.1574 token/s`. Explicit CPU-MoE generated `8.5–9.1 token/s`. No speedup is claimed: this is bounded correctness and capacity evidence, and the substantial performance deficit is an explicit limitation.

The authoritative manifest is `manifest.json`. Full-model raw evidence is archived at `/workspace/evidence/issue49-full-model/bda99191979056b6f4c05403224d5d6af39101044df72f4f17912c265e34e59f.tar.zst` with SHA-256 `bda99191979056b6f4c05403224d5d6af39101044df72f4f17912c265e34e59f`.
