#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from common import diagnostics, identity, run, write

def invoke(exe,root,model,mode,steps,cold):
    cmd=[str(exe),"--model",str(model.resolve()),"--mode",mode,"--steps",str(steps)]
    if mode=="cold": cmd += ["--capacity","8","--cold-bytes",str(cold),"--ring-bytes","16777216"]
    r=run(cmd,root); return r,diagnostics(r["stdout"]+r["stderr"],"PHASE5_LIVE")

def main():
    p=argparse.ArgumentParser(); p.add_argument("--cuda-build",type=Path,required=True); p.add_argument("--f16",type=Path,required=True); p.add_argument("--mxfp4",type=Path,required=True); p.add_argument("--split-dir",type=Path,required=True); p.add_argument("--phase5-manifest",type=Path,required=True); p.add_argument("--hot-capacities"); p.add_argument("--cold-cases"); p.add_argument("--warm-epochs",type=int,required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    root=Path.cwd().resolve(); exe=(a.cuda_build/"bin/phase5-cold-cache-probe").resolve(); cases=[]
    for rep,original,token in (("f16",a.f16,"F16"),("mxfp4",a.mxfp4,"MXFP4")):
        split=sorted(a.split_dir.glob(f"*{token}-split.gguf-*.gguf"))[0]
        base_run,baseline=invoke(exe,root,original,"disabled",a.warm_epochs,0)
        for kind,model in (("original",original),("split",split)):
            captures=[]
            for capture in range(2):
                record,diag=invoke(exe,root,model,"cold",a.warm_epochs,7000000 if rep=="f16" else 2000000)
                checks={"command":record["exit_code"]==0,"exact_prompt":diag["prompt_ids"]==baseline["prompt_ids"],"exact_tokens":diag["tokens"]==baseline["tokens"],"exact_logits":diag["logits_hash"]==baseline["logits_hash"],"exact_routes":diag["route_hash"]==baseline["route_hash"] and diag["route_records"]==baseline["route_records"],"storage_used":diag["storage_read_requests"]>0 and diag["storage_read_bytes"]>0,"no_source_copy":diag["cold_source_bytes"]==0,"no_errors":diag["storage_short_reads"]==diag["storage_io_errors"]==diag["storage_cancelled_reads"]==0,"bounded":diag["cold_actual_bytes"]<=diag["cold_requested_bytes"] and diag["ring_actual_bytes"]<=diag["ring_requested_bytes"] and diag["ring_pinned_bytes"]<=diag["ring_actual_bytes"]}
                captures.append({"capture":capture+1,"diagnostics":diag,"checks":checks,"output_digests":{"stdout":record["stdout_sha256"],"stderr":record["stderr_sha256"]}})
            cases.append({"representation":rep,"kind":kind,"model":identity(root,model),"baseline":baseline,"captures":captures})
    status=all(all(all(x["checks"].values()) for x in c["captures"]) for c in cases)
    write(a.output,{"schema_version":"phase6-demand-parity-v1","status":"pass" if status else "fail","warm_epochs":a.warm_epochs,"cases":cases}); return 0 if status else 1
if __name__=="__main__": raise SystemExit(main())
