# Issue 69 Delta D2d v2 — fail-closed experimental direct I/O

Delta D2d v2 passes its executor gates at parent mechanism target
`1c9812be76486339781880010a263b03eb6cda44` and nested runtime target
`c23cf1a5270d6f08c30aa3324369fba6e8b94c53`.

This target supersedes the failed v1 review target. It resolves both review
findings without changing the selected D2c default:

- an explicit `LLAMA_LOAD_MODE_DIRECT_IO` request now rejects direct-open,
  source-handle, staging, alignment, EOF, and runtime capability failures;
  none of those cases can continue through buffered I/O;
- the async-cold-fill cancellation test uses a bounded retry around the
  intentionally non-blocking `try_queue_cold_fill()` API, eliminating the
  try-lock assertion race without changing runtime scheduling.

The focused native suite passes 12/12 on the exact nested target. The two
targeted lifetime tests pass 2/2 under the glibc heap checker, and the corrected
transfer-ring test also passed ten consecutive isolated repetitions.

The buffered `NORMAL`/`FADV_RANDOM` smoke captures are unchanged historical v1
mechanism evidence. The correction touches only explicit direct-I/O failure
handling and test synchronization. Fresh exact-target 64 GiB, zero-swap,
OS-cold positional `O_DIRECT` S0/A1 pairs produced:

| Cell | Decode ratio, fill/direct | Block-read reduction | Process-read reduction | Cold hits | Cold admissions |
| --- | ---: | ---: | ---: | ---: | ---: |
| S0 | 1.02840 | 9.04% | 8.91% | 604 | 1021 |
| A1 | 1.05907 | 7.05% | 7.06% | 375 | 1355 |

All four fresh cells used eight effective positional direct-I/O workers,
reported zero buffered fallback operations, preserved exact generated and
numerical identity, remained within the whole-cgroup bound without swap or OOM,
and drained terminal resources to zero. Async cold fill remains experimental
and default off; the D2c selection remains buffered positional `pread`, four
workers for the decision fixture, direct promotion, and `cold_admissions = 0`.

## Raw evidence

- Release: `issue69-delta-d2d-experimental-modes-v2`
- Asset: `issue69-delta-d2d-final-v2.tar.zst`
- Size: `175170` bytes
- SHA-256: `1f47cffe024653da15fde2eefccf0556e6b902f115e0fd24b6ff31517aac4d28`

The archive contains the two nested implementation patches, focused and
targeted test logs, historical buffered-mode smoke captures, fresh corrected
S0/A1 direct/fill captures, resource samples, and embedded per-file checksums.
PR #70 and nested #3 remain draft and unmerged pending replacement exact-target
independent review.
