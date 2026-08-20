# Reproducible paper figures

This directory is the executable figure contract for issue #124. The generated SVGs are the manuscript inputs; PDFs remain vector outputs for publication workflows, and PNGs are previews. The renderer performs deterministic trailing-whitespace canonicalization on SVG text; no generated SVG is manually edited after rendering.

## Regeneration

From a clean checkout:

```bash
python3 -m venv .venv-paper-figures
.venv-paper-figures/bin/pip install -r paper/figures/requirements.txt
.venv-paper-figures/bin/python paper/figures/generate_all.py
```

`zstd`/`unzstd` and network access are needed only when the verified #99 release member is not already cached. Set `K3_PAPER_FIGURE_CACHE` to a persistent cache directory if desired. `generate_all.py --offline` forbids downloads and fails unless the immutable release member is already present and checksum-valid. The fetcher verifies the release asset and the extracted parquet independently before either quality figure runs.

The command runs all scientific cardinality/evidence-class assertions, writes the three formats for every figure, checks manuscript/README/provenance references, rejects host-specific input dependencies and superseded #105 release references, and writes `generated/checksums.json`.

## Inventory

| paper figure | section | script | outputs | source issue | release/commit | source data | evidence class |
|---|---|---|---|---|---|---|---|
| Fig. 1 | §2.2 | `scripts/fig01_memory_mismatch.py` | `generated/fig01-memory-mismatch.svg`, `.pdf`, `.png`; `provenance/fig01-memory-mismatch.md` | #73, #102 | #102 `issue102-cross-prompt-v1`; target `0c4ed0ae92f4cc7efc79e544f04f745ff0b168cf` | #73 model manifest; #102 preregistration | model constants / explanatory |
| Fig. 2 | §4.6 | `scripts/fig02_architecture.py` | `generated/fig02-architecture.svg`, `.pdf`, `.png`; `provenance/fig02-architecture.md` | #114, #124 | manuscript PR #123 baseline `b4f87e9575626d0e39ae750ff6e05c2a48e42160` | architecture contract in `paper/paper.md` | design / explanatory |
| Fig. 3 | §5.4 | `scripts/fig03_bounded_routing.py` | `generated/fig03-bounded-routing.svg`, `.pdf`, `.png`; `provenance/fig03-bounded-routing.md` | #77, #115, #124 | #77 `issue77-phase13-6-evidence-v1`, target `9d0433896032055d9e114b61686717ec172e0329`; #99 final target | committed #99 preregistration | method / explanatory; registered constants |
| Fig. 4 | §7.4 | `scripts/fig04_cross_workload.py` | `generated/fig04-cross-workload.svg`, `.pdf`, `.png`; `provenance/fig04-cross-workload.md` | #102, #105 | #102 `issue102-cross-prompt-v1`; #105 `issue105-curated-analysis-v3`, target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468` | `results/2026-08-17/issue105/tables/physical_runs.csv` | `MEASURED_PHYSICAL` |
| Fig. 5 | §7.5 | `scripts/fig05_loads_to_tps.py` | `generated/fig05-loads-to-tps.svg`, `.pdf`, `.png`; `provenance/fig05-loads-to-tps.md` | #105 | `issue105-curated-analysis-v3`; target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468` | physical runs; locality–TPS validation | physical points + `POST_HOC_EXPLORATORY` fit/LOFO |
| Fig. 6 | §7.6 | `scripts/fig06_exact_cache_equivalence.py` | `generated/fig06-exact-cache-equivalence.svg`, `.pdf`, `.png`; `provenance/fig06-exact-cache-equivalence.md` | #105 | `issue105-curated-analysis-v3`; target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468` | capacity curves; virtual-capacity table/summary | S2 target `MEASURED_PHYSICAL`; capacity `EXACT_REPLAY`; post-hoc |
| Fig. 7 | §8.2 | `scripts/fig07_predictive_damage.py` | `generated/fig07-predictive-damage.svg`, `.pdf`, `.png`; `provenance/fig07-predictive-damage.md` | #99 | `issue99-long-horizon-quality-v1`; target `eeaab5fa3f62047e8617ab3ed408ccbddbb56872` | verified release `longrun-checkpoints.parquet`; committed analysis/preregistration | `DIRECT_FIXED_CONTEXT` |
| Fig. 8 | §8.4 | `scripts/fig08_controlled_feedback.py` | `generated/fig08-controlled-feedback.svg`, `.pdf`, `.png`; `provenance/fig08-controlled-feedback.md` | #99 | `issue99-long-horizon-quality-v1`; target `eeaab5fa3f62047e8617ab3ed408ccbddbb56872` | verified release `longrun-checkpoints.parquet`; committed analysis/preregistration | `DIRECT_FIXED_CONTEXT` + `FREE_TRAJECTORY` |
| Fig. A1 | Appendix C | `scripts/figa1_core_periphery.py` | `generated/figa1-core-periphery.svg`, `.pdf`, `.png`; `provenance/figa1-core-periphery.md` | #105 | `issue105-curated-analysis-v3`; target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468` | core/periphery analysis; all committee cells | `POST_HOC_EXPLORATORY` + `FIXED_ROUTE_COUNTERFACTUAL` |

The paper-specific scripts refactor only reviewed selection, aggregation, and rendering logic. The scientific calculations remain those in `scripts/issue105/analyze_evidence.py` and `scripts/issue99/reproduce_release.py`; the relevant reviewed sidecars are recorded per figure below.
