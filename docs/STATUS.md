# Current Status

Last updated: **2026-07-31**

This file is the first handoff document for a new ChatGPT or Codex session.

## Project state

- Project Phase 1 is complete and merged.
- GitHub issue #7 is closed as completed.
- PR #8 merged into `main` as `7b4d21e8793c451ce34c691f438afceabd64a841`.
- PR #9 merged into `main` as `eff5efc754919bf1a50735e27c7ad39f4d93384e`; its STANDARD materiality and repeated-review circuit-breaker rules govern later issues.
- Project Phase 2 is complete and merged through PR #11. The final reviewed project head was `c56770e9148fb94173561b7c4f2aade63cdefff7`; PR #11 merged into `main` as `d74781faec12e8552c1598084b210f784ac0a43b`, with nested `murillo128/llama.cpp` gitlink `4daaaa1a4dd26d6465f84891b854b5f7ddc03020`. The bounded F16/MXFP4 CPU corpus and CPU/CUDA subset remain reproducible, and the unchanged raw corpus is published at immutable Hub revision `2d838d6b4d0aca4e9af1e7d899e57ad29330c72e`.
- Project Phase 3 is complete and merged. Issue #13 was implemented by PR #15 and squash-merged into `main` as `6d15dab02f8129240ca83579445898be2f5f987f`. The merged nested `murillo128/llama.cpp` gitlink is `a120de8e2d0b552c51eacd7d701ef1dd994bc3db`.
- Phase 3 Checkpoint A, Checkpoint B, and the renewed final complete-PR review all returned **PASS_WITH_NOTES** with safety **YES**. The final reviewed integration head was `d8cfa06e39a87223ca97e3326d7e08e96cd64018`; the renewed final review is issue comment `5129200934`.
- Phase 3 preserves the raw standing performance result as `fail`: 22/24 original cells pass, while MXFP4 CUDA disabled-to-resident prompt throughput and TTFT exceed their unchanged confidence budgets. Design-authority comments `5128658370` and `5128726338` accept this narrow Phase-3-only result as `PASS_WITH_NOTES`. This is not a waiver for correctness, default-path performance, decode, cache, transport, misses, multi-request behavior, full-size behavior, or tail latency.
- Project Phase 4 is complete and merged. Issue #17 was implemented by PR #18 and squash-merged into `main` as `b196cc07249726651d39aaa624703bc4256a3012`. The merged nested `murillo128/llama.cpp` gitlink is `57fe1eabbe3d0ced59096a0744efc91e286fb1c7`.
- Project Phase 5 is complete and merged. Issue #20 was implemented by PR #21 and squash-merged into `main` as `c5512bc073ae7aab4a14773028828e516e16f3f6`. The merged nested `murillo128/llama.cpp` gitlink is `26317ee1d848dd7a73f22a3666a055cad5d5cb03`.
- Project Phase 6 is complete and merged. Issue #22 was implemented by PR #23 and squash-merged into `main` as `66ab6dba60b55ce47d0ecf94fcf88a778df9cdc6`. The merged nested `murillo128/llama.cpp` gitlink is `7a606dd4e11a108929f799253809a904f55feae4`; asynchronous Linux I/O, direct-I/O evaluation, transfer/compute overlap, prefetch, and CPU miss execution remain Phase 7–8 work.
- Project Phase 7 is complete and merged. Issue #24 was implemented by PR #25 and squash-merged into `main` as `97ef68d787c54b443eac72a3480fe70eba88d8dd`. Checkpoint A, Checkpoint B, and the final complete-PR review returned **PASS** with safety **YES** at comments `5135836934`, `5140081178`, and `5140490542`. The merged nested `murillo128/llama.cpp` gitlink is `b71e40f91b1a0dab578d56ac733211453704d674`, and the Phase 7 evidence head is `1e2faeec1c1cc1781d9f65f030b1736f4adcfe51`.
- Project Phase 8 is a fully captured final-review candidate on issue #26 and PR #27; it is not merged. Checkpoints A, B, and C accepted the bounded mechanism, production-path evidence correction, and cold-cache bootstrap correction at comments `5141694340`, `5144721775`, and `5146173479`. The accepted nested candidate is `dc4d50c68378d908131b518662160fdd08f4e005`, and the exact standing-evidence capture head is `eb3d0093157da7757036882dc81b37dd622bbf46`. The authoritative manifest owns the final evidence head and final-review attestation. Phase 9 has not begun.
- Phase 1 evidence is descriptive for the tiny K3 fixtures on `skynet`; it is not a model-quality or production-performance claim.

