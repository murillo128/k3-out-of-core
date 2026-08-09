# Issue 69 Delta D2d — retained experimental storage/cache modes

Delta D2d passes at parent mechanism target
`1e3b6b690fff56b4fbe3e55073139f5a4564b6f9` and nested runtime target
`fe848e4c097110276f1578265375ca4f03661ebf`.

`ACCEPTED`: the D2c default is unchanged: buffered positional `pread`, four
explicit workers for the decision fixture, direct promotion, and
`cold_admissions = 0`. The alternative modes are explicit and default off:

- per-fd `POSIX_FADV_RANDOM` for buffered model files;
- positional `O_DIRECT` with independently bounded per-worker staging;
- opportunistic async cold fill using the existing bounded cold-cache budget;
- the previously reviewed explicit storage-worker count.

Unsupported combinations fail during model/transport initialization. In
particular, random-access advice is buffered-only, async fill requires cold mode
and `PROMOTE_AND_GPU`, and unavailable/misaligned `O_DIRECT` cannot silently
become a buffered runtime.

## Archived failure classification and focused validation

`OBSERVED`: the two archived D2c crashes were stale incremental test
executables linked against a candidate-modified shared library whose public
`llama_model_params` layout had changed. After rebuilding the affected targets,
`test-expert-miss-policy` and `test-hot-expert-cache` both pass normally and
under the available glibc heap checker (`MALLOC_CHECK_=3`, perturbed heap).

The complete issue-defined focused native suite passes 12/12. Added coverage
exercises default-off and rejected combinations, independent O_DIRECT worker
staging, deferred cold-policy terminals, and async-fill success, failure,
cancellation, generation, and teardown/drain behavior.

## Bounded configuration and mechanism evidence

One 64 GiB whole-cgroup, zero-swap, OS-cold S0 smoke cell proved normal buffered
positional reads and `POSIX_FADV_RANDOM` are separately selectable, report their
effective access policy, use four effective workers, preserve exact output, and
finish with zero terminal state.

The bounded 64 GiB O_DIRECT regression retained the prior eight-worker control:

| Cell | Decode ratio, fill/direct | Block-read reduction | Process-read reduction | Cold hits | Cold admissions |
| --- | ---: | ---: | ---: | ---: | ---: |
| S0 | 1.05095 | 9.52% | 9.40% | 637 | 1023 |
| A1 | 1.01728 | 7.02% | 7.21% | 382 | 1353 |

Both pairs used native positional direct reads with zero buffered fallback,
preserved exact generated and numerical identity, stayed below the 64 GiB cap
with zero swap/OOM, and drained I/O, fill, references, pins, scheduler requests,
and transfer events to zero. This is a regression/mechanism check only and does
not supersede the D2c buffered-path selection.

## Raw evidence

- Release: `issue69-delta-d2d-experimental-modes-v1`
- Asset: `issue69-delta-d2d-final-v1.tar.zst`
- Size: `169210` bytes
- SHA-256: `be305a5d2c3662ad3c586eac93b1f0bca6b002c0d1825c95eb0bdf5273728565`

The archive contains the six bounded runtime captures and resource samples, the
targeted and 12-test CTest logs, the exact nested commit patch, and embedded
per-file SHA-256 identities. PR #70 and nested #3 remain unmerged pending the
fresh D2d exact-target independent review.
