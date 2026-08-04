# DeepSeek-V4-Flash 24 GB CUDA validation

Result: **negative at Checkpoint A; stopped before tuning and the full-model matrix.**

The exact four-split `UD-Q3_K_XL` artifact at revision `85ce4196ab6e82852e25dfec2b7e2beaae56f5f1` passed all size and SHA-256 checks and completed the smallest ordinary provider-disabled inference on the RTX 3090 host without swap, major faults, OOM retry, or managed oversubscription.

Metadata-only inventory then found three complete routed-expert bundle layouts:

- 41 layers use `IQ3_XXS / IQ3_XXS / MXFP4` and 10,878,976-byte bundles;
- layer 26 uses `MXFP4 / MXFP4 / Q6_K` and 15,794,176-byte bundles;
- layer 42 uses `IQ3_XXS / IQ3_XXS / Q6_K` and 13,303,808-byte bundles.

The current generic provider derives one prototype layout, requires every routed layer to match it, and uses that prototype for hot-slot, cold-cache, and transfer-ring sizing. It therefore fails closed during descriptor discovery before cache allocation when it reaches the second layout class. Safely supporting this artifact requires an accepted typed-layout-class boundary with bounded per-class resource accounting. That is larger than the issue's permitted minimal architecture-independent descriptor extension, so the slot/kernel probe and full matrix were not run.

The negative result is isolated from storage correctness: 121,634,816,000 routed payload bytes were deferred; the generic storage map retained four loader-owned sources and 33,024 spans; and ten real bundles, including one crossing split files, were byte-identical across direct source reads, synchronous storage, and buffered asynchronous `io_uring` reads. Missing-file, wrong-size, wrong-SHA, short-read, EIO, integrity, cancellation, and drain coverage is preserved by the acquisition fixtures and focused native tests.

Six of seven K3 focused CUDA suites passed. The transfer-ring native-event reuse assertion failed on both the candidate and a clean build at the exact nested base, at the same source line, so it is recorded as an `OBSERVED` pre-existing host/driver-sensitive failure rather than a candidate regression.

The technical record is [manifest.json](manifest.json), the bounded layout record is [inventory-summary.json](inventory-summary.json), and the external evidence members are identified by [archive-index.json](archive-index.json).