## Phase 3 merged baseline

- Issue: <https://github.com/murillo128/k3-out-of-core/issues/13>
- Merged PR: <https://github.com/murillo128/k3-out-of-core/pull/15>
- Execution profile: `STANDARD`
- Immutable Phase 3 execution base: `81df862da6e4ff9db005f6265470070bb5456f4c`
- Final reviewed PR head: `d8cfa06e39a87223ca97e3326d7e08e96cd64018`
- PR #15 squash merge: `6d15dab02f8129240ca83579445898be2f5f987f`
- Nested `llama.cpp` head: `a120de8e2d0b552c51eacd7d701ef1dd994bc3db`
- Standing evidence commit: `93635d7ece8fdc617291d5a036bda1c8bc2b6c77`
- Standing capture SHA-256: `23eff115b87a9e8cee101bd1c0b02f299786175e786b4b30dd4a7e66617d4970`
- Checkpoint B review: issue comment `5128960944`
- Renewed final review: issue comment `5129200934`
- Structured closeout: `complete-with-performance-notes`, raw performance gate `fail`, design disposition `accepted-with-notes`.
- Evidence: [`../results/2026-07-29/skynet/phase3-resident-provider/PHASE3.md`](../results/2026-07-29/skynet/phase3-resident-provider/PHASE3.md)

## Phase 4 merged baseline

- Issue: <https://github.com/murillo128/k3-out-of-core/issues/17>
- Merged PR: <https://github.com/murillo128/k3-out-of-core/pull/18>
- Execution profile: `STANDARD`
- Project execution base: `0da90c6711e00613820183c1811dcaf1baffb409`
- Nested execution base: `a120de8e2d0b552c51eacd7d701ef1dd994bc3db`
- Nested mechanism head reviewed at Checkpoint A: `8ededcb548b0d9dc6248d6ba490aecedca576bec`
- Final reviewed project head: `792da4b6e09aa374905610cf323af140711d3518`
- Nested `llama.cpp` head: `57fe1eabbe3d0ced59096a0744efc91e286fb1c7`
- PR #18 squash merge: `b196cc07249726651d39aaa624703bc4256a3012`
- Checkpoint A: issue comment `5131012078`, `PASS_WITH_NOTES`, safety `YES`.
- Standing parity: 4/4 F16/MXFP4 exact-top-k/all-routed-key CUDA cases pass with byte-exact disabled/hot routes and logits.
- Standing lifecycle: seven native cases and two 20-token warm runs pass; warm runs record 251 hits, 51 misses, balanced pins, and no integrity failures.
- Evidence: [`../results/2026-07-30/skynet/phase4-hot-cache/PHASE4.md`](../results/2026-07-30/skynet/phase4-hot-cache/PHASE4.md)
- Final complete-PR review: issue comment `5131346667`, `PASS_WITH_NOTES`, safety `YES`; no required delta.

## Phase 5 merged baseline

