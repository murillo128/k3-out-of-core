# Issue 69 Checkpoint A — attribution baseline

Status: `PASS`

The production provider remains the accepted issue #65 baseline. Nested commit
`6b011b8f2dbce23197496ecd54afc4e3b571feb6` adds only test/evidence seams; parent
commit `768440765db8cc539cb006eb7f5b822aa8c95049` owns the profiling harness used by
this manifest. The exact accepted model revision and all three shard hashes were
reverified before publication.

## Decision gate

All four structural premises in issue #69 are `OBSERVED`:

| Premise | Observation |
| --- | --- |
| Storage-worker topology coupling | Legacy positional I/O resolves to 1 worker in S0 and 2 workers in S1/A1, so topology changes the storage comparison. |
| Strict hot→cold inclusion | Every hot key is duplicated in cold. A1 has 1,573 hot keys and 1,579 cold keys but still only 1,579 distinct hierarchy keys; it reads 35,939,155,968 backing bytes versus S1's 34,333,851,648. |
| Material cold→pinned staging | A1 attributes 13.24% of process on-CPU samples and 28.74% of main-thread samples to transfer-ring staging/memcpy. Its trace records 433 stages totaling 742.60 ms in the fixed window. |
| Multi-device completion barrier | S1 has 109/109 and A1 128/128 miss-rich layer cycles where all reads complete before the next H2D; S0 has 0/110. |

The cache-policy optimization decision remains `OPEN` until the required
post-structural profile. In this baseline, cold policy/victim work accounts for
10.21% of S0 process samples and 18.76% of A1 process samples.

## Fixed-fixture observations

Mode-P generated-token/text identity is exact across S0/S1/A1. As established by
issue #65, Mode-P logits digests differ between the one-device and multi-device
topology paths; Mode-C exactness remains a final-candidate gate.

| Cell | Decode tok/s | Hot keys | Cold keys | Duplicate keys | Distinct keys | Storage reads | Storage bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| S0 | 1.3645 | 268 | 1,579 | 268 | 1,579 | 4,195 | 34,152,448,000 |
| S1 | 1.3939 | 536 | 1,579 | 536 | 1,579 | 4,217 | 34,333,851,648 |
| A1 | 1.4451 | 1,573 | 1,579 | 1,573 | 1,579 | 4,412 | 35,939,155,968 |

The 128 GiB control exposes 11,008 effective cold slots, exactly the routed-key
working set, using 119,755,767,808 actual bytes. After warming all keys, the
measured 24-token window performs exactly zero backing reads and zero backing
bytes. Minimum `MemAvailable` is 89,216,412 KiB and `SwapTotal` is zero. The old
path still stages 48,225,058,816 bytes and spends 8,195,524 µs staging, which
isolates the cold-RAM→pinned→VRAM feed-path floor from filesystem latency.

## Visual and machine-readable evidence

- [S0 process FlameGraph](profiles/s0/process.svg)
- [S0 main-thread FlameGraph](profiles/s0/main.svg)
- [S0 storage-worker FlameGraph](profiles/s0/storage.svg)
- [S0→A1 differential FlameGraph](profiles/s0-to-a1-differential.svg)
- [S1 process FlameGraph](profiles/s1/process.svg)
- [A1 process FlameGraph](profiles/a1/process.svg)
- [Checkpoint manifest](manifest.json)

Each profile directory also contains its folded stacks and `summary.json` with
the exact command, 10-second profiler delay, thread-selection rule, `perf stat`
counters, kernel, build/cache hashes, and FlameGraph revision. Perfetto provides
the logical-window wall-time and wait correlation; on-CPU samples are not used
as wall-time estimates.

Raw `perf.data`, fixed-window traces, unprofiled workloads, resource samples,
and the zero-storage control are published in the immutable
[`issue69-checkpoint-a-attribution-v1`](https://github.com/murillo128/k3-out-of-core/releases/tag/issue69-checkpoint-a-attribution-v1)
release with checksum-addressed assets.
