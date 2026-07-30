#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, os, subprocess
from pathlib import Path

PROJECT_BASE = "eb1b5baf5d505eadbc4298ecf322489cdfd7aae5"
LLAMA_BASE = "26317ee1d848dd7a73f22a3666a055cad5d5cb03"
CHECKPOINT_COMMENT = 5133647261
CHECKPOINT_PROJECT = "34dbf82ded955913b387ec9b36d1b499362e7a1b"
CHECKPOINT_LLAMA = "9af35746763913982bfd8eee995686296131c778"
PHASE5_MANIFEST = "results/2026-07-30/skynet/phase5-cold-cache/phase5-manifest.json"

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""): h.update(block)
    return h.hexdigest()

def identity(root: Path, path: Path) -> dict:
    path = path.resolve(); return {"path": str(path.relative_to(root.resolve())), "size": path.stat().st_size, "sha256": sha256(path)}

def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

def write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

def run(command: list[str], cwd: Path) -> dict:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    return {"command": command, "exit_code": completed.returncode,
            "stdout": completed.stdout, "stderr": completed.stderr,
            "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest()}

def diagnostics(output: str, prefix: str) -> dict:
    line = next((line for line in output.splitlines() if line.startswith(prefix)), None)
    if line is None: raise RuntimeError(f"missing {prefix}")
    result = {}
    for field in line.split("\t")[1:]:
        key, value = field.split("=", 1)
        try: result[key] = int(value)
        except ValueError:
            try: result[key] = float(value)
            except ValueError: result[key] = value
    return result

def filesystem(path: Path) -> dict:
    stat = os.statvfs(path)
    source = subprocess.run(["findmnt", "-no", "SOURCE,FSTYPE,TARGET", "--target", str(path)], text=True, capture_output=True)
    return {"findmnt": source.stdout.strip(), "block_size": stat.f_bsize,
            "physically_on_nvme": "/nvme" in source.stdout.lower() or "nvme" in source.stdout.lower()}
