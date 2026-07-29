from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/phase1/capture_benchmarks.py"
SPEC = importlib.util.spec_from_file_location("capture_benchmarks", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class CaptureBenchmarksTests(unittest.TestCase):
    def fixture_stdout(self) -> str:
        ids = ",".join(str(value) for value in range(128))
        latencies = ",".join("0.01" for _ in range(127))
        runs = []
        for kind, indexes in (("warmup", [0]), ("measured", range(5))):
            for index in indexes:
                runs.extend(
                    (
                        f"RUN\t{kind}\t{index}\tprompt_tokens=5\tgenerated_tokens=128"
                        "\tterminal_eog=0\tttft_seconds=0.02\tprompt_tokens_per_second=250"
                        "\tdecode_tokens_per_second=100\tpeak_rss_kib=1000"
                        "\tgpu_used_peak_bytes=-1",
                        f"LATENCIES\t{kind}\t{index}\t{latencies}",
                        f"IDS\t{kind}\t{index}\t{ids}",
                    )
                )
        return "\n".join(
            [
                "CONFIG\tprompt=According to all known laws\tseed=1\ttemperature=0"
                "\tcontext=512\tgenerate=128\tthreads=8\tgpu_layers=0",
                "DEVICE\t0\tCPU\tTest CPU\t0\t1\t2",
                "LOAD\tseconds=0.5\tpeak_rss_kib=900"
                "\tgpu_baseline_used_bytes=-1\tgpu_used_after_load_bytes=-1",
                "PROMPT_IDS\t18805,308,799,5624,12524",
                *runs,
                "RESULT\tload_calls=1\twarmups=1\tmeasured=5\texit=0",
            ]
        )

    def test_parse_and_validate_complete_probe(self) -> None:
        parsed = benchmark.parse_probe_stdout(self.fixture_stdout())
        checks = benchmark.validate_probe_contract(parsed, "cpu")
        self.assertEqual(len(parsed["runs"]), 6)
        self.assertTrue(all(checks.values()))

    def test_validate_rejects_missing_measured_run(self) -> None:
        parsed = benchmark.parse_probe_stdout(self.fixture_stdout())
        parsed["runs"].pop()
        with self.assertRaisesRegex(benchmark.BenchmarkError, "contract failed"):
            benchmark.validate_probe_contract(parsed, "cpu")

    def test_validate_accepts_stable_natural_eog_before_cap(self) -> None:
        parsed = benchmark.parse_probe_stdout(self.fixture_stdout())
        for run in parsed["runs"]:
            run["generated_tokens"] = 49
            run["terminal_eog"] = True
            run["generated_ids"] = run["generated_ids"][:49]
            run["decode_token_latencies_seconds"] = run[
                "decode_token_latencies_seconds"
            ][:48]
        checks = benchmark.validate_probe_contract(parsed, "cpu")
        self.assertTrue(checks["all_runs_respect_128_token_cap"])

    def test_validate_rejects_short_run_without_eog(self) -> None:
        parsed = benchmark.parse_probe_stdout(self.fixture_stdout())
        for run in parsed["runs"]:
            run["generated_tokens"] = 49
            run["generated_ids"] = run["generated_ids"][:49]
            run["decode_token_latencies_seconds"] = run[
                "decode_token_latencies_seconds"
            ][:48]
        with self.assertRaisesRegex(benchmark.BenchmarkError, "contract failed"):
            benchmark.validate_probe_contract(parsed, "cpu")

    def test_percentiles_use_linear_interpolation(self) -> None:
        result = benchmark.percentiles([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(result["p50"], 3.0)
        self.assertEqual(result["p95"], 4.8)
        self.assertEqual(result["p99"], 4.96)

    def test_metric_summary_uses_population_deviation(self) -> None:
        result = benchmark.metric_summary([1.0, 2.0, 3.0])
        self.assertEqual(result["mean"], 2.0)
        self.assertAlmostEqual(result["population_standard_deviation"], (2.0 / 3.0) ** 0.5)

    def test_aggregate_requires_token_stability(self) -> None:
        parsed = benchmark.parse_probe_stdout(self.fixture_stdout())
        parsed["runs"][-1]["generated_ids"][-1] = 999
        with self.assertRaisesRegex(benchmark.BenchmarkError, "token stability failed"):
            benchmark.aggregate_runs(parsed, list(range(32)))

    def test_hard_failure_scan_rejects_nonfinite_message(self) -> None:
        result = benchmark.hard_failure_scan("BENCH_ERROR: nan")
        self.assertFalse(result["passed"])
        self.assertIn("nan_or_inf", result["matches"])

    def test_normalized_text_strips_trailing_whitespace(self) -> None:
        result = benchmark.normalized_text("one  \ntwo\n", Path("/tmp/example"))
        self.assertEqual(result, "one\ntwo\n")


if __name__ == "__main__":
    unittest.main()
