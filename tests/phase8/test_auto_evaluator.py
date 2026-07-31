from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase8"))

from evaluate_auto_cost import UINT64_MAX, evaluate_auto  # noqa: E402


def cost() -> dict[str, int]:
    return {
        "version": 1,
        "struct_size": 96,
        "cpu_fixed_decode_ns": 10,
        "cpu_fixed_prefill_ns": 20,
        "cpu_per_lane_decode_ns": 2,
        "cpu_per_lane_prefill_ns": 4,
        "gpu_fixed_decode_ns": 30,
        "gpu_fixed_prefill_ns": 40,
        "gpu_per_lane_decode_ns": 3,
        "gpu_per_lane_prefill_ns": 5,
        "h2d_fixed_ns": 7,
        "h2d_bytes_per_second": 1_000_000_000,
        "decision_hysteresis_ns": 1,
    }


def record(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "cost": cost(),
        "prefill": False,
        "lanes": 2,
        "bundle_bytes": 100,
        "queued_cpu_work_ns": 0,
        "queued_h2d_work_ns": 0,
        "queued_gpu_work_ns": 0,
        "same_key_submitted_bytes": 0,
    }
    value.update(updates)
    return value


class AutoEvaluatorTests(unittest.TestCase):
    def test_cpu_gpu_and_prefill_buckets(self) -> None:
        cpu_result = evaluate_auto(record())
        self.assertEqual((cpu_result["backend"], cpu_result["reason"]), ("cpu", "cpu_faster"))
        gpu_cost = cost()
        gpu_cost.update(cpu_fixed_decode_ns=10_000, cpu_per_lane_decode_ns=10_000)
        gpu_result = evaluate_auto(record(cost=gpu_cost))
        self.assertEqual(gpu_result["backend"], "gpu")
        prefill_result = evaluate_auto(record(prefill=True))
        self.assertEqual(prefill_result["cpu_work_ns"], 28)
        self.assertEqual(prefill_result["gpu_work_ns"], 50)

    def test_tie_overflow_queued_work_and_same_key(self) -> None:
        tie_cost = cost()
        tie_cost.update(
            cpu_fixed_decode_ns=2,
            cpu_per_lane_decode_ns=1,
            gpu_fixed_decode_ns=1,
            gpu_per_lane_decode_ns=1,
            h2d_fixed_ns=1,
        )
        tie = evaluate_auto(record(cost=tie_cost, lanes=1, bundle_bytes=0))
        self.assertEqual((tie["backend"], tie["reason"]), ("gpu", "tie"))
        queued = evaluate_auto(record(queued_cpu_work_ns=100, queued_h2d_work_ns=3, queued_gpu_work_ns=5))
        self.assertEqual(queued["cpu_finish_ns"], 114)
        self.assertEqual(queued["gpu_finish_ns"], 151)
        same_key = evaluate_auto(record(bundle_bytes=100, same_key_submitted_bytes=7))
        self.assertEqual(same_key["h2d_work_ns"], 14)
        overflow = evaluate_auto(record(lanes=UINT64_MAX))
        self.assertEqual((overflow["backend"], overflow["reason"], overflow["overflow"]),
                         ("gpu", "overflow", True))

    def test_rejects_invalid_coefficients(self) -> None:
        invalid = cost()
        invalid["h2d_bytes_per_second"] = 0
        with self.assertRaises(ValueError):
            evaluate_auto(record(cost=invalid))
        invalid = cost()
        invalid["version"] = 2
        with self.assertRaises(ValueError):
            evaluate_auto(record(cost=invalid))
        invalid = cost()
        invalid["struct_size"] = 0
        with self.assertRaises(ValueError):
            evaluate_auto(record(cost=invalid))


if __name__ == "__main__":
    unittest.main()
