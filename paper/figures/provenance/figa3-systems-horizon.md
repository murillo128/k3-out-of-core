# Figure A3 — Systems/locality horizon diagnostic

- **Manuscript section / claim:** Appendix B, referenced from §7.1. Shows why the frozen 64-token systems window is not described as a steady-state trajectory.
- **Evidence class:** `MEASURED_PHYSICAL_DIAGNOSTIC`; systems/locality only.
- **Scientific source:** issue #102 release `issue102-cross-prompt-v1`, target `0c4ed0ae92f4cc7efc79e544f04f745ff0b168cf`.
- **Input:** `results/2026-08-13/sergio-test-1/phase13-6pg-cross-prompt/long-horizon-analysis.json` (SHA-256 `99cea4736cd230e8c2dd4df28d0e409cc04301837633edee374506a1b181fbb7`). The immutable release members are indexed by the committed #102 evidence archive index.
- **Filters / expected cardinality:** three frozen workloads (`sentinel`, `low_hit`, `high_hit`) × two policies × six cumulative horizons `[16,32,64,128,256,512]`. Assert every reviewed curve classification remains `NON_MONOTONIC`; no horizon or prompt is dropped.
- **Generator / command:** `paper/figures/scripts/figa3_systems_horizon.py`; `python paper/figures/scripts/figa3_systems_horizon.py`.
- **Environment:** Python 3.9.25; Matplotlib 3.9.4.
- **Outputs / SHA-256:** `generated/figa3-systems-horizon.svg` `134cdab854a1c64a3f9658dc880d9b8a8b39157c558f8bbb057364e0bcfeed1f`; `.pdf` `a1bcc4fc9f889b0d6eed7e5f6980ca4ea65ef66c69cf3b268a121694cd5b8c5d`; `.png` `1d324eafacecefa4bf7304968438c1339dc49023541cffc24c7387c389fcd472`.
- **Proposed caption:** Cumulative physical hit-ratio trajectories at 16–512 generated tokens for all three frozen diagnostic prompts; the 64-token main window is marked.
- **Interpretation limits:** non-monotonic systems/locality trajectories do not measure semantic quality, prove long-run convergence, or invalidate the bounded 64-token physical comparison.
