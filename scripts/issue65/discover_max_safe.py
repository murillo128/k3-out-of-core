#!/usr/bin/env python3
"""Discover an explicit whole-expert capacity with bounded fresh-process probes."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Callable


PROMPT = (
    "<｜begin▁of▁sentence｜><｜User｜>Explain why a careful measurement should distinguish "
    "observed facts from assumptions.<｜Assistant｜><think>"
)
MIB = 1024 * 1024
MEMORY_REJECTION_PATTERNS = (
    "out of memory", "cuda error: out of memory", "failed to allocate",
    "allocation failed", "ggml_status_alloc_failed",
    # Provider error 6 is allocation_failed. The cold-cache error below is the
    # deterministic byte-budget rejection when minimum_slots exceeds the slots
    # representable by the configured cold cache.
    "provider error 6",
    "expert cache initialization failed at shared cold cache (provider error 8)",
)
LIFECYCLE_ZERO_KEYS = (
    "active_background_flights", "current_hot_pins", "cold_current_transfer_refs",
    "cold_current_request_refs", "cold_current_cpu_execution_refs",
)


@dataclass(frozen=True)
class ProbeDecision:
    outcome: str
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--project-revision", required=True)
    parser.add_argument("--nested-revision", required=True)
    parser.add_argument("--model-repository", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-variant", required=True)
    parser.add_argument("--artifact-identity-manifest", type=Path, required=True)
    parser.add_argument("--resident-device", type=int, required=True)
    parser.add_argument("--target-device", type=int, required=True)
    parser.add_argument(
        "--role-template", required=True,
        help="Comma-separated ordinal:slots roles with exactly one {candidate} placeholder.")
    parser.add_argument("--target-uuid", required=True)
    parser.add_argument("--target-bdf", required=True)
    parser.add_argument("--reserve-bytes", type=int, default=1_073_741_824)
    parser.add_argument("--slot-stride", type=int, default=11_835_264)
    parser.add_argument("--lower-bound", type=int, default=268)
    parser.add_argument("--upper-bound", type=int)
    parser.add_argument("--peer-staging-bytes", type=int, default=67_108_864)
    parser.add_argument("--n-gpu-layers", type=int)
    prompt = parser.add_mutually_exclusive_group()
    prompt.add_argument("--prompt", default=PROMPT)
    prompt.add_argument("--prompt-file", type=Path)
    parser.add_argument("--cold-bytes", type=int, default=17_179_869_184)
    parser.add_argument("--ring-bytes", type=int, default=67_173_120)
    parser.add_argument("--queue-depth", type=int, default=256)
    parser.add_argument("--io-workers", type=int)
    parser.add_argument("--max-generate", type=int, default=24)
    parser.add_argument("--sample-period", type=float, default=0.25)
    parser.add_argument("--max-probes", type=int, default=32)
    args = parser.parse_args()
    if args.prompt_file is not None:
        args.prompt = args.prompt_file.read_text().removesuffix("\n")
        args.prompt_source = str(args.prompt_file)
    else:
        args.prompt_source = "COMMAND_LINE_OR_DEFAULT"
    return args


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_identity(args: argparse.Namespace) -> dict[str, object]:
    source = json.loads(args.artifact_identity_manifest.read_text())
    artifact = source.get("artifact", {})
    files = artifact.get("files", [])
    if (artifact.get("repository") != args.model_repository or
            artifact.get("revision") != args.model_revision or
            artifact.get("variant") != args.model_variant or not files or
            any(not item.get("name") or not isinstance(item.get("size"), int) or
                not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))) for item in files) or
            args.model.name not in {item["name"] for item in files}):
        raise RuntimeError("artifact identity manifest does not match the requested model")
    return {
        "identity_manifest": str(args.artifact_identity_manifest),
        "identity_manifest_sha256": sha256_file(args.artifact_identity_manifest),
        "model_repository": artifact["repository"],
        "model_revision": artifact["revision"],
        "variant": artifact["variant"],
        "total_bytes": artifact.get("total_bytes"),
        "files": files,
        "runtime_model_path": str(args.model),
    }


def _cuda_version(value: int) -> str:
    return f"{value // 1000}.{(value % 1000) // 10}"


def cuda_runtime_inventory() -> dict[str, str]:
    driver = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
        text=True, timeout=5).splitlines()[0].strip()
    library = ctypes.util.find_library("cudart") or "libcudart.so.12"
    cudart = ctypes.CDLL(library)
    runtime_value = ctypes.c_int()
    driver_api_value = ctypes.c_int()
    if (cudart.cudaRuntimeGetVersion(ctypes.byref(runtime_value)) != 0 or
            cudart.cudaDriverGetVersion(ctypes.byref(driver_api_value)) != 0):
        raise RuntimeError("CUDA runtime/driver API version query failed")
    nvcc = shutil.which("nvcc") or "/usr/local/cuda/bin/nvcc"
    nvcc_output = subprocess.check_output([nvcc, "--version"], text=True, timeout=5)
    toolkit_match = re.search(r"release\s+([0-9]+(?:\.[0-9]+)?)", nvcc_output)
    if not toolkit_match:
        raise RuntimeError("CUDA toolkit version is unavailable")
    return {
        "nvidia_driver_version": driver,
        "cuda_driver_api_version": _cuda_version(driver_api_value.value),
        "cuda_runtime_version": _cuda_version(runtime_value.value),
        "cuda_toolkit_version": toolkit_match.group(1),
    }


def gpu_inventory() -> list[dict[str, object]]:
    command = [
        "nvidia-smi",
        "--query-gpu=index,pci.bus_id,uuid,memory.total,memory.used,memory.free",
        "--format=csv,noheader,nounits",
    ]
    output = subprocess.check_output(command, text=True, timeout=5)
    result: list[dict[str, object]] = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 6:
            raise RuntimeError("unexpected nvidia-smi inventory row")
        result.append({
            "cuda_ordinal": int(fields[0]),
            "pci_bdf": fields[1].lower().replace("00000000:", "00000000:"),
            "uuid": fields[2],
            "total_vram_bytes": int(fields[3]) * MIB,
            "used_bytes": int(fields[4]) * MIB,
            "free_bytes": int(fields[5]) * MIB,
        })
    return result


def normalize_bdf(value: str) -> str:
    match = re.fullmatch(r"(?:([0-9a-fA-F]{4,8}):)?([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])", value)
    if not match:
        raise ValueError(f"invalid PCI BDF: {value}")
    return f"{int(match.group(1) or '0', 16):08x}:{int(match.group(2), 16):02x}:{int(match.group(3), 16):02x}.{int(match.group(4), 16):x}"


def sample_gpus() -> list[dict[str, object]]:
    try:
        return gpu_inventory()
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return []


def prepare_raw_directory(path: Path) -> None:
    """Create one campaign directory and reject any pre-existing contents."""
    if path.exists():
        if not path.is_dir():
            raise RuntimeError("raw campaign path is not a directory")
        if any(path.iterdir()):
            raise RuntimeError("raw campaign directory must be new or empty")
        return
    path.mkdir(parents=True)


def require_fresh_candidate_paths(output: Path, log: Path) -> None:
    if output.exists() or log.exists():
        raise RuntimeError("candidate output or log already exists")


def build_command(args: argparse.Namespace, candidate: int, output: Path) -> list[str]:
    roles = args.role_template.format(candidate=candidate)
    command = [
        str(args.probe), "--model", str(args.model), "--output", str(output),
        "--mode", "cold", "--expert-runtime-mode", "PRODUCTION_PERFORMANCE",
        "--prompt", args.prompt, "--hot-policy", "LRU", "--cold-policy", "LRU",
        "--scope", "GLOBAL", "--admission", "ALWAYS", "--miss-policy", "PROMOTE_AND_GPU",
        "--hot-slots", "268", "--cold-bytes", str(args.cold_bytes),
        "--ring-bytes", str(args.ring_bytes),
        "--role-config", "EXPLICIT", "--resident-device", str(args.resident_device),
        "--expert-role-devices", roles, "--peer-transport", "HOST_STAGED",
        "--peer-staging-bytes", str(args.peer_staging_bytes),
        "--queue-depth", str(args.queue_depth),
        "--trace-capacity", "0", "--n-ctx", "4096", "--n-batch", "128",
        "--n-ubatch", "128", "--max-generate", str(args.max_generate), "--background", "0",
        "--observe-routes", "0", "--transport", "POSITIONAL",
        "--config-source", "EXPLICIT", "--integrity", "NONE",
    ]
    if args.n_gpu_layers is not None:
        command.extend(("--n-gpu-layers", str(args.n_gpu_layers)))
    if args.io_workers is not None:
        command.extend(("--io-workers", str(args.io_workers)))
    return command


def exact_target_device(evidence: dict, target_uuid: str, candidate: int) -> dict | None:
    matches = [
        device for device in evidence.get("multi_gpu", {}).get("devices", [])
        if device.get("uuid") == target_uuid
    ]
    if len(matches) != 1:
        return None
    device = matches[0]
    if (device.get("hot_requested_slots"), device.get("hot_effective_slots")) != (candidate, candidate):
        return None
    return device


def classify_candidate(
        returncode: int,
        evidence: dict | None,
        log_text: str,
        samples: list[dict[str, object]],
        target_uuid: str,
        candidate: int,
        reserve_bytes: int,
        generated_tokens: int = 24) -> ProbeDecision:
    lower_log = log_text.lower()
    if returncode != 0:
        if any(pattern in lower_log for pattern in MEMORY_REJECTION_PATTERNS):
            return ProbeDecision("reject", "allocation_or_memory_budget")
        return ProbeDecision("abort", f"non_memory_process_failure_{returncode}")
    if (evidence is None or evidence.get("status") != "pass" or
            len(evidence.get("generated_ids", [])) != generated_tokens):
        return ProbeDecision("abort", "incomplete_or_failed_workload")
    if exact_target_device(evidence, target_uuid, candidate) is None:
        return ProbeDecision("abort", "requested_capacity_not_honored_exactly")
    lifecycle = evidence.get("lifecycle", {})
    if any(lifecycle.get(key, 0) != 0 for key in LIFECYCLE_ZERO_KEYS):
        return ProbeDecision("abort", "unclean_lifecycle")
    free_values = [
        int(gpu["free_bytes"])
        for sample in samples for gpu in sample.get("gpus", [])
        if gpu.get("uuid") == target_uuid
    ]
    if not free_values:
        return ProbeDecision("abort", "target_headroom_not_observed")
    if min(free_values) < reserve_bytes:
        return ProbeDecision("reject", "safety_reserve_not_preserved")
    return ProbeDecision("pass", "all_candidate_gates_passed")


def bounded_binary_search(
        lower: int,
        upper: int,
        max_probes: int,
        probe: Callable[[int], ProbeDecision]) -> tuple[int, list[tuple[int, ProbeDecision]]]:
    if lower <= 0 or upper < lower or max_probes <= 0:
        raise ValueError("invalid discovery bounds")
    records: list[tuple[int, ProbeDecision]] = []

    def evaluate(candidate: int) -> ProbeDecision:
        if len(records) >= max_probes:
            raise RuntimeError("capacity discovery exceeded max_probes")
        decision = probe(candidate)
        records.append((candidate, decision))
        if decision.outcome == "abort":
            raise RuntimeError(decision.reason)
        return decision

    baseline = evaluate(lower)
    if baseline.outcome != "pass":
        raise RuntimeError("already-safe lower bound did not pass")
    if upper == lower:
        return lower, records
    high = evaluate(upper)
    if high.outcome == "pass":
        return upper, records
    accepted, rejected = lower, upper
    while accepted + 1 < rejected:
        candidate = accepted + (rejected - accepted) // 2
        decision = evaluate(candidate)
        if decision.outcome == "pass":
            accepted = candidate
        else:
            rejected = candidate
    return accepted, records


def parse_buffer_bytes(log_text: str, label: str) -> dict[int, int]:
    pattern = re.compile(rf"CUDA(\d+) {re.escape(label)} buffer size (?:is|=)\s*([0-9.]+) MiB")
    result: dict[int, int] = {}
    for ordinal, value in pattern.findall(log_text):
        result[int(ordinal)] = max(result.get(int(ordinal), 0), round(float(value) * MIB))
    return result


def device_ledgers(
        inventory: list[dict[str, object]], evidence: dict, samples: list[dict[str, object]],
        log_text: str, reserve_bytes: int, peer_staging_bytes: int) -> list[dict[str, object]]:
    model_bytes = parse_buffer_bytes(log_text, "model")
    graph_bytes = parse_buffer_bytes(log_text, "compute")
    expert_by_uuid = {item.get("uuid"): item for item in evidence.get("multi_gpu", {}).get("devices", [])}
    result: list[dict[str, object]] = []
    for base in inventory:
        uuid = str(base["uuid"])
        observed = [
            gpu for sample in samples for gpu in sample.get("gpus", []) if gpu.get("uuid") == uuid
        ]
        expert = expert_by_uuid.get(uuid, {})
        ordinal = int(base["cuda_ordinal"])
        result.append({
            "cuda_ordinal": ordinal,
            "uuid": uuid,
            "pci_bdf": normalize_bdf(str(base["pci_bdf"])),
            "total_vram_bytes": int(base["total_vram_bytes"]),
            "ordinary_resident_model_bytes": model_bytes.get(ordinal, 0),
            "requested_expert_hot_slots": expert.get("hot_requested_slots", 0),
            "effective_expert_hot_slots": expert.get("hot_effective_slots", 0),
            "expert_hot_cache_bytes": expert.get("hot_pool_bytes", 0),
            "expert_h2d_ring_bytes": expert.get("ring_actual_bytes", 0),
            "peer_device_bytes": 0,
            "pinned_host_staging_bytes_total": peer_staging_bytes,
            "graph_working_reserved_bytes": graph_bytes.get(ordinal, 0),
            "graph_working_peak_bytes": graph_bytes.get(ordinal, 0),
            "configured_safety_reserve_bytes": reserve_bytes,
            "minimum_observed_free_bytes": min((int(item["free_bytes"]) for item in observed), default=None),
            "maximum_observed_used_bytes": max((int(item["used_bytes"]) for item in observed), default=None),
        })
    return result


def validate_capacity_manifest(manifest: dict[str, object]) -> None:
    required_top = {
        "schema_version", "status", "revisions", "artifact", "runtime", "configuration",
        "topology", "bounds", "probe_order", "selected_max_safe_slots",
        "selected_device_ledgers", "raw_directory",
    }
    required_runtime = {
        "nvidia_driver_version", "cuda_driver_api_version", "cuda_runtime_version",
        "cuda_toolkit_version", "cuda_graphs", "expert_runtime_mode",
    }
    required_configuration = {
        "provider_mode", "hot_policy", "cold_policy", "policy_scope", "admission",
        "miss_policy", "cold_cache_bytes", "transfer_ring_bytes", "queue_depth",
        "io_worker_count", "prompt_source", "prompt_sha256",
        "peer_transport", "peer_staging_bytes", "fixture_transport", "integrity",
        "background_promotion", "trace_capacity", "observe_routes", "n_ctx",
        "n_batch", "n_ubatch", "generated_tokens", "sample_period_seconds",
    }
    artifact = manifest.get("artifact", {})
    if (not required_top.issubset(manifest) or
            not required_runtime.issubset(manifest.get("runtime", {})) or
            not required_configuration.issubset(manifest.get("configuration", {})) or
            not isinstance(artifact, dict) or not artifact.get("identity_manifest_sha256") or
            not artifact.get("files")):
        raise RuntimeError("MAX_SAFE manifest is missing reproducibility fields")
    if manifest.get("status") == "pass" and (
            not isinstance(manifest.get("selected_max_safe_slots"), int) or
            not manifest.get("selected_device_ledgers")):
        raise RuntimeError("passing MAX_SAFE manifest lacks selected capacity/device ledgers")


def build_capacity_manifest(
        args: argparse.Namespace, *, status: str, abort_reason: str | None,
        inventory: list[dict[str, object]], artifact: dict[str, object],
        cuda_runtime: dict[str, str], deterministic_upper: int, upper: int,
        ordered: list[tuple[int, ProbeDecision]], selected: int | None,
        selected_evidence: dict | None, selected_samples: list[dict[str, object]],
        selected_log: str) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema_version": "issue65-max-safe-capacity-v2",
        "status": status,
        "abort_reason": abort_reason,
        "revisions": {"project": args.project_revision, "nested": args.nested_revision},
        "artifact": artifact,
        "runtime": {
            **cuda_runtime,
            "cuda_graphs": "DISABLED",
            "expert_runtime_mode": "PRODUCTION_PERFORMANCE",
        },
        "configuration": {
            "provider_mode": "COLD_CACHE",
            "hot_policy": "LRU",
            "cold_policy": "LRU",
            "policy_scope": "GLOBAL",
            "admission": "ALWAYS",
            "miss_policy": "PROMOTE_AND_GPU",
            "cold_cache_bytes": args.cold_bytes,
            "transfer_ring_bytes": args.ring_bytes,
            "queue_depth": args.queue_depth,
            "io_worker_count": args.io_workers,
            "peer_transport": "HOST_STAGED",
            "peer_staging_bytes": args.peer_staging_bytes,
            "fixture_transport": "POSITIONAL",
            "integrity": "NONE",
            "background_promotion": False,
            "trace_capacity": 0,
            "observe_routes": False,
            "config_source": "EXPLICIT",
            "role_config": "EXPLICIT",
            "n_ctx": 4096,
            "n_batch": 128,
            "n_ubatch": 128,
            "n_gpu_layers": args.n_gpu_layers,
            "generated_tokens": args.max_generate,
            "prompt_source": args.prompt_source,
            "prompt_sha256": hashlib.sha256(args.prompt.encode()).hexdigest(),
            "selection": "ARGMAX",
            "sample_period_seconds": args.sample_period,
        },
        "topology": {
            "resident_cuda_ordinal": args.resident_device,
            "expert_role_template": args.role_template,
            "target_cuda_ordinal": args.target_device,
            "target_uuid": args.target_uuid,
            "target_pci_bdf": normalize_bdf(args.target_bdf),
            "peer_transport": "HOST_STAGED",
            "peer_staging_bytes": args.peer_staging_bytes,
        },
        "bounds": {
            "slot_stride_bytes": args.slot_stride,
            "reserve_bytes": args.reserve_bytes,
            "already_safe_lower_slots": args.lower_bound,
            "deterministic_upper_slots": deterministic_upper,
            "searched_upper_slots": upper,
            "max_probes": args.max_probes,
        },
        "probe_order": [
            {"candidate_slots": candidate, "outcome": decision.outcome, "reason": decision.reason}
            for candidate, decision in ordered
        ],
        "selected_max_safe_slots": selected,
        "selected_device_ledgers": device_ledgers(
            inventory, selected_evidence, selected_samples, selected_log,
            args.reserve_bytes, args.peer_staging_bytes) if selected_evidence else [],
        "raw_directory": str(args.raw_dir),
    }
    validate_capacity_manifest(manifest)
    return manifest


def main() -> None:
    args = parse_args()
    if (args.role_template.count("{candidate}") != 1 or args.reserve_bytes <= 0 or
            args.slot_stride <= 0 or args.sample_period <= 0 or not args.prompt or
            args.cold_bytes <= 0 or args.ring_bytes <= 0 or args.queue_depth <= 0 or
            args.max_generate <= 0 or (args.io_workers is not None and args.io_workers <= 0) or
            (args.n_gpu_layers is not None and args.n_gpu_layers < 0)):
        raise SystemExit("invalid discovery configuration")
    if not args.probe.is_file() or not args.model.is_file():
        raise SystemExit("probe or model is missing")
    if not args.artifact_identity_manifest.is_file():
        raise SystemExit("artifact identity manifest is missing")
    artifact = artifact_identity(args)
    cuda_runtime = cuda_runtime_inventory()
    try:
        prepare_raw_directory(args.raw_dir)
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    inventory = gpu_inventory()
    targets = [item for item in inventory if int(item["cuda_ordinal"]) == args.target_device]
    if len(targets) != 1:
        raise SystemExit("target CUDA ordinal is unavailable")
    target = targets[0]
    if target["uuid"] != args.target_uuid or normalize_bdf(str(target["pci_bdf"])) != normalize_bdf(args.target_bdf):
        raise SystemExit("target UUID/BDF does not match the requested physical device")
    deterministic_upper = (int(target["total_vram_bytes"]) - args.reserve_bytes) // args.slot_stride
    upper = args.upper_bound if args.upper_bound is not None else deterministic_upper
    if upper > deterministic_upper:
        raise SystemExit("upper bound exceeds the deterministic VRAM/stride bound")

    probe_records: dict[int, dict[str, object]] = {}

    def run(candidate: int) -> ProbeDecision:
        stem = f"candidate-{candidate:05d}"
        output = args.raw_dir / f"{stem}.json"
        log = args.raw_dir / f"{stem}.log"
        require_fresh_candidate_paths(output, log)
        command = build_command(args, candidate, output)
        environment = os.environ.copy()
        environment.update({"GGML_CUDA_GRAPH_OPT": "0", "GGML_CUDA_DISABLE_GRAPHS": "1"})
        started = time.monotonic()
        samples: list[dict[str, object]] = []
        with log.open("xb") as stream:
            process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, env=environment)
            while process.poll() is None:
                samples.append({"elapsed_seconds": time.monotonic() - started, "gpus": sample_gpus()})
                time.sleep(args.sample_period)
            returncode = process.wait()
        log_text = log.read_text(errors="replace")
        evidence = json.loads(output.read_text()) if output.is_file() else None
        decision = classify_candidate(
            returncode, evidence, log_text, samples, args.target_uuid, candidate,
            args.reserve_bytes, args.max_generate)
        probe_records[candidate] = {
            "candidate_slots": candidate,
            "outcome": decision.outcome,
            "reason": decision.reason,
            "fresh_process": True,
            "returncode": returncode,
            "elapsed_seconds": time.monotonic() - started,
            "command": command,
            "output_path": str(output),
            "log_path": str(log),
            "samples": samples,
            "evidence": evidence,
            "log_text": log_text,
        }
        print(f"candidate {candidate}: {decision.outcome} ({decision.reason})", flush=True)
        return decision

    try:
        selected, ordered = bounded_binary_search(args.lower_bound, upper, args.max_probes, run)
        status = "pass"
        abort_reason = None
    except RuntimeError as error:
        selected = None
        ordered = [(candidate, ProbeDecision(record["outcome"], record["reason"]))
                   for candidate, record in probe_records.items()]
        status = "abort"
        abort_reason = str(error)

    selected_record = probe_records.get(selected) if selected is not None else None
    selected_evidence = selected_record.get("evidence") if selected_record else None
    selected_log = selected_record.get("log_text", "") if selected_record else ""
    selected_samples = selected_record.get("samples", []) if selected_record else []
    manifest = build_capacity_manifest(
        args, status=status, abort_reason=abort_reason, inventory=inventory,
        artifact=artifact, cuda_runtime=cuda_runtime, deterministic_upper=deterministic_upper,
        upper=upper, ordered=ordered, selected=selected,
        selected_evidence=selected_evidence, selected_samples=selected_samples,
        selected_log=selected_log)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n")
    if status != "pass":
        raise SystemExit(f"MAX_SAFE discovery aborted: {abort_reason}")


if __name__ == "__main__":
    main()
