# Phase 12.5 Checkpoint B — full-stack trace capability

`OBSERVED`: Checkpoint B passes on parent target `f5cdcc3f8c691aa0f8069d46599a7b5aedbf6887` and nested target `e7e86d5cb20060a9ea38cfd2a52ac37eff5fd8d7`.

The 13.4 MiB tiny trace and 398.6 MiB one-token DeepSeek provider trace are parseable by Perfetto v50.1 Trace Processor. Each contains application TrackEvents, Linux scheduler/syscall/block/fault/filemap/process/system evidence, and CUPTI kernels, copies, synchronization and API activity on one normalized timeline. Required-source loss, CUPTI errors/drops, unknown timestamps, incomplete slices, invalid flows, clock mismatches and zero-correlated synchronization overlapping request execution are all zero.

The provider trace reconstructs 2,157 dispatch-to-terminal flows, 6,251 graph-correlated kernels and 6,471 flight-correlated copies. Its raw-to-common-clock residual is 0.088 ms, below the declared 1 ms tolerance. CUPTI peak shared allocation is 137,363,328 bytes, below the 256 MiB hard limit. All seven teardown/drain/surrender slices are present before the single trace stop.

Both workloads exactly match their adjacent accepted no-session reference across prompt IDs, generated IDs/text, logits and all route records. The five committed SQL files reproduce token accounting, storage queue/service rows, CPU runqueue state, CUDA activity/correlation and GPU/storage overlap. For capability validation—not causal ranking—the short trace reports a 1.187 s storage queue-wait p95 versus 3.695 ms operation-service-wall p95.

Raw traces are intentionally not committed. Their exact local identities are recorded in `checkpoint-b.json`; immutable external publication belongs to Checkpoint C. This evidence remains diagnostic for warm virtio/ext4 and does not substitute for Phase 12 physical-NVMe gates.
