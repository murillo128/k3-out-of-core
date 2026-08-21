# Issue #100 GPQA Diamond execution tools

This directory implements the accepted Checkpoint-A contract plus the
production-AUTO admission amendment without placing
GPQA questions, choice permutations, correct answers, model outputs, or
per-item scores in Git.

The tool boundary is deliberate:

- `prepare_campaign.py` reconstructs and validates all protected prompts,
  permutations, answers, seeds, and the 228-run order from the pinned encrypted
  dataset and the four immutable Checkpoint-A manifests.
- `gpqa_probe.cpp` performs only native K3 inference and resource validation. It
  requests production AUTO capacity inside the benchmark process, fails before
  prompt inference below 5,874 slots, has no answer key, and does no scoring.
- `run_campaign.py` enforces the exact-target execution authorization, runs each
  arm in a fresh process, scores protected response bytes, durably appends
  accepted records, and resumes only checksum-valid evidence.
- `analyze_campaign.py` independently re-scores all 228 raw attempts and applies
  the frozen paired bootstrap, McNemar, Wilson, and disposition rules.

## Hard gate

Do not run `run_campaign.py` until all of the following bind the same published
project commit:

1. the exact binary has been built from that commit;
2. Python tests and the non-scored EXACT/S2 conformance run pass;
3. the authoritative issue amendment still authorizes execution; and
4. `freeze_execution_authorization.py` binds that amendment and conformance.

The campaign runner rejects a dirty worktree, a changed project or nested
commit, a changed binary, preregistration, AUTO-admission amendment, protected
plan, capacity mode/floor, model
manifest identity, or execution authorization.

## Reproducible command sequence

Prepare protected inputs outside Git:

```bash
python3 scripts/issue100/prepare_campaign.py \
  --dataset-zip /path/to/dataset.zip \
  --item-universe /path/to/item-universe-v3.json \
  --exact30-selection /path/to/exact30-selection-v3.json \
  --campaign /path/to/campaign-v3.json \
  --output-root /mnt/nvme1/issue100/prepared
```

Configure and build the recovery target with Release/max-native CPU runtime:

```bash
cmake -S llama.cpp -B /mnt/nvme1/issue100/recovery-native-build \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=ON -DGGML_CUDA=OFF -DBUILD_SHARED_LIBS=ON \
  -DLLAMA_BUILD_TESTS=ON -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_SERVER=OFF -DLLAMA_BUILD_TOOLS=OFF
cmake --build /mnt/nvme1/issue100/recovery-native-build --parallel 32

cmake -S scripts/issue100 -B /mnt/nvme1/issue100/recovery-probe-build \
  -DCMAKE_BUILD_TYPE=Release \
  -DISSUE100_LLAMA_SOURCE="$PWD/llama.cpp" \
  -DISSUE100_FROZEN_BUILD=/mnt/nvme1/issue100/recovery-native-build
cmake --build /mnt/nvme1/issue100/recovery-probe-build --parallel 32 \
  --target issue100-gpqa-probe
```

Run the outcome-blind fixture after the implementation commit is clean:

```bash
python3 scripts/issue100/run_conformance.py \
  --binary /mnt/nvme1/issue100/recovery-probe-build/bin/issue100-gpqa-probe \
  --reboot-evidence /mnt/nvme1/issue100/recovery-reboot-v3.json \
  --output-root /mnt/nvme1/issue100/conformance-TARGET
```

After conformance, bind the published execution amendment and start or resume
the scientific campaign:

```bash
python3 scripts/issue100/freeze_execution_authorization.py \
  --preregistration corpus/phase13/issue100-preregistration-v2.json \
  --protected-plan /mnt/nvme1/issue100/prepared-v1/protected-plan.json \
  --binary /mnt/nvme1/issue100/recovery-probe-build/bin/issue100-gpqa-probe \
  --conformance /mnt/nvme1/issue100/conformance-TARGET/conformance.json \
  --previous-execution-authorization /mnt/nvme1/issue100/execution-authorization-ac3849f-auto.json \
  --campaign-root /mnt/nvme1/issue100/campaign-auto-ac3849f \
  --recovery-amendment-url RECOVERY_AMENDMENT_URL \
  --recovery-amendment-sha256 RECOVERY_AMENDMENT_BODY_SHA256 \
  --independent-review-url REVIEW_URL \
  --independent-review-sha256 REVIEW_BODY_SHA256 \
  --reboot-evidence /mnt/nvme1/issue100/recovery-reboot-v3.json \
  --output /mnt/nvme1/issue100/execution-authorization-recovery-v3.json

python3 scripts/issue100/run_campaign.py \
  --preregistration corpus/phase13/issue100-preregistration-v2.json \
  --protected-plan /mnt/nvme1/issue100/prepared-v1/protected-plan.json \
  --execution-authorization /mnt/nvme1/issue100/execution-authorization-recovery-v3.json \
  --output-root /mnt/nvme1/issue100/campaign-auto-ac3849f
```

Once `228/228` accepted runs and `30/30` pairs are complete:

```bash
python3 scripts/issue100/analyze_campaign.py \
  --campaign-root /mnt/nvme1/issue100/campaign \
  --protected-plan /mnt/nvme1/issue100/prepared/protected-plan.json \
  --output /mnt/nvme1/issue100/final-analysis.json
```

Raw attempts and protected scoring evidence remain under `/mnt/nvme1`; only
small provenance records and aggregate, contamination-safe summaries are
eligible for publication in this repository.
