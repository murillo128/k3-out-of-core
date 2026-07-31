#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from common import diagnostics, git, identity, run_command, tab_records, write

PARITY_KEYS = ("prompt_ids", "tokens", "logits_hash", "route_hash", "route_records")


def exact(lhs: dict, rhs: dict) -> bool:
    return all(lhs.get(key) == rhs.get(key) for key in PARITY_KEYS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--f16", type=Path, required=True)
    parser.add_argument("--mxfp4", type=Path, required=True)
    parser.add_argument("--split-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.project_root.resolve()
    nested = root / "llama.cpp"
    executable = (args.cuda_build / "bin/phase7-async-runtime-probe").resolve()
    split_dir = args.split_dir.resolve()
    split_models = {
        "f16": sorted(split_dir.glob("*F16-split.gguf-*.gguf"))[0],
        "mxfp4": sorted(split_dir.glob("*MXFP4-split.gguf-*.gguf"))[0],
    }
    models = {
        "f16_original": args.f16.resolve(),
        "f16_split": split_models["f16"],
        "mxfp4_original": args.mxfp4.resolve(),
        "mxfp4_split": split_models["mxfp4"],
    }
    commands: list[dict] = []

    def invoke(name: str, model: Path | None = None, mode: str | None = None, steps: int | None = None,
               extra: list[str] | None = None) -> dict:
        command = [str(executable)]
        if model is not None:
            command += ["--model", str(model), "--mode", str(mode), "--steps", str(steps)]
        if extra:
            command += extra
        record, stdout, stderr = run_command(command, root)
        record["name"] = name
        record["records"] = tab_records(stdout + "\n" + stderr)
        commands.append(record)
        return record

    cases: list[dict] = []
    cold_options = ["--capacity", "16", "--cold-bytes", "67108864", "--ring-bytes", "16777216"]
    for name, model in models.items():
        disabled5 = invoke(f"{name}-disabled-5", model, "disabled", 5)
        cold5 = invoke(f"{name}-cold-5", model, "cold", 5, cold_options)
        disabled20 = invoke(f"{name}-disabled-20", model, "disabled", 20)
        warm1 = invoke(f"{name}-cold-20-a", model, "cold", 20, cold_options)
        warm2 = invoke(f"{name}-cold-20-b", model, "cold", 20, cold_options)
        values = [diagnostics("\n".join(record["stdout_tail"]), "PHASE5_LIVE") for record in
                  (disabled5, cold5, disabled20, warm1, warm2)]
        checks = {
            "commands": all(record["exit_code"] == 0 for record in (disabled5, cold5, disabled20, warm1, warm2)),
            "five_step_exact": exact(values[0], values[1]),
            "warm_a_exact": exact(values[2], values[3]),
            "warm_b_exact": exact(values[2], values[4]),
            "storage_used": all(value.get("storage_read_requests", 0) > 0 for value in (values[1], values[3], values[4])),
            "bounded": all(value.get("cold_actual_bytes", 0) <= value.get("cold_requested_bytes", 0) and
                           value.get("ring_actual_bytes", 0) <= value.get("ring_requested_bytes", 0)
                           for value in (values[1], values[3], values[4])),
            "drained": all(value.get("scheduler_active", 0) == 0 and value.get("ring_live_events", 0) == 0
                           for value in (values[1], values[3], values[4])),
            "trace_complete": all(value.get("io_trace_dropped", 0) == 0 and value.get("ring_trace_dropped", 0) == 0
                                  for value in (values[1], values[3], values[4])),
        }
        cases.append({
            "name": name,
            "model": identity(root, model),
            "disabled_five_step": values[0],
            "cold_five_step": values[1],
            "disabled_twenty_step": values[2],
            "cold_twenty_step_a": values[3],
            "cold_twenty_step_b": values[4],
            "checks": checks,
        })

    target = models["f16_split"]
    resident = invoke("placement-resident", target, "resident", 5)
    hot = invoke("placement-hot", target, "hot", 5, ["--capacity", "16"])
    disabled = next(record for record in commands if record["name"] == "f16_split-disabled-5")
    cold = next(record for record in commands if record["name"] == "f16_split-cold-5")
    placement_values = {
        name: diagnostics("\n".join(record["stdout_tail"]), "PHASE5_LIVE")
        for name, record in (("disabled", disabled), ("resident", resident), ("hot", hot), ("cold", cold))
    }
    placement_checks = {
        "commands": resident["exit_code"] == 0 and hot["exit_code"] == 0,
        "all_exact": all(exact(placement_values["disabled"], placement_values[name]) for name in ("resident", "hot", "cold")),
        "resident_no_remap": placement_values["resident"].get("execution_ids_cpu") == 0 and
                             placement_values["resident"].get("execution_ids_non_cpu") == 0,
        "resident_routing_matches_disabled": all(
            placement_values["resident"].get(key) == placement_values["disabled"].get(key)
            for key in ("routing_ids_cpu", "routing_ids_non_cpu", "routing_backend_device_type", "routing_backend_name")
        ),
        "resident_quiet": placement_values["resident"].get("resident_runtime_quiet") == 1,
        "cached_remap_cpu": all(placement_values[name].get("execution_ids_cpu", 0) > 0 and
                                placement_values[name].get("execution_ids_non_cpu") == 0 and
                                placement_values[name].get("execution_ids_backend_device_type") == 0
                                for name in ("hot", "cold")),
        "cached_execution_gpu": all(placement_values[name].get("execution_backend_device_type") == 1
                                    for name in ("hot", "cold")),
    }

    direct = invoke("f16-split-direct-io", target, "cold", 5, cold_options + ["--load-mode", "dio"])
    direct_value = diagnostics("\n".join(direct["stdout_tail"]), "PHASE5_LIVE")
    direct_checks = {
        "command": direct["exit_code"] == 0,
        "selected": direct_value.get("load_mode") == "dio",
        "source_accounted": direct_value.get("storage_direct_sources", 0) + direct_value.get("storage_direct_unsupported", 0) > 0,
        "path_accounted": direct_value.get("io_direct_operations", 0) > 0 or
                          direct_value.get("io_buffered_fallback_operations", 0) > 0,
        "exact": exact(placement_values["disabled"], direct_value),
    }

    cancellation = invoke("provider-post-h2d-cancellation", target, "cold", 5, cold_options + ["--cancel-after-h2d"])
    storage_cancel = invoke("storage-read-cancellation", target, "cold", 5, cold_options + ["--cancel-on-storage"])
    pinned = invoke("native-pinned-overlap")
    pageable = invoke("forced-pageable", extra=["--force-pageable"])
    required_overlap = invoke("production-plus-controlled-overlap", target, "cold", 20, cold_options + ["--require-overlap"])
    mechanism_checks = {
        "post_h2d_cancellation": cancellation["exit_code"] == 0,
        "storage_cancellation": storage_cancel["exit_code"] == 0,
        "native_pinned_overlap": pinned["exit_code"] == 0,
        "pageable_fallback": pageable["exit_code"] == 0,
        "required_overlap": required_overlap["exit_code"] == 0,
    }

    split_lineage = {}
    for representation, pattern in (("f16", "*F16-split.gguf-*.gguf"), ("mxfp4", "*MXFP4-split.gguf-*.gguf")):
        files = sorted(split_dir.glob(pattern))
        split_lineage[representation] = {
            "count": len(files),
            "files": [identity(root, path) for path in files],
            "source_model": identity(root, models[f"{representation}_original"]),
            "tool_head": git(nested, "rev-parse", "HEAD"),
            "mode": "split-max-tensors-1",
        }

    status = (
        all(all(case["checks"].values()) for case in cases)
        and all(placement_checks.values())
        and all(direct_checks.values())
        and all(mechanism_checks.values())
        and all(value["count"] == 218 for value in split_lineage.values())
    )
    output = {
        "schema_version": "phase7-runtime-matrix-v1",
        "status": "pass" if status else "fail",
        "revisions": {
            "project": git(root, "rev-parse", "HEAD"),
            "llama_cpp": git(nested, "rev-parse", "HEAD"),
            "gitlink": git(root, "rev-parse", "HEAD:llama.cpp"),
        },
        "configuration": {
            "hot_capacity": 16,
            "cold_bytes": 67108864,
            "transfer_ring_bytes": 16777216,
            "short_steps": 5,
            "warm_steps": 20,
            "warm_captures": 2,
        },
        "cases": cases,
        "placement": {"values": placement_values, "checks": placement_checks},
        "direct_io": {"diagnostics": direct_value, "checks": direct_checks},
        "mechanism": {"checks": mechanism_checks},
        "split_lineage": split_lineage,
        "commands": commands,
    }
    write(args.output, output)
    print("PASS: Phase 7 runtime matrix captured" if status else "FAIL: Phase 7 runtime matrix failed")
    return 0 if status else 1


if __name__ == "__main__":
    raise SystemExit(main())
