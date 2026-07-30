#!/usr/bin/env python3
"""Compare the bounded resident-provider administration before and after the corrective fast path."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import MODELS, cmake_configuration, compile_cpp, git, parse_fields, run, sha256, validate_models


CORRECTIVE_BASE = "523f825d2df5efa7c9a08561e2b64861ad5594c5"


def ensure_corrective_base(root: Path) -> tuple[Path, Path]:
    checkout_root = Path("/tmp") / f"k3-phase3-admin-base-{CORRECTIVE_BASE[:12]}"
    source = checkout_root / "llama.cpp"
    if not source.exists():
        checkout_root.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--quiet", "--shared", str(root / "llama.cpp"), str(source)], root)
        run(["git", "checkout", "--quiet", "--detach", CORRECTIVE_BASE], source)
    if git(source, "rev-parse", "HEAD") != CORRECTIVE_BASE or git(source, "status", "--porcelain"):
        raise RuntimeError("corrective-base checkout identity or cleanliness mismatch")

    build = checkout_root / "build-cpu"
    run([
        "cmake", "-S", str(source), "-B", str(build),
        "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=ON",
        "-DLLAMA_BUILD_TESTS=ON", "-DLLAMA_CURL=OFF", "-DGGML_CUDA=OFF",
    ], root)
    run(["cmake", "--build", str(build), "--target", "llama", "-j4"], root)
    return source, build


def execute(binary: Path, model: Path, root: Path) -> dict[str, Any]:
    completed = run([str(binary), str(model)], root, check=False)
    if completed.returncode != 0 or "RESULT\texit=0" not in completed.stdout:
        raise RuntimeError(f"administrative probe failed ({completed.returncode}):\n{completed.stderr}")
    return {
        "command": [str(binary), str(model)],
        "metrics": parse_fields(completed.stdout, "ADMIN"),
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu-build", type=Path, required=True)
    parser.add_argument("--f16", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    model = args.f16.resolve()
    validate_models({"f16": model})
    candidate_revision = git(root / "llama.cpp", "rev-parse", "HEAD")
    if candidate_revision == CORRECTIVE_BASE or git(root / "llama.cpp", "status", "--porcelain"):
        raise RuntimeError("administrative evidence requires a clean committed candidate after the corrective base")
    base_source, base_build = ensure_corrective_base(root)
    candidate_build = args.cpu_build.resolve()
    if cmake_configuration(base_build) != cmake_configuration(candidate_build):
        raise RuntimeError("corrective-base and candidate CPU configurations differ")

    with tempfile.TemporaryDirectory(prefix="k3-phase3-admin-") as temporary_name:
        temporary = Path(temporary_name)
        base_binary = temporary / "provider-admin-base"
        candidate_binary = temporary / "provider-admin-candidate"
        source = root / "scripts/phase3/provider_admin_probe.cpp"
        compilations = {
            "corrective_base": compile_cpp(root, base_build, base_binary, [source], base_source),
            "candidate": compile_cpp(
                root, candidate_build, candidate_binary, [source], root / "llama.cpp",
                ["-DK3_FAST_PATH_DIAGNOSTICS=1"],
            ),
        }
        base = execute(base_binary, model, root)
        candidate = execute(candidate_binary, model, root)

    old = base["metrics"]
    new = candidate["metrics"]
    checks = {
        "same_workload_shape": old["contexts"] == new["contexts"] == 2 and
            old["prompt_tokens"] == new["prompt_tokens"] == 5 and
            old["prepare_calls"] == new["prepare_calls"] > 0 and
            old["first_bindings"] == new["first_bindings"] == 7 and
            old["second_bindings"] == new["second_bindings"] == 7,
        "base_per_binding_leases": old["handles_acquired"] == old["handles_released"] and
            old["handles_acquired"] == old["prepare_calls"] * old["first_bindings"],
        "candidate_per_ubatch_lease": new["handles_acquired"] == new["handles_released"] == new["prepare_calls"],
        "lease_reduction_observed": new["handles_acquired"] < old["handles_acquired"],
        "one_registration_and_validation_per_layer": new["bundle_registrations"] ==
            new["bundle_full_validations"] == new["first_bindings"],
        "subsequent_binds_use_fast_path": new["bundle_fast_path_hits"] > 0 and
            new["bundle_full_validations"] < new["bind_calls"],
        "binding_storage_reserved": new["first_binding_capacity"] >= 8 and
            new["second_binding_capacity"] >= 8,
    }
    if not all(checks.values()):
        raise RuntimeError(f"administrative fast-path checks failed: {checks}")

    report = {
        "schema_version": "phase3-provider-administration-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "scope": "focused F16 CPU two-context administrative diagnostic; not a standing performance capture",
        "revisions": {"corrective_base": CORRECTIVE_BASE, "candidate": candidate_revision},
        "model": {**MODELS["f16"], "path": str(model), "observed_sha256": sha256(model)},
        "build_configuration": cmake_configuration(candidate_build),
        "compilations": compilations,
        "base": base,
        "candidate": candidate,
        "checks": checks,
        "interpretation": {
            "base_full_descriptor_validations": "one per bind call by direct inspection of the exact corrective-base source",
            "candidate_full_descriptor_validations": "reported counter",
            "timing_gate": "none",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "status": "pass", "checks": len(checks)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