- Issue: <https://github.com/murillo128/k3-out-of-core/issues/20>
- Merged PR: <https://github.com/murillo128/k3-out-of-core/pull/21>
- Execution profile: `STANDARD`
- Project execution base: `114f0de6f5d1cbd5f9ef6255f9100f3f4d52380a`
- Nested execution base: `57fe1eabbe3d0ced59096a0744efc91e286fb1c7`
- Checkpoint A corrected project head: `6404770597f979a8290d1de3f6bc503ab7d74d8b`
- Checkpoint A corrected nested head: `5ffed360965a1de7e2d788b8637a470183d27165`
- Final reviewed project head: `4a999c799fad77bcc0502200c8199fe01f1cbbc6`
- Nested `llama.cpp` head: `26317ee1d848dd7a73f22a3666a055cad5d5cb03`
- PR #21 squash merge: `c5512bc073ae7aab4a14773028828e516e16f3f6`
- Checkpoint A: issue comment `5132379446`, `PASS`, safety `YES`.
- Final complete-PR review: issue comment `5132639557`, `PASS_WITH_NOTES`, safety `YES`; no required delta.
- Standing parity: 4/4 F16/MXFP4 all-routed/forced-eviction cases pass across native pinned and explicit pageable fallback with exact prompt, route/weight, generated-ID, and full-logit hashes.
- Standing mechanism: native ring queues both top-k lanes before one barrier; fallback records zero pinned/async claims; source pinned bytes are zero.
- Standing lifecycle: CPU/CUDA/ASan+UBSan faults and two 20-step warm runs pass with balanced references and bounded owned bytes.
- Evidence: [`../results/2026-07-30/skynet/phase5-cold-cache/PHASE5.md`](../results/2026-07-30/skynet/phase5-cold-cache/PHASE5.md)

## Phase 6 merged baseline

- Issue: <https://github.com/murillo128/k3-out-of-core/issues/22>
- Merged PR: <https://github.com/murillo128/k3-out-of-core/pull/23>
- Execution profile: `STANDARD`
- Project execution base: `eb1b5baf5d505eadbc4298ecf322489cdfd7aae5`
- Nested execution base: `26317ee1d848dd7a73f22a3666a055cad5d5cb03`
- Checkpoint A accepted project head: `34dbf82ded955913b387ec9b36d1b499362e7a1b`
- Checkpoint A accepted nested head: `9af35746763913982bfd8eee995686296131c778`
- Final reviewed project head: `987a6af1ffae3f95a83390d642dccea73c5566d4`
- Nested `llama.cpp` head: `7a606dd4e11a108929f799253809a904f55feae4`
- Standing evidence head: `0b129ebea800fa18e67fcad747479ad6469033b8`
- PR #23 squash merge: `66ab6dba60b55ce47d0ecf94fcf88a778df9cdc6`
- Checkpoint A corrective review: issue comment `5133647261`, `PASS`, safety `YES`.
- Final complete-PR review: issue comment `5134171772`, `PASS`, safety `YES`; no required delta.
- Standing parity: original and generated 218-part split F16/MXFP4 cases preserve exact prompt, route, generated-token, and full-logit hashes; repeated demand becomes a cold hit without reread, and forced-small cases exercise deterministic eviction/reread.
- Standing integrity and lifetime: declared source spans and populated cold bundles match byte-for-byte and by SHA-256, split bundles cross three files, routed payload allocation/mmap binding/prefetch are zero, handles return to baseline, administration remains topology-bounded, and cancellation cleans and retries correctly.
- Evidence: [`../results/2026-07-30/skynet/phase6-gguf-storage/PHASE6.md`](../results/2026-07-30/skynet/phase6-gguf-storage/PHASE6.md)

## Phase 7 merged baseline

