# Figure 3 — Frozen policy-selection surface

- **Manuscript section / claim:** §3.4. Shows why S2_P50 (two swaps, p50 per-swap regret) was frozen before the later cross-workload and long-horizon campaigns.
- **Evidence class:** all three panels are `MEASURED_PHYSICAL` under the distinct #98 early-first-full protocol.
- **Scientific source:** issue #98 release `issue98-profile-shape-extension-v3`, target `485819939e9d074f99a646443a2bbab8f1466eb8`; archive `issue98-profile-shape-extension-v3.tar.zst`, 198,462 bytes, SHA-256 `f85b8fae5e4122956592723f537c4e0c905d97f47dab586da50b5e34c9356643`.
- **Inputs:** verified release members `issue98-profile-shape-extension-v3/screening/run-*/validated-summary.json` (21 cells), `confirmation/run-*/validated-summary.json` (six cells), and `final/final-synthesis.json` (60,307 bytes, SHA-256 `5142b1ce19dfe0b55e40b2f23e8932cb9ec025b9569781c56fceda0eb2766dc9`). `paper/figures/fetch_issue98.py` obtains and verifies them explicitly.
- **Filters / expected cardinality:** seven profiles × three screening observations = 21/21 passing cells; three alternated S2_P50/KNEE pairs = 6/6 passing confirmation cells. Raw min/median/max and pair ratios must exactly match the final synthesis; no row is dropped.
- **Generator / command:** `paper/figures/scripts/fig03_policy_selection.py`; `python paper/figures/scripts/fig03_policy_selection.py` (or `K3_PAPER_FIGURE_CACHE=... python paper/figures/generate_all.py`).
- **Environment:** Python 3.9.25; Matplotlib 3.9.4, NumPy 2.0.2.
- **Outputs / SHA-256:** `generated/fig03-policy-selection.svg` `124f3e91a15f873f1fb6a9a288651020d34d806092f498001879e5ec52c47d18`; `.pdf` `1baef6881220be1f611d2cc6e40df7c3988666fa7cba3384b1b60e54ca1eeff8`; `.png` `b6817c4ec4d5ccd1b7588157a09c33573fe6048931b1c18d2f28119f45daffe4`.
- **Proposed caption:** Complete physical profile-selection surface and paired confirmation: all observations are retained, S2_P50 is highlighted, and the preregistered highest-median-TPS/tie-break rule is shown.
- **Interpretation limits:** #98 absolute TPS/locality must not be pooled with the later full-prompt protocol. The figure explains a frozen choice; it does not establish a universal optimum, semantic equivalence, or candidate-count sweep.
