# Phase 12.5 Checkpoint B — full-stack trace capability

`OBSERVED`: Checkpoint B passes on nested target `6de4f4fef72e9346bdad03459302adcfa19fd576`. The trace-producing parent target was `2fed7264d1f76041d34c2ac82854d21d1079e751`; the committed verifier target was `d4674ae8b81c1c0d6c8470f333da3cb831ab2963`. Both captures used the same nested binary and gitlink.

The 13.4 MiB tiny trace and 407.8 MiB one-token DeepSeek provider trace are parseable by Perfetto v50.1 Trace Processor. Each contains application TrackEvents, Linux scheduler/syscall/block/fault/filemap/process/system evidence, and CUPTI kernels, copies, synchronization and API activity on one normalized timeline. Required-source loss, CUPTI errors/drops, unknown timestamps, incomplete slices, invalid flows and clock mismatches are all zero.

The provider trace reconstructs 2,157 dispatch-to-terminal flows, 6,251 graph-correlated kernels and 6,471 flight-correlated copies. Its raw-to-common-clock residual is 0.423 ms, below the declared 1 ms tolerance. CUPTI peak shared allocation is 136,314,752 bytes, below the 256 MiB hard limit. All seven teardown/drain/surrender slices are present before the single trace stop.

Both workloads exactly match their adjacent accepted no-session reference across prompt IDs, generated IDs/text, logits and all route records. The five committed SQL files reproduce token accounting, storage queue/service rows, CPU runqueue state, CUDA activity/correlation and GPU/storage overlap. For capability validation—not causal ranking—the short trace reports a 1.197 s storage queue-wait p95 versus 3.988 ms operation-service-wall p95.

Raw traces are intentionally not committed. Their exact local identities are recorded in `checkpoint-b.json`; immutable external publication belongs to Checkpoint C. This evidence remains diagnostic for warm virtio/ext4 and does not substitute for Phase 12 physical-NVMe gates.
