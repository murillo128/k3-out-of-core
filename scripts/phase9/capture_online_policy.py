#!/usr/bin/env python3
"""Capture and independently verify the bounded Phase 9 online matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from cache_policy_simulator import canonical_json, verify_online_capture  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def model_identity(path: Path) -> dict[str, Any]:
    match = re.match(r"^(.*)-00001-of-(\d{5})\.gguf$", path.name)
    if not match:
        return {"path": str(path), "size": path.stat().st_size, "sha256": sha256_file(path)}
    parts = sorted(path.parent.glob(f"{match.group(1)}-*-of-{match.group(2)}.gguf"))
    if len(parts) != int(match.group(2)):
        raise RuntimeError(f"split model is incomplete: {path}")
    entries = [{"name": part.name, "size": part.stat().st_size, "sha256": sha256_file(part)} for part in parts]
    return {
        "path": str(path), "split_count": len(parts), "total_size": sum(entry["size"] for entry in entries),
        "entrypoint": {"size": path.stat().st_size, "sha256": sha256_file(path)},
        "parts_digest": hashlib.sha256(canonical_json(entries).encode()).hexdigest(),
    }


def file_identity(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size": path.stat().st_size, "sha256": sha256_file(path)}


def case_plan(arguments: argparse.Namespace) -> list[dict[str, Any]]:
    result = [
        {
            "name": "tiny-f16-original-cold-lru-cpu-background-off",
            "model": arguments.tiny_f16, "mode": "cold", "hot_policy": "LRU", "cold_policy": "LRU",
            "miss": "CPU_FALLBACK", "background": 0, "hot_slots": 16, "cold_bytes": 67108864,
        },
        {
            "name": "tiny-f16-split-cold-lru-cpu-background-off",
            "model": arguments.tiny_f16_split, "mode": "cold", "hot_policy": "LRU", "cold_policy": "LRU",
            "miss": "CPU_FALLBACK", "background": 0, "hot_slots": 16, "cold_bytes": 67108864,
        },
        {
            "name": "tiny-f16-original-cold-slru-window-cpu-background-on",
            "model": arguments.tiny_f16, "mode": "cold", "hot_policy": "SLRU", "cold_policy": "SLRU",
            "miss": "CPU_FALLBACK", "background": 1, "hot_slots": 16, "cold_bytes": 67108864,
            "admission": "FREQUENCY_WINDOW", "ratio": 7500, "window": 256,
        },
        {
            "name": "tiny-f16-split-cold-slru-window-cpu-background-on",
            "model": arguments.tiny_f16_split, "mode": "cold", "hot_policy": "SLRU", "cold_policy": "SLRU",
            "miss": "CPU_FALLBACK", "background": 1, "hot_slots": 16, "cold_bytes": 67108864,
            "admission": "FREQUENCY_WINDOW", "ratio": 7500, "window": 256,
        },
        {
            "name": "tiny-mxfp4-original-hot-lfru-promote",
            "model": arguments.tiny_mxfp4, "mode": "hot", "hot_policy": "LFRU", "cold_policy": "LRU",
            "miss": "PROMOTE_AND_GPU", "background": 0, "hot_slots": 16, "cold_bytes": 67108864,
        },
        {
            "name": "tiny-mxfp4-split-hot-lfru-promote",
            "model": arguments.tiny_mxfp4_split, "mode": "hot", "hot_policy": "LFRU", "cold_policy": "LRU",
            "miss": "PROMOTE_AND_GPU", "background": 0, "hot_slots": 16, "cold_bytes": 67108864,
        },
        {
            "name": "tiny-mxfp4-original-cold-lfu-auto-background-on",
            "model": arguments.tiny_mxfp4, "mode": "cold", "hot_policy": "LFU_AGING", "cold_policy": "LFU_AGING",
            "miss": "AUTO", "background": 1, "hot_slots": 16, "cold_bytes": 67108864, "aging": 256,
        },
        {
            "name": "tiny-mxfp4-split-cold-lfu-auto-background-on",
            "model": arguments.tiny_mxfp4_split, "mode": "cold", "hot_policy": "LFU_AGING", "cold_policy": "LFU_AGING",
            "miss": "AUTO", "background": 1, "hot_slots": 16, "cold_bytes": 67108864, "aging": 256,
        },
    ]
    if arguments.qwen_f16:
        result.append({
            "name": "qwen15-moe-f16-cold-lru-cpu-background-off",
            "model": arguments.qwen_f16, "mode": "cold", "hot_policy": "LRU", "cold_policy": "LRU",
            "miss": "CPU_FALLBACK", "background": 0, "hot_slots": 4, "cold_bytes": 1073741824,
            "ring_bytes": 134217728, "n_ubatch": 1, "max_generate": 1, "observe_routes": 0,
        })
    return result


def run_case(case: dict[str, Any], arguments: argparse.Namespace) -> dict[str, Any]:
    capture_path = arguments.output_dir / f"{case['name']}.capture.json"
    native_path = arguments.output_dir / f"{case['name']}.native-verification.json"
    command = [
        str(arguments.probe), "--model", str(case["model"]), "--output", str(capture_path),
        "--mode", case["mode"], "--hot-policy", case["hot_policy"], "--cold-policy", case["cold_policy"],
        "--scope", case.get("scope", "GLOBAL"), "--admission", case.get("admission", "ALWAYS"),
        "--miss-policy", case["miss"], "--hot-slots", str(case["hot_slots"]),
        "--cold-bytes", str(case["cold_bytes"]), "--ring-bytes", str(case.get("ring_bytes", 16777216)),
        "--ratio", str(case.get("ratio", 7500)), "--window", str(case.get("window", 1024)),
        "--aging", str(case.get("aging", 1024)), "--n-ubatch", str(case.get("n_ubatch", 64)),
        "--max-generate", str(case.get("max_generate", arguments.max_generate)),
        "--background", str(case["background"]),
        "--observe-routes", str(case.get("observe_routes", 1)),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"online probe failed for {case['name']}: {completed.stderr[-4000:]}")
    capture = json.loads(capture_path.read_text())
    if capture["mechanism"]["active_background_flights"] != 0:
        raise RuntimeError(f"{case['name']}: capture retained active background flights")
    python_verification = verify_online_capture(capture)
    native_command = [str(arguments.native_replay), "--capture-input", str(capture_path), "--output", str(native_path)]
    native = subprocess.run(native_command, capture_output=True, text=True, check=False)
    if native.returncode != 0:
        raise RuntimeError(f"native replay failed for {case['name']}: {native.stderr[-4000:]}")
    native_verification = json.loads(native_path.read_text())
    for tier in ("hot", "cold"):
        native_tier = native_verification[tier]
        python_tier = python_verification[tier]
        compared_keys = ("status", "tier", "events", "inactive") if python_tier.get("inactive") else (
            "status", "tier", "events", "config_digest", "final_digest")
        if any(python_tier.get(key) != native_tier.get(key) for key in compared_keys):
            raise RuntimeError(f"native/Python verification mismatch for {case['name']} {tier}")
    if python_verification["output_identity"] != native_verification["output_identity"]:
        raise RuntimeError(f"native/Python output identity mismatch for {case['name']}")
    return {
        "name": case["name"], "status": "pass", "command": command,
        "command_exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "model": model_identity(Path(case["model"])),
        "capture": {"path": str(capture_path), "size": capture_path.stat().st_size, "sha256": sha256_file(capture_path)},
        "native_verification": {"path": str(native_path), "size": native_path.stat().st_size, "sha256": sha256_file(native_path)},
        "python_verification": python_verification,
        "routes": ({
            "records": len(capture["routes"]),
            "sha256": hashlib.sha256(canonical_json(capture["routes"]).encode()).hexdigest(),
            "supported": True,
        } if capture["routes"] else {
            "records": 0,
            "sha256": hashlib.sha256(canonical_json([]).encode()).hexdigest(),
            "supported": False,
            "unavailable_reason": "accepted model exposes a zero-extent route-observer output; topology is bound from policy events",
        }),
        "event_counts": {"hot": len(capture["hot"]["events"]), "cold": len(capture["cold"]["events"])},
        "mechanism": capture["mechanism"], "capacities": capture["capacities"],
        "latency_us": capture["latency_us"], "peak_rss_kib": capture["peak_rss_kib"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--native-replay", type=Path, required=True)
    parser.add_argument("--tiny-f16", type=Path, required=True)
    parser.add_argument("--tiny-f16-split", type=Path, required=True)
    parser.add_argument("--tiny-mxfp4", type=Path, required=True)
    parser.add_argument("--tiny-mxfp4-split", type=Path, required=True)
    parser.add_argument("--qwen-f16", type=Path)
    parser.add_argument("--phase8-manifest", type=Path, required=True)
    parser.add_argument("--max-generate", type=int, default=8)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    cases = [run_case(case, arguments) for case in case_plan(arguments)]
    phase8 = json.loads(arguments.phase8_manifest.read_text())
    accepted = {entry["name"]: entry["model"] for entry in phase8["inputs"]["k3_models"]}
    accepted["larger_public_moe_f16"] = phase8["inputs"]["larger_public_moe"]["gguf"]
    names = {
        "tiny-f16-original-cold-lru-cpu-background-off": "k3_f16_original",
        "tiny-f16-split-cold-lru-cpu-background-off": "k3_f16_split",
        "tiny-f16-original-cold-slru-window-cpu-background-on": "k3_f16_original",
        "tiny-f16-split-cold-slru-window-cpu-background-on": "k3_f16_split",
        "tiny-mxfp4-original-hot-lfru-promote": "k3_mxfp4_original",
        "tiny-mxfp4-split-hot-lfru-promote": "k3_mxfp4_split",
        "tiny-mxfp4-original-cold-lfu-auto-background-on": "k3_mxfp4_original",
        "tiny-mxfp4-split-cold-lfu-auto-background-on": "k3_mxfp4_split",
        "qwen15-moe-f16-cold-lru-cpu-background-off": "larger_public_moe_f16",
    }
    for case in cases:
        observed = case["model"].get("entrypoint", case["model"])
        expected = accepted[names[case["name"]]]
        if observed["size"] != expected["size"] or observed["sha256"] != expected["sha256"]:
            raise RuntimeError(f"{case['name']}: model identity differs from accepted Phase 8 lineage")
    by_name = {case["name"]: case for case in cases}
    def pair_check(left: str, right: str) -> dict[str, Any]:
        lhs, rhs = by_name[left], by_name[right]
        return {
            "output_identity_exact": lhs["python_verification"]["output_identity"] ==
                rhs["python_verification"]["output_identity"],
            "routes_exact": lhs["routes"]["sha256"] == rhs["routes"]["sha256"],
        }
    checks = {
        "f16_lru_background_off_original_split": pair_check(
            "tiny-f16-original-cold-lru-cpu-background-off",
            "tiny-f16-split-cold-lru-cpu-background-off"),
        "f16_slru_background_on_original_split": pair_check(
            "tiny-f16-original-cold-slru-window-cpu-background-on",
            "tiny-f16-split-cold-slru-window-cpu-background-on"),
        "mxfp4_lfru_hot_original_split": pair_check(
            "tiny-mxfp4-original-hot-lfru-promote",
            "tiny-mxfp4-split-hot-lfru-promote"),
        "mxfp4_lfu_background_on_original_split": pair_check(
            "tiny-mxfp4-original-cold-lfu-auto-background-on",
            "tiny-mxfp4-split-cold-lfu-auto-background-on"),
        "accepted_phase8_model_identities": True,
        "all_requests_terminal": all(case["mechanism"]["active_background_flights"] == 0 for case in cases),
    }
    if not all(value is True for value in checks.values() if not isinstance(value, dict)) or not all(
            value for pair in checks.values() if isinstance(pair, dict) for value in pair.values()):
        raise RuntimeError(f"online parity check failed: {checks}")
    output = {
        "schema_version": "phase9-online-policy-matrix-v1", "status": "pass",
        "rules": {
            "dropped_transcript_is_failure": True, "native_python_event_equality": "exact",
            "timing_excluded_from_decisions": True, "fresh_process_per_case": True,
        },
        "phase8_manifest": {
            "path": str(arguments.phase8_manifest), "size": arguments.phase8_manifest.stat().st_size,
            "sha256": sha256_file(arguments.phase8_manifest),
        },
        "tools": {
            "probe": file_identity(arguments.probe),
            "probe_libllama": file_identity(arguments.probe.parent / "libllama.so"),
            "native_replay": file_identity(arguments.native_replay),
            "native_replay_libllama": file_identity(arguments.native_replay.parent / "libllama.so"),
            "capture_script": file_identity(Path(__file__)),
        },
        "checks": checks, "cases": cases,
    }
    arguments.summary.parent.mkdir(parents=True, exist_ok=True)
    arguments.summary.write_text(canonical_json(output))
    print(canonical_json({
        "status": "pass", "cases": len(cases), "summary": str(arguments.summary),
        "sha256": sha256_file(arguments.summary),
    }), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
