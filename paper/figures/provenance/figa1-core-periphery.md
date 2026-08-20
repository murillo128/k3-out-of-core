# Figure A1 — Core/periphery and static-pinning limitation

- **Manuscript section / claim:** Appendix C, referenced from §9.3. Shows a graded recurrent core and why static pinning is not uniformly beneficial at the same replayed capacity.
- **Evidence class:** Panel A `POST_HOC_EXPLORATORY`; Panel B `FIXED_ROUTE_COUNTERFACTUAL` + `POST_HOC_EXPLORATORY`.
- **Scientific source:** issue #105 final target `6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468`, analysis commit `76e0c3d578c4dba56e91d15ad643d8740037788a`, release `issue105-curated-analysis-v3`, archive SHA-256 `e0fe96c2f4dd3d2cfc8ced16901949936ba3e72c79ebdd4eb412f371fe843fb3`.
- **Inputs:** `results/2026-08-17/issue105/analysis/core-periphery-analysis.json` (`0f21902f655f86e46f892f885d26c590f38be9bd8a4c2509d43dc945a6d2ad92`); `analysis/committee-counterfactual-cells.csv` (`b9e70c327119c0924159614a689025be4f87a2dda7a9c473e385b02c73ac8f9d`); reviewed Figure 06 sidecar (`1e32e11a28fb78ab97bee648f190c9a7b15414bbb75c6d1da97d68dcd82e4521`). Original reviewed generator `scripts/issue105/analyze_evidence.py` (`d12d988c520c31cebe25c5ad76cb4577064b54da01a2a5eb8edeb5f427393666`).
- **Filters / expected cardinality:** Panel A uses five DECODE γ points. Panel B uses all 1,440 DECODE committee cells = 288 cells at each of five γ values; preserve all improve/unchanged/regress/infeasible classes; assert totals 308 regressions and 196 infeasible cells. This extends the reviewed source figure only by making previously retained unchanged/infeasible categories visible; scientific counts are unchanged.
- **Generator:** `paper/figures/scripts/figa1_core_periphery.py`, adapted from reviewed Figure 06 logic.
- **Regeneration:** `python paper/figures/scripts/figa1_core_periphery.py`.
- **Environment:** Python 3.9.25; Matplotlib 3.9.4, NumPy 2.0.2, pandas 2.2.3.
- **Outputs / SHA-256:** `generated/figa1-core-periphery.svg` `d9e611cd0ccf2b07fdee18896c39ab02f7fdae99079017e833882a3a9ee96374`; `.pdf` `1ff012563d5b38656d5b0293979fb6d9360df0a93dbb039d1bc16db8636b1b01`; `.png` `64c032267938a2ea2fd7399eab2a7869a2bda7da87e5eddff1ab0de84476a559`.
- **Proposed caption:** Decode core size/selected mass across recurrence γ and all same-capacity fixed-route pinning outcomes, including regressions and infeasible cells.
- **Interpretation limits:** selected-frequency core is not an expert-semantics claim; pinning is not physically benchmarked and supplies no TPS, measured RAM-saving, or production-policy result. Kept in the appendix because it is a limitation/interpretation figure rather than a new central physical result.
