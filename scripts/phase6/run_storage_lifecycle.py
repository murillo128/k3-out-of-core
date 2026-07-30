#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from common import diagnostics, run, write

def main():
    p=argparse.ArgumentParser(); p.add_argument("--cpu-build",type=Path,required=True); p.add_argument("--cuda-build",type=Path,required=True); p.add_argument("--f16",type=Path,required=True); p.add_argument("--mxfp4",type=Path,required=True); p.add_argument("--split-dir",type=Path,required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    root=Path.cwd().resolve(); exe=(a.cuda_build/"bin/phase5-cold-cache-probe").resolve(); cases=[]
    for rep,model,cold in (("f16",a.f16,7000000),("mxfp4",a.mxfp4,2000000)):
        cmd=[str(exe),"--model",str(model.resolve()),"--mode","cold","--capacity","8","--cold-bytes",str(cold),"--ring-bytes","16777216","--steps","3","--cancel-on-storage"]
        r=run(cmd,root); d=diagnostics(r["stdout"]+r["stderr"],"PHASE6_CANCEL")
        checks={"aborted":d["cancelled_status"]==2,"partial_read":d["cancelled_storage_reads"]==1 and d["cancelled_storage_bytes"]>0,"no_publication":d["cancelled_hot_admissions"]==d["cancelled_cold_admissions"]==0,"references_balanced":d["cancelled_hot_refs"]==d["cancelled_transfer_refs"]==d["cancelled_request_refs"]==0,"cleanup":d["cancelled_failed_cleanups"]>0 and d["cancelled_cold_failed_cleanups"]>0,"retry":d["retry_status"]==0 and d["retry_hot_admissions"]>0 and d["retry_cold_admissions"]>0}
        cases.append({"representation":rep,"diagnostics":d,"checks":checks,"output_digests":{"stdout":r["stdout_sha256"],"stderr":r["stderr_sha256"]}})
    lifetime_result=run([str((a.cpu_build/"bin/test-expert-storage").resolve())],root)
    lifetime=diagnostics(lifetime_result["stdout"]+lifetime_result["stderr"],"PHASE6_STORAGE_LIFETIME")
    lifetime_checks={"command":lifetime_result["exit_code"]==0,"supported":lifetime["supported"]==1,
                     "peak_opened":lifetime["peak"]==lifetime["baseline"]+2,
                     "balanced":lifetime["balanced"]==1 and lifetime["final"]==lifetime["baseline"]}
    handle_lifetime={"diagnostics":lifetime,"checks":lifetime_checks,
                     "output_digests":{"stdout":lifetime_result["stdout_sha256"],"stderr":lifetime_result["stderr_sha256"]}}
    status=all(all(c["checks"].values()) for c in cases) and all(lifetime_checks.values())
    coverage={"positional_read_faults":"test-expert-storage","atomic_publication":"test-cold-expert-cache","cancel_cleanup_retry":True,"trim_surrender_reinitialize":"test-hot-expert-cache","quiescent_unload":True,"hard_integrity_poison":"test-expert-storage"}
    write(a.output,{"schema_version":"phase6-lifecycle-v1","status":"pass" if status else "fail","cases":cases,"handle_lifetime":handle_lifetime,"coverage":coverage}); return 0 if status else 1
if __name__=="__main__": raise SystemExit(main())
