#!/usr/bin/env python3
"""Apply the frozen Phase 10 gates without selecting a nonqualifying profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from prefetch_common import Phase10Error, require_capture_heads, validate_profile


ROOT = Path(__file__).resolve().parents[2]


def load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise Phase10Error(f"{path} is not a JSON object")
    return value


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False,
        separators=(",", ": ")) + "\n").encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def evidence_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path.resolve())


def file_record(path: Path) -> dict[str, Any]:
    return {"path": evidence_path(path), "size": path.stat().st_size,
        "sha256": sha256_file(path)}


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def verify_evidence_heads(project_head: str, nested_head: str) -> None:
    for repo, value, name in ((ROOT, project_head, "project"),
            (ROOT / "llama.cpp", nested_head, "nested")):
        completed = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify",
            f"{value}^{{commit}}"], check=False, capture_output=True, text=True)
        if completed.returncode != 0 or completed.stdout.strip() != value:
            raise Phase10Error(f"{name} evidence head does not resolve")
    gitlink = subprocess.run(["git", "-C", str(ROOT), "ls-tree", project_head, "--", "llama.cpp"],
        check=True, capture_output=True, text=True).stdout.split()
    if len(gitlink) < 3 or gitlink[2] != nested_head:
        raise Phase10Error("performance evidence project/nested gitlink mismatch")


def wilson_lower_bps(successes: int, trials: int) -> int:
    if trials <= 0 or successes <= 0:
        return 0
    z = 1.96
    probability = successes/trials
    denominator = 1 + z*z/trials
    center = probability + z*z/(2*trials)
    margin = z*math.sqrt(probability*(1 - probability)/trials + z*z/(4*trials*trials))
    return max(0, math.floor(10000*(center - margin)/denominator))


def artifact_kind(profile: dict[str, Any]) -> str:
    names = [item["name"] for item in profile["source"]["artifacts"]]
    if any(name.startswith("mxfp4-") for name in names):
        return "mxfp4"
    return "f16"


def matching_offline(profile: dict[str, Any], offline: dict[str, Any]) -> dict[str, Any] | None:
    selection = profile["selection"]
    artifact = artifact_kind(profile)
    matches = [item for item in offline["shortlist"] if item["artifact"] == artifact and
        item["fold"] == selection["fold_index"] and item["policy"] == selection["policy"] and
        item["transport"] == selection["transport"] and item["readiness"] == selection["readiness"] and
        item["candidates_per_target"] == selection["candidates_per_target"] and
        item["temporal_window_tokens"] == selection["temporal_window_tokens"]]
    return matches[0] if len(matches) == 1 else None


def predictive_envelopes(online: dict[str, Any], offline: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    by_profile: dict[str, dict[str, Any]] = {}
    disabled: set[str] = set()
    for case in online["cases"]:
        profile_path = ROOT / case["profile"]["path"] if not Path(case["profile"]["path"]).is_absolute() else Path(case["profile"]["path"])
        profile = load(profile_path)
        validate_profile(profile)
        profile_sha = sha256_file(profile_path)
        if profile_sha != case["profile"]["sha256"]:
            raise Phase10Error("online matrix profile hash mismatch")
        disabled.add(profile_sha)
        entry = by_profile.setdefault(profile_sha, {"profile": file_record(profile_path),
            "profile_id": profile["profile_id"], "policy": profile["selection"]["policy"],
            "transport": profile["selection"]["transport"],
            "readiness": profile["selection"]["readiness"],
            "break_even_bps": profile["selection"]["break_even_bps"], "cases": [],
            "held_out": None, "qualifies": False, "disposition": ""})
        summary = case["active"]["summary"]
        entry["cases"].append({"name": case["name"], "predictions": summary["prediction_events"],
            "admitted": summary["admitted"], "timely_useful": summary["timely_useful"],
            "wasted_unused": summary["wasted_unused"]})
        matched = matching_offline(profile, offline)
        if matched is not None:
            held = matched["held_out_test"]
            entry["held_out"] = {"predictions": held["predictions"],
                "timely_successes": held["timely_successes"],
                "precision_bps": held["precision_bps"],
                "precision_lcb_bps": wilson_lower_bps(held["timely_successes"], held["predictions"]),
                "offline_disposition": matched["disposition"]}
    for entry in by_profile.values():
        if entry["policy"] == "RANDOM_BASELINE":
            entry["disposition"] = "disabled-random-control-never-selectable"
        elif entry["held_out"] is None:
            entry["disposition"] = "disabled-no-exact-held-out-selection-evidence"
        elif entry["held_out"]["precision_lcb_bps"] <= entry["break_even_bps"]:
            entry["disposition"] = "disabled-held-out-precision-lcb-not-above-break-even"
        else:
            entry["disposition"] = "disabled-no-qualifying-endpoint-evidence"
    return sorted(by_profile.values(), key=lambda item: (item["policy"], item["profile"]["sha256"])), disabled


def seed_envelope(path: Path) -> tuple[dict[str, Any], str]:
    evidence = load(path)
    verify_evidence_heads(evidence["project_head"], evidence["nested_head"])
    profile_path = ROOT / evidence["profile"]["path"] if not Path(evidence["profile"]["path"]).is_absolute() else Path(evidence["profile"]["path"])
    profile = load(profile_path)
    validate_profile(profile)
    if sha256_file(profile_path) != evidence["profile"]["sha256"]:
        raise Phase10Error("seed evidence profile hash mismatch")
    if evidence["qualifies"] or evidence["status"] != "fail":
        raise Phase10Error("negative selection expected a rejected seed envelope")
    failed = sorted(name for name, passed in evidence["gates"].items() if not passed)
    return {"evidence": file_record(path), "profile": file_record(profile_path),
        "profile_id": profile["profile_id"], "transport": profile["selection"]["transport"],
        "readiness": profile["selection"]["readiness"], "runtime": evidence["runtime"],
        "failed_gates": failed, "qualifies": False,
        "disposition": "disabled-fixed-performance-gates-failed"}, sha256_file(profile_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--offline-replay", type=Path, required=True)
    parser.add_argument("--online-matrix", type=Path, required=True)
    parser.add_argument("--buffered-seed", type=Path, required=True)
    parser.add_argument("--h2d-seed", type=Path, required=True)
    parser.add_argument("--transport-break-even", type=Path, required=True)
    parser.add_argument("--waste-comparison", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        require_capture_heads(args.project_head, args.nested_head)
        offline = load(args.offline_replay)
        online = load(args.online_matrix)
        if offline.get("status", "pass") != "pass":
            raise Phase10Error("offline replay is not accepted evidence")
        if online["status"] != "pass":
            raise Phase10Error("online matrix is not accepted evidence")
        verify_evidence_heads(offline["project_head"], offline["nested_head"])
        verify_evidence_heads(online["project_head"], online["nested_head"])
        seeds = []
        disabled: set[str] = set()
        for path in (args.buffered_seed, args.h2d_seed):
            envelope, profile_sha = seed_envelope(path)
            seeds.append(envelope)
            disabled.add(profile_sha)
        predictive, predictive_disabled = predictive_envelopes(online, offline)
        disabled.update(predictive_disabled)
        output = {"schema_version": "phase10-selection-v1", "status": "investigation-required",
            "project_head": args.project_head, "nested_head": args.nested_head,
            "inputs": {"offline_replay": file_record(args.offline_replay),
                "online_matrix": file_record(args.online_matrix),
                "transport_break_even": file_record(args.transport_break_even),
                "waste_comparison": file_record(args.waste_comparison)},
            "rules": {"frozen_before_online_results": True,
                "performance": "two-sided 95% Student-t paired ABBA block means",
                "held_out_precision_lcb": "two-sided 95% Wilson lower bound",
                "precision_must_exceed_exact_break_even": True,
                "waste_external_threshold_transferred": False,
                "random_selectable": False, "exact_envelope_only": True,
                "universal_claim": False},
            "seed_envelopes": seeds, "predictive_envelopes": predictive,
            "qualifying_profiles": [], "selected_profiles": [],
            "disabled_profiles": sorted(disabled),
            "defaults": {"phase9_unchanged": True, "prefetch_config": None,
                "prefetch_profile_path": None, "implicit_enablement": False},
            "circuit_breaker": {"profile": "STANDARD", "triggered": True,
                "consecutive_failures": 2, "mechanism": "blocking-hot-seed",
                "action": "stop-tuning-and-return-to-investigation-required"},
            "technical_closeout_state": "investigation-required"}
        schema = load(ROOT / "schemas/phase10/selection-v1.schema.json")
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(output)
        write(args.output, output)
        print(json.dumps({"status": output["status"], "selected": 0,
            "disabled": len(disabled)}, sort_keys=True))
        return 0
    except (OSError, ValueError, Phase10Error, ValidationError, subprocess.SubprocessError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
