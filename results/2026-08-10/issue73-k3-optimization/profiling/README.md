# Issue 73 full-K3 profiling and bounded optimization

This packet records the measurement-tier check, steady-decode
Perfetto/CUPTI attribution, and the bounded eight-worker causal experiment on
the selected B0/T4 full-Kimi-K3 configuration. The production workload uses
the pinned 33-file artifact, the 100-token prompt, 24 generated tokens,
`n_ubatch=4`, eight ordinary CUDA layers on GPU 0, expert capacities
`0:549,1:1125,2:1125,3:1125`, a 16-GiB managed cold cache with asynchronous
fill disabled, positional O_DIRECT, QD64, and the promote-and-GPU miss path.

## Measurement tiers

The P0 -> P1 -> P0 sandwich decoded at 0.268138, 0.264938, and 0.274765
tok/s. P1 was 2.38% below the pooled adjacent controls, while the two P0
controls differed by 2.44%. P1 therefore does not satisfy the approximately
1% perturbation gate and is not decision-driving. All three processes are
exactly identical in prompt IDs, generated IDs, text, and all 24 logit
digests. The P0 and P1 executable arguments and external samplers are
identical after normalizing the output path; the tier remains evidence
classification rather than a different execution path.

The first full-K3 trace attempts exposed a bounded instrumentation limit. The
accepted 128-MiB CUPTI budget initially reserved 96 MiB for retained records
and 32 MiB for 1-MiB activity buffers. Four-GPU fan-out exhausted the activity
share at 1,000, 500, and 250 ms. Nested commits `1fbdb53c7` and `763a425e3`
kept the 128-MiB total fixed, split it 64/64, and reduced the buffer quantum to
256 KiB. Native and real system-capture tests passed. A subsequent 250-ms
prefill capture stayed within 85.72 MiB, proving the capacity fix, but was
diagnostic-only because request 12 was a prefill ordinal and one memcpy had an
unknown timestamp.

The corrected seed-73 selection derives the steady-decode domain from 100
prompt tokens at `n_ubatch=4`: request 34 is the ninth request after 25
prefill ubatches, with routed layer 15. That capture is valid:

- trace SHA-256 `5371e7fd1d61e720933ec814d79229d51bec2824f9a4e30fde99f6ef86ddb797`;
- 2,008,800 bytes and 250.545 ms logical window;
- 52 complete routed-layer intervals;
- 239 kernels, 418 memcpys, and 406 synchronization records;
- zero data loss, CUPTI errors, drops, unknown timestamps, unmatched
  correlations, or unsupported records;
- 68,419,504-byte CUPTI peak and zero active bytes at close;
- exact generated output and all 24 logits versus adjacent P0.

The traced workload reached 0.299903 tok/s versus 0.279794 for the
reverse-adjacent untraced P0, a material +7.19% perturbation. Its TPS is
therefore attribution-only and is not used to rank or freeze a candidate.

## Critical path

Four complete routed-layer cycles inside the seeded window provide wall-exact
accounting over 199.118 ms. Mean routed-layer wall is 49.779 ms: provider wall
is 31.473 ms (63.2%) and the following graph wall is 18.307 ms (36.8%).
Provider post-issue dependency time alone is 53.7% of total wall, pre-issue is
7.9%, and scheduler issue span is 1.6%.

Provider service unions are deliberately non-additive. Storage-read service
occupies 94.425 ms (47.4% of accounted wall), H2D scopes 28.271 ms (14.2%),
and CUDA expert-H2D intervals 27.178 ms. The trace contains 585 complete
storage requests: queue wait is 7.03 ms mean, 6.99 ms p50, 18.46 ms p95,
19.54 ms p99, and 20.27 ms max; operation service wall is 6.07 ms mean and
7.27 ms p95. Across the complete 250-ms window, 88.3% of H2D-union time
already overlaps storage-read service. QD64 exposes capacity for 128
operations while the endpoint peaks at 84, so queue depth is not the limiting
bound.

## Bounded causal intervention

Eight positional workers reduced terminal mean queue wait from 23.27 ms to
17.90 ms against the first four-worker control, but the P0 endpoint did not
reproduce an end-to-end gain:

| Cell | Workers | Decode tok/s | TTFT s | p50 / p95 / p99 / max s |
|---|---:|---:|---:|---:|
| control before | 4 | 0.279794 | 308.898 | 3.591 / 4.004 / 4.137 / 4.137 |
| W8 | 8 | 0.284286 | 306.731 | 3.471 / 3.994 / 4.148 / 4.148 |
| control after | 4 | 0.308454 | 289.273 | 3.210 / 3.621 / 3.737 / 3.737 |

W8 is +1.61% versus the first control, -7.84% versus the second, and -3.11%
versus pooled controls; the controls themselves span 9.74%. All output,
logits, storage, transfer, memory, and terminal invariants are exact and
clean. `REJECTED`: the queue-counter improvement does not graduate to five
paired confirmations.

`ACCEPTED`: freeze the unchanged B0/T4/four-worker/QD64 configuration as the
technical `K3_BEST` candidate. No performance runtime change is retained;
the only retained code changes make the required bounded four-GPU trace
possible. Repeated fresh-process P0 confirmation remains the final acceptance
gate.

Portable analyzer outputs are [p1-sandwich-summary.json](p1-sandwich-summary.json),
[critical-path.json](critical-path.json), and
[w8-sandwich-summary.json](w8-sandwich-summary.json).
[selection-summary.json](selection-summary.json) records the decision and
[raw-evidence-index.json](raw-evidence-index.json) binds the retained external
artifacts.
