# Figure A4 — Capacity-conditioned perturbation and predictive damage

- **Manuscript section / claim:** Appendix D, referenced from §8.7. Shows that accepted capacity regime changes alter realized substitutions and fixed-context ΔNLL in the three-prompt bridge.
- **Evidence class:** lower capacity `CAPACITY_FIXED_CONTEXT`; higher capacity `DIRECT_FIXED_CONTEXT`; both are controlled model-execution evidence, not physical-throughput runs.
- **Scientific source:** issue #99 final target `eeaab5fa3f62047e8617ab3ed408ccbddbb56872`, release `issue99-long-horizon-quality-v1`.
- **Inputs:** `results/2026-08-20/issue99/analysis/analysis.json` (SHA-256 `fdd0877cd5f25bd6858181eb2fede8291ab280f33616da7b0fd0d58717b3553c`). Values trace to the immutable release checkpoint/token datasets documented by the final #99 sidecars; `fetch_issue99.py` verifies the checkpoint member (`ea9d98b5bccaa91d5ed356f214a8f81de0e7ed3fcdf6f9e4dd761eda1d6f64e6`).
- **Filters / expected cardinality:** six terminal pairs = three bridge prompts × KNEE/S2_P50 at token 512, each containing both accepted capacity regimes. Assert final outcomes `CAPACITY_CHANGES_PREDICTIVE_DAMAGE = yes` and `CAPACITY_CHANGES_REALIZED_PERTURBATION = yes`; no pair is dropped.
- **Generator / command:** `paper/figures/scripts/figa4_capacity_quality.py`; `python paper/figures/scripts/figa4_capacity_quality.py`.
- **Environment:** Python 3.9.25; Matplotlib 3.9.4, NumPy 2.0.2.
- **Outputs / SHA-256:** `generated/figa4-capacity-quality.svg` `99230e5273e56be5a42f4873e85bfc96c22ae7533e3d4271345d40b7f9aeef50`; `.pdf` `c6a730754211ee99b8dde05b939dbcec5d53b5d71fe7b5be4295529d61e29055`; `.png` `024b1df51a7c2ac977a9ce1440788aa68ad8240b1e870529804d1140aba1268c`.
- **Proposed caption:** Paired capacity regimes for realized substitutions and fixed-context predictive damage at token 512 across all three bridge prompts and both approximate policies.
- **Interpretation limits:** three prompts do not establish a universal, monotonic, or cross-model capacity law; ΔNLL is not task accuracy; the plot is not a measured RAM/TPS comparison.
