# Issue #100 GPQA Diamond execution tools

This directory implements the accepted Checkpoint-A contract without placing
GPQA questions, choice permutations, correct answers, model outputs, or
per-item scores in Git.

The tool boundary is deliberate:

- `prepare_campaign.py` reconstructs and validates all protected prompts,
  permutations, answers, seeds, and the 228-run order from the pinned encrypted
  dataset and the four immutable Checkpoint-A manifests.
- `gpqa_probe.cpp` performs only native K3 inference and resource validation. It
  has no answer key and does no scoring.
- `run_campaign.py` enforces the independent execution authorization, runs each
  arm in a fresh process, scores protected response bytes, durably appends
  accepted records, and resumes only checksum-valid evidence.
- `analyze_campaign.py` independently re-scores all 228 raw attempts and applies
  the frozen paired bootstrap, McNemar, Wilson, and disposition rules.

## Hard gate

Do not run `run_campaign.py` until all of the following bind the same published
project commit:

1. the exact binary has been built from that commit;
2. Python tests and the non-scored EXACT/S2 conformance run pass;
3. an independent reviewer publishes a pre-execution `PASS`; and
4. `freeze_execution_authorization.py` records that published review target.

The campaign runner rejects a dirty worktree, a changed project or nested
commit, a changed binary, preregistration, protected plan, capacity, model
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

Configure and build with the pinned CPU runtime:

```bash
cmake -S scripts/issue100 -B /mnt/nvme1/issue100/build \
  -DCMAKE_BUILD_TYPE=Release \
  -DISSUE100_LLAMA_SOURCE="$PWD/llama.cpp" \
  -DISSUE100_FROZEN_BUILD=/mnt/nvme1/issue77/build/cpu
cmake --build /mnt/nvme1/issue100/build --target issue100-gpqa-probe
```

Run the outcome-blind fixture after the implementation commit is clean:

```bash
python3 scripts/issue100/run_conformance.py \
  --binary /mnt/nvme1/issue100/build/bin/issue100-gpqa-probe \
  --output-root /mnt/nvme1/issue100/conformance-TARGET
```

After the independent pre-execution review, freeze its published verdict and
start or resume the scientific campaign:

```bash
python3 scripts/issue100/freeze_execution_authorization.py \
  --preregistration corpus/phase13/issue100-preregistration-v2.json \
  --protected-plan /mnt/nvme1/issue100/prepared/protected-plan.json \
  --binary /mnt/nvme1/issue100/build/bin/issue100-gpqa-probe \
  --conformance /mnt/nvme1/issue100/conformance-TARGET/conformance.json \
  --review-comment-url REVIEW_URL \
  --review-verdict-sha256 REVIEW_BODY_SHA256 \
  --output /mnt/nvme1/issue100/execution-authorization.json

python3 scripts/issue100/run_campaign.py \
  --preregistration corpus/phase13/issue100-preregistration-v2.json \
  --protected-plan /mnt/nvme1/issue100/prepared/protected-plan.json \
  --execution-authorization /mnt/nvme1/issue100/execution-authorization.json
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