- Issue: <https://github.com/murillo128/k3-out-of-core/issues/24>
- Merged PR: <https://github.com/murillo128/k3-out-of-core/pull/25>
- Execution profile: `STANDARD`
- Immutable project execution base: `96b0b483c6bc0f92b6fb9bb46acfd6bf06a46c4c`
- Nested execution base: `7a606dd4e11a108929f799253809a904f55feae4`
- Accepted Checkpoint A project/nested heads: `be8672b9ba991a108ca6d0ffb43fae0e960519d4` / `990a416b62e896e2a15f0b160236cb9e3575e4e2`
- Accepted Checkpoint B project/nested heads: `a39eeafa4fee6af6a44fd03d630cf1cac79500d3` / `b71e40f91b1a0dab578d56ac733211453704d674`
- Final reviewed project head: `1b9d040da332e547af4571f81743012cd168a4cc`
- Phase 7 evidence head: `1e2faeec1c1cc1781d9f65f030b1736f4adcfe51`
- Nested `llama.cpp` head: `b71e40f91b1a0dab578d56ac733211453704d674`
- PR #25 squash merge: `97ef68d787c54b443eac72a3480fe70eba88d8dd`
- Checkpoint A: comment `5135836934`, `PASS`, safety `YES`.
- Checkpoint B: comment `5140081178`, `PASS`, safety `YES`.
- Final complete-PR review: comment `5140490542`, `PASS`, safety `YES`; no required implementation delta.
- Standing validation: focused CPU, CUDA, ASan+UBSan, and accepted ASLR-disabled TSan suites each pass 6/6. Original/split F16/MXFP4 five-step and repeated 20-step captures preserve exact prompt, route, generated-token, and full-logit results with bounded resources and terminal drain.
- Standing mechanism: buffered `io_uring` default; explicit direct-I/O with visible buffered fallback; bounded priority/single-flight scheduler; provider/storage/H2D cancellation and retry; native event lifetime; controlled positive disk/H2D and H2D/compute overlap; pageable fallback honesty; cached-only CPU remap placement.
- Technical manifest: [`../results/2026-07-31/skynet/phase7-async-runtime/phase7-manifest.json`](../results/2026-07-31/skynet/phase7-async-runtime/phase7-manifest.json), retained as the immutable final-review candidate and accepted by final review comment `5140490542`.
- Derived summary: [`../results/2026-07-31/skynet/phase7-async-runtime/PHASE7.md`](../results/2026-07-31/skynet/phase7-async-runtime/PHASE7.md).

## Phase 8 final-review candidate

- Issue: <https://github.com/murillo128/k3-out-of-core/issues/26>
- Draft PR: <https://github.com/murillo128/k3-out-of-core/pull/27>
- Execution profile: `STANDARD`
- Immutable project execution base: `5fe0bda6965da7d2b0f85dd14b97427a7b60f161`
- Nested execution base: `b71e40f91b1a0dab578d56ac733211453704d674`
- Accepted Checkpoint A project/nested heads: `07da45728b38b2d7c6a3a1b156dffcea6b94ec54` / `4cfee48aacb6b33ebcbda796b26106b69440e633`
- Accepted Checkpoint B project/nested heads: `30013880641fd2f10a1952b5b9619e6d872e233b` / `a885ff7750a4e73901b7f378e7dc45880a7d1536`
- Accepted Checkpoint C project/nested heads: `a52581e23b6192e51a6cd5452c121b5a014371f1` / `dc4d50c68378d908131b518662160fdd08f4e005`
- Standing-evidence capture head: `eb3d0093157da7757036882dc81b37dd622bbf46`
- Checkpoint A: comment `5141694340`, `PASS`, safety `YES`.
- Checkpoint B: comment `5144721775`, `PASS`, safety `YES`.
- Checkpoint C: comment `5146173479`, `PASS_WITH_NOTES`, safety `YES`; no required delta. The unchanged callback-free fixture teardown race remains a non-material test-only note.
- Standing correctness: the 25-case production-path Checkpoint B probe, original/split F16/MXFP4 cold/warm matrix, larger public MoE F16 bootstrap case, and prior default modes pass. Descriptor-only PP/TG discovery records zero scheduler reserve calls and zero backend bytes before the bounded hierarchy is initialized and final `COLD_CACHE` reserve occurs.
- Standing policy evidence: ten controlled hybrid overlap repetitions are positive; the deterministic 300-cell matrix covers all explicit policies and AUTO CPU-favorable, GPU-favorable, and tie regimes across decode/prefill, hot ratios, reuse, and background promotion.
- Standing validation: focused CPU, CUDA, ASan+UBSan, and accepted ASLR-disabled TSan suites each pass 5/5; Phase 8 evidence tests pass 32/32; the immutable Phase 7 manifest verifies at its accepted closeout head.
- Resource scope: peak device-wide VRAM was 3661 MiB with 242 MiB minimum free. The exact-layout 1,446,456,066,048-byte full-K3 MXFP4 sparse store validates layout and controlled policy behavior, not full-model inference or model quality.
- Technical manifest: [`../results/2026-07-31/skynet/phase8-miss-execution/phase8-manifest.json`](../results/2026-07-31/skynet/phase8-miss-execution/phase8-manifest.json). Its `closeout_state` and `final_review` fields are authoritative for the mandatory complete-PR review.
- Derived summary: [`../results/2026-07-31/skynet/phase8-miss-execution/PHASE8.md`](../results/2026-07-31/skynet/phase8-miss-execution/PHASE8.md).

