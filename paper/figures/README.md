# Reproducible paper figures

This directory is the executable figure contract for issue #124. The SVGs are the manuscript inputs, PDFs are vector publication outputs, and PNGs are previews. Every output is produced by Python; generated SVGs are never edited by hand.

## Regeneration

From a clean checkout:

```bash
python3 -m venv .venv-paper-figures
.venv-paper-figures/bin/pip install -r paper/figures/requirements.txt
.venv-paper-figures/bin/python paper/figures/generate_all.py
```

The first run needs network access plus `zstd`/`unzstd` to retrieve immutable release members that are intentionally not committed. Set `K3_PAPER_FIGURE_CACHE` to choose a persistent cache. A later `generate_all.py --offline` run rejects network access and succeeds only when all release assets and extracted members are already present and checksum-valid.

`fetch_issue98.py`, `fetch_issue99.py`, and `fetch_issue105.py` verify release tags, release targets, archive SHA-256 values, member SHA-256 values, and scientific cardinalities before any dependent plot runs. `generate_all.py` regenerates every figure, executes the per-figure scientific assertions, rejects incomplete or unexpected output inventories, checks manuscript/README/provenance references, rejects host-specific paths and superseded #105 releases, and writes `generated/checksums.json`.

## Inventory

| paper figure | section | script | outputs | source issue | release/commit | source data | evidence class |
|---|---|---|---|---|---|---|---|
| Fig. 1 | §2.2 | `scripts/fig01_memory_mismatch.py` | `generated/fig01-memory-mismatch.svg`, `.pdf`, `.png`; `provenance/fig01-memory-mismatch.md` | #73, #102 | #102 `issue102-cross-prompt-v1`, target `0c4ed0ae92f4cc7efc79e544f04f745ff0b168cf` | model manifest; #102 preregistration | model constants / explanatory |
| Fig. 2 | §3.1 | `scripts/fig02_workload_dependence.py` | `generated/fig02-workload-dependence.svg`, `.pdf`, `.png`; `provenance/fig02-workload-dependence.md` | #102, #105 | #105 `issue105-curated-analysis-v3`, target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468` | `tables/physical_runs.csv`; #102 synthesis | `MEASURED_PHYSICAL`; descriptive/post-hoc summary |
| Fig. 3 | §3.4 | `scripts/fig03_policy_selection.py` | `generated/fig03-policy-selection.svg`, `.pdf`, `.png`; `provenance/fig03-policy-selection.md` | #98 | `issue98-profile-shape-extension-v3`, target `485819939e9d074f99a646443a2bbab8f1466eb8` | verified release raw screening, confirmation, synthesis members | `MEASURED_PHYSICAL` under the distinct #98 protocol |
| Fig. 4 | §4.6 | `scripts/fig04_architecture.py` | `generated/fig04-architecture.svg`, `.pdf`, `.png`; `provenance/fig04-architecture.md` | #114, #124 | manuscript PR #123 baseline and current architecture contract | `paper/paper.md` | design / explanatory |
| Fig. 5 | §5.4 | `scripts/fig05_bounded_routing.py` | `generated/fig05-bounded-routing.svg`, `.pdf`, `.png`; `provenance/fig05-bounded-routing.md` | #77, #99, #124 | #77 `issue77-phase13-6-evidence-v1`; #99 final target `eeaab5fa3f62047e8617ab3ed408ccbddbb56872` | committed mechanism and preregistration constants | method / explanatory; registered constants |
| Fig. 6 | §7.4 | `scripts/fig06_stagec_comparison.py` | `generated/fig06-stagec-comparison.svg`, `.pdf`, `.png`; `provenance/fig06-stagec-comparison.md` | #102, #105 | #105 `issue105-curated-analysis-v3`, target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468` | `tables/physical_runs.csv` | `MEASURED_PHYSICAL` |
| Fig. 7 | §7.5 | `scripts/fig07_loads_to_tps.py` | `generated/fig07-loads-to-tps.svg`, `.pdf`, `.png`; `provenance/fig07-loads-to-tps.md` | #105 | `issue105-curated-analysis-v3`, target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468` | physical runs; locality–TPS validation | physical points `MEASURED_PHYSICAL`; fit/LOFO `POST_HOC_EXPLORATORY` |
| Fig. 8 | §7.6 | `scripts/fig08_exact_cache_equivalence.py` | `generated/fig08-exact-cache-equivalence.svg`, `.pdf`, `.png`; `provenance/fig08-exact-cache-equivalence.md` | #105 | `issue105-curated-analysis-v3`, target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468` | capacity curves; virtual-cache table/summary | S2 target `MEASURED_PHYSICAL`; capacity `EXACT_REPLAY`; post-hoc |
| Fig. 9 | §8.2–§8.4 | `scripts/fig09_quality_feedback.py` | `generated/fig09-quality-feedback.svg`, `.pdf`, `.png`; `provenance/fig09-quality-feedback.md` | #99 | `issue99-long-horizon-quality-v1`, target `eeaab5fa3f62047e8617ab3ed408ccbddbb56872` | verified release checkpoints; committed analysis | `DIRECT_FIXED_CONTEXT` + `FREE_TRAJECTORY` |
| Fig. 10 | §8.5, §8.7 | `scripts/fig10_decision_quality.py` | `generated/fig10-decision-quality.svg`, `.pdf`, `.png`; `provenance/fig10-decision-quality.md` | #99 | `issue99-long-horizon-quality-v1`, target `eeaab5fa3f62047e8617ab3ed408ccbddbb56872` | predictor hierarchy; systems–quality join | controlled evidence + `POST_HOC_EXPLORATORY` |
| Fig. A1 | Appendix C | `scripts/figa1_route_structure.py` | `generated/figa1-route-structure.svg`, `.pdf`, `.png`; `provenance/figa1-route-structure.md` | #102, #105 | #105 `issue105-curated-analysis-v3`, target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468` | verified overlap/endpoints; route features; working-set analysis | `MEASURED_OBSERVER`; physical join + `POST_HOC_EXPLORATORY` |
| Fig. A2 | Appendix B | `scripts/figa2_prefill_depth.py` | `generated/figa2-prefill-depth.svg`, `.pdf`, `.png`; `provenance/figa2-prefill-depth.md` | #102 | `issue102-cross-prompt-v1`, target `0c4ed0ae92f4cc7efc79e544f04f745ff0b168cf` | prefill-depth curve and prefix corpus | bounded explanatory physical diagnostic |
| Fig. A3 | Appendix B | `scripts/figa3_systems_horizon.py` | `generated/figa3-systems-horizon.svg`, `.pdf`, `.png`; `provenance/figa3-systems-horizon.md` | #102 | `issue102-cross-prompt-v1`, target `0c4ed0ae92f4cc7efc79e544f04f745ff0b168cf` | long-horizon systems analysis | measured systems/locality diagnostic |
| Fig. A4 | Appendix D | `scripts/figa4_capacity_quality.py` | `generated/figa4-capacity-quality.svg`, `.pdf`, `.png`; `provenance/figa4-capacity-quality.md` | #99 | `issue99-long-horizon-quality-v1`, target `eeaab5fa3f62047e8617ab3ed408ccbddbb56872` | final analysis and verified release checkpoints | `DIRECT_FIXED_CONTEXT`; paired bridge analysis |
| Fig. A5 | Appendix C | `scripts/figa5_core_periphery.py` | `generated/figa5-core-periphery.svg`, `.pdf`, `.png`; `provenance/figa5-core-periphery.md` | #99, #105 | #105 `issue105-curated-analysis-v3`; #99 final target | core/periphery analysis; all committee cells; #99 outcome | `POST_HOC_EXPLORATORY` + `FIXED_ROUTE_COUNTERFACTUAL` |

