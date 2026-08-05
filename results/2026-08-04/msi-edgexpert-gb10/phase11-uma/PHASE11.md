# Phase 11 — coherent UMA on NVIDIA GB10

Status: **SUPPORTED_EXPLICIT_NONDEFAULT**.

The physical Spark target passed one-pool fixed-address UMA correctness, readiness, generation,
eviction, reclamation, pressure, cancellation, unload, repeated-epoch, and lifecycle gates. Expert
payload H2D and transfer-ring bytes remain zero. Phase 9 global LRU/ALWAYS and Phase 10 default-off
behavior are unchanged.

Autofit is **SAFE_CAPACITY_ONLY_NOT_PERFORMANCE_SELECTED**. The full-K3 exact-layout residency probe
materialized five clean runs each at 0.5W, W, 1.5W, safe explicit, and autofit. Safe explicit/autofit
resolved to 81,243,832,320 bytes (4,630
slots); 81,261,379,584 bytes, one slot higher, was rejected before I/O. This proves
bounded residency and pressure behavior, not full-model inference quality or throughput.

Tiny F16 and MXFP4 explicit-W/autofit pairs are structurally equivalent. Their throughput ratios
remain descriptive (0.988407 F16 and
0.925451 MXFP4); no minimum speedup is claimed.
WASTE remains historical external context because hardware, representation, kernels, storage layout,
and workload differ.
