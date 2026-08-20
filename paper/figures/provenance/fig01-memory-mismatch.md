# Figure 1 — K3 selected-payload service-demand funnel

- **Manuscript section / claim:** §2.2. Explains why sparse top-16 execution still presents 1,472 selected ExpertBundles, or about 25.83 GB of cumulative selected payload, per token before reuse/cache.
- **Evidence class:** model/runtime constants; explanatory derivation. No physical measurement panel.
- **Scientific source:** issues #73 and #102. #102 final target `0c4ed0ae92f4cc7efc79e544f04f745ff0b168cf`, release `issue102-cross-prompt-v1` (archive SHA-256 `e198913eb541b2a2e7465a01e09215fc5fecf6fb91574ff1841b11bf2664250c`).
- **Inputs:** `results/2026-08-10/issue73-k3-optimization/checkpoint-a/manifest.json` (SHA-256 `0dd860eb65164c23ce41b171c3ba502ab7a3ef4b97c41d9bd178bdabb0aa8b5f`); `corpus/phase13/issue102-preregistration-v1.json` (SHA-256 `31444069cb1221bbf585288fb39e476d69c7e74a1fb56b2ec23043b3a5cb6149`).
- **Filters / expected cardinality:** no row filtering. Assert 896 experts/layer, exact top-16, 92 routed layers, 17,547,264 bytes/bundle; derive `16 × 92 = 1,472` and `1,472 × 17,547,264 = 25,829,572,608` bytes/token.
- **Generator:** `paper/figures/scripts/fig01_memory_mismatch.py`.
- **Regeneration:** `python paper/figures/scripts/fig01_memory_mismatch.py`.
- **Environment:** Python 3.9.25; Matplotlib 3.9.4 (shared pins in `paper/figures/requirements.txt`).
- **Outputs / SHA-256:** `generated/fig01-memory-mismatch.svg` `070bc6f63a604d0b000c0cfa28e96b8e26ed1f2549fa7373ae42b0218dcd397e`; `.pdf` `3f46ed594936137f9739f4b4a263efdaa96069adea6fd630b65c9f3ea47d2aa0`; `.png` `2173ce014ea09ec80ff51e11bddd215c4baf1c92fd6ee215a9bd783d9c72f57b`.
- **Proposed caption:** K3 selected-payload service-demand funnel. The derived 25.83 GB/token is cumulative logical selected payload before reuse/cache, not resident RAM or measured backing traffic.
- **Interpretation limits:** no total checkpoint size is inferred; no RAM requirement, physical bytes-read value, TPS, or cache-hit rate is claimed.
