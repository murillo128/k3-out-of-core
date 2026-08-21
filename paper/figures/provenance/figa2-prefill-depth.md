# Figure A2 — Prefill-depth protocol diagnostic

- **Manuscript section / claim:** Appendix B, referenced from §7.1. Explains why the final physical protocol consumes the full prompt and why absolute early-first-full measurements are not pooled with it.
- **Evidence class:** bounded `MEASURED_PHYSICAL_DIAGNOSTIC`; explanatory and explicitly not Stage-A/Stage-C acceptance evidence.
- **Scientific source:** issue #102 release `issue102-cross-prompt-v1`, target `0c4ed0ae92f4cc7efc79e544f04f745ff0b168cf`.
- **Inputs:** `results/2026-08-13/sergio-test-1/phase13-6pg-cross-prompt/prefill-depth-locality-curve.json` (SHA-256 `31d7fc0b4ae809ef296898bab9579b20010588ae0523685ed68453c3fdf9cfb4`); `prefill-depth-prefix-corpus.json` (`9de09549e794de4b4f40edfde87db47932407dcad423742b1d91388c018c6e58`).
- **Filters / expected cardinality:** five fixed depths `[9,16,32,64,100]`; EXACT and S2_P50 medians at every depth. Assert status pass, bounded-diagnostic classification, prefix-corpus identity, S2 locality and TPS advantage at each measured depth, and reviewed non-monotonic-decay interpretation. No point is dropped.
- **Generator / command:** `paper/figures/scripts/figa2_prefill_depth.py`; `python paper/figures/scripts/figa2_prefill_depth.py`.
- **Environment:** Python 3.9.25; Matplotlib 3.9.4, NumPy 2.0.2.
- **Outputs / SHA-256:** `generated/figa2-prefill-depth.svg` `0adf59366ef99135f1421c73d180a5d42108d0efa6263aef1ff3741836f2a633`; `.pdf` `aeb9d3bde1778b1f454d292ddbbdad6be4fc2225bd77c6566767e03e1f47bab2`; `.png` `58e0931b5f0fd59d4f9fdcebe1f4dabac8c1943e6e138a320715c1c8ab20594f`.
- **Proposed caption:** Physical locality and relative TPS across a bounded prefill-depth diagnostic; the exceptionally large early advantage decays substantially under fuller prompt ingestion.
- **Interpretation limits:** interior points have one clean process per arm; this is a protocol diagnostic, not the main acceptance corpus, a steady-state curve, or a basis for pooling distinct protocols.
