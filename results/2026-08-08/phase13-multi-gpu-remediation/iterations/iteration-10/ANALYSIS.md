# Phase 13 iteration 10 — device-side inactive-ID preparation rejected

Candidate: parent base `030c1a2225a6fa5f866507df36a37a7b82b8e99c`, nested candidate `ba98600576d97109a3130af126ff9780d1812b71`. Reverted without rewriting history by nested `50071d8f8b4cf06e291a704956516834c851519b`; its CUDA source tree is identical to the Iteration-9 nested target `67ab784bdb93aa9e43a9a48a14eb7ebc8bcd1b98`.

`OBSERVED`: the candidate moved the allow-inactive `MUL_MAT_ID` ID remap and inactive-row zeroing onto the device so the MMQ branch could be enqueued without the preceding D2H ID read and host stream synchronization. Native CUDA compilation used `-j76`; all 12 focused expert CTests passed sequentially, including peer-staging stale generation and mid-D2H/mid-H2D cancellation.

The Mode-C B qualification retained the accepted exact identity SHA-256 `60658621b12340bc02d1fbb614142e4a17c5dd52eb529bfd4b0b2eb1a1255889`, all 24 logit digests and 1,032 route records. A target-specific shape seam confirmed that the measured branch was active: routed `iq2_xs` used `ne2=16`, `ne12=16`, `ne02=268`, with MMQ eligible; the same fast-path predicate is true for decode.

`REJECTED`: the one-process Mode-P Tier-1 B screen measured `1.174266451` tok/s versus the Iteration-9 B reference `1.172384334` tok/s, only `+0.1605%`. This misses the declared `+3%` retention threshold by a wide margin. Generated output identity remains exact (`782383806379a7b5efcb36545a48bc2d82aa988b808d598a59b811302084f23b`). Because the first disjunct of the declared falsifier failed, no five-pair campaign or retained-candidate trace is authorized; the candidate is reverted.

The result corrects the Iteration-9 causal interpretation: the D2H ID synchronization is observable but not throughput-dominant for this production fixture. The next trace/source-aligned bounded mechanism is the forced-positional async transport: one worker serializes all queued `pread` requests while the Mode-P B trace attributes about `6.225 ms/layer` to storage service and `6.304 ms/layer` to staging. Iteration 11 will test a bounded positional-read worker count derived from the expert-device count. Prediction: B storage service falls at least 25%, provider wall at least 10%, and B Mode-P TPS rises at least 3%, while A is unchanged by construction; revert on any missed performance or correctness/lifecycle threshold.

