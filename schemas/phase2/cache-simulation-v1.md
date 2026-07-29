# Cache simulation schemas version 1

Status: `ACCEPTED` for issue #10 Phase 3 evidence.

The simulator consumes a completed route-trace version 1 file, an expert-storage-map version 1 JSON document, and a cache-simulation manifest version 1 JSON document. It imports neither GGML nor CUDA.

Each selected `(layer, expert_id)` is one logical request for the storage map's atomic gate/up/down bundle. Requests retain canonical trace order, including consumed top-k rank order. The initial hierarchy is inclusive: every hot resident must also be cold-resident. A request checks hot, then cold, then the authoritative backing store. A cold eviction invalidates the same hot entry if present.

For each tier, `bytes_requested` is the atomic bundle size presented to that tier by the lookup cascade. `bytes_transferred` is the bundle size served by the first tier that hits. The backing store is authoritative, so every backing-store request is a backing-store hit; `backing_store_request_rate` is the modeled disk-demand rate.

LRU is a deterministic test baseline, not a production policy. `belady_min` uses exact future references and is labeled only as an offline lower bound. On every fitting miss it admits the demanded bundle and, when replacement is required, selects the farthest-next-use victim only from current residents; admission bypass is not part of this oracle. Version 1 requires all referenced bundle sizes to be equal for this exact MIN claim. Both pinned K3 maps satisfy that condition. Unequal-size synthetic cases validate slot/byte-constrained LRU; the simulator rejects an exact MIN claim for those cases rather than mislabeling a heuristic as a lower bound.

Reuse distance counts distinct atomic bundles referenced between consecutive references to the same bundle. First references are reported as `cold`. Per-layer expert skew records exact counts and the maximum expert share.

Theoretical stall is not a production-latency prediction. The manifest supplies fixed latency and bandwidth for hot, cold, and backing-store service. Version 1 uses the explicit `serial_no_overlap` model, charging the first tier that serves a request: `fixed_latency_us + bytes / bandwidth`. Cache residency carries from prefill into decode, while all counters, reuse observations, skew, and theoretical stalls are also reported separately by the phase of the triggering request.
