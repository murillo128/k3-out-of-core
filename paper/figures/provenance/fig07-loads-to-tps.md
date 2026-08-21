# Figure 7 — Backing loads/token to physical decode TPS

- **Manuscript section / claim:** §7.5. Shows raw physical points and reviewed leave-one-family-out validation for loads/token predicting decode TPS inside the measured domain.
- **Evidence class by panel:** Panel A points `MEASURED_PHYSICAL`; Panel A fit and Panel B residual/LOFO summaries `POST_HOC_EXPLORATORY`.
- **Scientific source:** issue #105 target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468`, analysis commit `76e0c3d578c4dba56e91d15ad643d8740037788a`, release `issue105-curated-analysis-v3`, archive SHA-256 `e0fe96c2f4dd3d2cfc8ced16901949936ba3e72c79ebdd4eb412f371fe843fb3`.
- **Inputs:** `results/2026-08-17/issue105/tables/physical_runs.csv` (`47527a419d6ec3d1c9939beb3d6ec6b7776627079db6e4011707e746bb03b64c`); `analysis/locality-tps-validation.json` (`8e81c4cfe22bc59ab65fb6df475515efe72dd23d3d409a46a3765161db0e0f9e`); reviewed `analysis/figures/figure-03.sidecar.json` (`0a825c040f7d4d4ffcd517cf48f319cbe57cbe6ca68776551e086412b85bea26`). Original generator `scripts/issue105/analyze_evidence.py` (`d12d988c520c31cebe25c5ad76cb4577064b54da01a2a5eb8edeb5f427393666`).
- **Filters / expected cardinality:** 128 Stage-A/primary/S2_P50 rows = 16 families × eight levels; 16 LOFO folds and 128 residual observations. Assert selected predictor `loads_per_token`; R² and RMSE are read from the authorized analysis artifact.
- **Generator / command:** `paper/figures/scripts/fig07_loads_to_tps.py` wrapping the reviewed paper-specific refactor; `python paper/figures/scripts/fig07_loads_to_tps.py`.
- **Environment:** Python 3.9.25; Matplotlib 3.9.4, NumPy 2.0.2, pandas 2.2.3.
- **Outputs / SHA-256:** `generated/fig07-loads-to-tps.svg` `23e20f81503dfbc191b4967f93bc75c2805c011e38b7d97177e1749adc145380`; `.pdf` `fe262a95d844b3b34a28c542019aa6a3d86275aae9a94ff19c95a442fd9fdd5c`; `.png` `16a3749ee2f0331f8b881cc91d300f49d6fa867878654fa820fee98403f3b7b4`.
- **Proposed caption:** Physical Stage-A points with an exploratory in-domain fit and 16 held-out-family residual summaries; pooled LOFO R² 0.993536 and RMSE 0.000928 token/s.
- **Interpretation limits:** association is not causality or a hardware-independent law. The 0.465884 working-set result predicts hit ratio and is not presented as a competing TPS predictor. No projected TPS is shown.
