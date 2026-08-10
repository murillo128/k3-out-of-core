# Kimi K3 immutable router tensor pack

This directory records the static router input for
`moonshotai/Kimi-K3@9f62e4e9fffbd0a83ddd60e1c209d828994b3569` qualified by Phase 13.5.
The authoritative binary bytes are published at the immutable release tag
`issue75-kimi-k3-router-pack-v1` in these ordered assets:

- `kimi-k3-router-tensors-native-source-split-00031-of-00033.tar.zst`
- `kimi-k3-router-tensors-native-source-split-00032-of-00033.tar.zst`
- `kimi-k3-router-tensors-native-source-split-00033-of-00033.tar.zst`

The pack contains exactly 92 native F32 router projection matrices and 92 native
F32 selection-correction vectors. It intentionally contains no dynamic route,
cache, transfer, or performance evidence.

On a fresh CPU-only machine, download the assets into one directory and run the
verification command recorded in `manifest.json`. Verification checks asset and
per-tensor sizes/hashes, exact routed-layer coverage, and a bounded vector-norm /
cosine-similarity smoke operation without access to the full 1.56-TB model.
