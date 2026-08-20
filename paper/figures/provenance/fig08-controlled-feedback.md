# Figure 8 — Controlled autoregressive feedback

- **Manuscript section / claim:** §8.4. Separates direct routing perturbation from free-trajectory token-mediated feedback and retains all registered bridge prompts.
- **Evidence class:** `DIRECT_FIXED_CONTEXT` and `FREE_TRAJECTORY`; neither is a physical backing benchmark.
- **Scientific source:** issue #99 final target `eeaab5fa3f62047e8617ab3ed408ccbddbb56872`, release `issue99-long-horizon-quality-v1`. Core asset 225,619,088 bytes, SHA-256 `59590e168f2d122ef8948d60aa1b1102c79e553846b4d4570ca62cdb4a7e3763`.
- **Inputs:** verified release `longrun-checkpoints.parquet`, SHA-256 `ea9d98b5bccaa91d5ed356f214a8f81de0e7ed3fcdf6f9e4dd761eda1d6f64e6`; committed preregistration `73917df7f533a7a15f8b2de708c03b937613f9a13ded0d1a2c33a7aad19afdba`; committed analysis `fdd0877cd5f25bd6858181eb2fede8291ab280f33616da7b0fd0d58717b3553c`; reviewed Figure 03 sidecar `c8d7e616938d908be010b3af9251f43a75afecd0596c94062ddd2a560e2f264a`. Original reviewed generator `scripts/issue99/reproduce_release.py` SHA-256 `8d4747ff3b4e10455be9d98a18ebf262588095416565ba5c81131be22c36d4e4`.
- **Acquisition:** same checksum-verifying `paper/figures/fetch_issue99.py` contract as Figure 7.
- **Filters / expected cardinality:** registered bridge cohort exactly `issue102-sentinel`, `04-factual-b4`, `10-planning-b2`; high-cache KNEE/S2_P50 × direct/free × seven checkpoints 16..1024 = exactly 84 rows and 12 complete prompt/policy/evidence cells. Plot raw cumulative mean hidden relative L2 versus EXACT. Read the 1.4210366× effect and its interval from committed analysis.
- **Generator:** `paper/figures/scripts/fig08_controlled_feedback.py`, adapted from reviewed Figure 03 while making direct/free raw trajectories explicit.
- **Regeneration:** `python paper/figures/scripts/fig08_controlled_feedback.py` (or `python paper/figures/generate_all.py`).
- **Environment:** Python 3.9.25; Matplotlib 3.9.4, pandas 2.2.3, pyarrow 21.0.0; `zstd` for first extraction.
- **Outputs / SHA-256:** `generated/fig08-controlled-feedback.svg` `65a0267012dbb33ec47756ab2aded3e50742fab40671872f6ffd32402eb32123`; `.pdf` `a6793696d0a23ab9890894422bb240a377fa50df8a5f2d842e973b24fe287de8`; `.png` `aa3f92091895e91b9a0cdc224f3a42903ae95cb0987bc24afdbb06aa0963b357`.
- **Proposed caption:** Direct fixed-context and free-trajectory hidden-state divergence through 1,024 tokens for all three bridge prompts; registered mean amplification 1.4210× with heterogeneous shapes.
- **Interpretation limits:** 1.4210× is amplification of the measured controlled perturbation, not 1.42× worse quality, accuracy loss, or universal growth. Heterogeneity and non-monotonic trajectories are retained.
