# Phase 7 asynchronous runtime evidence

Status: **OBSERVED — final-review candidate**

This directory contains the bounded authoritative input evidence for issue #24, Phase 7. The machine-readable authority is `phase7-manifest.json`; this document is a derived summary.

## Exact revisions and reviews

- Project execution base: `96b0b483c6bc0bfc2679669e5bb049081c7660ae`
- Checkpoint B project head: `a39eeafa4fee6af6a44fd03d630cf1cac79500d3`
- Nested execution base: `7a606dd4e11a108929f799253809a904f55feae4`
- Accepted nested candidate and gitlink: `b71e40f91b1a0dab578d56ac733211453704d674`
- Checkpoint A: `PASS`, safety `YES`, comment `5135836934`
- Checkpoint B: `PASS`, safety `YES`, comment `5140081178`
- Final complete-PR review: pending

The Phase 7.5 closeout does not modify the accepted nested implementation.

## Evidence inventory

- `checkpoint-b-final-correction.json`: provider-integrated post-H2D cancellation/retry, exact FlightId overlap accounting, pageable fallback, event-capacity boundary, focused native/sanitizer results, and bounded scope.
- `checkpoint-b-placement-correction.json`: cached-only physical-remap placement plus disabled/resident/hot/cold exact parity and resident structural zero-work.
- `runtime-matrix.json`: exact-head original/split F16/MXFP4 parity, repeated warm execution, tails/resources, direct I/O, cancellation, fallback, overlap, and complete split lineage.
- `validation-results.json`: exact commands, exit codes, durations, output digests, native test totals, sanitizer disposition, and prior evidence verification.
- `phase7-manifest.json`: schema-validated final-review candidate binding all evidence and source-of-truth artifacts.
- `verification-result.json`: verifier result for the manifest and current candidate.

## Native validation

| Suite | Result |
|---|---:|
| Focused CPU CTest | 6/6 pass |
| Focused CUDA CTest | 6/6 pass |
| ASan+UBSan CTest | 6/6 pass |
| TSan, ASLR disabled | 6/6 pass |

The default-ASLR TSan invocation still fails before test code with the documented `unexpected memory mapping` runtime limitation. The accepted ASLR-disabled invocation passes. Phase 5 and Phase 6 evidence tests, the Phase 7 evidence tests, and the Phase 6 verifier at its exact accepted candidate all pass.

## Correctness and repeated warm execution

The exact matrix covers F16 and MXFP4, both original GGUF and generated 218-part split GGUF. Each case runs disabled/cold five-step parity and a disabled 20-step baseline against two cold 20-step captures.

- Prompt IDs are `18805,308,799,5624,12524` in every case.
- Generated IDs are `318,57195,11,1459,387` in every five-step case.
- F16 full-logit hash is `9216548397385032021`; route hash is `18343456112903461280` with 63 records.
- MXFP4 full-logit hash is `3239440680031925322`; route hash is `3147569586974852339` with 63 records.
- Every cold capture matches its disabled baseline exactly and drains scheduler requests and transfer events with zero trace drops.

Representative first warm captures:

| Case | Prompt tok/s | Decode tok/s | TTFT us | p50 us | p95 us | p99 us | Storage bytes | H2D bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| F16 original | 47.4843 | 108.377 | 105298 | 8096 | 17529 | 105298 | 40108032 | 177733632 |
| F16 split | 46.7814 | 107.339 | 106880 | 8104 | 17992 | 106880 | 40108032 | 177733632 |
| MXFP4 original | 72.1563 | 130.661 | 69294 | 7232 | 10571 | 69294 | 10653696 | 47628288 |
| MXFP4 split | 72.7093 | 131.543 | 68767 | 7191 | 10443 | 68767 | 10653696 | 47628288 |

These values are descriptive tiny-fixture measurements, not full-size K3 performance conclusions.

## Transport, placement, overlap, and boundedness

- Buffered `io_uring` is the default cold transport. Direct I/O remains explicit opt-in with visible per-operation buffered fallback.
- The direct capture opened 218 direct sources, issued 117 direct operations, moved 30670848 useful bytes through 30730752 aligned bytes, and recorded 21 buffered fallback operations without changing outputs.
- Disabled and resident create no distinct physical remap; their ordinary routing tensors remain consistently on `CUDA0`, and resident records zero cache/storage/async/scheduler/ring activity.
- Hot and cold place only their 63 observed distinct remap tensors on CPU while routing and expert execution remain on CUDA.
- The controlled native trace records 10059 us disk/H2D union overlap across two positive cross-flight pairs, 201326592 unique read bytes, 100663296 H2D bytes, and pair digest `12655469559912981935`.
- The same controlled capture records 10376 us H2D/compute overlap, 100663296 overlapped H2D bytes, and 7247757312 units of compute work.
- The production tiny demand-only capture honestly records zero overlap; it is not relabelled as positive.
- Provider post-H2D cancellation, storage-read cancellation, same-key retry, lane/hot generation advancement, native event reuse blocking, unload drain, event-capacity pre-allocation rejection, and forced-pageable synchronous fallback all pass.
- All configured cold, staging, ring, queue, request, operation, event, and trace resources remain within their declared bounds.

## Deferred work and carried notes

- Phase 8 owns `CPU_FALLBACK` and `AUTO` miss execution.
- Phase 9 owns production cache-policy selection; Phase 10 owns speculative prefetch.
- Multi-request concurrency, multi-GPU, UMA, and full-size performance remain later phases.
- The fixed ordinary-prompt tokenizer limitation and Phase 3 raw 22/24 performance result remain visible.
- Final acceptance of this PR requires an independent complete-PR review; the PR remains draft.
