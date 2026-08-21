# Figure 5 — Provider-mediated out-of-core architecture

- **Manuscript section / claim:** §4.6. Shows the implemented ownership boundary and the read-only residency/service signal consumed by routing.
- **Evidence class:** design/explanatory; no empirical panel.
- **Scientific/design source:** issues #114 and #124; PR #123 baseline `b4f87e9575626d0e39ae750ff6e05c2a48e42160` and the architecture contract in `paper/paper.md`.
- **Inputs:** no scientific dataset. Diagram labels and conceptual constants are encoded in `paper/figures/scripts/fig02_architecture.py` and its paper-stem wrapper.
- **Filters / expected cardinality:** exactly two ownership regions; routing/execution, provider API, residency directory, cache/storage hierarchy, admission/eviction/materialization, async verified backing path, immutable backing, and optional accelerator tier must be visible.
- **Generator / command:** `paper/figures/scripts/fig04_architecture.py` wrapping `fig02_architecture.py`; `python paper/figures/scripts/fig04_architecture.py`.
- **Environment:** Python 3.9.25; Matplotlib 3.9.4.
- **Outputs / SHA-256:** `generated/fig04-architecture.svg` `82a7a2c04cf0929748170f3cc1125bbc4a0098215a8a47fb643add48a08165ad`; `.pdf` `cbf4570fbb08c7ea0f797ff5b809f739bf7086aaf7234bb6ef41449d65f68ea6`; `.png` `6aa3d3631539e3a74ac313e198f589e447c52a265d8d6e127984f86f0e760359`.
- **Proposed caption:** Provider-mediated out-of-core architecture and ownership boundary. Residency state flows read-only to bounded selection; routing does not own admission or eviction.
- **Interpretation limits:** provider/cache/asynchronous-offload patterns are not claimed as new. The optional accelerator tier is not CUDA/UMA evidence; the primary physical measurements use CPU execution and local NVMe backing.
