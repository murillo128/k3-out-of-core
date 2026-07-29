from __future__ import annotations

import importlib.util
import struct
import tempfile
import unittest
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REAL_FIXTURE = ROOT / "tests/fixtures/phase2/k3-f16-cpu-route-v1.bin"
SPEC = importlib.util.spec_from_file_location("route_trace", ROOT / "scripts/phase2/route_trace.py")
route_trace = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(route_trace)


def string(value: str) -> bytes:
    encoded = value.encode()
    return struct.pack("<I", len(encoded)) + encoded


def trace_bytes(*, version: int = 1, top_k: int = 2, records: list[tuple[int, int, int, int]] | None = None) -> bytes:
    header = b"".join(
        [
            string("fixture.gguf"),
            struct.pack("<Q", 784318432),
            string("a" * 64),
            string("source-revision"),
            string("published-gguf-revision"),
            string("llama-revision"),
            string("run-1"),
            struct.pack("<III", 8, top_k, 7),
        ]
    )
    output = bytearray(b"K3ROUTE\0" + struct.pack("<II", version, len(header)) + header)
    items = records if records is not None else [(0, 0, 1, 0), (0, 0, 2, 0)]
    for ordinal, (request, ubatch, layer, row) in enumerate(items):
        payload = bytearray(struct.pack("<QQQIiIiI", ordinal, request, ubatch, 1, layer, row, row, 1))
        payload += struct.pack("<iI", 0, top_k)
        payload += struct.pack("<" + "i" * top_k, *range(top_k))
        payload += struct.pack("<" + "f" * top_k, *([0.5] * top_k))
        output += struct.pack("<II", 0x44434552, len(payload)) + payload
    checksum = zlib.crc32(output) & 0xFFFFFFFF
    output += b"K3DONE\0\0" + struct.pack("<QII", len(items), checksum, 0)
    return bytes(output)


class RouteTraceTests(unittest.TestCase):
    def read(self, data: bytes):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.bin"
            path.write_bytes(data)
            return route_trace.read_route_trace(path)

    def test_valid_trace(self):
        result = self.read(trace_bytes())
        self.assertEqual(result["header"]["top_k"], 2)
        self.assertEqual(result["header"]["model_size"], 784318432)
        self.assertEqual(result["header"]["published_gguf_revision"], "published-gguf-revision")
        self.assertEqual([record["layer"] for record in result["records"]], [1, 2])
        self.assertEqual(result["records"][0]["selected_experts"], [0, 1])

    def test_minimal_real_fixture(self):
        result = route_trace.read_route_trace(REAL_FIXTURE)
        header = result["header"]
        self.assertEqual(header["model_name"], "Kimi-K3-0.40B-F16.gguf")
        self.assertEqual(header["model_size"], 784318432)
        self.assertEqual(
            header["model_sha256"],
            "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
        )
        self.assertEqual(
            header["llama_cpp_revision"], "92c4627e19219134ed42e24aa84a1514bf3dffa3"
        )
        self.assertEqual(len(result["records"]), 252)
        self.assertEqual(result["records"][0]["phase"], "PREFILL")
        self.assertEqual(result["records"][-1]["phase"], "DECODE")
        self.assertEqual(
            sorted({record["layer"] for record in result["records"]}), list(range(1, 8))
        )

    def test_unsupported_version(self):
        with self.assertRaisesRegex(route_trace.RouteTraceError, "unsupported schema version"):
            self.read(trace_bytes(version=2))

    def test_truncated_trailer(self):
        with self.assertRaisesRegex(route_trace.RouteTraceError, "completion trailer"):
            self.read(trace_bytes()[:-5])

    def test_checksum_mismatch(self):
        data = bytearray(trace_bytes())
        data[20] ^= 1
        with self.assertRaisesRegex(route_trace.RouteTraceError, "checksum mismatch"):
            self.read(data)

    def test_impossible_count(self):
        with self.assertRaisesRegex(route_trace.RouteTraceError, "top-k is impossible"):
            self.read(trace_bytes(top_k=0))

    def test_noncanonical_order(self):
        data = trace_bytes(records=[(0, 1, 2, 0), (0, 1, 1, 0)])
        with self.assertRaisesRegex(route_trace.RouteTraceError, "canonical"):
            self.read(data)


if __name__ == "__main__":
    unittest.main()
