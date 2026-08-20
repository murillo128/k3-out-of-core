# Figure 5 — Backing loads/token to physical decode TPS

- **Manuscript section / claim:** §7.5. Shows the measured relation and the reviewed leave-one-family-out validation for loads/token predicting decode TPS.
- **Evidence class:** Panel A points are `MEASURED_PHYSICAL`; Panel A fit and all of Panel B are `POST_HOC_EXPLORATORY`.
- **Scientific source:** issue #105 final target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468`, analysis commit `76e0c3d578c4dba56e91d15ad643d8740037788a`, release `issue105-curated-analysis-v3`, archive SHA-256 `e0fe96c2f4dd3d2cfc8ced16901949936ba3e72c79ebdd4eb412f371fe843fb3`.
- **Inputs:** `results/2026-08-17/issue105/tables/physical_runs.csv` (SHA-256 `47527a419d6ec3d1c9939beb3d6ec6b7776627079db6e4011707e746bb03b64c`); `results/2026-08-17/issue105/analysis/locality-tps-validation.json` (`8e81c4cfe22bc59ab65fb6df475515efe72dd23d3d409a46a3765161db0e0f9e`); reviewed reference sidecar `analysis/figures/figure-03.sidecar.json` (`0a825c040f7d4d4ffcd517cf48f319cbe57cbe6ca68776551e086412b85bea26`). Original reviewed generator `scripts/issue105/analyze_evidence.py` (`d12d988c520c31cebe25c5ad76cb4577064b54da01a2a5eb8edeb5f427393666`).
- **Filters / expected cardinality:** primary fit uses 128 `STAGE_A`/`primary`/`S2_P50` rows, 16 families × 8 levels, source class `MEASURED_PHYSICAL`. Assert selected predictor `loads_per_token`, 128 model rows, 16 LOFO folds, and 128 residual observations. R² and RMSE are read from the authorized artifact, not hardcoded.
- **Generator:** `paper/figures/scripts/fig05_loads_to_tps.py`, a paper-specific rendering refactor of the reviewed Figure 03 calculation.
- **Regeneration:** `python paper/figures/scripts/fig05_loads_to_tps.py`.
- **Environment:** Python 3.9.25; Matplotlib 3.9.4, NumPy 2.0.2, pandas 2.2.3.
- **Outputs / SHA-256:** `generated/fig05-loads-to-tps.svg` `23e20f81503dfbc191b4967f93bc75c2805c011e38b7d97177e1749adc145380`; `.pdf` `fe262a95d844b3b34a28c542019aa6a3d86275aae9a94ff19c95a442fd9fdd5c`; `.png` `16a3749ee2f0331f8b881cc91d300f49d6fa867878654fa820fee98403f3b7b4`.
- **Proposed caption:** Physical Stage-A points with an exploratory in-domain fit and 16 held-out-family residual summaries; pooled LOFO R² 0.993536 and RMSE 0.000928 token/s.
- **Interpretation limits:** association is not causality or a hardware-independent law. The 0.465884 working-set result predicts hit ratio and is not compared graphically as if it predicted TPS. No projected TPS is shown.
