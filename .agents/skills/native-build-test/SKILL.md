---
name: native-build-test
description: Accelerate non-trivial CMake and CTest loops conservatively by deriving safe parallelism from online CPUs and available memory while preserving incremental builds, test isolation, truthful failures, and optimized host-native execution for expensive tests.
---

# Native Build and Test Acceleration

## Responsibility

Use this utility skill when an executor or independent reviewer is about to run a non-trivial native CMake build or CTest suite. It optimizes operational build/test scheduling and avoids unnecessary de-optimization of expensive same-host tests. The controlling issue, exact test semantics, and evidence requirements remain authoritative.

When the work includes performance, real-model, full-model, hardware, or otherwise expensive integration execution, also read `MAX_NATIVE_POLICY.md` once and apply it unless the controlling issue explicitly requires a different build envelope.

The objective is to avoid habitual fixed low parallelism such as `-j4` and slow portability-oriented builds on capable hosts without trading speed for OOMs, flaky shared-resource tests, hidden retries, invalid A/B comparisons, or irreproducible validation.

## Hard boundaries

- Keep the existing CMake generator, toolchain, targets, and test selection unless the controlling issue authorizes a change. Preserve an explicitly issue-pinned build configuration when that configuration is itself an evidence input.
- Do **not** preserve a slower generic/minimum-ISA build merely for reproducibility when the active outcome is host-specific performance or an expensive same-host integration/full-model test. Follow `MAX_NATIVE_POLICY.md`: reproducibility is the exact optimized build fingerprint, not deliberate de-optimization.
- Prefer incremental builds. Do not clean, reconfigure, use `--clean-first`, or create another build directory solely to gain parallelism. A separate optimized or sanitizer build directory is justified when the active test class genuinely requires a different build envelope; reuse it afterward.
- Do not install or enable `ccache`, `sccache`, Ninja, or another tool automatically. Reuse them only when the existing build is already configured to do so.
- Do not run two builds that write the same build directory concurrently.
- Do not overlap a build with tests from that build directory.
- Do not parallelize decision-driving performance runs, full-model runs, GPU-sharing tests, fixed-port tests, pressure tests, or tests that intentionally contend for the same files/device unless the issue explicitly defines that concurrency.
- Parallelism is an operational choice unless the issue or command makes it part of the tested configuration. Never alter a decision-driving queue depth, worker count, thread count, or benchmark concurrency under this skill.
- A job count typed by the agent from habit is not authoritative. An explicit issue requirement or operator-provided override is.

## Build-envelope rule

Before paying for an expensive test, classify the required build:

```text
host-specific performance / full-model / expensive integration
    -> optimized Release + maximum stable native features exposed by the host

focused ordinary correctness
    -> reuse the existing optimized build when semantics permit

Debug / assertions / ASan / UBSan / TSan / race instrumentation
    -> dedicated instrumented build only for the tests that require it

portability / minimum-ISA compatibility
    -> explicit generic/capped build only because portability is the claim
```

Do not run a many-minute/hour full-model test in a generic, AVX2-capped, Debug, sanitizer, or otherwise slower build unless that exact build property is decision-driving.

For A/B comparisons, require matching build fingerprints except for the intentional delta. A mismatch in ISA/native flags, compiler/toolchain, linkage, Release/debug mode, backend variant, or other material codegen input invalidates performance attribution; do not interpret the resulting TPS difference.

## Respect explicit overrides

Use an explicit positive integer from the first applicable source:

1. an issue-defined exact command or decision-driving build/test concurrency;
2. operator-provided `K3_BUILD_JOBS` / `K3_TEST_JOBS`;
3. pre-existing `CMAKE_BUILD_PARALLEL_LEVEL` / `CTEST_PARALLEL_LEVEL`;
4. otherwise derive safe values below.

When an exact issue command contains a fixed parallelism value, run it as written first. Change only the operational parallelism on later repetitions when the issue does not treat that value as evidence identity and the result remains directly comparable.

An issue-pinned historical build envelope remains authoritative only for the evidence cell that actually requires it. It does not automatically cap a later `*_BEST` or expensive production-representative qualification unless the issue says so.

## Derive safe parallelism once per host and build class

Determine:

- online logical CPUs from `getconf _NPROCESSORS_ONLN`, falling back to `nproc` and then `1`;
- available host memory from `/proc/meminfo::MemAvailable`;
- finite cgroup-v2 headroom from `memory.max - memory.current` when available;
- effective available memory as the minimum finite host/cgroup value.

Reserve `max(2 GiB, 10% of effective available memory)` for the OS, linker, test harness, and runtime. Estimate:

```text
ordinary C/C++ Release build       2 GiB per compile job
CUDA, sanitizer, LTO, or known-heavy build
                                   3 GiB per compile job
```

Then select:

```text
memory_jobs = floor(usable_memory / per_job_memory)
build_jobs  = clamp(min(online_cpus, memory_jobs, 32), 1, 32)
test_jobs   = clamp(min(build_jobs, 16), 1, 16)
```

Use the lower value when the host is already under material memory pressure. Once a stable lower value is discovered after an OOM, retain it for that host/build class during the session rather than probing upward repeatedly.

A reference shell calculation is:

