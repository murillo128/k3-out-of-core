# Phase 13 multi-GPU remediation

Disposition: `SUPPORTED_MULTI_GPU`. Correctness, lifecycle, the filtered trace gate and the frozen campaign pass. Performance remains `SCALING_NEGATIVE`.

- Immutable pre-remediation speedup: `0.662145x`.
- Corrected A/B speedup: `0.502846x` (95% paired bootstrap `0.495074`–`0.509403`).
- Capacity-matched B-prime speedup: `0.373684x`.
- Exact identity: `60658621b12340bc02d1fbb614142e4a17c5dd52eb529bfd4b0b2eb1a1255889` across 15 processes.
- Decode H2D global join: removed; final measured fraction `0`.
- LRU feasibility scan: `0.010520%` of B decode wall, below the 3% index threshold.
- Windowed trace: seed 61, request 15, layer 11, 1000 ms; A/B traces are 1067526 and 445768 bytes.
- Focused validation: 12/12 CTests; stale staging generation, D2H/H2D cancellation, actual device-delay and in-flight one-device failure gates pass.

The corrected result is slower than the historical synchronized baseline. The manifest preserves both results rather than relabeling the old number.
