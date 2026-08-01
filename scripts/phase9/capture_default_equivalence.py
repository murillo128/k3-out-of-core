#!/usr/bin/env python3
"""Prove retained null defaults equal explicit global LRU in repeated warm runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from evidence_common import canonical_json, file_identity  # noqa: E402


def identity(capture: dict[str, Any]) -> str:
    value = {"prompt_ids": capture["prompt_ids"], "generated_ids": capture["generated_ids"],
             "logits_fnv64": capture["logits_fnv64"], "routes": capture["routes"]}
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def run(probe: Path, directory: Path, case: dict[str, Any], source: str,
        repetition: int) -> dict[str, Any]:
    output = directory / f"{case['name']}-{case['mode'].lower()}-{source.lower()}-r{repetition}.json"
    command = [str(probe), "--model", case["model"], "--output", str(output),
        "--mode", case["mode"].lower(), "--hot-policy", "LRU", "--cold-policy", "LRU",
        "--scope", "GLOBAL", "--admission", "ALWAYS", "--miss-policy", "PROMOTE_AND_GPU",
        "--hot-slots", str(case["hot_slots"]), "--cold-bytes", str(case["cold_bytes"]),
        "--ring-bytes", str(case["ring_bytes"]), "--ratio", "7500", "--window", "1024",
        "--aging", "1024", "--n-ubatch", str(case["n_ubatch"]), "--max-generate", "20",
        "--background", "0", "--observe-routes", str(case["observe_routes"]),
        "--transport", "BUFFERED", "--config-source", source]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"default-equivalence run failed: {completed.stderr[-4000:]}")
    capture = json.loads(output.read_text())
    return {"case": case["name"], "mode": case["mode"], "config_source": source,
            "repetition": repetition, "command": command, "artifact": file_identity(output),
            "output_identity": identity(capture), "generated_tokens": len(capture["generated_ids"]),
            "hot_config": capture["hot"]["config"], "cold_config": capture["cold"]["config"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--phase8-manifest", type=Path, required=True)
    parser.add_argument("--working-sets", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    phase8 = json.loads(args.phase8_manifest.read_text())
    working = json.loads(args.working_sets.read_text())
    selection = json.loads(args.selection.read_text())
    if selection["selected"] != {"hot": "LRU-GLOBAL", "cold": "LRU-GLOBAL"}:
        raise RuntimeError("this retention capture requires Checkpoint C global LRU selection")
    models = {entry["name"]: entry["model"]["path"] for entry in phase8["inputs"]["k3_models"]}
    by_name = {entry["name"]: entry for entry in working["cases"]}
    formats = (("f16", "tiny-f16-original-cold-lru-cpu-background-off"),
               ("mxfp4", "tiny-mxfp4-original-cold-lfu-auto-background-on"))
    cases = []
    for format_name, working_name in formats:
        ws = by_name[working_name]
        for representation in ("original", "split"):
            for mode in ("HOT", "COLD"):
                cases.append({"name": f"tiny-{format_name}-{representation}", "mode": mode,
                    "model": models[f"k3_{format_name}_{representation}"], "hot_slots": 16,
                    "cold_bytes": ws["observed_capacities"]["cold_actual_bytes"],
                    "ring_bytes": 16777216, "n_ubatch": 64, "observe_routes": 1})
    qwen = by_name["qwen15-moe-f16-cold-lru-cpu-background-off"]
    cases.append({"name": "qwen15-moe-f16", "mode": "COLD",
        "model": phase8["inputs"]["larger_public_moe"]["gguf"]["path"], "hot_slots": 4,
        "cold_bytes": qwen["token_working_set_bytes"]["decode"]["max"],
        "ring_bytes": 134217728, "n_ubatch": 1, "observe_routes": 0})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    runs = [run(args.probe, args.output_dir, case, source, repetition)
            for case in cases for source in ("EXPLICIT", "NULL") for repetition in range(2)]
    groups = []
    for case in cases:
        rows = [row for row in runs if row["case"] == case["name"] and row["mode"] == case["mode"]]
        identities = {row["output_identity"] for row in rows}
        hot_configs = {canonical_json(row["hot_config"]) for row in rows}
        cold_configs = {canonical_json(row["cold_config"]) for row in rows} if case["mode"] == "COLD" else set()
        if len(rows) != 4 or len(identities) != 1 or len(hot_configs) != 1 or len(cold_configs) > 1:
            raise RuntimeError(f"null/default equivalence failed for {case['name']} {case['mode']}")
        if any(row["generated_tokens"] != 20 for row in rows):
            raise RuntimeError(f"20-step warm run ended early for {case['name']} {case['mode']}")
        resolved = rows[0]["hot_config"]
        if resolved["policy"] != "LRU" or resolved["scope"] != "GLOBAL" or resolved["admission"] != "ALWAYS":
            raise RuntimeError("retained null default did not resolve to global LRU/ALWAYS")
        groups.append({"case": case["name"], "mode": case["mode"], "runs": 4,
                       "output_identity": next(iter(identities)), "resolved_hot_config": resolved,
                       "resolved_cold_config": rows[0]["cold_config"] if case["mode"] == "COLD" else None})
    result = {"schema_version": "phase9-default-equivalence-v1", "status": "pass",
              "inputs": {"probe": file_identity(args.probe), "phase8_manifest": file_identity(args.phase8_manifest),
                         "working_sets": file_identity(args.working_sets), "selection": file_identity(args.selection)},
              "selected_default": selection["selected"], "semantic_default_switch": False,
              "runs": runs, "groups": groups,
              "limits": ["single-request discrete-GPU envelope", "Qwen route observer unavailable",
                         "performance statistics remain authoritative in policy-statistics.json"]}
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(canonical_json(result))
    print(canonical_json({"status": "pass", "summary": str(args.summary), "runs": len(runs)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