The architecture, routing, loads-to-TPS, exact-cache, and core/periphery entry points wrap the reviewed rendering/calculation modules `scripts/fig02_architecture.py`, `scripts/fig03_bounded_routing.py`, `scripts/fig05_loads_to_tps.py`, `scripts/fig06_exact_cache_equivalence.py`, and `scripts/figa1_core_periphery.py`. The wrappers change only the final manuscript stem. Scientific calculations remain those of the reviewed source artifacts and are asserted against those artifacts before rendering.

## Decision-to-evidence audit

This maps every material manuscript rationale to visible frozen evidence. Internal issue/release identities intentionally live here and in the provenance files, not in public captions.

| manuscript decision or boundary | visible evidence |
|---|---|
| Treat expert demand and residency as workload-conditioned rather than explained by prompt length or process drift alone. | Fig. 2; route-structure detail in Fig. A1, both explicitly referenced from §3.1. |
| Do not assume one global workload-independent hot set. | Fig. A1 route overlap/endpoints plus Fig. A5 counterfactual pinning outcomes; §3.1 and §9.3 reference both. |
| Freeze S2_P50 rather than KNEE or the other registered profiles. | Fig. 3 shows all 21 screening cells, the preregistered selection rule, and all six paired confirmation cells; §3.4 references it. |
| Keep a two-swap, p50-regret profile. | Fig. 3 exposes swap budget and regret quantile for every candidate profile; Fig. 5 shows how those fixed bounds constrain each token. |
| Keep top-32 candidate expansion. | Fig. 5 records the inherited, fixed mechanism boundary. The manuscript presents this as a protocol/design choice because the paper package does not contain a complete candidate-count sweep suitable for a new empirical claim. |
| Consume the full prompt and do not pool early-first-full absolute measurements with the final protocol. | Fig. A2 shows the bounded prefill-depth diagnostic; §7.1 explicitly references it. |
| Confine the headline physical claim to the frozen 64-token window rather than calling it steady state. | Fig. A3 shows non-monotonic 16–512-token locality paths; §7.1 explicitly references it. |
| Measure physical backing loads directly and use them as the service-demand quantity tied to TPS. | Fig. 7 shows raw physical points and held-out-family residual behavior; §3.2 and §7.5 reference it. |
| Use the 24-prompt Stage-C subset for matched EXACT/KNEE comparisons without presenting it as a random population sample. | Fig. 6 retains all 24 prompts and both ratios; Table 2 and §7.1/§7.4 define the selected subset. |
| Represent exact-cache equivalence as discrete replay brackets, not measured RAM savings or a continuous threshold. | Fig. 8; §3.3 and §7.6 retain the evidence-class and interpolation limits. |
| Keep fixed hard routing bounds instead of adding a cumulative-regret/perturbation/depth safety controller. | Fig. 10A predictor hierarchy; §8.5 explicitly references the held-out weak/no-signal result. |
| Frame the outcome as a systems–quality trade-off, not free locality or quality neutrality. | Fig. 9 controlled damage/feedback plus Fig. 10B systems–quality association; §8.7 and §11.1 reference both. |
| Retain capacity as a quality-analysis variable. | Fig. A4 paired bridge analysis; §8.7 explicitly references it and limits the claim to three bridge prompts. |
| Do not adopt static recurrent-core pinning. | Fig. A5 retains improve/unchanged/regress/infeasible counterfactual cells; §9.3 references it and states that it is not a physical benchmark. |
| Do not derive a core/periphery semantic-risk controller. | Fig. A5 plus the final negative quality-interaction classification in §9.3; the manuscript limits core/periphery to frequency/overlap structure. |
| Do not claim task-level or cross-model validity. | Explicit scope boundary in §8.6 and §11.2/§11.4. No accepted frozen task or cross-model evidence exists, so this is a protocol scope choice rather than an empirically supported generality claim. |
