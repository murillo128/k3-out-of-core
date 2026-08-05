#!/usr/bin/env python3
"""Ordinary synchronous and bounded-threaded positional reads for Phase 12P."""
from __future__ import annotations

import hashlib
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Operation:
    ordinal: int
    offset: int
    length: int
    expected_sha256: str


def _pread(fd: int, operation: Operation, cancel: threading.Event) -> dict[str, object]:
    digest = hashlib.sha256(); cursor = 0; retries = 0; started = time.monotonic_ns()
    while cursor < operation.length:
        if cancel.is_set():
            raise RuntimeError("read cancelled")
        try:
            block = os.pread(fd, min(1 << 20, operation.length - cursor), operation.offset + cursor)
        except InterruptedError:
            retries += 1; continue
        if not block:
            raise EOFError("zero progress before requested length")
        digest.update(block); cursor += len(block)
    ended = time.monotonic_ns()
    if digest.hexdigest() != operation.expected_sha256:
        raise ValueError("completed operation checksum mismatch")
    return {"ordinal": operation.ordinal, "started_ns": started, "ended_ns": ended, "bytes": cursor, "retries": retries}


def effective_qd(intervals: Iterable[dict[str, object]], requested_qd: int) -> dict[str, object]:
    events: list[tuple[int, int]] = []
    maximum = 0
    for interval in intervals:
        events.append((int(interval["started_ns"]), 1)); events.append((int(interval["ended_ns"]), -1))
    events.sort(key=lambda item: (item[0], item[1]))  # completions before starts at equal time
    histogram: dict[int, int] = {}; active = 0; previous = events[0][0] if events else 0
    for timestamp, delta in events:
        if timestamp > previous:
            histogram[active] = histogram.get(active, 0) + timestamp - previous
        active += delta; maximum = max(maximum, active); previous = timestamp
    steady = sum(duration for qd, duration in histogram.items() if qd > 0)
    concurrent = sum(duration for qd, duration in histogram.items() if qd >= 2)
    fraction = concurrent / steady if steady else 0.0
    supported = requested_qd == 1 or (maximum == requested_qd and fraction >= 0.90)
    return {
        "requested_qd": requested_qd, "maximum_concurrency": maximum,
        "steady_active_ns": steady, "fraction_at_least_two": fraction,
        "histogram_ns": {str(key): value for key, value in sorted(histogram.items())},
        "status": "SUPPORTED" if supported else "UNSUPPORTED_EFFECTIVE_QD",
    }


def read_sync(path: Path, operations: Iterable[Operation]) -> dict[str, object]:
    cancel = threading.Event(); intervals: list[dict[str, object]] = []
    fd = os.open(path, os.O_RDONLY)
    try:
        for operation in operations:
            intervals.append(_pread(fd, operation, cancel))
    finally:
        os.close(fd)
    return {"api": "synchronous_buffered_pread", "worker_count": 1, "intervals": intervals, "effective_qd": effective_qd(intervals, 1)}


def read_threaded(path: Path, operations: Iterable[Operation], requested_qd: int, workers: int, cancel: threading.Event | None = None) -> dict[str, object]:
    if requested_qd <= 0 or workers < requested_qd:
        raise ValueError("workers must be at least requested QD")
    stop = cancel or threading.Event(); semaphore = threading.Semaphore(requested_qd)
    intervals: list[dict[str, object]] = []; futures: list[Future[dict[str, object]]] = []
    fd = os.open(path, os.O_RDONLY)

    def bounded(operation: Operation) -> dict[str, object]:
        with semaphore:
            return _pread(fd, operation, stop)

    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="phase12p-pread") as pool:
            futures = [pool.submit(bounded, operation) for operation in operations]
            try:
                for future in as_completed(futures):
                    intervals.append(future.result())
            except BaseException:
                stop.set()
                for future in futures:
                    future.cancel()
                raise
    finally:
        os.close(fd)
    intervals.sort(key=lambda item: item["ordinal"])
    return {
        "api": "threaded_buffered_pread", "worker_count": workers, "intervals": intervals,
        "effective_qd": effective_qd(intervals, requested_qd),
    }


def physical_order(operations: Iterable[Operation]) -> list[Operation]:
    return sorted(operations, key=lambda item: (item.offset, item.ordinal))


def locality_window_8(operations: Iterable[Operation]) -> list[Operation]:
    pending = list(operations); output: list[Operation] = []
    while pending:
        index = min(range(min(8, len(pending))), key=lambda candidate: (pending[candidate].offset, pending[candidate].ordinal))
        output.append(pending.pop(index))
    return output
