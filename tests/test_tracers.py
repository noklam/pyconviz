"""Comprehensive tests for asyncio, GIL threading, and free-threaded Python tracers."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from pathlib import Path

import pytest

from pyconvis.asyncio_tracer import trace_asyncio
from pyconvis.runner import (
    compare_gil_subprocess,
    detect_script_mode,
    find_freethreaded_python,
    trace_script_subprocess,
)
from pyconvis.schema import validate_trace_dict
from pyconvis.threading_tracer import trace_threading


def test_asyncio_hello_and_queue_correlation() -> None:
    async def prog() -> None:
        ch: asyncio.Queue[int] = asyncio.Queue()

        async def sender() -> None:
            await asyncio.sleep(0.01)
            await ch.put(42)

        t = asyncio.create_task(sender())
        val = await ch.get()
        assert val == 42
        await t

    doc = trace_asyncio(prog, title="Hello Asyncio")
    data = doc.to_dict()
    validate_trace_dict(data)

    assert doc.metadata.model == "asyncio"
    assert len(doc.units) == 2
    child = [u for u in doc.units if u.id != "main"][0]
    assert child.parent_id == "main"
    assert child.depth == 1
    assert not child.leaked

    # Single-threaded event loop invariant: peak parallel CPU units <= 1
    assert doc.metrics.peak_parallel_cpu_units == 1
    q_events = [e for e in doc.events if e.kind == "queue_message"]
    assert len(q_events) == 1
    assert q_events[0].from_unit == child.id
    assert q_events[0].to_unit == "main"
    assert q_events[0].value == "42"


def test_asyncio_blocking_loop_detection() -> None:
    async def prog() -> None:
        async def bad_blocker() -> None:
            time.sleep(0.03)  # Synchronous block inside async def!

        async def polite_sleeper() -> None:
            await asyncio.sleep(0.01)

        await asyncio.gather(bad_blocker(), polite_sleeper())

    doc = trace_asyncio(prog, title="Blocking Loop", blocking_threshold_ms=15.0)
    validate_trace_dict(doc.to_dict())
    blocking_spans = [s for s in doc.spans if s.state == "blocking_loop"]
    assert len(blocking_spans) >= 1
    assert doc.metrics.total_blocking_loop_ms >= 20.0


def test_asyncio_leak_detection() -> None:
    async def prog() -> None:
        async def leaked_worker() -> None:
            while True:
                await asyncio.sleep(0.05)

        asyncio.create_task(leaked_worker())
        await asyncio.sleep(0.015)

    doc = trace_asyncio(prog, title="Asyncio Leak")
    validate_trace_dict(doc.to_dict())
    assert doc.metrics.leaked_units == 1
    leaked = [u for u in doc.units if u.leaked]
    assert len(leaked) == 1
    assert "leaked_worker" in leaked[0].name


def test_asyncio_lock_and_event_handoffs() -> None:
    async def prog() -> None:
        lock = asyncio.Lock()
        ev = asyncio.Event()

        async def holder() -> None:
            async with lock:
                await asyncio.sleep(0.015)
            ev.set()

        async def waiter() -> None:
            await asyncio.sleep(0.003)
            async with lock:
                pass
            await ev.wait()

        await asyncio.gather(holder(), waiter())

    doc = trace_asyncio(prog, title="Asyncio Sync")
    validate_trace_dict(doc.to_dict())
    lock_events = [e for e in doc.events if e.kind == "lock_handoff"]
    assert len(lock_events) == 1


def test_threading_queue_lock_and_io() -> None:
    def prog() -> None:
        q: queue.Queue[str] = queue.Queue()
        lock = threading.Lock()

        def producer(idx: int) -> None:
            with lock:
                time.sleep(0.01)
            q.put(f"msg-{idx}")

        threads = [threading.Thread(target=producer, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        got = [q.get() for _ in range(2)]
        for t in threads:
            t.join()
        assert len(got) == 2

    doc = trace_threading(prog, title="Threading Sync")
    validate_trace_dict(doc.to_dict())
    assert doc.metadata.model == "threading"
    assert len(doc.units) == 3
    assert len([e for e in doc.events if e.kind == "queue_message"]) == 2
    assert len([e for e in doc.events if e.kind == "lock_handoff"]) >= 1
    assert doc.metrics.total_io_wait_ms > 10.0


def test_threading_leak_detection(tmp_path: Path) -> None:
    script = tmp_path / "thread_leak.py"
    script.write_text(
        "import threading, time\n"
        "def runaway():\n"
        "    while True:\n"
        "        time.sleep(0.05)\n"
        "t = threading.Thread(target=runaway, daemon=True)\n"
        "t.start()\n"
        "time.sleep(0.015)\n",
        encoding="utf-8",
    )
    doc = trace_script_subprocess(script, mode="threading")
    validate_trace_dict(doc.to_dict())
    assert doc.metrics.leaked_units == 1


def test_gil_vs_freethreaded_comparison(tmp_path: Path) -> None:
    ft_py = find_freethreaded_python()
    if ft_py is None:
        pytest.skip("Free-threaded Python not installed")

    script = tmp_path / "cpu_pool.py"
    script.write_text(
        "import threading\n"
        "def burn_cpu():\n"
        "    acc = 0\n"
        "    for step in range(600):\n"
        "        s = step & 0xff\n"
        "        for j in range(2500):\n"
        "            acc = (acc ^ (j & 0xff) ^ s) & 0xff\n"
        "    return acc\n"
        "threads = [threading.Thread(target=burn_cpu) for _ in range(4)]\n"
        "for t in threads:\n"
        "    t.start()\n"
        "for t in threads:\n"
        "    t.join()\n",
        encoding="utf-8",
    )
    assert detect_script_mode(script) == "threading"

    comp = compare_gil_subprocess(script, python_executable=ft_py)
    comp_dict = comp.to_dict()
    validate_trace_dict(comp_dict)

    assert comp.left.metadata.gil_enabled is True
    assert comp.right.metadata.gil_enabled is False

    # Under GIL=1, workers experience significant waiting_gil intervals;
    # under GIL=0, effective parallelism scales well past 1.8x on multi-core hardware!
    assert comp.left.metrics.gil_wait_pct > 25.0
    assert comp.right.metrics.gil_wait_pct == 0.0
    assert comp.right.metrics.peak_parallel_cpu_units >= 3
    assert comp.right.metrics.effective_parallelism > comp.left.metrics.effective_parallelism * 1.5
    assert comp.right.metadata.duration_ns < comp.left.metadata.duration_ns
