import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("checkpoint_b", ROOT / "scripts/phase11/capture_checkpoint_b.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def diagnostics(name):
    return {"prompt_ids": "18805,308,799,5624,12524", "tokens": "1,2", "logits_hash": 1,
        "route_hash": 2, "route_records": 3, "provider_pool_bytes": MODULE.MODELS[name]["pool_bytes"],
        "provider_pool_generations": 1, "provider_effective_capacity": 2, "provider_tensor_copies": 0,
        "provider_failures": 0, "storage_read_requests": 1, "storage_read_bytes": 1,
        "scheduler_active": 0, "ring_requested_bytes": 0, "ring_actual_bytes": 0,
        "ring_h2d_bytes": 0, "io_async": 0, "execution_ids_cpu": 7, "execution_ids_non_cpu": 0,
        "execution_backend_device_type": 2}


def valid_document():
    cases = {}
    for name in MODULE.MODELS:
        value = diagnostics(name)
        cases[name] = {"split_count": 2, "split_command": ["split"],
            "original_baseline": {"diagnostics": copy.deepcopy(value)},
            "original_uma": {"diagnostics": copy.deepcopy(value)},
            "split_uma": {"diagnostics": copy.deepcopy(value)}}
    return {"schema_version": "phase11-checkpoint-b-v1", "status": "pass",
        "scope": "gb10_coherent_uma_buffered_storage_fallback",
        "revisions": {"project_head": "1" * 40, "nested_head": "2" * 40, "gitlink": "2" * 40},
        "models": {}, "cases": cases,
        "failure_lifecycle": {"auto_prefetch_probes": 1, "auto_touch_calls": 4,
            "readiness_retry_generation": 2, "stale_rejected": 1, "restored_capacity": 1,
            "cancellation_cleanups": 1, "cancellation_retry": 1, "scheduler_active": 0},
        "commands": ["build", "test"]}


class CheckpointBTest(unittest.TestCase):
    def test_valid(self):
        MODULE.validate(valid_document())

    def test_rejects_copy_or_parity_drift(self):
        for field, value in (("provider_tensor_copies", 1), ("ring_h2d_bytes", 1), ("logits_hash", 9)):
            with self.subTest(field=field):
                document = valid_document()
                document["cases"]["f16"]["split_uma"]["diagnostics"][field] = value
                with self.assertRaises(ValueError):
                    MODULE.validate(document)

    def test_rejects_failure_lifecycle_drift(self):
        document = valid_document()
        document["failure_lifecycle"]["stale_rejected"] = 0
        with self.assertRaises(ValueError):
            MODULE.validate(document)


if __name__ == "__main__":
    unittest.main()