## Phase 1 merged baseline

- Issue: <https://github.com/murillo128/k3-out-of-core/issues/7>
- Merged PR: <https://github.com/murillo128/k3-out-of-core/pull/8>
- Execution profile: `STANDARD`
- Immutable execution base: `511e87fc98cca8069fc57526fbb04b10789967eb`
- Final reviewed and attested branch head: `6173b5298496a5ce4ccb456cafd7b46be0e850ed`
- PR #8 merge commit: `7b4d21e8793c451ce34c691f438afceabd64a841`
- Current policy head after PR #9: `eff5efc754919bf1a50735e27c7ad39f4d93384e`
- Evidence: [`../results/2026-07-29/skynet/phase1-closeout-clean/SUMMARY.md`](../results/2026-07-29/skynet/phase1-closeout-clean/SUMMARY.md)

The clean Phase 1 branch was created directly from the immutable base. Work from issue #3 and PR #4 was not reused, cherry-picked, amended, or resumed.

## Pinned Phase 1 inputs

- `llama.cpp`: `84245db4c790af22135f34992689edcc11877003`, exact and clean at validation.
- F16 source revision: `d853649387ffe8f48ce0198a29ac1a44205031f7`.
- MXFP4 source revision: `ef3902c318fb8e13c3507e26055656e687fdfe38`.
- Published GGUF revision: `88de02cf8fa37f87eb06daaed370ac9c3411d5ca`.
- F16 GGUF: 784318432 bytes, SHA-256 `411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7`.
- MXFP4 GGUF: 751976576 bytes, SHA-256 `0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169`.

## Validated host

```text
Host: skynet
CPU: 11th Gen Intel(R) Core(TM) i7-11700K @ 3.60GHz
RAM: 64 GiB
GPU: NVIDIA GeForce GTX 1650, 4096 MiB
Driver: 535.288.01
OS: Ubuntu 24.04.3 LTS
Kernel: 6.8.0-136-generic
```

## Review notes carried forward

- Tokenizer acceptance is limited to the fixed ordinary prompt. Named HF special tokens conflict with GGUF BOS/EOS/PAD metadata and `<|im_end|>` is outside the GGUF vocabulary.
- MXFP4 selected top-10 ID sets match at every inference step, while ordered ranks swap within the same set at steps 8 and 24.
- The benchmark's 128-token setting is a maximum cap. The fixture naturally emits EOG after 49 generated tokens; forcing post-EOG decoding would change semantics.
- VRAM is sampled device-wide telemetry and the OS page cache was not flushed.
- The Phase 3 raw performance gate and its two accepted notes must remain visible in all later performance comparisons.
- Phase 7 tiny-fixture timings are descriptive. Production demand-only overlap was honestly zero; controlled native traces establish the mechanism.
- Phase 8 completes explicit CPU fallback and deterministic AUTO miss execution. Its tiny K3, larger public MoE bootstrap, and exact-layout sparse-store results are mechanism and controlled crossover evidence. Cache-policy selection, speculative prefetch, concurrency, UMA, multi-GPU, and full production K3 quality/performance remain later phases.

## Immediate next action

Publish the exact Phase 8 final-review candidate for issue #26 / PR #27, run the fail-closed manifest verifier at the published project and nested heads, and obtain the mandatory fresh independent complete-PR review. If it returns `PASS` or `PASS_WITH_NOTES` with safety `YES` and no required delta, add only the bounded review attestation, reverify, and leave PR #27 merge-ready without merging it. Do not begin Phase 9 in the Phase 8 closeout session.
