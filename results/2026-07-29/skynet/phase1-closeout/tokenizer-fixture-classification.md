# Tokenizer vocabulary fixture classification

**Status:** OBSERVED — quarantined external fixture failure

The stable CPU and CUDA subsets pass. The excluded test clones the external
`https://huggingface.co/ggml-org/vocabs` repository and does not reference Kimi-K3.
Its GGUF inputs remain Git LFS pointer text, so llama.cpp reads `vers` rather
than the required `GGUF` magic. This quarantine applies only to
`test-tokenizers-ggml-vocabs` and must be removed when real payloads are available.

## Recovery attempt

- Git LFS status: `unavailable`.
- Detail: git: 'lfs' is not a git command. See 'git --help'.

The most similar command is
	log

## Affected files

- `llama.cpp/models/ggml-vocabs/HybridDNA/ggml-vocab-carbon.gguf` — LFS oid `6dcc59f0f217eaddd7141c085e96ae2753dae61e2eb42695b15b912517166012`, expected 6009062 bytes
- `llama.cpp/models/ggml-vocabs/PLaMo2/ggml-vocab-plamo2.gguf` — LFS oid `6f0df3eac864f04137699d780d7fcdafec713c8c4990948c540a88fae398a0fd`, expected 2440910 bytes
- `llama.cpp/models/ggml-vocabs/RWKV/ggml-vocab-rwkv-7-world.gguf` — LFS oid `ef1da3b3fa3c026800367dab2c1204fb92964a2199350e7133e67d1af91c2e70`, expected 1365451 bytes
- `llama.cpp/models/ggml-vocabs/SPM/ggml-vocab-gemma-3.gguf` — LFS oid `68d854494379ec3133f08de356ae37c7ad2c8a0cdcd23ded05e90eeee2c79924`, expected 6512800 bytes
- `llama.cpp/models/ggml-vocabs/UGM/ggml-vocab-nomic-bert-moe.gguf` — LFS oid `d0ccc742eeb2a0148adc5505aeaf65a8f6ff7d1c4a91bff8abe0b027048fff74`, expected 6821834 bytes
- `llama.cpp/models/ggml-vocabs/WPM/ggml-vocab-jina-v2-en.gguf` — LFS oid `d2e94cd2a61a79d50328c4d53f87c21132ce1177b463a6153064334fe1847808`, expected 631075 bytes

## Stable matrix

- cpu: 54/54 stable tests passed; fixture test separately reproduced the pointer failure.
- cuda: 54/54 stable tests passed; fixture test separately reproduced the pointer failure.
