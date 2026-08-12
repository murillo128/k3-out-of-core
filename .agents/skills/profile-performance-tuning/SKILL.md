---
name: profile-performance-tuning
description: Profile and tune an already-correct runtime using clean production-performance measurements, causal Perfetto/perf/FlameGraph attribution, bounded one-change-at-a-time experiments, and a validation pyramid that defers broad conformance until the retained winner.
---

# Profile and Performance Tune

## Responsibility

Use this skill when an approved controlling issue enters a performance-profiling or performance-tuning round.

This skill owns the **measurement and tuning procedure**. It does not choose phase scope, change architecture, weaken correctness, invent performance thresholds, or authorize semantic changes. The controlling issue and durable decisions own those choices.

The core rule is:

> Establish a trustworthy correctness floor, measure performance on a clean production path, profile separately, optimize the largest causal bottleneck one change at a time, and pay for broad conformance once on the retained winner rather than after every exploratory edit.

## Entry gate

Before tuning:

- identify the accepted correctness/recovery checkpoint or baseline;
- identify the exact target metric and any hard exit floor from the controlling issue;
- preserve a runnable baseline for A/B comparison;
- confirm the workload, model/artifact, cache/storage topology, thread/device configuration, and other decision-driving inputs are frozen enough for comparison;
- do not profile through a known correctness, lifetime, cancellation, stale-generation, or resource-safety defect.

A safety defect is not performance noise. Fix it or return to the owning design/execution workflow before using the result.

## Separate performance, profiling, and conformance modes

### Production-performance mode — Mode-P discipline

All throughput, latency, resource-efficiency, candidate-selection, and hard performance gates use an **unprofiled Release production path**.

When the runtime has an explicit Mode P, use it. When it does not, apply the same discipline manually: only work required by normal inference/runtime execution may run inside the timed region.

Disable or keep outside the timed hot path unless the controlling issue explicitly makes one part of production semantics:

```text
Perfetto / ftrace / perf record
CUPTI or other profiler capture
route observers / top-M research capture
quality / logit / hidden-state / teacher-forced traces
hash_state
state-attestation / compliance transcripts
per-token or per-layer evidence serialization
per-token file writes
verbose/debug logging
research-only synchronization
benchmark JSON/report generation
```

Do **not** disable production safety semantics merely to improve a number. Generation checks, bounds, required readiness, real error handling, cancellation/drain, ownership/lifetime rules, and fail-closed behavior remain enabled when they are part of the production runtime.

Mode-C/compliance cost is never a performance denominator unless the controlling issue explicitly measures it as a product feature.

### Profiling mode

Profiler runs are diagnostic evidence, not throughput acceptance runs.

Use a bounded window representative of the same production state. Keep workload/configuration adjacent to the unprofiled control and quantify profiler perturbation when the trace is used for before/after attribution.

### Conformance mode

Use full correctness/compliance/failure/lifetime validation to qualify a frozen implementation target. Do not repeatedly run the broadest matrix after every exploratory performance edit when narrower tests can prove the touched seam.

A known correctness failure is never deferred just because the implementation may be rewritten.

## Measurement order

Do not begin a tuning round with a large parameter sweep or a long final campaign.

1. Capture one clean unprofiled baseline on the real decision workload.
2. Record enough scalar counters to know work identity and resource state.
3. Capture a bounded profiling window.
4. Attribute non-overlapping wall time and rank causal buckets.
5. Change the largest material in-scope bucket first.
6. Validate narrowly, then screen the real workload.
7. Retain or reject the delta before moving to the next bucket.
8. Freeze the best coherent candidate.
9. Run complete locally applicable conformance once on that candidate.
10. Run the final statistical performance campaign required by the issue.

## Profiling stack

Reuse existing repository tracing and analysis infrastructure. Do not create a second observability framework merely for a tuning round.

### Perfetto — elapsed wall, waits, and overlap

Use Perfetto for temporal attribution:

- request/provider/cache/scheduler spans;
- I/O submission and completion;
- block/syscall/scheduler correlation where available;
- CPU/GPU/transfer dependency gaps;
- synchronization and exposed waits;
- overlap between storage, transfer, and compute.

Decompose **exposed critical-path wall**, not the sum of overlapping service durations.

### `perf stat` — CPU and system counters

Collect the smallest useful set for the current hypothesis, commonly including:

```text
task-clock
cycles
instructions
cache-references
cache-misses
context-switches
cpu-migrations
page-faults
```

Add hardware- or issue-specific counters only when they answer a material question.

### `perf record` + FlameGraph — on-CPU attribution

Capture process-wide CPU stacks, and separate inference/storage/worker threads when symbol and thread identity are reliable.

Generate folded stacks and FlameGraphs using the repository's accepted/pinned tooling when one is already defined.

FlameGraphs answer **where CPU time executes**. They do not by themselves answer wall-clock latency. Correlate on-CPU attribution with Perfetto waits/overlap before assigning recoverable wall time.

### Memory-pressure evidence

When the workload owns a large resident/cache footprint, record enough host evidence to detect reclaim or refault amplification, including where available:

```text
RSS / peak RSS / PSS / MemAvailable
cgroup memory.current / memory.max / memory.events
PSI memory
major/minor faults
pgscan / pgsteal
workingset_refault / workingset_activate
swap / pswpin / pswpout
OOM events
```

Zero swap does not prove zero memory pressure. Reclaim/refault and shrinking available headroom can still dominate performance.

### I/O evidence

Distinguish administrative concurrency from real kernel/device concurrency.

Record as appropriate:

