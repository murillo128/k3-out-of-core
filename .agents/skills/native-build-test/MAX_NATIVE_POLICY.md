# Max-native build/test policy

This policy is consumed by `native-build-test` and `profile-performance-tuning`.

## Principle

Reproducibility means reproducing the exact optimized build/runtime envelope used on the decision host. It does **not** mean intentionally compiling below the maximum stable ISA or backend feature set exposed by that host.

For host-specific performance work and expensive runtime/integration/full-model tests, default to the fastest production-representative build that preserves the tested semantics:

```text
Release / production optimization
+ maximum stable native CPU ISA/features exposed by the host
+ maximum normal production backend optimizations
+ production linkage/configuration chosen for the runtime
```

Use the repository-supported native feature-detection path when it is trustworthy. If native auto-detection is unreliable, use an explicit feature set matching the maximum guest/host features actually exposed. Do not intentionally cap to AVX2, generic x86-64, or another portability baseline solely to make a run reproducible.

## Build fingerprint

Reproduce and compare optimized runs by recording enough identity to prove the build envelope is the same, including as applicable:

```text
host CPU/model and exposed feature flags
compiler + version
generator/toolchain
Release/build type and optimization level
GGML_NATIVE or equivalent
resolved ISA/backend feature set
relevant C/CXX flags
static/shared linkage mode
LTO/PGO or other production codegen options when used
backend variant selected at runtime
thread count / affinity / NUMA policy when decision-driving
project + nested source identity
binary identity for final evidence when required
```

For an A/B performance or expensive behavioral comparison, the fingerprint must match except for the intentional source/configuration delta under test. A material build-fingerprint mismatch invalidates the comparison.

Static versus shared linkage and native versus explicit ISA are separate dimensions. Do not change several build dimensions at once and then attribute the result to one runtime delta.

## Test-class defaults

### Focused correctness/unit tests

Reuse the already-configured optimized build whenever the test semantics do not require another configuration. Correctness does not require deliberately slow code generation.

### Expensive integration, real-model, full-model and hardware tests

Use an optimized Release/max-native build by default. These tests may take minutes or hours; do not multiply their cost with a generic/portable ISA merely for reproducibility.

### Performance tests

Always use the production-performance build envelope required by `profile-performance-tuning`: unprofiled Release/production optimization, maximum stable native features for the fixed decision host, and Mode-P discipline.

### Profiling / attribution runs

Profiling should preserve the optimized production code path whenever the profiler permits it.

Perfetto/ftrace does **not** require a de-optimized build. Run it against the same Release/max-native binary/configuration used by the adjacent production cell, with tracing enabled only for the bounded profiling run.

`perf stat` likewise uses the same optimized binary.

For `perf record` / folded stacks / FlameGraph, keep normal production optimization and max-native code generation. Add symbol/debug information (`-g`, an equivalent CMake configuration, or separate debug symbols) without lowering optimization. Do not switch to `Debug`, `-O0`, generic ISA, or another slow build merely to obtain symbols.

If reliable stack unwinding requires an additional code-generation change such as `-fno-omit-frame-pointer`, treat that as a **profiling-only fingerprint delta**, use it only when needed, and keep an adjacent unprofiled control on the untouched production binary. Prefer DWARF/separate-symbol unwinding when reliable enough to avoid changing production code generation.

Profiler perturbation belongs to the profiler configuration/run, not to an intentionally slower application build. Throughput acceptance still comes only from the clean unprofiled production cell.

### Debug/assertion/sanitizer/race tests

Use Debug, ASan/UBSan, TSan, Compute Sanitizer, or another deliberately instrumented build only when that configuration is the object of the test or is required to expose the relevant defect. Run the narrowest applicable subset there; do not make every long integration/full-model repetition pay sanitizer/debug cost.

### Portability / compatibility / minimum-ISA tests

Use generic or explicitly capped ISA builds only when portability/minimum-CPU compatibility is itself the claim being tested. Keep those cells separate from production-performance and expensive same-host qualification.

## Evidence interpretation

A result produced under a slower compatibility build may remain valid evidence for the claim it was designed to test, but it does not define the performance ceiling or constrain `*_BEST` selection on a more capable host.

Conversely, a max-native build is reproducible only on a host exposing the required feature set. Record that host envelope honestly rather than pretending it is a portable binary claim.
