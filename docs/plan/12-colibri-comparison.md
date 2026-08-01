# Phase 12–13 Colibrì comparison addendum

This addendum is normative for the Colibrì-derived comparison work in Phases 12, 12.5, and 13. It does not change phase order, authorize Colibrì code import, reopen Phase 9, or expand the approved Phase 10 controlling issue.

The external prior-art record is [`../COLIBRI_K3_PRIOR_ART.md`](../COLIBRI_K3_PRIOR_ART.md).

## Scope boundary

- Phase 10 continues to use its approved K3 random, static-hot, previous-token, temporal, and cross-layer baselines. General Colibrì learned-pinning or lookahead claims are not K3-specific evidence at the pinned v1.4.0 revision.
- Phase 9's accepted exact global LRU/ALWAYS default remains authoritative.
- Colibrì becomes a mandatory full-size K3 external baseline only after the Phase 12 full-size mechanism can support a fair comparison.
- Comparable Colibrì runs must use text-only K3, preserve all top-16 selected experts, keep `K3_TOPP=0`, and report any non-default activation arithmetic separately.

## Phase 12 additions — full-size storage and execution

### 12.C1 Pinned external baseline

Before using Colibrì evidence:

- [ ] Pin the exact Colibrì repository revision, release, license, K3 source checkpoint revision, and any repacked container revision.
- [ ] Record exact commands, environment variables, prompts, context, generated length, hardware, OS, filesystem, drive identity, and output hashes.
- [ ] Distinguish original safetensors, Colibrì repacked safetensors, and any locally regenerated artifact by filename, SHA-256, and tensor policy.
- [ ] Record resident trunk precision separately from routed-expert MXFP4 preservation.
- [ ] Keep optional expert dropping disabled and identify exact-float versus integer-dot activation execution.

### 12.C2 Storage-layout matrix

On common hardware where practical, compare:

1. original Hugging Face safetensors using validated contiguous expert bundles;
2. Colibrì-style spec-valid repacked safetensors with byte-identical MXFP4 expert payloads;
3. the project's measured GGUF projection-span and coalesced-bundle paths;
4. WASTE's one-record-per-expert format as an external normalized reference;
5. any new project expert format only if the existing Phase 12 gate independently justifies it.

For every representation record:

- [ ] complete expert payload bytes and alignment;
- [ ] physical reads per expert and per token;
- [ ] read amplification and shard-boundary behavior;
- [ ] logical expert order versus physical file-offset order;
- [ ] metadata, index, checksum, and corruption-detection behavior;
- [ ] conversion/repack time, temporary disk footprint, restartability, and verification cost;
- [ ] resident trunk footprint and startup time;
- [ ] output quality and numerical equivalence appropriate to each tensor representation.

Do not infer that GGUF, safetensors, or a custom container is superior from file size or syscall count alone.

### 12.C3 Read-order and I/O submission matrix

Using identical routed demand and physical layout, compare:

- [ ] logical selected-expert order;
- [ ] ascending backing-file offset order;
- [ ] any locality-aware grouping that preserves canonical compute accumulation;
- [ ] ordinary parallel `pread` with buffered I/O;
- [ ] ordinary parallel `pread` with `O_DIRECT` where supported;
- [ ] buffered `io_uring`;
- [ ] direct-I/O `io_uring` with the project's documented fallback.

Report separately:

```text
layout benefit
request-order benefit
submission-API benefit
queue-depth benefit
overlap benefit
CPU/GPU arithmetic benefit
```

The project must not claim an `io_uring` win by comparing against an unoptimized, differently laid-out, or serial baseline.

### 12.C4 Full-size K3 external comparison

When common hardware is available, compare the project against:

- [ ] pinned WASTE target-only K3;
- [ ] pinned Colibrì target-only K3 using source MXFP4 experts;
- [ ] the project GGUF/MXFP4 runtime.

Normalize or expose:

