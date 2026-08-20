# Figure 4 — Cross-workload physical systems result

- **Manuscript section / claim:** §7.4. Shows workload-conditioned Stage-A S2_P50 TPS and the complete 24-prompt Stage-C physical TPS contrasts.
- **Evidence class:** Panel A and Panel B are `MEASURED_PHYSICAL` only.
- **Scientific source:** issue #102 final target `0c4ed0ae92f4cc7efc79e544f04f745ff0b168cf`, release `issue102-cross-prompt-v1`, archive SHA-256 `e198913eb541b2a2e7465a01e09215fc5fecf6fb91574ff1841b11bf2664250c`; curated through issue #105 final target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468`, analysis commit `76e0c3d578c4dba56e91d15ad643d8740037788a`, release `issue105-curated-analysis-v3`, archive SHA-256 `e0fe96c2f4dd3d2cfc8ced16901949936ba3e72c79ebdd4eb412f371fe843fb3`.
- **Input:** `results/2026-08-17/issue105/tables/physical_runs.csv` (SHA-256 `47527a419d6ec3d1c9939beb3d6ec6b7776627079db6e4011707e746bb03b64c`).
- **Filters / expected cardinality:** file 184 rows, all source class `MEASURED_PHYSICAL`; Panel A `stage=STAGE_A`, `case_role=primary`, `policy=S2_P50`: 128 unique prompts, 16 families, exactly eight ordinal levels/family. Panel B `stage=STAGE_C`, `case_role=primary`: 24 unique prompts with one EXACT and one KNEE row each plus a case-matched frozen Stage-A S2_P50 row; assert 24/24 ratios above 1.0 for each contrast. Eight `STAGE_A_SENTINEL` rows provide a p90–p10 timing reference, not a CI.
- **Generator:** `paper/figures/scripts/fig04_cross_workload.py`.
- **Regeneration:** `python paper/figures/scripts/fig04_cross_workload.py`.
- **Environment:** Python 3.9.25; Matplotlib 3.9.4, NumPy 2.0.2, pandas 2.2.3.
- **Outputs / SHA-256:** `generated/fig04-cross-workload.svg` `430e886e1db3385e7e84b80a08933163eb0b0e5582518580c4e2fc49e0a47028`; `.pdf` `2cb8cc61e89ffedae58c5938f28191c468f79f32b188d4b58f9cc45d407ca6ae`; `.png` `f74d73d9eec844d19f1e74a0d525611fbf82882b197ae85bd52eb062063f088d`.
- **Proposed caption:** All 128 Stage-A S2_P50 physical observations and all 24 Stage-C S2/EXACT and S2/KNEE physical TPS ratios, with the complete heterogeneity and one observation per prompt/policy cell visible.
- **Interpretation limits:** Stage C is a frozen non-random cross-family/locality-selected subset, not a random sample. The 24/24 result is not a population confidence statement. Ordinal length levels are not treated as equal token intervals; #98 data are not pooled.
