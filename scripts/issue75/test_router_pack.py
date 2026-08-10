#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from extract_router_pack import validate_artifact_identity
from router_pack import (
    PackError,
    assert_relative_members,
    payload_name,
    sha256_file,
    static_smoke_test,
    validate_inventory,
    validate_payload_tree,
    validate_tensor_records,
)


def tiny_config() -> dict:
    return {
        "router": {
            "first_routed_layer": 1,
            "routed_layer_count": 2,
            "experts_per_layer": 3,
            "hidden_dimension": 4,
            "projection_dtype": "F32",
            "projection_element_bytes": 4,
            "correction_dtype": "F32",
            "correction_element_bytes": 4,
        },
        "smoke_test": {"layers": [1], "experts": [0, 1]},
    }


def records(config: dict) -> list[dict]:
    result = []
    for layer in (1, 2):
        for role, name, shape, size in (
            ("router_projection_weight", f"blk.{layer}.ffn_gate_inp.weight", [4, 3], 48),
            ("selection_correction_bias", f"blk.{layer}.exp_probs_b.bias", [3], 12),
        ):
            result.append(
                {
                    "layer": layer,
                    "source_tensor_name": name,
                    "semantic_role": role,
                    "shape": shape,
                    "dtype": "F32",
                    "byte_length": size,
                    "source_file": "tiny.gguf",
                    "source_split": {"index": 0, "number": 1, "count": 1},
                    "source_range": {"offset": 100 + layer, "end_exclusive": 100 + layer + size},
                    "payload_path": payload_name(layer, role),
                    "sha256": "1" * 64,
                    "asset": "tiny.tar.zst",
                }
            )
    return result


class TensorValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = tiny_config()
        self.records = records(self.config)

    def test_exact_inventory_passes(self) -> None:
        validate_tensor_records(self.records, self.config)
        inventory = {
            "schema_version": "kimi-k3-router-tensor-inventory-v1",
            "tensor_count": len(self.records),
            "tensors": self.records,
        }
        self.assertEqual(validate_inventory(inventory, self.config), self.records)

    def assert_mutation_fails(self, update) -> None:
        changed = copy.deepcopy(self.records)
        update(changed)
        with self.assertRaises(PackError):
            validate_tensor_records(changed, self.config)

    def test_missing_layer_fails_closed(self) -> None:
        self.assert_mutation_fails(lambda value: value.pop())

    def test_duplicate_layer_role_fails_closed(self) -> None:
        self.assert_mutation_fails(lambda value: value.append(copy.deepcopy(value[0])))

    def test_unexpected_name_fails_closed(self) -> None:
        self.assert_mutation_fails(
            lambda value: value[0].update(source_tensor_name="blk.1.ffn_gate_inp.extra")
        )

    def test_shape_mismatch_fails_closed(self) -> None:
        self.assert_mutation_fails(lambda value: value[0].update(shape=[3, 4]))

    def test_dtype_mismatch_fails_closed(self) -> None:
        self.assert_mutation_fails(lambda value: value[0].update(dtype="F16"))

    def test_range_mismatch_fails_closed(self) -> None:
        self.assert_mutation_fails(
            lambda value: value[0].update(source_range={"offset": 0, "end_exclusive": 47})
        )


class PayloadTests(unittest.TestCase):
    def test_payload_hashes_and_smoke_operation(self) -> None:
        config = tiny_config()
        inventory = records(config)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for record in inventory:
                path = root / record["payload_path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                if record["semantic_role"] == "router_projection_weight":
                    values = [
                        1.0, 0.0, 0.0, 0.0,
                        0.0, 1.0, 0.0, 0.0,
                        1.0, 1.0, 0.0, 0.0,
                    ]
                else:
                    values = [0.25, -0.5, 0.75]
                path.write_bytes(struct.pack(f"<{len(values)}f", *values))
                record["sha256"] = sha256_file(path)
            validation = validate_payload_tree(root, inventory)
            self.assertEqual(validation, {"tensor_count": 4, "payload_bytes": 120})
            smoke = static_smoke_test(root, inventory, [1], 0, 1)
            self.assertEqual(smoke["status"], "PASS")
            self.assertAlmostEqual(smoke["layers"][0]["router_vector_norms"][0], 1.0)
            self.assertAlmostEqual(smoke["layers"][0]["cosine_similarity"], 0.0)

            gate = root / inventory[0]["payload_path"]
            gate.write_bytes(gate.read_bytes()[:-4] + b"xxxx")
            with self.assertRaises(PackError):
                validate_payload_tree(root, inventory)

    def test_archive_member_safety(self) -> None:
        assert_relative_members(["tensors/blk.001.bin"])
        for member in ("../secret", "/absolute", "other/file"):
            with self.assertRaises(PackError):
                assert_relative_members([member])


class SourceIdentityTests(unittest.TestCase):
    def test_source_size_and_hash_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            split = root / "tiny-00001-of-00001.gguf"
            split.write_bytes(b"qualified bytes")
            digest = sha256_file(split)
            identity = {
                "artifact": {
                    "repository": "example/model",
                    "revision": "a" * 40,
                    "variant": "test",
                    "total_bytes": split.stat().st_size,
                    "files": [
                        {
                            "name": split.name,
                            "path": str(split),
                            "sha256": digest,
                            "size": split.stat().st_size,
                        }
                    ],
                }
            }
            identity_path = root / "identity.json"
            identity_path.write_text(json.dumps(identity, sort_keys=True))
            config = {
                "source_artifact": {
                    "repository": "example/model",
                    "revision": "a" * 40,
                    "variant": "test",
                    "identity_manifest_sha256": sha256_file(identity_path),
                    "file_count": 1,
                    "total_bytes": split.stat().st_size,
                }
            }
            _, verified = validate_artifact_identity(identity_path, config, 1)
            self.assertEqual(verified[0]["sha256"], digest)

            split.write_bytes(b"tampered bytes")
            with self.assertRaises(PackError):
                validate_artifact_identity(identity_path, config, 1)

    def test_identity_manifest_hash_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "identity.json"
            path.write_text("{}")
            config = {"source_artifact": {"identity_manifest_sha256": "0" * 64}}
            with self.assertRaises(PackError):
                validate_artifact_identity(path, config, 1)


if __name__ == "__main__":
    unittest.main()
