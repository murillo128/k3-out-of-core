#!/usr/bin/env python3
from __future__ import annotations
import argparse, re
from pathlib import Path
from common import git, run, write

COMMANDS = [
 ("build-cpu", ["cmake","--build","llama.cpp/build-cpu","--target","llama-gguf-split","test-expert-weight-provider","test-hot-expert-cache","test-cold-expert-cache","test-expert-transfer-ring","test-expert-storage","-j4"]),
 ("build-cuda", ["cmake","--build","llama.cpp/build-cuda","--target","llama","llama-gguf-split","test-expert-weight-provider","test-hot-expert-cache","test-cold-expert-cache","test-expert-transfer-ring","test-expert-storage","phase6-gguf-storage-probe","phase5-cold-cache-probe","phase6-bundle-integrity-probe","-j4"]),
 ("ctest-cpu", ["ctest","--test-dir","llama.cpp/build-cpu","--output-on-failure","-R","expert-weight-provider|hot-expert-cache|cold-expert-cache|expert-transfer-ring|expert-storage"]),
 ("ctest-cuda", ["ctest","--test-dir","llama.cpp/build-cuda","--output-on-failure","-R","expert-weight-provider|hot-expert-cache|cold-expert-cache|expert-transfer-ring|expert-storage"]),
 ("build-sanitizers", ["cmake","--build","llama.cpp/build-phase5-asan","--target","test-expert-weight-provider","test-hot-expert-cache","test-cold-expert-cache","test-expert-transfer-ring","test-expert-storage","-j4"]),
 ("ctest-sanitizers", ["ctest","--test-dir","llama.cpp/build-phase5-asan","--output-on-failure","-R","expert-weight-provider|hot-expert-cache|cold-expert-cache|expert-transfer-ring|expert-storage"]),
 ("unittest-phase5", ["python3","-m","unittest","discover","-s","tests/phase5","-p","test_*.py","-v"]),
 ("unittest-phase6", ["python3","-m","unittest","discover","-s","tests/phase6","-p","test_*.py","-v"]),
 ("diff-nested", ["git","-C","llama.cpp","diff","--check","26317ee1d848dd7a73f22a3666a055cad5d5cb03..HEAD"]),
 ("diff-project", ["git","diff","--check","eb1b5baf5d505eadbc4298ecf322489cdfd7aae5..HEAD"]),
]
def main():
 p=argparse.ArgumentParser(); p.add_argument("--project-root",type=Path,required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args(); root=a.project_root.resolve(); records=[]
 for name,cmd in COMMANDS:
  r=run(cmd,root); text=r.pop("stdout")+r.pop("stderr"); match=re.search(r"(\d+) tests? passed",text); r.update({"name":name,"passed":int(match.group(1)) if match else None,"total":int(match.group(1)) if match else None}); records.append(r)
 value={"schema_version":"phase6-validation-v1","status":"pass" if all(r["exit_code"]==0 for r in records) else "fail","project_head":git(root,"rev-parse","HEAD"),"llama_cpp_head":git(root/"llama.cpp","rev-parse","HEAD"),"commands":records}; write(a.output,value); return 0 if value["status"]=="pass" else 1
if __name__=="__main__": raise SystemExit(main())