```bash
online="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 1)"
avail_kib="$(awk '/^MemAvailable:/ { print $2 }' /proc/meminfo 2>/dev/null)"
: "${avail_kib:=2097152}"

if [ -r /sys/fs/cgroup/memory.max ] && [ -r /sys/fs/cgroup/memory.current ]; then
    cg_max="$(cat /sys/fs/cgroup/memory.max)"
    cg_cur="$(cat /sys/fs/cgroup/memory.current)"
    if [ "$cg_max" != max ] && [ "$cg_max" -gt "$cg_cur" ] 2>/dev/null; then
        cg_kib="$(( (cg_max - cg_cur) / 1024 ))"
        [ "$cg_kib" -lt "$avail_kib" ] && avail_kib="$cg_kib"
    fi
fi

reserve_kib="$(( avail_kib / 10 ))"
[ "$reserve_kib" -lt 2097152 ] && reserve_kib=2097152
usable_kib="$(( avail_kib - reserve_kib ))"
[ "$usable_kib" -lt 2097152 ] && usable_kib=2097152

# Use 3145728 instead for CUDA/sanitizer/LTO or a known-heavy build.
per_job_kib=2097152
memory_jobs="$(( usable_kib / per_job_kib ))"
[ "$memory_jobs" -lt 1 ] && memory_jobs=1

build_jobs="$online"
[ "$memory_jobs" -lt "$build_jobs" ] && build_jobs="$memory_jobs"
[ "$build_jobs" -gt 32 ] && build_jobs=32
[ "$build_jobs" -lt 1 ] && build_jobs=1

test_jobs="$build_jobs"
[ "$test_jobs" -gt 16 ] && test_jobs=16

printf 'native build parallelism: build=%s test=%s cpus=%s available_kib=%s\n' \
    "$build_jobs" "$test_jobs" "$online" "$avail_kib"
```

Print the chosen values once in the terminal/log. Do not add routine job-count prose to issue comments or technical manifests unless build resource behavior is itself material evidence.

## Build efficiently

- Configure only when required by changed CMake inputs, a missing build directory, or a genuinely different build class from `MAX_NATIVE_POLICY.md`.
- Build the smallest useful target set first, then the wider required set at the checkpoint.
- Prefer the generator-neutral form:

```bash
cmake --build <build-dir> --parallel "$build_jobs" --target <target>...
```

- When a command omits `--parallel`, exporting `CMAKE_BUILD_PARALLEL_LEVEL="$build_jobs"` is acceptable.
- Replace an agent-invented `-j4` with the derived value. Do not rewrite an operator/issue-mandated `-j4` silently.
- Preserve multi-config `--config` arguments and all other command semantics.
- Reuse already-built dependencies and existing native targets rather than invoking a broad `all` build after every edit.
- For a fixed decision host, prefer the repository's supported maximum-native feature path. If auto-detection is unreliable, use an explicit maximum feature set matching the host rather than falling back to a slower generic baseline.

## Test efficiently and safely

For independent focused native tests, prefer:

```bash
ctest --test-dir <build-dir> \
      --parallel "$test_jobs" \
      --output-on-failure \
      <existing selectors>
```

Use `CTEST_PARALLEL_LEVEL="$test_jobs"` when a repository wrapper invokes CTest without an explicit parallel flag.

Before an expensive integration/full-model/hardware test, verify the build class is appropriate. Do not spend real-model runtime on a deliberately slower compatibility or instrumentation build unless that build is the test subject.

Run serially when a test:

- measures performance or timing;
- consumes the full model or a large shared cache;
- owns a GPU, NVMe namespace, fixed port, daemon, shared mutable fixture, or pressure budget;
- is known or declared not parallel-safe;
- must be isolated for reproducible evidence.

Build focused test executables in one parallel build, then run independent tests in one CTest invocation instead of alternating build/test per target.

## Failure handling

- Treat a compiler diagnostic or test assertion as a real failure; parallelism does not justify an automatic retry.
- When the build shows credible memory exhaustion (`Killed`, exit `137`, `cc1plus`/`nvcc` killed, allocator failure, or cgroup OOM evidence), halve `build_jobs` and retry the exact incremental build once. Do not clean first.
- If the retry also OOMs, retain the lower stable value or reduce once more only when necessary to complete required validation; report the host limitation honestly.
- If a parallel CTest run fails and shared-resource interference is plausible, rerun only the failed test(s) serially once. Preserve and report the original parallel failure. A serial pass identifies a parallel-safety/flakiness problem; it does not erase the first result.
- Never loop retries until green and never classify silence or a long-running process as failure while it remains active.

## Expected behavior on common project hosts

The formula intentionally remains conservative for scheduling but not for code generation:

- a 32-logical-CPU / ~192-GiB OCI host normally selects `build_jobs=32` rather than `4`, while expensive host-specific CPU tests use its maximum stable native ISA rather than an AVX2 portability cap;
- a 72-logical-CPU / ~48-GiB host remains memory-limited rather than selecting all CPUs;
- a small 8-GiB VM stays near `build_jobs=2` or `3` depending on current headroom;
- CUDA/sanitizer builds receive fewer compile jobs than ordinary Release builds on the same host, and sanitizer execution is kept to the tests that require it.

These are examples, not fixed configuration. Always derive from the actual host and respect explicit overrides and test purpose.