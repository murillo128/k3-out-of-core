# Phase 6 — GGUF-backed storage and synchronous demand reads

Issue #22 implements model-owned positional GGUF storage, metadata-only routed tensors, and atomic synchronous storage-to-cold admissions.

- Checkpoint A corrective review: `PASS`, safety `YES`, issue comment `5133647261`.
- Original and generated 218-part split F16/MXFP4 captures preserve exact prompt IDs, logical route hashes, generated IDs, and full-logit hashes against disabled CUDA baselines.
- Two 20-step captures per representation and layout exercise forced cold eviction/reread with bounded cold/ring/pinned bytes and zero resident-source copy bytes; separate large-cold captures prove repeated demand becomes a cold hit without a second storage read.
- Every successful storage-to-cold admission compares a rolling digest of the positional source reads with an independent digest over the final cold destinations before publication. Mismatch poisons storage, publishes no `READY` entry, and is covered by the storage fault test.
- A separate native evidence probe copies one populated `READY` cold bundle through a read-only observer. The capture independently `pread`s its declared GGUF spans and requires exact byte count and SHA-256 equality for original and split F16/MXFP4; the split bundles span three distinct files.
- Routed allocation, mmap binding, and prefetch are zero; normal resident tensors remain loaded.
- Cancellation after a partial positional read returns public abort status, publishes no hot/cold mapping, balances references, cleans failed slots, and succeeds on retry.
- Structured descriptor-lifetime evidence proves the storage-owned handles return to baseline, storage administration is checked against a topology-derived upper bound, and all five focused tests pass under CPU, CUDA, and ASan+UBSan builds.
- Split binaries are deleted after capture; their complete size/SHA-256 and immutable source-model lineage is stored in the authoritative manifest.
- Timing is descriptive. Filesystem/NVMe identity is recorded in the manifest and no unsupported direct-I/O, overlap, prefetch, CPU-fallback, concurrency, UMA, or multi-GPU claim is made.

The authoritative record is `phase6-manifest.json`; `verify_phase6.py --strict` is the fail-closed verifier.
