# Figure 8 — Exact-cache locality-equivalence brackets

- **Manuscript section / claim:** §7.6. Shows discrete EXACT replay-capacity brackets required to reach locality physically obtained by S2_P50.
- **Evidence class:** reference locality `MEASURED_PHYSICAL`; larger EXACT capacities `EXACT_REPLAY`; bracket construction `POST_HOC_EXPLORATORY`. S2 replay rows are validated but not relabeled as physical data.
- **Scientific source:** issue #105 target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468`, analysis commit `76e0c3d578c4dba56e91d15ad643d8740037788a`, release `issue105-curated-analysis-v3`, archive SHA-256 `e0fe96c2f4dd3d2cfc8ced16901949936ba3e72c79ebdd4eb412f371fe843fb3`.
- **Inputs:** `results/2026-08-17/issue105/tables/capacity_curves.parquet` (`de97464ea1f10b3a1439ba0f52a51861cbbb1007bf19d3bcb84d64bd8bd1b0ba`); `analysis/virtual-cache-capacity.csv` (`311464cbffb6385b1e62df1038fb1c832b57d3659c2653a06534b0fc2c00d80f`); `analysis/virtual-cache-capacity-summary.json` (`9abe39c246f9ee930369936ed2b2f420a71a0263aa935034dced1ecb6e71f865`); reviewed Figure 08 sidecar (`c341e7577c699cfe512e2128d273333fb8e37b26bf540856a74ca171cbe594d4`).
- **Filters / expected cardinality:** 3,816 curve rows: 1,584 `EXACT_LRU`/`EXACT_REPLAY`, 792 S2 fixed-route, 1,440 committee fixed-route; 44 virtual-capacity rows and 44 cases. Parse discrete `(lower_slots, upper_slots] / 7,849` intervals without interpolation and preserve `INCONCLUSIVE` cases.
- **Generator / command:** `paper/figures/scripts/fig08_exact_cache_equivalence.py` wrapping the reviewed Figure 08 refactor; `python paper/figures/scripts/fig08_exact_cache_equivalence.py`.
- **Environment:** Python 3.9.25; Matplotlib 3.9.4, NumPy 2.0.2, pandas 2.2.3, pyarrow 21.0.0.
- **Outputs / SHA-256:** `generated/fig08-exact-cache-equivalence.svg` `56eb8a22f79f71fde7c97b1b3f633ea280430f5a0adfff9700e53d42055251b8`; `.pdf` `9dd211ba1f30d4c3bbc17502511104d6149f545b4d4f768e92609389e5fb0dd2`; `.png` `b288743ac1a08dfd7b239fd5da7319c49e5626798004cd70cac27368b679eced`.
- **Proposed caption:** Discrete `(lower, upper]` EXACT-replay capacity brackets for matching physical S2_P50 locality across all 44 frozen observer prompts.
- **Interpretation limits:** no interpolated exact threshold, measured RAM saving, physical larger-cache TPS, or projected-throughput claim. A replayed capacity is not a physical measurement.
