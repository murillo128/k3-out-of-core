#!/usr/bin/env python3
"""Verify that the committed Phase 1 closeout evidence is self-consistent."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results/2026-07-29/skynet/phase1-closeout-clean"
MANIFEST = ROOT / "manifests/kimi-k3-0.40b-phase1.json"

EXPECTED = {
    "base": "511e87fc98cca8069fc57526fbb04b10789967eb",
    "branch": "codex/phase1-closeout-clean",
    "llama": "84245db4c790af22135f34992689edcc11877003",
    "f16_revision": "d853649387ffe8f48ce0198a29ac1a44205031f7",
    "mxfp4_revision": "ef3902c318fb8e13c3507e26055656e687fdfe38",
    "published_revision": "88de02cf8fa37f87eb06daaed370ac9c3411d5ca",
    "f16_sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
    "mxfp4_sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
}

EVIDENCE_FILES = (
    "environment.json",
    "inputs.json",
    "test-matrix.json",
    "fixture-classification.json",
    "tokenizer.json",
    "mxfp4-validation.json",
    "inference.json",
    "inference-f16-cpu.log",
    "inference-f16-cuda.log",
    "inference-mxfp4-cpu.log",
    "inference-mxfp4-cuda.log",
    "benchmarks.json",
    "benchmark-f16-cpu.log",
    "benchmark-f16-cuda.log",
    "benchmark-mxfp4-cpu.log",
    "benchmark-mxfp4-cuda.log",
    "SUMMARY.md",
    "checkpoints.json",
)


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(results: Path) -> None:
    missing = [name for name in EVIDENCE_FILES if not (results / name).is_file()]
    if missing:
        raise ValueError("cannot write checksums; missing: " + ", ".join(missing))
    lines = [f"{sha256(results / name)}  {name}\n" for name in EVIDENCE_FILES]
    (results / "evidence.sha256").write_text("".join(lines), encoding="utf-8")


def verify_checksums(results: Path, errors: list[str]) -> None:
    checksum_path = results / "evidence.sha256"
    if not checksum_path.is_file():
        errors.append("missing evidence.sha256")
        return
    seen: set[str] = set()
    for line_number, raw in enumerate(checksum_path.read_text(encoding="utf-8").splitlines(), 1):
        parts = raw.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            errors.append(f"evidence.sha256:{line_number}: malformed entry")
            continue
        expected, name = parts
        if name not in EVIDENCE_FILES:
            errors.append(f"evidence.sha256:{line_number}: unexpected path {name!r}")
            continue
        if name in seen:
            errors.append(f"evidence.sha256:{line_number}: duplicate path {name!r}")
            continue
        seen.add(name)
        path = results / name
        if not path.is_file():
            errors.append(f"missing checksummed artifact: {name}")
        elif sha256(path) != expected:
            errors.append(f"checksum mismatch: {name}")
    for name in EVIDENCE_FILES:
        if name not in seen:
            errors.append(f"artifact absent from evidence.sha256: {name}")


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def verify_evidence(results: Path, allow_pending_c: bool, errors: list[str]) -> None:
    for name in EVIDENCE_FILES:
        require((results / name).is_file(), f"missing artifact: {name}", errors)
    if errors:
        return

    environment = load_json(results / "environment.json")
    inputs = load_json(results / "inputs.json")
    tests = load_json(results / "test-matrix.json")
    fixture = load_json(results / "fixture-classification.json")
    tokenizer = load_json(results / "tokenizer.json")
    mxfp4 = load_json(results / "mxfp4-validation.json")
    inference = load_json(results / "inference.json")
    benchmarks = load_json(results / "benchmarks.json")
    checkpoints = load_json(results / "checkpoints.json")

    require(environment.get("validation", {}).get("status") == "pass", "environment validation did not pass", errors)
    require(environment.get("host", {}).get("hostname") == "skynet", "unexpected evidence host", errors)
    gpu = environment.get("host", {}).get("gpu", {}).get("devices", [{}])[0]
    require(gpu.get("name") == "NVIDIA GeForce GTX 1650", "unexpected GPU", errors)

    contract = inputs.get("approved_contract", {})
    require(contract.get("execution_base") == EXPECTED["base"], "wrong execution base", errors)
    require(contract.get("execution_branch") == EXPECTED["branch"], "wrong execution branch", errors)
    require(contract.get("execution_profile") == "STANDARD", "wrong execution profile", errors)
    require(contract.get("llama_cpp_commit") == EXPECTED["llama"], "wrong llama.cpp revision", errors)
    require(inputs.get("llama_cpp", {}).get("clean") is True, "llama.cpp checkout was not clean", errors)
    require(inputs.get("validation", {}).get("status") == "pass", "input validation did not pass", errors)
    sources = inputs.get("source_models", {})
    require(sources.get("f16_reference", {}).get("revision") == EXPECTED["f16_revision"], "wrong F16 source revision", errors)
    require(sources.get("mxfp4_source", {}).get("revision") == EXPECTED["mxfp4_revision"], "wrong MXFP4 source revision", errors)
    artifacts = inputs.get("published_gguf_artifacts", {})
    require(artifacts.get("Kimi-K3-0.40B-F16.gguf", {}).get("sha256") == EXPECTED["f16_sha256"], "wrong F16 GGUF checksum", errors)
    require(artifacts.get("Kimi-K3-0.40B-MXFP4.gguf", {}).get("sha256") == EXPECTED["mxfp4_sha256"], "wrong MXFP4 GGUF checksum", errors)

    require(tests.get("validation", {}).get("status") == "pass", "stable test matrix did not pass", errors)
    require(fixture.get("validation", {}).get("status") == "pass", "external fixture validation did not pass", errors)
    require(tokenizer.get("validation", {}).get("status") == "pass", "tokenizer validation did not pass", errors)
    require(tokenizer.get("cross_model", {}).get("hf_and_gguf_prompt_ids_equal") is True, "tokenizer prompt IDs differ", errors)
    require(mxfp4.get("status") == "pass", "MXFP4 validation did not pass", errors)
    summary = mxfp4.get("summary", {})
    require(summary.get("sample_count") == 81, "MXFP4 sample count is not 81", errors)
    require(summary.get("maximum_absolute_error") == 0.0, "MXFP4 sampled values differ", errors)
    require(inference.get("status") == "pass", "inference validation did not pass", errors)
    hard = inference.get("hard_failures", {})
    require(all((hard.get("all_four_runs_exit_zero"), hard.get("all_full_vocabulary_logits_finite"), hard.get("no_log_hard_failures"))), "inference hard-failure gate failed", errors)
    require(not hard.get("changed_source_hashes"), "source model hashes changed", errors)
    require(not hard.get("invalid_expert_ids_observed"), "invalid expert ID observed", errors)
    require(not hard.get("unstable_same_artifact_generation"), "CPU/CUDA generation differs", errors)
    for model in ("f16", "mxfp4"):
        comparison = inference.get("same_artifact_cpu_cuda", {}).get(model, {})
        require(comparison.get("status") == "pass", f"{model} CPU/CUDA comparison failed", errors)
        require(comparison.get("generated_ids_exact") is True, f"{model} generated IDs differ", errors)
        require(comparison.get("top_10_id_sets_exact_each_step") is True, f"{model} selected top-10 ID sets differ", errors)
    require(benchmarks.get("status") == "pass", "benchmark validation did not pass", errors)
    bench_summary = benchmarks.get("summary", {})
    require(bench_summary.get("combination_count") == 4, "benchmark matrix is incomplete", errors)
    require(bench_summary.get("total_measured_runs") == 20, "benchmark measured-run count is not 20", errors)
    require(bench_summary.get("all_contract_checks_pass") is True, "benchmark contract checks failed", errors)
    require(bench_summary.get("all_hard_failure_scans_pass") is True, "benchmark hard-failure scan failed", errors)
    require(bench_summary.get("all_token_stability_checks_pass") is True, "benchmark token stability failed", errors)

    accepted = {"PASS", "PASS_WITH_NOTES"}
    for name in ("A", "B"):
        require(checkpoints.get(name, {}).get("verdict") in accepted, f"Checkpoint {name} is not accepted", errors)
    c_verdict = checkpoints.get("C", {}).get("verdict")
    if allow_pending_c:
        require(c_verdict in accepted | {"PENDING"}, "Checkpoint C has invalid state", errors)
    else:
        require(c_verdict in accepted, "Checkpoint C is incomplete", errors)


def verify_source_of_truth(root: Path, allow_pending_c: bool, errors: list[str]) -> None:
    manifest = load_json(MANIFEST)
    expected_status = {"phase1-validated", "checkpoint-c-pending"} if allow_pending_c else {"phase1-validated"}
    require(manifest.get("baseline", {}).get("status") in expected_status, "manifest Phase 1 status is stale", errors)
    require(manifest.get("repositories", {}).get("project", {}).get("execution_base") == EXPECTED["base"], "manifest execution base is stale", errors)
    require(manifest.get("source_models", {}).get("f16_reference", {}).get("revision") == EXPECTED["f16_revision"], "manifest F16 revision is stale", errors)
    require(manifest.get("source_models", {}).get("mxfp4_source", {}).get("revision") == EXPECTED["mxfp4_revision"], "manifest MXFP4 revision is stale", errors)
    require(manifest.get("pending_validation") == [], "manifest retains Phase 1 pending validation", errors)
    required_text = {
        "docs/STATUS.md": ("Phase 1 technical evidence is complete", "NVIDIA GeForce GTX 1650"),
        "docs/plan/00-foundation.md": ("Phase 1 technical exit gate: **ACCEPTED**", "phase1-closeout-clean"),
        "docs/MODELS_AND_VALIDATION.md": ("11th Gen Intel(R) Core(TM) i7-11700K", "NVIDIA GeForce GTX 1650"),
        "docs/REPOSITORIES_AND_ARTIFACTS.md": ("Phase 1 validated monolithic baseline", "phase1-closeout-clean"),
    }
    for relative, markers in required_text.items():
        text = (root / relative).read_text(encoding="utf-8")
        for marker in markers:
            require(marker in text, f"{relative} missing closeout marker: {marker}", errors)
    status_text = (root / "docs/STATUS.md").read_text(encoding="utf-8")
    if not allow_pending_c:
        require("Checkpoint C: **PASS" in status_text, "STATUS does not record accepted Checkpoint C", errors)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-pending-checkpoint-c", action="store_true")
    parser.add_argument("--write-checksums", action="store_true")
    parser.add_argument("--output", type=Path, default=RESULTS / "closeout-verification.json")
    args = parser.parse_args()
    if args.write_checksums:
        write_checksums(RESULTS)
    errors: list[str] = []
    try:
        verify_evidence(RESULTS, args.allow_pending_checkpoint_c, errors)
        verify_checksums(RESULTS, errors)
        verify_source_of_truth(ROOT, args.allow_pending_checkpoint_c, errors)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    report = {
        "schema_version": 1,
        "status": "pass" if not errors else "fail",
        "mode": "pre-checkpoint-c" if args.allow_pending_checkpoint_c else "strict",
        "errors": errors,
        "evidence_directory": str(RESULTS.relative_to(ROOT)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
