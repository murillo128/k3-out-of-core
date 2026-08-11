#!/usr/bin/env python3
"""Run one exact/cache-aware teacher-forced Phase 13.6 quality pair."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


class QualityPairError(RuntimeError):
    pass


def run_logged(command: list[str], log_path: Path) -> None:
    print(f"PHASE13_QUALITY_PAIR start={' '.join(command)}", flush=True)
    with log_path.open("w") as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            if line.startswith("PHASE13_") or "decode failed" in line:
                print(line, end="", flush=True)
        return_code = process.wait()
    if return_code != 0:
        raise QualityPairError(f"command failed with status {return_code}; see {log_path}")


def probe_command(args: argparse.Namespace, output: Path, trace: Path,
                  max_generate: int) -> list[str]:
    command = [
        str(args.probe),
        "--model", str(args.model),
        "--output", str(output),
        "--quality-trace-output", str(trace),
        "--candidate-count", str(args.candidate_count),
        "--max-generate", str(max_generate),
        "--n-ctx", str(args.n_ctx),
        "--n-batch", str(args.n_batch),
        "--n-ubatch", str(args.n_ubatch),
        "--threads", str(args.threads),
    ]
    command += ["--prompt", args.prompt] if args.prompt is not None else [
        "--prompt-token", str(args.prompt_token)]
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--analyzer", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-token", type=int)
    parser.add_argument("--candidate-count", type=int, default=32)
    parser.add_argument("--max-generate", type=int, default=24)
    parser.add_argument("--n-ctx", type=int, default=512)
    parser.add_argument("--n-batch", type=int, default=512)
    parser.add_argument("--n-ubatch", type=int, default=4)
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--expected-prompt-tokens", type=int)
    parser.add_argument("--expected-generated-ids")
    parser.add_argument("--routing-capacity-slots", type=int, required=True)
    parser.add_argument("--routing-max-swaps", type=int, required=True)
    parser.add_argument("--routing-max-score-regret", type=float, required=True)
    args = parser.parse_args()
    if any(value <= 0 for value in (
            args.candidate_count, args.max_generate, args.n_ctx, args.n_batch,
            args.n_ubatch, args.threads, args.routing_capacity_slots)) or \
            not 0 < args.routing_max_swaps <= 16 or args.routing_max_score_regret <= 0:
        parser.error("counts/capacity/regret must be positive and max-swaps must be in 1..16")
    if args.expected_prompt_tokens is not None and args.expected_prompt_tokens <= 0:
        parser.error("expected-prompt-tokens must be positive")
    for executable in (args.probe, args.analyzer, args.python, args.model):
        if not executable.exists():
            parser.error(f"missing input: {executable}")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("output-dir must be absent or empty; refusing to overwrite evidence")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    exact_json = args.output_dir / "exact-routes.json"
    exact_trace = args.output_dir / "exact-quality.p13q"
    changed_json = args.output_dir / "changed-routes.json"
    changed_trace = args.output_dir / "changed-quality.p13q"
    comparison_json = args.output_dir / "quality-comparison.json"

    exact = probe_command(args, exact_json, exact_trace, args.max_generate)
    run_logged(exact, args.output_dir / "exact.log")
    with exact_json.open() as source:
        exact_result = json.load(source)
    reference_ids = exact_result.get("generated_ids")
    if not isinstance(reference_ids, list) or not reference_ids or \
            any(not isinstance(token, int) or token < 0 for token in reference_ids):
        raise QualityPairError("exact run did not emit a valid reference sequence")
    if args.expected_prompt_tokens is not None and \
            len(exact_result.get("prompt_ids", [])) != args.expected_prompt_tokens:
        raise QualityPairError("exact run prompt-token count does not match the pinned case")
    if args.expected_generated_ids is not None:
        try:
            expected_ids = [int(value) for value in args.expected_generated_ids.split(",")]
        except ValueError as exc:
            raise QualityPairError("expected-generated-ids is not a comma-separated integer list") from exc
        if reference_ids != expected_ids:
            raise QualityPairError("exact run generated IDs do not match the pinned case")

    changed = probe_command(args, changed_json, changed_trace, len(reference_ids)) + [
        "--teacher-forced-ids", ",".join(map(str, reference_ids)),
        "--routing-enabled", "1",
        "--routing-capacity-slots", str(args.routing_capacity_slots),
        "--routing-max-swaps", str(args.routing_max_swaps),
        "--routing-max-score-regret", str(args.routing_max_score_regret),
    ]
    run_logged(changed, args.output_dir / "changed.log")

    analyze = [
        str(args.python), str(args.analyzer),
        "--exact-trace", str(exact_trace),
        "--changed-trace", str(changed_trace),
        "--exact-routes", str(exact_json),
        "--changed-routes", str(changed_json),
        "--output", str(comparison_json),
    ]
    run_logged(analyze, args.output_dir / "analyze.log")
    with (args.output_dir / "commands.json").open("w") as destination:
        json.dump({
            "schema_version": "phase13-quality-pair-commands-v1",
            "exact": exact,
            "changed": changed,
            "analyze": analyze,
            "reference_ids": reference_ids,
        }, destination, indent=2)
        destination.write("\n")
    print(f"PHASE13_QUALITY_PAIR status=pass output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
