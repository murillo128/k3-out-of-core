# Expert storage map version 1

Status: `ACCEPTED` for issue #10 Phase 2 evidence.

`expert-storage-map-v1` records immutable GGUF source layout separately from loaded runtime/backend state. A map is valid only for the exact model size and SHA-256 recorded in `model` and the exact `llama.cpp` revision recorded alongside it.

Each of the 56 K3 `entries` identifies one routed `(layer, expert_id)` pair. `atomic_bundle_bytes` is the sum of its gate, up, and down expert slices; the three projections remain separate source spans and form one policy-level atomic bundle.

Every projection records:

- the source tensor name, source-file index, exact caller-supplied file identity, and file size;
- whole-tensor file offset and byte size;
- GGML type ID/name, logical shape, physical byte strides, expert axis, and GGUF alignment;
- `layout_kind`, one of `contiguous`, `strided`, or `segmented`;
- ordered source `spans`, each with `file_offset`, `length`, and contiguous `logical_offset`;
- runtime buffer identity and independent flags for layout transformation, backend transformation, and repacking.

Version 1 span rules are:

- `contiguous`: one span;
- `strided`: two or more equal-length spans separated by one constant positive source stride;
- `segmented`: two or more spans not representable by the strided rule.

Spans must reconstruct exactly `expert_slice_bytes` without gaps or overlap in logical order. The pinned K3 F16 and MXFP4 artifacts require one contiguous span per projection. Their eight expert spans must concatenate byte-for-byte to the whole source tensor.

Path-loaded and explicitly ordered split-path models are supported. Anonymous `FILE *` and user-supplied tensor models return `LLAMA_MODEL_STORAGE_ERROR_NO_FILE_BACKING_METADATA`; implementations must not infer a path from memory mappings, file descriptors, pointers, or `/proc`.