- expert and trunk precision;
- text-only versus multimodal scope;
- selected experts and router semantics;
- active expert bytes per token;
- storage bandwidth and real-record latency;
- cache size, hit rate, physical residency, and page pressure;
- CPU, GPU, and storage service time;
- exposed versus overlapped wait;
- prompt throughput, TTFT, decode throughput, and p50/p95/p99 latency;
- deterministic outputs and approved quality evidence.

Raw tokens/second is not sufficient because WASTE, Colibrì, and this project use materially different expert formats and arithmetic paths.

### 12.C5 Multi-NVMe storage topology

When the test host exposes multiple independent NVMe controllers:

- [ ] compare one drive against deterministic shard or expert-bank placement across drives;
- [ ] record controller, NUMA, filesystem, and queue topology;
- [ ] preserve one authoritative copy of each logical expert in the active layout unless a replication experiment is explicit;
- [ ] record bytes and service time per drive;
- [ ] distinguish aggregate storage bandwidth from cache or prefetch effects;
- [ ] verify clean degradation or failure behavior when one drive becomes unavailable.

This is storage-topology work, not multi-GPU Phase 14 work.

### 12.C6 Full-size policy consistency

Colibrì uses a per-layer LRU while Phase 9 retained exact global LRU/ALWAYS as this runtime's default.

- [ ] Replay and measure the accepted project default on full-size K3 traces before comparing policy names.
- [ ] Include a bounded per-layer LRU external-semantic baseline when the exact Colibrì behavior can be reproduced.
- [ ] If full-size evidence materially reverses the Phase 9 decision, return to design authority with the trace, online measurements, physical-residency evidence, and proposed bounded policy change.
- [ ] Do not silently switch defaults in Phase 12.

## Phase 12.5 additions — trace identity

The Phase 12.5 event and manifest schema must be able to attribute Colibrì-relevant storage effects without becoming format-specific.

- [ ] Include backing artifact, shard/file identity, requested span, aligned span, and physical file offset in storage events where available.
- [ ] Include logical demand ordinal and actual submission ordinal so offset reordering can be reconstructed.
- [ ] Include drive/controller identity for multi-NVMe runs.
- [ ] Correlate read completion with the exact logical expert bundle and canonical consumption order.
- [ ] Distinguish ordinary parallel reads from `io_uring` submissions in the environment and event metadata.

## Phase 13 additions — single-request chunked prefill

Before attributing gains to multi-request batching, establish a single-request chunked-prefill baseline inspired by Colibrì.

### 13.C1 Correctness

- [ ] Compare token-at-a-time prefill with bounded layer-major chunks, including chunk size 1 and at least one larger size such as 32 where memory permits.
- [ ] Preserve sequential KDA/MLA/recurrent state updates and canonical expert accumulation.
- [ ] Require exact generated-token identity and the approved logits/hidden-state equivalence for the tested runtime.
- [ ] Treat any numerically different fused or reordered path as a separate decision rather than calling it batching.

### 13.C2 Decomposition

For each chunk size record:

- [ ] token-expert compute pairs;
- [ ] unique expert bundles loaded;
- [ ] expert bytes read and transferred per prompt token;
- [ ] cache hits and evictions;
- [ ] dense-weight reuse and compute utilization;
- [ ] prompt throughput, TTFT, memory, and latency tails;
- [ ] interaction with existing exact issue-ahead and prefetch policies.

Compare the resulting deduplication with Colibrì's externally reported K3 result, but do not adopt its approximately 2.7x figure as a universal gate.

### 13.C3 Composition with multi-request batching

- [ ] Establish chunked single-request prefill first.
- [ ] Add cross-request expert union/coalescing as a separate dimension.
- [ ] Report whether gains overlap rather than multiplying them.
- [ ] Keep unique expert records separate from token-expert compute pairs.

## Exit implications

Phase 12 cannot close its storage decision without explaining the measured position of original safetensors, repacked safetensors, GGUF, WASTE, and Colibrì where the required artifacts and hardware are available. A missing comparison must be documented with the exact blocking dimension rather than silently omitted.

Phase 13 cannot claim batching or prefill-deduplication gains without a token-at-a-time and single-request chunked baseline.
