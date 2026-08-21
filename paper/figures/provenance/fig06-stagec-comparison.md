# Figure 6 — Frozen Stage-C physical comparisons

- **Manuscript section / claim:** §7.4. Shows every matched S2_P50/EXACT and S2_P50/KNEE physical TPS contrast and corresponding backing-load difference in the frozen 24-prompt Stage-C subset.
- **Evidence class:** both panels and all plotted values are `MEASURED_PHYSICAL`.
- **Scientific source:** #102 physical authority, release `issue102-cross-prompt-v1`, target `0c4ed0ae92f4cc7efc79e544f04f745ff0b168cf`; canonical table from #105 `issue105-curated-analysis-v3`, target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468`.
- **Input:** `results/2026-08-17/issue105/tables/physical_runs.csv` (SHA-256 `47527a419d6ec3d1c9939beb3d6ec6b7776627079db6e4011707e746bb03b64c`).
- **Filters / expected cardinality:** 184 rows total; 128 Stage-A primary S2_P50 rows; 48 fresh Stage-C rows = EXACT and KNEE for each of 24 unique prompts; eight sentinels. Assert all compared policies exist once per prompt, S2_P50 wins 24/24 in both TPS contrasts, reduces loads/token in every contrast, and all capacities equal 7,849 slots. No prompt is dropped.
- **Generator / command:** `paper/figures/scripts/fig06_stagec_comparison.py`; `python paper/figures/scripts/fig06_stagec_comparison.py`.
- **Environment:** Python 3.9.25; Matplotlib 3.9.4, NumPy 2.0.2, pandas 2.2.3.
- **Outputs / SHA-256:** `generated/fig06-stagec-comparison.svg` `0fa87881e10880e5f017d90e9092da2b21957822a5b935d681f5a2c6e761e07a`; `.pdf` `ab03a1d8f21220bb6553cdef8f739b56ff834672993f0f5be0a5be557c525bb1`; `.png` `7e04b2c4679062117d39cfb2ce770c2eed03052829c086833c491c890e82dd77`.
- **Proposed caption:** Per-prompt physical TPS ratios and backing-load deltas for all 24 frozen Stage-C prompts at one measured cache capacity.
- **Interpretation limits:** the Stage-C subset was selected by the frozen protocol and is not IID; there is one observation per policy/prompt, so 24/24 is an observed-corpus statement rather than a population probability; sentinel spread is a drift reference, not uncertainty.
