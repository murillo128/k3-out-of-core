#!/usr/bin/env python3
"""Bounded correctness and failure tests for Phase 12P evidence tooling."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase12p"))
from common import (  # noqa: E402
    CONFIG_SHA256, FULL_SCALE, PAYLOAD_DOMAIN, SEED, SOURCE_REVISION, Journal, Scale,
    canonical_bytes, checked_add, checked_mul, logical_projection_offset, payload_chunks,
    preflight, route_document, route_identity, selected_experts, validate_journal_chains,
)
from corpus import HEADER_SIZE, INDEX, generate  # noqa: E402
from positional_read import Operation, locality_window_8, physical_order, read_sync, read_threaded  # noqa: E402
from verify import verify  # noqa: E402

FIXTURE = Scale(layers=2, experts=32, selected=16, projection_bytes=4096, tokens=4)


class Phase12PTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="phase12p-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp)

    def test_exact_full_scale_and_checked_arithmetic(self) -> None:
        FULL_SCALE.validate_full()
        self.assertEqual(FULL_SCALE.bundle_bytes, 17_547_264)
        self.assertEqual(FULL_SCALE.materialized_bundles, 1472)
        self.assertEqual(FULL_SCALE.useful_bytes, 25_829_572_608)
        self.assertEqual(FULL_SCALE.logical_bytes, 1_446_456_066_048)
        with self.assertRaises(OverflowError): checked_add((1 << 64) - 1, 1)
        with self.assertRaises(OverflowError): checked_mul(1 << 63, 2)
        with self.assertRaises(ValueError): logical_projection_offset(FULL_SCALE, 92, 0, 0)
        with self.assertRaises(ValueError): logical_projection_offset(FULL_SCALE, 0, 896, 0)
        with self.assertRaises(ValueError): logical_projection_offset(FULL_SCALE, 0, 0, 3)

    def test_selected_keys_and_route_contract(self) -> None:
        self.assertEqual(selected_experts(FULL_SCALE, 0), tuple((2608 + 17 * rank) % 896 for rank in range(16)))
        route = route_document(FIXTURE)
        self.assertEqual([request["request"] for request in route["requests"]], ["COLD_SPREAD", "LOGICAL_SHUFFLE", "HALF_HOT"])
        self.assertTrue(all(len(request["records"]) == 4 * 2 * 16 for request in route["requests"]))
        half_hot = route["requests"][2]["records"]
        first = [row[3] for row in half_hot[:16]]
        second_token = [row[3] for row in half_hot[2 * 16:2 * 16 + 16]]
        self.assertEqual(first[:8], second_token[:8])
        self.assertNotEqual(first[8:], second_token[8:])

    def test_payload_algorithm_independent_golden(self) -> None:
        key = b"".join((PAYLOAD_DOMAIN, SOURCE_REVISION.encode(), b"\0", bytes.fromhex(CONFIG_SHA256), struct.pack("<QII", SEED, 1, 2), b"\x00"))
        expected = b"".join(hashlib.sha256(key + struct.pack("<Q", index)).digest() for index in range(3))[:65]
        self.assertEqual(b"".join(payload_chunks(1, 2, 0, 65)), expected)

    def test_two_clean_generations_and_complete_verification(self) -> None:
        one = generate(self.temp / "one", FIXTURE)
        verified_one = verify(self.temp / "one")
        shutil.rmtree(self.temp / "one")
        two = generate(self.temp / "two", FIXTURE)
        verified_two = verify(self.temp / "two")
        for field in ("route_sha256", "layout_a_definition_sha256", "layout_b_definition_sha256", "aggregate_useful_sha256"):
            self.assertEqual(one[field], two[field])
        self.assertEqual(verified_one["aggregate_useful_sha256"], verified_two["aggregate_useful_sha256"])

    def test_restart_truncates_incomplete_tail_and_preserves_identity(self) -> None:
        interrupted = self.temp / "interrupted"
        with self.assertRaisesRegex(RuntimeError, "injected bounded generation stop"):
            generate(interrupted, FIXTURE, stop_after_bundles=3)
        journal = interrupted / "layout-a/journal.jsonl"
        partial = Journal(journal, "phase12p-projection-spans-v1")
        layer, expert = sorted(
            (layer, expert)
            for layer in range(FIXTURE.layers)
            for expert in selected_experts(FIXTURE, layer)
        )[3]
        partial.append(layer, expert, "STARTED")
        partial.close()
        resumed = generate(interrupted, FIXTURE, resume=True)
        verified = verify(interrupted)
        clean = generate(self.temp / "clean", FIXTURE)
        self.assertEqual(resumed["aggregate_useful_sha256"], clean["aggregate_useful_sha256"])
        self.assertEqual(verified["status"], "PASS")

    def _generated(self, name: str = "run") -> Path:
        path = self.temp / name; generate(path, FIXTURE); return path

    def test_corrupt_payload_header_index_journal_route_and_truncation(self) -> None:
        mutations = (
            ("payload", "layout-a/projection-spans.bin", "selected", None),
            ("header", "layout-b/contiguous-experts.bin", 0, None),
            ("index", "layout-b/contiguous-experts.bin", HEADER_SIZE + FIXTURE.useful_bytes, None),
            ("journal", "layout-a/journal.jsonl", 10, None),
            ("route", "route-corpus.json", 20, None),
            ("truncate_store", "layout-b/contiguous-experts.bin", 0, "truncate"),
        )
        for name, relative, offset, action in mutations:
            with self.subTest(name=name):
                run = self._generated(name); path = run / relative
                if offset == "selected":
                    offset = json.loads((run / "layout-a/index.json").read_text())["entries"][0]["layout_a_offsets"][0]
                if action == "truncate":
                    with path.open("r+b") as stream: stream.truncate(path.stat().st_size - 1)
                else:
                    with path.open("r+b") as stream:
                        stream.seek(offset); byte = stream.read(1); stream.seek(offset); stream.write(bytes([byte[0] ^ 1]))
                with self.assertRaises((ValueError, EOFError, KeyError, UnicodeDecodeError, json.JSONDecodeError)):
                    verify(run)
                shutil.rmtree(run)

    def test_hole_detection(self) -> None:
        run = self._generated(); index = json.loads((run / "layout-a/index.json").read_text())
        offset = index["entries"][0]["layout_a_offsets"][0]
        path = run / "layout-a/projection-spans.bin"
        with path.open("r+b", buffering=0) as stream:
            try: os.posix_fallocate(stream.fileno(), offset, FIXTURE.projection_bytes)
            except AttributeError: self.skipTest("posix_fallocate unavailable")
            try:
                import ctypes
                libc = ctypes.CDLL(None, use_errno=True)
                if libc.fallocate(stream.fileno(), 3, ctypes.c_longlong(offset), ctypes.c_longlong(FIXTURE.projection_bytes)) != 0:
                    self.skipTest("hole punching unavailable")
            except OSError: self.skipTest("hole punching unavailable")
        with self.assertRaises(ValueError): verify(run)

    def test_journal_tail_recovery_and_state_rejection(self) -> None:
        path = self.temp / "journal.jsonl"
        journal = Journal(path, "phase12p-projection-spans-v1")
        for state in Journal.STATES: journal.append(0, 1, state, "0" * 64)
        journal.close()
        with path.open("ab") as stream: stream.write(b'{"partial":')
        journal = Journal(path, "phase12p-projection-spans-v1"); journal.close()
        records = Journal.read_valid(path)
        self.assertEqual(validate_journal_chains(records), {(0, 1)})
        bad = copy.deepcopy(records); bad[-1]["state"] = "WRITTEN"
        with self.assertRaises(ValueError): validate_journal_chains(bad)

    def test_preflight_reserve_rejects_current_overlay(self) -> None:
        gate = preflight(self.temp)
        self.assertEqual(gate["required_available_bytes"], 160 * (1 << 30))
        self.assertFalse(gate["passed"])
        output = self.temp / "full"
        with self.assertRaises(RuntimeError):
            generate(output, FULL_SCALE)
        self.assertEqual([path.name for path in output.iterdir()], ["storage-preflight.json"])

    def test_positional_reads_order_short_zero_progress_and_cancellation(self) -> None:
        path = self.temp / "reads.bin"; block = os.urandom(1 << 20); path.write_bytes(block * 8)
        digest = hashlib.sha256(block).hexdigest()
        operations = [Operation(i, i * len(block), len(block), digest) for i in range(8)]
        self.assertEqual(read_sync(path, operations)["effective_qd"]["status"], "SUPPORTED")
        threaded = read_threaded(path, operations, 2, 2)
        self.assertEqual(threaded["effective_qd"]["maximum_concurrency"], 2)
        shuffled = [operations[index] for index in (7, 0, 6, 1, 5, 2, 4, 3)]
        self.assertEqual([item.ordinal for item in physical_order(shuffled)], list(range(8)))
        self.assertEqual(sorted(item.ordinal for item in locality_window_8(shuffled)), list(range(8)))
        truncated = self.temp / "short.bin"; truncated.write_bytes(block[:-1])
        with self.assertRaises(EOFError): read_sync(truncated, [Operation(0, 0, len(block), digest)])
        cancelled = threading.Event(); cancelled.set()
        with self.assertRaises(RuntimeError): read_threaded(path, operations, 2, 2, cancelled)

    def test_incompatible_versions_and_empty_directory_gate(self) -> None:
        run = self._generated(); manifest = run / "layout-a/manifest.json"
        document = json.loads(manifest.read_text()); document["payload_generator"] = "stale"
        manifest.write_bytes(canonical_bytes(document))
        with self.assertRaises(ValueError): verify(run)
        with self.assertRaises(ValueError): generate(run, FIXTURE)

if __name__ == "__main__":
    unittest.main(verbosity=2)
