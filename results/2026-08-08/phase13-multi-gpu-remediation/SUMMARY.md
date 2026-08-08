# Phase 13 multi-GPU remediation

Disposition: `SUPPORTED_MULTI_GPU`. The frozen symmetric topology passes correctness, lifecycle, resource, trace and revised performance closeout gates. Classification: `PARITY_QUALIFIED`.

- Mode C: exact full identity `60658621b12340bc02d1fbb614142e4a17c5dd52eb529bfd4b0b2eb1a1255889` across A/B, all logits exact and 1032 routes per process. These TPS values are diagnostic only.
- Mode P: A `1.384835` tok/s, B `1.381988` tok/s, speedup `0.997944x`.
- Paired 95% bootstrap: `0.986679`–`1.009211`; preferred `0.95x` and hard `0.90x` floors pass.
- Mode-P generated-output identity: `782383806379a7b5efcb36545a48bc2d82aa988b808d598a59b811302084f23b` across 10 fresh processes.
- B-prime: not repeated; comment `5226796421` makes it diagnostic only and no capacity hypothesis remained active.
- Decode H2D global join: removed; final measured B fraction `0.000000%`.
- LRU feasibility scan: `0.065977%` of B decode wall, below the 3% threshold.
- Windowed Mode-P trace: seed 61, request 15, layer 11, 1000 ms; A/B traces are 3915036 and 3980057 bytes.
- Focused validation: 12/12 CTests; stale staging generation, D2H/H2D cancellation, actual device-delay and in-flight one-device failure gates pass.

Iteration 11 is retained. Iterations 10, 12 and 13 falsify the remaining bounded synchronization/staging corrections as throughput-dominant. The residual serialized resident graph is architectural follow-up #63, not a reason to broaden #61.
