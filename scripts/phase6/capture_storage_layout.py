#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
from common import PHASE5_MANIFEST, diagnostics, identity, run, sha256, write

def main():
    p=argparse.ArgumentParser(); p.add_argument("--cpu-build",type=Path,required=True); p.add_argument("--f16",type=Path,required=True); p.add_argument("--mxfp4",type=Path,required=True); p.add_argument("--split-dir",type=Path,required=True); p.add_argument("--phase5-manifest",type=Path,required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    root=Path.cwd().resolve()
    if str(a.phase5_manifest) != PHASE5_MANIFEST: raise RuntimeError("unexpected Phase 5 authority")
    phase5={"path":str(a.phase5_manifest),"size":a.phase5_manifest.stat().st_size,"sha256":sha256(a.phase5_manifest)}
    cases=[]; splits={}
    for name, model in (("f16",a.f16),("mxfp4",a.mxfp4)):
        files=sorted(a.split_dir.glob(f"*{'F16' if name=='f16' else 'MXFP4'}-split.gguf-*.gguf"))
        if len(files) != 218: raise RuntimeError(f"{name}: expected 218 splits")
        splits[name]=[{**identity(root,path),"number":i+1,"count":len(files)} for i,path in enumerate(files)]
        for kind,path in (("original",model),("split",files[0])):
            executable = (root/"llama.cpp/build-cuda/bin/phase6-gguf-storage-probe").resolve()
            command=[str(executable),"--model",str(path.resolve()),"--capacity","8","--cold-bytes","67108864","--ring-bytes","16777216"]
            record=run(command,root); diag=diagnostics(record["stdout"]+record["stderr"],"PHASE6_LOAD")
            checks={"command":record["exit_code"]==0,"no_routed_allocation":diag["deferred_allocated_bytes"]==0,"no_mmap_binding":diag["deferred_mmap_bound_bytes"]==0,"no_prefetch":diag["deferred_prefetch_bytes"]==0,"metadata_only":diag["routed_tensors"]==diag["routed_null"],"directory_complete":diag["storage_entries"]==56 and diag["storage_spans"]==168,"split_handles":diag["storage_files"]==(1 if kind=="original" else 218)}
            cases.append({"representation":name,"kind":kind,"model":identity(root,path),"diagnostics":diag,"checks":checks,"output_digests":{"stdout":record["stdout_sha256"],"stderr":record["stderr_sha256"]}})
    value={"schema_version":"phase6-storage-layout-v1","status":"pass" if all(all(c["checks"].values()) for c in cases) else "fail","phase5_manifest":phase5,"split_command":{"tool_head":__import__('subprocess').check_output(["git","-C","llama.cpp","rev-parse","HEAD"],text=True).strip(),"max_tensors":1},"splits":splits,"cases":cases}
    write(a.output,value); return 0 if value["status"]=="pass" else 1
if __name__=="__main__": raise SystemExit(main())
