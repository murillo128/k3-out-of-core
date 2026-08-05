# Phase 12.5 Perfetto event schema

Schema version: `k3-perfetto-track-event-v1`.

The optional `LLAMA_PERFETTO` build emits static `k3.*` TrackEvent categories. The default build omits the SDK and CUPTI sources; the enabled build remains dormant unless an external system session activates `k3.*` and the evidence owner sets `LLAMA_PERFETTO_CAPTURE=1` before CUDA initialization.

## Identity and clock rules

All application identities are unsigned numeric values. The high byte identifies the domain: request `1`, token `2`, graph `3`, scheduler flight `4`, storage `5`, transfer `6`, CUDA `7`, and resource `8`. Pair and operation identities pack bounded slot/generation/operation components below the domain byte. They are values, not addresses.

Application TrackEvents are emitted on `CLOCK_MONOTONIC_RAW`; Trace Processor normalizes them onto its common timeline. CUPTI is registered with a `CLOCK_MONOTONIC_RAW` callback and every retained CUPTI interval is emitted with Perfetto built-in clock ID `5` (`MONOTONIC_RAW`). `trace_session_start` and `trace_session_stop` publish raw-clock anchors. Qualifying verification permits at most 1 ms of anchor residual and rejects unknown CUPTI timestamps, negative intervals, or packet order regression on a CUDA track.

## Static categories

| Category | Representative events | Stable fields |
| --- | --- | --- |
| `k3.request` | model/context create and teardown, request begin, decode, process ubatch | request ID, phase, token/ubatch index, token count, graph type |
| `k3.route` | extraction, selected key, route publication | request, phase, token, layer, original expert, rank/count |
| `k3.provider` | initialize, prepare, request, bind, acquire/remap, release | request, graph epoch, layer, layout class, selected count, terminal state |
| `k3.scheduler` | enqueue, join, dispatch, flight, terminal | flight, layer, original expert, layout class, priority, queue depth, state |
| `k3.cache.hot` | lookup, hit/miss, reserve, victim, eviction, admission, pin/unpin, publish | request, layer/expert, slot, generation, layout class |
| `k3.cache.cold` | lookup, hit/miss/join, reserve, victim, eviction, admission, pin/unpin, publish | layer/expert, slot, generation, layout class, reference kind |
| `k3.storage` | plan, submit, queue start, request, operation, terminal | flight, layer/expert, request slot/generation/ordinal, operation index, offsets/bytes, native result, io_uring flag |
| `k3.transfer` | lane reserve/release, stage, H2D, event wait | flight/cold/hot generations, lane, event generation, layout class, bytes |
| `k3.graph` | build, reserve, compute, expert layer boundary | graph, request, layer, token count, selected count |
| `k3.cuda` | runtime/driver API, queued launch, kernel, memcpy/memset, synchronization | application and CUPTI correlation, device/context/stream/grid, launch geometry, bytes/copy or sync kind |
| `k3.resource` | scheduler, storage, hot/cold cache, transfer occupancy | bounded numeric counter only |
| `k3.lifecycle` | trace anchors, cancellation, shutdown, surrender, unload | numeric status, error/drop counts, bounded resource diagnostics |

Scheduler dispatch and terminal instants are joined by a process-scoped Perfetto flow. Provider requests, scheduler flights, storage requests/operations, transfers, and CUDA activities use dedicated asynchronous tracks. `request_slot + request_generation`, `operation_index`, `lane + event_generation`, and CUPTI correlation fields disambiguate reuse.

The schema records no prompt or generated text, token pieces, paths, tensor data, logits, usernames, arbitrary environment values, secrets, or pointers. Kernel names are CUPTI-provided static/bounded names. High-cardinality free-form labels are forbidden.

## Verification interpretation

`cupti_unmatched_correlations` includes ordinary CUDA initialization/teardown calls executed outside any application scope. Required correlation is therefore verified structurally: every non-zero application correlation must resolve to a request, graph, or flight identity; decision-driving kernels must resolve to a graph; provider H2D copies must resolve to a flight; and CUPTI activity correlation IDs must resolve to a runtime/driver API record. Zero application correlation is permitted only for CUDA work outside a traced request/graph/flight scope.

OS events are not assigned application IDs. They share the normalized Perfetto timeline and are attributed by interval overlap and process/thread identity. Block events may be absent for a warm page-cache interval even when the configured block source is present; config preflight records source availability separately from observed event count.
