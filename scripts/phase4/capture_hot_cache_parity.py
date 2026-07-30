#!/usr/bin/env python3
"""Capture issue #17 disabled/hot CUDA correctness, cache, and timing evidence."""

from __future__ import annotations

import argparse
import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import (CHECKPOINT_A_LLAMA, MODELS, cmake_configuration, git, gpu_identity,
                    json_write, parse_fields, run, run_monitored, sha256, validate_models)


EXPECTED_IDS = [318, 57195, 11, 1459, 387, 1495, 2189, 261]
CAPACITIES = {
    "exact-top-k": {"capacity": 2, "n_ubatch": 1},
    "all-experts": {"capacity": 56, "n_ubatch": 5},
}


def execute(root: Path, binary: Path, model: Path, mode: str, configuration: dict[str, int],
            temporary: Path, stem: str) -> dict[str, Any]:
    routes = temporary / f"{stem}.routes"
    logits = temporary / f"{stem}.logits"
    command = [str(binary), "--model", str(model), "--mode", mode,
               "--capacity", str(configuration["capacity"]),
               "--n-ubatch", str(configuration["n_ubatch"]), "--max-generate", "8",
               "--routes", str(routes), "--logits", str(logits)]
    completed, peak_gpu_mib = run_monitored(command, root)
    if completed.returncode != 0 or "PHASE4_RESULT\texit=0" not in completed.stdout:
        raise RuntimeError(f"{stem} failed ({completed.returncode})\n{completed.stdout}\n{completed.stderr}")
    result: dict[str, Any] = {
        "command": command, "returncode": completed.returncode,
        "run": parse_fields(completed.stdout, "PHASE4_RUN"),
        "prompt_ids": parse_fields(completed.stdout, "PHASE4_PROMPT_IDS")["values"],
        "generated_ids": parse_fields(completed.stdout, "PHASE4_GENERATED_IDS")["values"],
        "routes_sha256": sha256(routes), "routes_size": routes.stat().st_size,
        "logits_sha256": sha256(logits), "logits_size": logits.stat().st_size,
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "peak_gpu_memory_mib": peak_gpu_mib,
    }
    if mode == "hot":
        result["first_hot"] = parse_fields(completed.stdout, "PHASE4_FIRST_HOT")
        result["final_hot"] = parse_fields(completed.stdout, "PHASE4_FINAL_HOT")
        result["last_ids"] = parse_fields(completed.stdout, "PHASE4_LAST_IDS")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--f16", type=Path, required=True)
    parser.add_argument("--mxfp4", type=Path, required=True)
    parser.add_argument("--phase3-manifest", type=Path, required=True)
    parser.add_argument("--capacities", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    models = {"f16": args.f16.resolve(), "mxfp4": args.mxfp4.resolve()}
    validate_models(models)
    requested = args.capacities.split(",")
    if requested != ["exact-top-k", "all-experts"]:
        raise RuntimeError("capacities must be exact-top-k,all-experts")
    candidate = git(root / "llama.cpp", "rev-parse", "HEAD")
    run(["git", "merge-base", "--is-ancestor", CHECKPOINT_A_LLAMA, candidate], root / "llama.cpp")
    binary = args.cuda_build.resolve() / "bin/phase4-hot-cache-probe"
    if not binary.is_file():
        raise FileNotFoundError(binary)

    cases = []
    with tempfile.TemporaryDirectory(prefix="k3-phase4-parity-") as name:
        temporary = Path(name)
        for artifact, model in models.items():
            for capacity_name in requested:
                configuration = CAPACITIES[capacity_name]
                disabled = execute(root, binary, model, "disabled", configuration, temporary,
                                   f"{artifact}-{capacity_name}-disabled")
                hot = execute(root, binary, model, "hot", configuration, temporary,
                              f"{artifact}-{capacity_name}-hot")
                first, final = hot["first_hot"], hot["final_hot"]
                per_miss_first = first["h2d_bytes"] // first["misses"]
                checks = {
                    "prompt_ids_exact": disabled["prompt_ids"] == hot["prompt_ids"] == "18805,308,799,5624,12524",
                    "generated_ids_exact": disabled["generated_ids"] == hot["generated_ids"] == ",".join(map(str, EXPECTED_IDS)),
                    "routes_exact": disabled["routes_sha256"] == hot["routes_sha256"],
                    "logits_exact": disabled["logits_sha256"] == hot["logits_sha256"],
                    "hot_duplicate_nodes_only": hot["run"]["graph_nodes"] == disabled["run"]["graph_nodes"] + 7 and hot["run"]["graph_bindings"] == 7 and disabled["run"]["graph_bindings"] == 0,
                    "disabled_hot_work_zero": all(disabled["run"][key] == 0 for key in ("provider_objects", "provider_callbacks", "provider_copies", "provider_syncs", "provider_failures")),
                    "fixed_addresses": first["address_hash"] == final["address_hash"] and first["address_count"] > 0,
                    "balanced_pins": final["current_pins"] == 0 and final["pin_acquires"] == final["pin_releases"],
                    "no_callback_allocations": final["remap_dynamic_allocations"] == 0,
                    "physical_ids_bounded": all(0 <= int(slot) < configuration["capacity"] for slot in hot["last_ids"]["execution"].split(",")),
                    "no_integrity_failures": all(final[key] == 0 for key in ("stale_failures", "copy_failures")) and hot["run"]["provider_failures"] == 0,
                    "h2d_bytes_match_misses": first["h2d_bytes"] == first["misses"]*per_miss_first and final["h2d_bytes"] == final["misses"]*per_miss_first,
                    "scheduler_checkpoints_complete": final["sync_checkpoints"] == final["remap_checkpoints"] == hot["run"]["provider_callbacks"],
                    "context_extent_recorded": final["extent"] == configuration["n_ubatch"] and final["required_capacity"] <= configuration["capacity"],
                }
                if capacity_name == "all-experts":
                    checks["true_cross_epoch_hits"] = final["hits"] > first["hits"] and final["hits"] > 0 and final["generation_changes"] == final["misses"]
                    checks["graph_reuse"] = hot["run"]["graphs_reused"] > 0
                if not all(checks.values()):
                    raise RuntimeError(f"parity failure {artifact}/{capacity_name}: {checks}")
                cases.append({"artifact": artifact, "capacity_class": capacity_name,
                              "configuration": configuration, "checks": checks,
                              "disabled": disabled, "hot": hot})

    report = {
        "schema_version": "phase4-hot-cache-parity-v1", "status": "pass",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "revision": candidate,
        "models": {name: {**MODELS[name], "path": str(path), "observed_sha256": sha256(path)} for name, path in models.items()},
        "phase3_manifest": {"path": str(args.phase3_manifest), "size": args.phase3_manifest.stat().st_size, "sha256": sha256(args.phase3_manifest)},
        "build": {"path": str(args.cuda_build.resolve()), "configuration": cmake_configuration(args.cuda_build.resolve()), "probe_sha256": sha256(binary)},
        "gpu": gpu_identity(root), "cases": cases,
    }
    json_write(args.output, report)
    print(f"PASS: wrote {args.output} with {len(cases)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
