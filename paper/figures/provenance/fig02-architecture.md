# Figure 2 — Provider-mediated out-of-core architecture

- **Manuscript section / claim:** §4.6. Shows implemented ownership boundaries and the read-only residency signal consumed by routing.
- **Evidence class:** design/explanatory; no empirical panel.
- **Scientific/design source:** issues #114 and #124; PR #123 manuscript baseline `b4f87e9575626d0e39ae750ff6e05c2a48e42160` on `paper/manuscript-v0`.
- **Inputs:** no scientific dataset. The diagram is generated from documented architecture constants and labels in the script; these are conceptual model constants, not copied numerical results.
- **Filters / expected cardinality:** exactly two ownership regions; routing/execution, provider API, residency directory, resident slots, admission/eviction/materialization, asynchronous verified backing, immutable backing store, and an optional accelerator tier must all be rendered.
- **Generator:** `paper/figures/scripts/fig02_architecture.py`.
- **Regeneration:** `python paper/figures/scripts/fig02_architecture.py`.
- **Environment:** Python 3.9.25; Matplotlib 3.9.4.
- **Outputs / SHA-256:** `generated/fig02-architecture.svg` `82a7a2c04cf0929748170f3cc1125bbc4a0098215a8a47fb643add48a08165ad`; `.pdf` `cbf4570fbb08c7ea0f797ff5b809f739bf7086aaf7234bb6ef41449d65f68ea6`; `.png` `6aa3d3631539e3a74ac313e198f589e447c52a265d8d6e127984f86f0e760359`.
- **Proposed caption:** Provider-mediated out-of-core architecture and ownership boundary. Cache/provider state flows read-only to bounded selection; routing does not own admission or eviction.
- **Interpretation limits:** provider/cache/asynchronous-offload patterns are not claimed as new. The optional accelerator box is not CUDA/UMA evidence; #102's physical regime is CPU-only.