```text
logical requests and bytes
physical/backing operations and bytes
active request high-water
active operation high-water
io_uring SQ/CQ or submission-batch high-water
per-device operations/bytes
buffered/direct/fallback state
exposed I/O wait
service latency / queue behavior when observable
```

Do not infer useful overlap from queue depth alone.

### CUDA/GPU evidence

When CUDA is part of the decision path, reuse the accepted CUPTI/device tracing vocabulary and record exposed H2D/peer/device waits separately from service time. When the host is CPU-only, do not install or require CUDA/CUPTI merely for profiling symmetry.

## Close the wall-time budget

For the representative steady-state region, account for non-overlapping wall time as far as the implementation permits. Typical buckets include:

```text
router / top-k
provider and resident-demand planning
cache lookup / admission / victim selection
scheduler and request administration
storage submission + exposed backing wait
staging / copies / checksum / remapping
H2D / peer transfer when applicable
expert compute
attention / dense / remaining graph compute
mutex / futex / scheduler delay / synchronization
memory reclaim / refault effects
sampling / token delivery
residual / unattributed
```

The bucket names are not mandatory. A coherent non-overlapping decomposition is.

If the residual is large enough to change the next optimization choice, improve attribution before editing code.

## Causal tuning loop

Optimize one material hypothesis at a time.

For every candidate record:

```text
observed bottleneck
causal hypothesis
smallest coherent change
focused correctness result
real-workload screening result
TPS / latency tails
resource deltas
profiling evidence when needed
accepted / rejected / performance-equivalent
```

Do not retain a change merely because a point estimate is higher.

Use the controlling issue's materiality/noise rules. When alternatives are performance-equivalent, follow the repository architecture-coherence rule: prefer clearer ownership/lifetime, fewer mechanisms/state machines, stronger deterministic failure behavior, lower maintenance cost, and better reuse.

If the issue does not set a tuning budget, default to a **bounded round of at most three retained causal implementation deltas** before reassessing the design. Rejected experiments do not need to consume a retained-delta slot, but repeated non-promising work against the same bucket should stop rather than becoming parameter fishing.

## Validation pyramid during tuning

Use the cheapest test that proves the risk introduced by the candidate.

### Level 1 — touched seam

Run the focused unit/native tests for the changed component.

### Level 2 — adversarial lifetime/concurrency

Run focused failure-path tests when the seam affects any of:

```text
generations
cancellation
retry
publication
scheduler state
I/O ownership
cache/slot lifetime
transfer/event lifetime
```

### Level 3 — bounded real-path smoke

Use the repository's smallest real-model or exact-layout smoke that proves:

- output/work identity expected by the issue;
- cache/provider/storage state;
- byte accounting;
- terminal resource balance;
- required concurrency/overlap structure.

### Level 4 — bounded decision-workload screen

Only candidates that survive focused correctness and look causally promising should pay for a larger real workload/capacity run.

### Level 5 — frozen-winner conformance

After selecting the retained candidate, run the complete locally applicable correctness/compliance/failure/lifetime suite once before the final performance campaign.

A later technical change invalidates that frozen qualification and requires the appropriate validation again.

## Statistical performance selection

Final performance claims use fresh, unprofiled production-performance runs.

Prefer interleaved baseline/candidate ordering when drift is plausible. Preserve all valid repetitions, including regressions. Report paired distributions/confidence or the repository's accepted equivalent rather than selecting the best run.

Throughput, tails, cold-start behavior, and resource use are separate endpoints. State tradeoffs accurately.

If a hard exit floor is defined by the issue, it is a real gate. A candidate below it cannot be rescued by a relative speedup, architectural elegance, or a good profiler story.

## External comparators

Treat another runtime/hardware/model representation as a parity target only when the comparison is genuinely comparable.

Record material differences such as:

```text
router/model quantization
resident non-expert footprint
cache organization
storage representation/API
CPU/GPU participation
kernel implementation
threading
numerical accumulation
```

A faster external runtime on the same machine is useful as a **sanity envelope** showing hardware capability, but it is not automatically an acceptance threshold. Use it to motivate and bound investigation, not to justify semantic changes outside the controlling issue.

## Scope control

Profiling may reveal that the largest recoverable bucket requires a semantic or architectural change outside the issue.

Do not silently broaden scope. Return to design authority with:

- measured bottleneck;
- estimated recoverable wall time;
- why the required intervention crosses scope;
- smallest proposed design delta;
- evidence showing why in-scope alternatives are insufficient.

Do not change routing, quantization, arithmetic, cache/storage policy, model representation, placement architecture, or public configuration unless the controlling issue already authorizes it.

## Evidence retention

Keep enough compact evidence to reproduce the decision:

- exact implementation and workload identities when material;
- unprofiled baseline and retained candidate results;
- profiler commands/configuration and perturbation note;
- compact Perfetto analysis/queries;
- `perf stat` summaries;
- folded stacks / FlameGraphs where useful;
- resource and I/O summaries;
- accepted and important rejected hypotheses.

Large `.pftrace`, `perf.data`, raw logs, or repetitive samples may use the repository's immutable checksum-addressed external-evidence pattern. Do not commit model weights, binaries, or large raw traces.

## Completion

A performance-tuning round is complete when:

- the issue's hard performance/resource gates pass on clean unprofiled production runs;
- the retained candidate is causally explained rather than selected by luck;
- full locally applicable conformance passes on the frozen candidate;
- remaining material wall time is attributed well enough to justify stopping or is returned to design with evidence;
- exact final evidence is sufficient for independent review without relying on profiler runs as the throughput denominator.
