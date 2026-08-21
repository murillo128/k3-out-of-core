# Figure 5 — Bounded cache-aware membership selection

- **Manuscript section / claim:** §5.4. Explains exact top-16 and candidate top-32 membership, contemporaneous service-tier state, strictly improving replacements, per-swap regret and swap limits, exactly-16 output, and original K3 weighting semantics.
- **Evidence class:** method/explanatory with registered constants; not a physical-performance or semantic-equivalence result.
- **Scientific source:** issue #77 release `issue77-phase13-6-evidence-v1`, target `9d0433896032055d9e114b61686717ec172e0329`; #99 final target `eeaab5fa3f62047e8617ab3ed408ccbddbb56872` supplies the frozen policy record.
- **Input:** `results/2026-08-17/issue99/checkpoint-a/preregistration.json` (SHA-256 `73917df7f533a7a15f8b2de708c03b937613f9a13ded0d1a2c33a7aad19afdba`).
- **Filters / expected cardinality:** read policy S2_P50 and assert candidate count 32, exact selection count 16, `max_swaps = 2`, and per-swap corrected-score regret `0.007303759455680847` from the committed record.
- **Generator / command:** `paper/figures/scripts/fig05_bounded_routing.py` wrapping `fig03_bounded_routing.py`; `python paper/figures/scripts/fig05_bounded_routing.py`.
- **Environment:** Python 3.9.25; Matplotlib 3.9.4.
- **Outputs / SHA-256:** `generated/fig05-bounded-routing.svg` `467ebc3bdcee20ef47a771f21736a8aa6b50225f8de6409de80dc311fd9a7830`; `.pdf` `a6470e51923eb335e6d415e6bf6eb11b2d52cbdc422297e27036ea66245e2da8`; `.png` `939cdc970278aa1d7b06b035a7eff6e8df2999ff17b13dedb33d57b5d5a5451a`.
- **Proposed caption:** Ordinary K3 scores define exact top-16 and candidate top-32 membership; contemporaneous service state may trigger at most two bounded deterministic swaps; original K3 probabilities still define final weights.
- **Interpretation limits:** the regret bound is local and operational, not semantic regret, a quality guarantee, or evidence of exact-output equivalence. Top-32 is a fixed protocol boundary, not a new sweep result.
