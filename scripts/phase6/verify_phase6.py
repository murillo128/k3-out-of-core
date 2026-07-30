#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path
from jsonschema import Draft202012Validator
from common import *

ALLOWED_NESTED={"include/llama.h","src/CMakeLists.txt","src/llama-cold-expert-cache.cpp","src/llama-cold-expert-cache.h","src/llama-context.cpp","src/llama-expert-storage.cpp","src/llama-expert-storage.h","src/llama-expert-transfer-ring.cpp","src/llama-expert-transfer-ring.h","src/llama-expert-weight-provider.cpp","src/llama-expert-weight-provider.h","src/llama-mmap.cpp","src/llama-mmap.h","src/llama-model-loader.cpp","src/llama-model-loader.h","src/llama-model.cpp","src/llama-model.h","tests/CMakeLists.txt","tests/phase5-cold-cache-probe.cpp","tests/phase6-gguf-storage-probe.cpp","tests/test-cold-expert-cache.cpp","tests/test-expert-storage.cpp","tests/test-hot-expert-cache.cpp"}
def main():
 p=argparse.ArgumentParser(); p.add_argument("--project-root",type=Path,required=True); p.add_argument("--manifest",type=Path,required=True); p.add_argument("--models-dir",type=Path,required=True); p.add_argument("--strict",action="store_true"); a=p.parse_args(); root=a.project_root.resolve(); errors=[]
 try:
  m=json.loads(a.manifest.read_text()); schema=json.loads((root/"schemas/phase6/phase6-manifest-v1.schema.json").read_text()); Draft202012Validator(schema).validate(m)
 except Exception as e: print(f"FAIL: {e}"); return 1
 r=m["revisions"]; nested=root/"llama.cpp"
 if git(nested,"rev-parse","HEAD")!=r["llama_cpp_candidate"] or git(root,"rev-parse","HEAD:llama.cpp")!=r["gitlink"]: errors.append("head/gitlink mismatch")
 if set(git(nested,"diff","--name-only",f"{LLAMA_BASE}..HEAD").splitlines())-ALLOWED_NESTED: errors.append("nested scope mismatch")
 if m["checkpoint_a"]!={"comment_id":CHECKPOINT_COMMENT,"verdict":"PASS","safety_to_proceed":"YES","project_head":CHECKPOINT_PROJECT,"llama_cpp_head":CHECKPOINT_LLAMA,"independent_read_only":True}: errors.append("checkpoint binding mismatch")
 for item in m["models"]+list(m["evidence"].values())+m["artifacts"]+[m["phase5_input"]]:
  path=root/item["path"]
  if not path.is_file() or path.stat().st_size!=item["size"] or sha256(path)!=item["sha256"]: errors.append(f"identity mismatch: {item['path']}")
 for key,value in m["gates"].items():
  if value is not True and key!="byte_integrity": errors.append(f"gate failed: {key}")
 if any(x["exit_code"] for x in m["validation"]): errors.append("validation command failed")
 if subprocess.run(["git","diff","--check",f"{PROJECT_BASE}..HEAD"],cwd=root).returncode or subprocess.run(["git","diff","--check",f"{LLAMA_BASE}..HEAD"],cwd=nested).returncode: errors.append("diff check failed")
 if a.strict and (git(root,"status","--porcelain","--untracked-files=all") or git(nested,"status","--porcelain","--untracked-files=all")): errors.append("worktree not clean")
 write(a.manifest.parent/"verification-result.json",{"schema_version":"phase6-verification-v1","status":"pass" if not errors else "fail","manifest_sha256":sha256(a.manifest),"errors":errors})
 for e in errors: print("FAIL:",e)
 if not errors: print("PASS: strict Phase 6 evidence verified")
 return 1 if errors else 0
if __name__=="__main__": raise SystemExit(main())
