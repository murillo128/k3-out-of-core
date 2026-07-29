#!/usr/bin/env python3
"""Strict deterministic verifier for the issue #10 Phase 2 manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


EXPECTED = {
    "project_base": "c0ef5d08c6efb8d1f7a08a62109feb1a488c72fa",
    "llama_base": "84245db4c790af22135f34992689edcc11877003",
    "llama_head": "4daaaa1a4dd26d6465f84891b854b5f7ddc03020",
    "gguf_revision": "88de02cf8fa37f87eb06daaed370ac9c3411d5ca",
    "corpus_revision": "2d838d6b4d0aca4e9af1e7d899e57ad29330c72e",
    "archive_sha256": "6aa924a6c18bee4e2490f317ced836bcc4740c3ec63e9427a95951e79a649a5f",
}
ALLOWED_NESTED_PATHS = {
    "include/llama.h",
    "src/llama-context.cpp",
    "src/llama-context.h",
    "src/llama-graph.cpp",
    "src/llama-graph.h",
    "src/llama-model-loader.cpp",
    "src/llama-model-loader.h",
    "src/llama-model.cpp",
    "src/llama-model.h",
    "src/llama.cpp",
}
ALLOWED_PROJECT_PREFIXES = (
    "corpus/phase2/",
    "docs/",
    "results/2026-07-29/skynet/phase2-observability/",
    "schemas/phase2/",
    "scripts/phase2/",
    "tests/fixtures/phase2/",
    "tests/phase2/",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=root, text=True, capture_output=True, check=True
    ).stdout.strip()


def validate_archive(
    archive_path: Path, external: dict[str, Any], errors: list[str]
) -> None:
    if not archive_path.is_file():
        errors.append(f"external archive is missing: {archive_path}")
        return
    if archive_path.stat().st_size != external["size"]:
        errors.append("external archive size differs from manifest")
    if sha256(archive_path) != external["sha256"]:
        errors.append("external archive checksum differs from manifest")
    expected = {member["path"]: member for member in external["members"]}
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            files = {member.name: member for member in archive.getmembers() if member.isfile()}
            if set(files) != set(expected):
                errors.append("external archive member set differs from manifest")
                return
            contents = {}
            for name, member in files.items():
                source = archive.extractfile(member)
                data = source.read() if source is not None else b""
                contents[name] = data
                if len(data) != expected[name]["size"]:
                    errors.append(f"archive member size differs: {name}")
                if hashlib.sha256(data).hexdigest() != expected[name]["sha256"]:
                    errors.append(f"archive member checksum differs: {name}")
            sums_name = "phase2-k3-route-corpus-v1/SHA256SUMS"
            declared = {}
            for line in contents[sums_name].decode().splitlines():
                checksum, relative = line.split("  ", 1)
                declared[f"phase2-k3-route-corpus-v1/{relative}"] = checksum
            for name, checksum in declared.items():
                if name not in contents or hashlib.sha256(contents[name]).hexdigest() != checksum:
                    errors.append(f"internal archive checksum differs: {name}")
    except (tarfile.TarError, UnicodeDecodeError, ValueError) as error:
        errors.append(f"external archive cannot be verified: {error}")


def validate_json_evidence(root: Path, errors: list[str]) -> None:
    result_root = root / "results/2026-07-29/skynet/phase2-observability"
    documents = {
        "route": json.loads((result_root / "phase2-route-regression.json").read_text()),
        "storage": json.loads((result_root / "phase2-storage-validation.json").read_text()),
        "performance": json.loads((result_root / "phase2-trace-enabled-performance.json").read_text()),
        "phase3": json.loads((result_root / "phase3-f16-reference-simulation.json").read_text()),
        "capture": json.loads((result_root / "phase4-corpus-capture.json").read_text()),
        "publication": json.loads((result_root / "phase4-corpus-publication.json").read_text()),
        "simulations": json.loads((result_root / "phase4-corpus-simulations.json").read_text()),
    }
    if documents["route"].get("status") != "pass" or len(documents["route"].get("cases", [])) != 4:
        errors.append("route regression evidence is not a four-case pass")
    storage = documents["storage"]
    if (
        storage.get("status") != "pass"
        or storage.get("totals", {}).get("entries") != 112
        or storage.get("totals", {}).get("projections") != 336
    ):
        errors.append("storage validation evidence is incomplete")
    performance = documents["performance"]
    if performance.get("status") != "OBSERVED" or len(performance.get("combinations", {})) != 4:
        errors.append("trace-enabled performance evidence is incomplete")
    phase3 = documents["phase3"]
    if phase3.get("schema_version") != "phase2-simulation-output-v1" or len(phase3.get("scenarios", [])) != 4:
        errors.append("Phase 3 reference simulation is incomplete")
    capture = documents["capture"]
    checks = capture.get("checks", {})
    if (
        capture.get("status") != "pass"
        or checks.get("cpu_cases") != 12
        or checks.get("cuda_cases") != 4
        or not checks.get("all_repeats_byte_identical")
        or not checks.get("all_cpu_cuda_generated_ids_exact")
    ):
        errors.append("corpus capture evidence is incomplete")
    publication = documents["publication"]
    if (
        publication.get("status") != "pass"
        or publication.get("published_revision") != EXPECTED["corpus_revision"]
        or publication.get("archive", {}).get("sha256") != EXPECTED["archive_sha256"]
        or not all(publication.get("verification", {}).values())
    ):
        errors.append("corpus publication evidence is incomplete")
    simulations = documents["simulations"]
    if simulations.get("status") != "pass" or len(simulations.get("cases", [])) != 12:
        errors.append("corpus simulation evidence is incomplete")

    schema_documents = [
        (
            "schemas/phase2/expert-storage-map-v1.schema.json",
            "results/2026-07-29/skynet/phase2-observability/phase2-f16-expert-storage-map-v1.json",
        ),
        (
            "schemas/phase2/expert-storage-map-v1.schema.json",
            "results/2026-07-29/skynet/phase2-observability/phase2-mxfp4-expert-storage-map-v1.json",
        ),
        (
            "schemas/phase2/cache-simulation-manifest-v1.schema.json",
            "results/2026-07-29/skynet/phase2-observability/phase3-simulation-manifest-v1.json",
        ),
        (
            "schemas/phase2/cache-simulation-output-v1.schema.json",
            "results/2026-07-29/skynet/phase2-observability/phase3-f16-reference-simulation.json",
        ),
    ]
    for schema_name, document_name in schema_documents:
        try:
            schema = json.loads((root / schema_name).read_text())
            document = json.loads((root / document_name).read_text())
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(document)
        except Exception as error:  # jsonschema reports useful structured messages
            errors.append(f"schema validation failed for {document_name}: {error}")


def validate_git(root: Path, manifest: dict[str, Any], strict: bool, errors: list[str]) -> None:
    nested = root / "llama.cpp"
    if git(nested, "rev-parse", "HEAD") != EXPECTED["llama_head"]:
        errors.append("nested llama.cpp head differs")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", EXPECTED["project_base"], "HEAD"],
        cwd=root,
        check=False,
    ).returncode != 0:
        errors.append("project head is not descended from the immutable base")
    if strict and git(root, "status", "--porcelain", "--untracked-files=all"):
        errors.append("project worktree is not clean")
    if strict and git(nested, "status", "--porcelain", "--untracked-files=all"):
        errors.append("nested llama.cpp worktree is not clean")

    nested_paths = set(
        git(nested, "diff", "--name-only", f"{EXPECTED['llama_base']}..{EXPECTED['llama_head']}").splitlines()
    )
    unexpected_nested = nested_paths - ALLOWED_NESTED_PATHS
    if unexpected_nested:
        errors.append(f"nested changes exceed permitted scope: {sorted(unexpected_nested)}")

    tracked = git(root, "ls-files").splitlines()
    for relative in tracked:
        lower = relative.lower()
        path = root / relative
        if (
            lower.endswith((".gguf", ".safetensors", ".trace", ".tar.gz"))
            or "/raw/" in lower
            or lower.startswith("models/")
            or lower.startswith("build")
        ):
            errors.append(f"prohibited generated artifact is tracked: {relative}")
        if lower.endswith(".bin") and relative != "tests/fixtures/phase2/k3-f16-cpu-route-v1.bin":
            errors.append(f"unexpected binary fixture is tracked: {relative}")
        if path.is_file() and path.stat().st_size > 10 * 1024 * 1024:
            errors.append(f"tracked file exceeds 10 MiB: {relative}")

    if strict:
        changed = git(root, "diff", "--name-only", f"{EXPECTED['project_base']}..HEAD").splitlines()
        for relative in changed:
            if relative == "llama.cpp":
                continue
            if not relative.startswith(ALLOWED_PROJECT_PREFIXES):
                errors.append(f"project change exceeds permitted scope: {relative}")

    plan = (root / "docs/plan/00-foundation.md").read_text()
    phase2 = plan.split("## Phase 2", 1)[1].split("## Phase 3", 1)[0]
    if "- [ ]" in phase2:
        errors.append("Phase 2 plan still contains unchecked tasks")

    checkpoints = {review["checkpoint"] for review in manifest["reviews"]}
    if "A" not in checkpoints:
        errors.append("accepted Checkpoint A review is not bound")
    if manifest["closeout_state"] == "complete" and "B" not in checkpoints:
        errors.append("complete manifest does not bind accepted Checkpoint B")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text())
    errors: list[str] = []

    schema_path = root / "schemas/phase2/phase2-manifest-v1.schema.json"
    try:
        schema = json.loads(schema_path.read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(manifest)
    except Exception as error:
        errors.append(f"manifest schema validation failed: {error}")

    revisions = manifest.get("revisions", {})
    required_revisions = {
        "project_execution_base": EXPECTED["project_base"],
        "llama_cpp_base": EXPECTED["llama_base"],
        "llama_cpp_storage_metadata": EXPECTED["llama_head"],
        "published_gguf": EXPECTED["gguf_revision"],
        "published_corpus": EXPECTED["corpus_revision"],
    }
    for name, expected in required_revisions.items():
        if revisions.get(name) != expected:
            errors.append(f"revision differs: {name}")

    artifacts = manifest.get("artifacts", [])
    paths = [artifact.get("path") for artifact in artifacts]
    if len(paths) != len(set(paths)):
        errors.append("manifest artifact paths are not unique")
    for artifact in artifacts:
        path = root / artifact["path"]
        if not path.is_file():
            errors.append(f"manifest artifact is missing: {artifact['path']}")
            continue
        if path.stat().st_size != artifact["size"] or sha256(path) != artifact["sha256"]:
            errors.append(f"manifest artifact identity differs: {artifact['path']}")

    external = manifest.get("external_archive", {})
    if external.get("revision") != EXPECTED["corpus_revision"]:
        errors.append("external archive revision differs")
    if external.get("sha256") != EXPECTED["archive_sha256"]:
        errors.append("external archive checksum differs")
    validate_archive(args.archive.resolve(), external, errors)

    for model in manifest.get("models", []):
        path = args.models_dir.resolve() / model["name"]
        if not path.is_file() or path.stat().st_size != model["size"] or sha256(path) != model["sha256"]:
            errors.append(f"model identity differs: {model['name']}")

    validate_json_evidence(root, errors)
    validate_git(root, manifest, args.strict, errors)

    result = {
        "schema_version": "phase2-verification-result-v1",
        "status": "pass" if not errors else "fail",
        "strict": args.strict,
        "manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "artifact_count": len(artifacts),
        "archive_member_count": len(external.get("members", [])),
        "errors": errors,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
