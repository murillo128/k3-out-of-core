# FreeToken prior-art note

Paper: Shuo Yang et al., *FreeToken: Efficient Edge-Native MoE Serving with Bandwidth-Adaptive Execution*, arXiv:2608.16157 (2026).

- Paper: <https://arxiv.org/abs/2608.16157>
- Repository: <https://github.com/FlashML-org/FreeToken>
- License: Apache-2.0.
- Reviewed: 2026-08-19.

## Relevance

FreeToken is strong adjacent systems prior art for local MoE inference. Its current offload design keeps experts in host RAM, uses a global LRU cache of expert slots in VRAM, and can execute cache misses through a bandwidth-adaptive hybrid CPU/GPU path. The hybrid path calibrates the machine and splits misses between PCIe transfer to the GPU and direct CPU execution while overlapping the two resources. This preserves the model's selected experts rather than changing router membership.

This is complementary to, rather than a replacement for, the K3 out-of-core result. FreeToken optimizes the regime in which the expert pool can reside in host RAM. K3 out-of-core targets the harder regime in which Kimi K3's expert pool does not fit in practical host RAM and NVMe remains part of the steady-state decode hierarchy.

As of this review, FreeToken's published supported-model list includes DeepSeek-V4, GLM-5.2, Qwen3.x MoE, GPT-OSS, Gemma-4, MiniMax-M2.5, and Muse-Glimmer, but not Kimi K3.

## Relationship to cache-aware routing

FreeToken provides a useful exact-routing systems baseline:

```text
router-selected experts
        |
        +-- VRAM hit --------------------> GPU
        |
        `-- miss ----+---- RAM -> PCIe --> GPU
                     `---- CPU execution
```

The K3 cache-aware router attacks a different variable: it changes expert membership only within explicit bounded regret so that fewer expensive expert loads are required in the first place.

The clean comparison is therefore conceptual:

```text
FreeToken:      optimize where/how exact selected experts execute
K3 S2_P50:      reduce physical expert demand by boundedly changing selection
```

FreeToken raises the bar for any claim that routing modification is necessary: a routing-changing policy should be justified only if its measured locality/performance benefit remains meaningful relative to strong exact-routing systems techniques, and its semantic cost is explicitly quantified.

## Project decision

**Prior art / positioning only. Do not add a FreeToken-style hybrid CPU/GPU implementation to the current K3 roadmap.**

The project has already established that full Kimi K3 is not a practically useful local runtime on ordinary hardware at the measured throughput envelope. Even a substantial constant-factor systems improvement would not change that product-level conclusion enough to justify another implementation branch.

The higher-value next step is to formalize the existing scientific result:

1. freeze and present the measured physical locality/TPS evidence and virtual-cache interpretation;
2. treat `S2_P50` as the main systems result: bounded cache-aware routing reduces expert demand and improves measured throughput across the frozen workload evidence;
3. complete the already-planned long-horizon semantic-quality and route-feedback study so the performance gain has a defensible quality boundary;
4. position FreeToken as evidence that exact-routing RAM↔GPU execution can be highly optimized when the model fits host RAM, while K3-out-of-core studies the qualitatively harder NVMe↔RAM regime and shows that routing itself can be used as a bounded systems control;
5. turn the frozen results into the planned paper/post rather than expanding the runtime feature surface.

Revisit hybrid CPU/GPU execution only if a future target model or hardware regime changes the feasibility conclusion; it is not justified by the current K3 evidence.