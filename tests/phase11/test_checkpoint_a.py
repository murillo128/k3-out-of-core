import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("checkpoint_a", ROOT / "scripts/phase11/capture_checkpoint_a.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def valid_document():
    probe = {"schema_version": "phase11-uma-probe-v1", "board": "EdgeXpert (MS-C931)",
        "product": "MS-C931", "gpu": "NVIDIA GB10", "compute_capability": "12.1", "device_count": 1,
        "pageable_memory_access": 1, "pageable_uses_host_page_tables": 1,
        "native_io_uring": "unavailable_host_seccomp", "io_uring_errno": 1,
        "memlock_limit_bytes": 8388608, "registered_large_buffer_evidence": "unavailable",
        "storage_transport": "buffered_pread", "cuda_driver_version": 13000, "cuda_runtime_version": 13000,
        "direct_io_mem_alignment": 512, "direct_io_offset_alignment": 512,
        "allocation_bytes": 4096, "memory_handoff_service_ns": 1, "readiness_service_ns": 1,
        "buffered_pread_service_ns": 1, "resident_pages": 1, "status": "pass"}
    return {"schema_version": "phase11-capabilities-v1", "status": "pass",
        "scope": "gb10_coherent_uma_buffered_storage_fallback",
        "revisions": {"project_head": "1" * 40, "nested_head": "2" * 40, "gitlink": "2" * 40},
        "platform": {"board": "EdgeXpert (MS-C931)", "product": "MS-C931", "architecture": "aarch64",
            "kernel": "6.17", "cpu_count": 20, "mem_total_bytes": 1, "mem_available_bytes": 1,
            "cgroup_memory_max_bytes": 1, "cgroup_memory_current_bytes": 0, "swap_total_bytes": 0,
            "seccomp_mode": 2},
        "board_firmware": {"board_vendor": "MSI", "board_version": "1", "bios_vendor": "MSI", "bios_version": "1", "bios_date": "date"},
        "cpu_topology": {"lscpu": []}, "gpu_inventory": "NVIDIA GB10, UUID, PCI, 12.1", "toolkit": "CUDA 13",
        "cuda_runtime_version": 13000, "storage_inventory": {}, "filesystem": {},
        "iommu": {"group_count": 1, "ats_event_count": 1, "implementation": "arm-smmu-v3"},
        "ats_evidence": "present", "c2c_evidence": "probe", "thermal_inventory": [],
        "service_measurements": {"bytes": 4096, "memory_handoff_ns": 1, "readiness_ns": 1, "buffered_pread_ns": 1},
        "memlock_limit_bytes": 8388608,
        "probe": probe, "commands": ["a", "b", "c", "d", "e"]}


class CheckpointATest(unittest.TestCase):
    def test_valid_amended_host_record(self):
        MODULE.validate(valid_document())

    def test_rejects_false_capability_claims(self):
        for field, value in (("native_io_uring", "available"), ("memlock_limit_bytes", 0),
                ("storage_transport", "io_uring")):
            with self.subTest(field=field):
                document = valid_document()
                document["probe"][field] = value
                with self.assertRaises(ValueError):
                    MODULE.validate(document)


if __name__ == "__main__":
    unittest.main()
