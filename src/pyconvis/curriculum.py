"""Focused, beginner-friendly 7-lesson Python Concurrency & GIL story arc."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from pyconvis.runner import (
    compare_gil_subprocess,
    find_freethreaded_python,
    trace_script_subprocess,
)
from pyconvis.schema import (
    ComparisonDocument,
    ComparisonPane,
    TraceDocument,
    validate_trace_dict,
)


@dataclass
class ScriptVariant:
    filename: str
    tab_label: str
    pane_label: str
    accent: str = "#38bdf8"
    record_gil_handoffs: bool = False


@dataclass
class LessonSpec:
    id: str
    number: str
    title: str
    track: str
    badge: str
    kind: Literal["single", "comparison"]
    trace_file: str
    default_camera: Literal["perspective", "top", "side"]
    layout_hint: Literal["radial", "ring", "pipeline", "tree"]
    summary: str
    concept_markdown: str
    what_to_notice: list[str]
    legend_keys: list[str]
    primary_script: str = ""
    variants: list[ScriptVariant] = field(default_factory=list)
    build_mode: Literal[
        "asyncio",
        "threading_gil1",
        "threading_gil0",
        "compare_gil",
        "compare_variants",
    ] = "asyncio"
    left_label: str | None = None
    right_label: str | None = None


CURRICULUM_LESSONS: list[LessonSpec] = [
    # -------------------------------------------------------------------------
    # PART 1: FOUNDATIONS — HOW TO READ 3D SPACETIME
    # -------------------------------------------------------------------------
    LessonSpec(
        id="hello-world",
        number="01",
        title="How to Read Spacetime: Asyncio vs Thread",
        track="1. Foundations",
        badge="Start Here",
        kind="comparison",
        trace_file="01_hello_world.json",
        default_camera="perspective",
        layout_hint="radial",
        summary="Time flows top-to-bottom. Watch `main` start one helper worker, wait for a message (`42`), and wrap up—in `asyncio` vs `threading`.",
        concept_markdown=(
            "Think of each vertical pillar as a **worker's timeline** flowing **downward** from `0ms` at the top.\n\n"
            "- **Center (`main`)**: Your main Python program.\n"
            "- **Blue horizontal bridge**: `main` starts a helper (`sender #1`), and reconnects at the bottom when it finishes.\n"
            "- **Pink arrow (`42`)**: `sender #1` puts the number `42` into a shared Queue so `main` can read it.\n\n"
            "Both panes do the exact same job: **Left** uses a lightweight `asyncio` Task (all on 1 OS thread), while **Right** asks the operating system for a separate `Thread`."
        ),
        what_to_notice=[
            "Read from top (`0ms`) downward: `main` branches out to start `sender #1`.",
            "While `sender #1` waits in blue (`Waiting on I/O`), `main` waits in purple (`Waiting for Queue`).",
            "When `42` shoots back to `main`, both finish cleanly.",
        ],
        legend_keys=["waiting_io", "waiting_sync", "queue_message"],
        variants=[
            ScriptVariant(
                "01_hello_asyncio.py",
                "asyncio (1 Thread)",
                "asyncio Task",
                "#38bdf8",
            ),
            ScriptVariant(
                "01b_hello_threading.py",
                "threading (OS Thread)",
                "threading.Thread",
                "#a855f7",
            ),
        ],
        build_mode="compare_variants",
    ),
    LessonSpec(
        id="recurrent-timers",
        number="02",
        title="Waiting Over Time: The Timer Spiral",
        track="1. Foundations",
        badge="3D vs Top-Down",
        kind="single",
        trace_file="02_recurrent_timers.json",
        default_camera="perspective",
        layout_hint="radial",
        summary="Sixteen small timer tasks run one after another around `main`, carving a spiral staircase down through time.",
        concept_markdown=(
            "Before comparing threads and `asyncio`, practice using the **three camera views** (`1`, `2`, `3` in the top bar) on this simple spiral!\n\n"
            "Here `main` starts a short timer task, waits for its `1` signal, and repeats 16 times. Because each timer starts *after* the previous one finishes, they step downward like a **spiral staircase**."
        ),
        what_to_notice=[
            "**3D View `[1]`**: Drag to rotate and see the staircase step downward one timer at a time.",
            "**Top-Down `[2]`**: Collapses time so you see *who talks to whom*—16 timers arranged like a clock sending `1` to `main`.",
            "**Side Time `[3]`**: Flattens depth into a clean top-to-bottom timeline.",
        ],
        legend_keys=["waiting_io", "waiting_sync", "queue_message"],
        primary_script="02_timers_asyncio.py",
        build_mode="asyncio",
    ),
    LessonSpec(
        id="ping-pong-ring",
        number="03",
        title="Fair Turn-Taking: 12-Player Ping-Pong Ring",
        track="1. Foundations",
        badge="Queue Order",
        kind="single",
        trace_file="03_ping_pong_ring.json",
        default_camera="top",
        layout_hint="ring",
        summary="Twelve tasks all listen on the same Queue—yet the ball passes around a neat circle instead of bouncing randomly!",
        concept_markdown=(
            "If 12 workers all wait on the **same Queue** (`await table.get()`), why doesn't the counter jump around chaotically?\n\n"
            "Because Python's Queue keeps waiting workers in a **First-In, First-Out (FIFO) line**—just like a ticket counter. As soon as `player #1` passes the ball, it goes to the back of the line, creating a fair 12-player ring!"
        ),
        what_to_notice=[
            "In **Top-Down `[2]`**, watch the pink arrows trace a clean 12-sided circle.",
            "Click **3D View `[1]`** to see that circle stretch downward into a corkscrew over 24 passes!",
        ],
        legend_keys=["waiting_io", "waiting_sync", "queue_message"],
        primary_script="04_ping_pong_ring.py",
        build_mode="asyncio",
    ),
    # -------------------------------------------------------------------------
    # PART 2: WHY PYTHON THREADS BEHAVE DIFFERENTLY — THE GIL & NO-GIL
    # -------------------------------------------------------------------------
    LessonSpec(
        id="gil-vs-freethreaded",
        number="04",
        title="Why Python Threads Don't Speed Up CPU Work (The GIL)",
        track="2. Python Threads & The GIL",
        badge="GIL vs No-GIL",
        kind="comparison",
        trace_file="04_cpu_gil_vs_nogil.json",
        default_camera="side",
        layout_hint="radial",
        summary="The big surprise in Python: 4 threads doing pure-Python math take turns one by one under the GIL—until Free-Threaded Python turns the GIL off!",
        concept_markdown=(
            "In most languages, starting 4 threads on a 4-core laptop runs math 4× faster. **Why didn't standard Python?**\n\n"
            "To keep memory bookkeeping simple, standard Python uses a **Global Interpreter Lock (GIL)**—imagine **4 workers sharing 1 microphone**:\n\n"
            "- **Left (Standard Python — GIL ON)**: Only **1 thread** can run Python code (green) at any instant! Every ~2ms Python passes the microphone, leaving the other 3 threads stuck in **orange (`Waiting for GIL`)**.\n"
            "- **Right (Free-Threaded Python 3.14t — GIL OFF)**: Python's new free-threaded mode removes the GIL. All **4 threads glow solid green at the same time** on 4 CPU cores—finishing in nearly a quarter of the time!"
        ),
        what_to_notice=[
            "Scan horizontally across the **Left** view: at any height, **only ONE worker is green** while the other 3 are orange!",
            "Look at the **Right** view (`GIL OFF`): all 4 workers burn bright green side-by-side and finish way higher up on the millisecond ruler.",
        ],
        legend_keys=["running_cpu", "waiting_gil", "queue_message"],
        primary_script="10_cpu_gil_vs_nogil.py",
        build_mode="compare_gil",
        left_label="Standard Python (GIL ON)",
        right_label="Free-Threaded 3.14t (GIL OFF)",
    ),
    LessonSpec(
        id="io-threads-vs-asyncio",
        number="05",
        title="When Python Threads DO Work: Waiting on I/O vs Asyncio",
        track="2. Python Threads & The GIL",
        badge="Threads vs Async",
        kind="comparison",
        trace_file="05_io_threads_vs_asyncio.json",
        default_camera="side",
        layout_hint="radial",
        summary="If standard Python has a GIL, why do threads still speed up web requests? Because waiting on network or disk drops the GIL!",
        concept_markdown=(
            "If the GIL only lets 1 thread run Python code at a time, why have Python developers used `threading` for web APIs and downloads for years?\n\n"
            "Because **waiting on external I/O** (network responses, database queries, disk reads) **drops the GIL microphone**!\n\n"
            "- **Left (`threading` with GIL ON)**: All 10 threads send their request, drop the GIL, and wait in blue **at the same time**.\n"
            "- **Right (`asyncio`)**: Gets the **exact same speedup on just 1 OS thread**! Instead of creating 10 heavy OS threads, 1 thread juggles all 10 waits with `await`—which is why `asyncio` easily scales to 10,000+ connections."
        ),
        what_to_notice=[
            "Both 10-worker runs finish at almost the **same height (~75 ms)** instead of `10 × 55 ms = 550 ms`!",
            "`threading` needed **10 extra OS threads**, while `asyncio` did the exact same waiting on **1 thread**.",
        ],
        legend_keys=["waiting_io", "running_cpu", "queue_message"],
        variants=[
            ScriptVariant(
                "11a_io_threading.py",
                "threading (10 Threads)",
                "GIL Threading (10 OS Threads)",
                "#38bdf8",
            ),
            ScriptVariant(
                "11b_io_asyncio.py",
                "asyncio (1 Thread)",
                "Asyncio (10 Tasks, 1 Thread)",
                "#10f5a0",
            ),
        ],
        build_mode="compare_variants",
    ),
    LessonSpec(
        id="gil-across-threads-vs-asyncio",
        number="06",
        title="One GIL, Two Models: Asyncio Tasks vs Thread GIL Handoffs",
        track="2. Python Threads & The GIL",
        badge="1 GIL: Async vs Thread",
        kind="comparison",
        trace_file="06_gil_asyncio_vs_threads.json",
        default_camera="side",
        layout_hint="radial",
        summary="Both run under 1 GIL! Asyncio holds 1 GIL on 1 thread across tasks (switching only at await); threading passes 1 GIL Lock across OS threads.",
        concept_markdown=(
            "A common myth is that `asyncio` \"bypasses\" the GIL, or that the GIL stops threads from mixing I/O and CPU. Watch both run **3 fast I/O workers + 1 pure-Python CPU worker**:\n\n"
            "- **Left (`asyncio` — 1 Thread Holds 1 GIL)**: Everything lives on **1 OS thread**, so that thread holds the **1 GIL Lock** continuously (`0%` GIL wait, zero cross-thread GIL handoffs). Tasks switch **cooperatively** *only* at `await`. Because `cpu_task` crunches ~50 ms of math with no `await`, ready `io_task`s sit stalled in slate (`Paused at await`) until `cpu_task` yields!\n"
            "- **Right (`threading` — 1 GIL Passed Across Threads)**: Each worker is an OS thread competing for **1 shared GIL Lock**. While `io_thread`s wait on OS I/O (blue, outside GIL), `cpu_thread` holds the GIL. As soon as an I/O response arrives, CPython preempts `cpu_thread` and fires a golden **`GIL` Lock Handoff** arrow so I/O finishes right away—while **still running at most 1 green CPU bar at a time**!"
        ),
        what_to_notice=[
            "**Left (`asyncio`)**: Zero orange GIL waits and zero golden `GIL` arrows—1 thread holds 1 GIL! Ready `io_task #1..#3` turn slate (`Paused at await`) until `cpu_task` hits `await`.",
            "**Right (`threading`)**: Follow the golden **`GIL`** arrows jumping across threads (`cpu_thread ↔ io_thread #1..#3`) so I/O responses finish ~20 ms earlier while `cpu_thread` waits in orange.",
            "Switch between **Side Time `[3]`** (millisecond timeline) and **Top-Down `[2]`** (spatial GIL handoff map across threads vs 1-thread `asyncio`)!",
        ],
        legend_keys=[
            "running_cpu",
            "blocking_loop",
            "waiting_io",
            "waiting_gil",
            "suspended_await",
            "gil_handoff",
            "queue_message",
        ],
        variants=[
            ScriptVariant(
                "15a_mixed_asyncio.py",
                "asyncio (1 Thread · 1 GIL)",
                "Asyncio: 1 Thread Holds 1 GIL",
                "#38bdf8",
            ),
            ScriptVariant(
                "15b_mixed_threading.py",
                "threading (GIL Across Threads)",
                "Threading: 1 GIL Across Threads",
                "#f59e0b",
                record_gil_handoffs=True,
            ),
        ],
        build_mode="compare_variants",
    ),
    LessonSpec(
        id="freethreaded-lock-contention",
        number="07",
        title="Without the GIL: Shared Data Still Needs a Lock",
        track="2. Python Threads & The GIL",
        badge="No-GIL + Lock",
        kind="single",
        trace_file="07_freethreaded_lock.json",
        default_camera="perspective",
        layout_hint="radial",
        summary="Turning off the GIL unlocks multi-core speed—but if threads update a shared list under a `Lock`, they line up single-file again!",
        concept_markdown=(
            "Free-threaded Python (`GIL OFF`) lets all 4 workers run math in parallel—**until they touch shared data**.\n\n"
            "To safely update a shared `ledger` list without corrupting it, workers wrap that section in `with ledger_lock:`. "
            "Notice what happens: workers run **green in parallel** during private math, then hit a traffic jam in **purple (`Waiting for Lock`)** as yellow arrows pass the lock one by one!"
        ),
        what_to_notice=[
            "At the top of each round, all 4 pillars are **green simultaneously** (parallel CPU!).",
            "As soon as they enter `with ledger_lock:`, 3 workers turn **purple** and wait for the yellow **Lock Handoff** arrow.",
            "Rule of thumb: do heavy work *outside* `with lock:`, and keep locked sections tiny.",
        ],
        legend_keys=["running_cpu", "waiting_sync", "lock_handoff"],
        primary_script="12_freethreaded_lock_contention.py",
        build_mode="threading_gil0",
    ),
    # -------------------------------------------------------------------------
    # PART 3: ASYNCIO'S GOLDEN RULE — BLOCKING vs NATIVE AWAIT vs OFFLOAD
    # -------------------------------------------------------------------------
    LessonSpec(
        id="blocking-vs-async-vs-offload",
        number="08",
        title="Blocking Asyncio: Trap vs. Native Async vs. Offload",
        track="3. Asyncio: Waiting vs Offloading",
        badge="3-Way Compare",
        kind="comparison",
        trace_file="08_blocking_3way.json",
        default_camera="side",
        layout_hint="radial",
        summary="Compare all 3 ways of handling a `slow_task` alongside 4 live heartbeats: blocking the loop, native `await`, and `asyncio.to_thread`!",
        concept_markdown=(
            "Because `asyncio` runs every task on **1 single thread**, a task only lets others run when it hits `await`. What happens when `request_task` needs to run a slow operation alongside 4 `heartbeat` tasks?\n\n"
            "- **1. Trap (`slow_task()`)**: Calls a normal synchronous function directly. Without `await`, Python's single thread is hijacked (**thick red bar**)—all 4 heartbeats **freeze completely**!\n"
            "- **2. Native Async (`await slow_async_task()`)**: Uses an async-native library (timers, async HTTP/DB). The OS watches the wait outside Python—**0 extra threads** are created, and heartbeats never miss a beat!\n"
            "- **3. Thread Offload (`await asyncio.to_thread(slow_task)`)**: When a library has **no `async` version** (legacy sync code, file I/O, heavy work), `to_thread()` delegates `slow_task` to a **real background helper thread** (gold `thread:slow_task` node) so the event loop stays free!"
        ),
        what_to_notice=[
            "**Left (Trap)**: During the thick red **`Blocking Asyncio Loop!`** bar, **zero** pink heartbeat arrows fire anywhere!",
            "**Middle (Native Async)**: Best choice when supported—heartbeats keep firing smoothly with **zero extra threads**.",
            "**Right (`to_thread` Offload)**: Spot the **gold `thread:slow_task` branch**! It runs the synchronous `slow_task` on a separate OS thread and sends a green result arrow back when done.",
        ],
        legend_keys=["blocking_loop", "waiting_io", "queue_message", "task_result"],
        variants=[
            ScriptVariant(
                "13a_blocking_bad.py",
                "1. Trap: slow_task()",
                "1. Trap: Sync slow_task()",
                "#ff2e63",
            ),
            ScriptVariant(
                "13b_native_async.py",
                "2. Native: await slow_async_task()",
                "2. Native Async (1 Thread)",
                "#38bdf8",
            ),
            ScriptVariant(
                "13c_offload_to_thread.py",
                "3. Offload: to_thread(slow_task)",
                "3. to_thread() (+Helper Thread)",
                "#10f5a0",
            ),
        ],
        build_mode="compare_variants",
    ),
]


def _compute_lesson_takeaways(
    spec: LessonSpec, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    if spec.id == "hello-world":
        p0, p1 = payload["panes"][0]["trace"], payload["panes"][1]["trace"]
        return [
            {
                "label": "Completion Time",
                "value": f"{p0['metrics']['duration_ms']:.0f} ms vs {p1['metrics']['duration_ms']:.0f} ms",
                "note": "Both pause briefly, pass 42 through a Queue, and rejoin main.",
                "tone": "sky",
            },
            {
                "label": "OS Threads Created",
                "value": "1 Thread vs 2 Threads",
                "note": "asyncio switches tasks inside 1 thread; threading spawns an OS thread.",
            },
        ]

    if spec.id == "recurrent-timers":
        m = payload["metrics"]
        return [
            {
                "label": "Total Time (16 Sequential Timers)",
                "value": f"{m['duration_ms']:.0f} ms",
                "note": "Timers run one after another, so their wait times add up vertically.",
                "tone": "sky",
            },
            {
                "label": "Messages Passed to main",
                "value": f"{m['message_count']} ticks",
                "note": "Switch between 3D [1], Top-Down [2], and Side [3] to compare views.",
            },
        ]

    if spec.id == "ping-pong-ring":
        m = payload["metrics"]
        return [
            {
                "label": "Workers Sharing 1 Queue",
                "value": "12 tasks",
                "note": "Python's Queue wakes waiting tasks in fair First-In, First-Out order.",
                "tone": "sky",
            },
            {
                "label": "Total Passes Around Ring",
                "value": f"{m['message_count']} passes",
                "note": "Switch to 3D View [1] to see the circle wind downward like a spring!",
            },
        ]

    if spec.id == "gil-vs-freethreaded":
        l_m = payload["panes"][0]["trace"]["metrics"]
        r_m = payload["panes"][1]["trace"]["metrics"]
        ratio = payload["speedup_factor"]
        return [
            {
                "label": "4-Thread CPU Finish Time",
                "value": f"{l_m['duration_ms']:.0f} ms → {r_m['duration_ms']:.0f} ms ({ratio}x faster)",
                "note": "Removing the GIL lets all 4 CPU cores run Python code simultaneously.",
                "tone": "emerald",
            },
            {
                "label": "Time Spent Waiting on GIL",
                "value": f"{l_m['gil_wait_pct']:.0f}% → 0%",
                "note": "With GIL ON, 3 out of 4 threads sit stalled in orange at any moment.",
                "tone": "amber",
            },
        ]

    if spec.id == "io-threads-vs-asyncio":
        l_m = payload["panes"][0]["trace"]["metrics"]
        r_m = payload["panes"][1]["trace"]["metrics"]
        return [
            {
                "label": "10 Slow I/O Calls (~55ms each)",
                "value": f"{l_m['duration_ms']:.0f} ms vs {r_m['duration_ms']:.0f} ms",
                "note": "Instead of 550 ms sequentially, both overlap all 10 waits!",
                "tone": "emerald",
            },
            {
                "label": "Why Both Work",
                "value": "10 Threads vs 1 Thread",
                "note": "Waiting on I/O releases the GIL—while asyncio needs only 1 thread.",
                "tone": "sky",
            },
        ]

    if spec.id == "gil-across-threads-vs-asyncio":
        a_tr = payload["panes"][0]["trace"]
        t_tr = payload["panes"][1]["trace"]
        gil_passes = sum(1 for e in t_tr["events"] if e["kind"] == "gil_handoff")
        a_io_ms = min(
            (e["recv_ns"] / 1_000_000 for e in a_tr["events"] if e["value"].startswith("io#")),
            default=a_tr["metrics"]["duration_ms"],
        )
        t_io_ms = min(
            (e["recv_ns"] / 1_000_000 for e in t_tr["events"] if e["value"].startswith("io#")),
            default=t_tr["metrics"]["duration_ms"],
        )
        return [
            {
                "label": "GIL Lock Handoffs Across Threads",
                "value": f"0 (1 Thread) vs {gil_passes} GIL passes",
                "note": "asyncio holds 1 GIL on 1 thread; threading passes 1 GIL Lock across threads.",
                "tone": "amber",
            },
            {
                "label": "First Fast I/O Response",
                "value": f"{a_io_ms:.0f} ms vs {t_io_ms:.0f} ms",
                "note": "Non-awaited CPU stalls asyncio I/O; threads preempt CPU at GIL slices.",
                "tone": "sky",
            },
            {
                "label": "Simultaneous Python CPU",
                "value": "1 worker max in both",
                "note": "Neither overlaps pure-Python CPU bytecode because both share 1 GIL!",
                "tone": "emerald",
            },
        ]

    if spec.id == "freethreaded-lock-contention":
        m = payload["metrics"]
        return [
            {
                "label": "Parallel CPU Utilization",
                "value": f"{m['effective_parallelism']:.1f}x cores",
                "note": "4 threads run math in parallel, but queue single-file inside `with lock:`.",
                "tone": "emerald",
            },
            {
                "label": "Sequential Lock Handoffs",
                "value": f"{m['sync_handoff_count']} passes",
                "note": "Keep locked critical sections short so threads don't wait in purple!",
                "tone": "amber",
            },
        ]

    if spec.id == "blocking-vs-async-vs-offload":
        p0 = payload["panes"][0]["trace"]["metrics"]
        p1 = payload["panes"][1]["trace"]["metrics"]
        p2 = payload["panes"][2]["trace"]["metrics"]
        return [
            {
                "label": "Time Asyncio Loop Was Frozen",
                "value": f"{p0['total_blocking_loop_ms']:.0f} ms → 0 ms → 0 ms",
                "note": "Direct `slow_task()` freezes every heartbeat; `await` & `to_thread` never freeze!",
                "tone": "crimson",
            },
            {
                "label": "Native Async vs to_thread()",
                "value": f"{p1['duration_ms']:.0f} ms (1 thread) vs {p2['duration_ms']:.0f} ms (+helper thread)",
                "note": "Prefer native `await` when available; use `to_thread()` to wrap sync functions.",
                "tone": "emerald",
            },
        ]

    return []


def build_all_examples(
    *,
    examples_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Execute focused 7-lesson curriculum scripts, validate traces, and write manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    # Clean stale .json trace files so only active lessons remain
    for old_json in output_dir.glob("*.json"):
        old_json.unlink()

    ft_python = find_freethreaded_python()
    manifest_lessons: list[dict[str, Any]] = []

    for spec in CURRICULUM_LESSONS:
        code_files: list[dict[str, str]] = []

        if spec.build_mode == "asyncio":
            p1 = examples_dir / spec.primary_script
            doc = trace_script_subprocess(
                p1,
                mode="asyncio",
                gil=1,
                title=spec.title,
                layout_hint=spec.layout_hint,
            )
            payload = doc.to_dict()
            code_files.append(
                {
                    "filename": spec.primary_script,
                    "label": spec.primary_script,
                    "code": p1.read_text(encoding="utf-8"),
                }
            )

        elif spec.build_mode == "threading_gil1":
            p1 = examples_dir / spec.primary_script
            doc = trace_script_subprocess(
                p1,
                mode="threading",
                gil=1,
                python_executable=ft_python,
                title=spec.title,
                layout_hint=spec.layout_hint,
            )
            payload = doc.to_dict()
            code_files.append(
                {
                    "filename": spec.primary_script,
                    "label": spec.primary_script,
                    "code": p1.read_text(encoding="utf-8"),
                }
            )

        elif spec.build_mode == "threading_gil0":
            if ft_python is None:
                raise RuntimeError(
                    f"Free-threaded Python (3.14t) required to build lesson {spec.id}."
                )
            p1 = examples_dir / spec.primary_script
            doc = trace_script_subprocess(
                p1,
                mode="threading",
                gil=0,
                python_executable=ft_python,
                title=spec.title,
                layout_hint=spec.layout_hint,
            )
            payload = doc.to_dict()
            code_files.append(
                {
                    "filename": spec.primary_script,
                    "label": spec.primary_script,
                    "code": p1.read_text(encoding="utf-8"),
                }
            )

        elif spec.build_mode == "compare_gil":
            p1 = examples_dir / spec.primary_script
            comp = compare_gil_subprocess(
                p1,
                python_executable=ft_python,
                title=spec.title,
                subtitle=spec.summary,
                layout_hint=spec.layout_hint,
            )
            if spec.left_label:
                comp.left_label = spec.left_label
            if spec.right_label:
                comp.right_label = spec.right_label
            payload = comp.to_dict()
            code_files.append(
                {
                    "filename": spec.primary_script,
                    "label": spec.primary_script,
                    "code": p1.read_text(encoding="utf-8"),
                }
            )

        elif spec.build_mode == "compare_variants":
            panes: list[ComparisonPane] = []
            traced_docs: list[TraceDocument] = []
            for v in spec.variants:
                vp = examples_dir / v.filename
                is_threading = "threading" in v.filename
                v_doc = trace_script_subprocess(
                    vp,
                    mode="auto",
                    gil=1,
                    python_executable=ft_python if is_threading else None,
                    title=v.pane_label,
                    layout_hint=spec.layout_hint,
                    record_gil_handoffs=v.record_gil_handoffs,
                )
                traced_docs.append(v_doc)
                code_files.append(
                    {
                        "filename": v.filename,
                        "label": v.tab_label,
                        "code": vp.read_text(encoding="utf-8"),
                    }
                )

            base_dur = traced_docs[0].metadata.duration_ns
            for idx, (v, v_doc) in enumerate(zip(spec.variants, traced_docs)):
                badge: str | None = None
                if idx > 0 and base_dur > 0:
                    sp = round(base_dur / max(1, v_doc.metadata.duration_ns), 2)
                    if sp >= 1.25:
                        badge = f"{sp}x Faster"
                panes.append(
                    ComparisonPane(
                        label=v.pane_label,
                        trace=v_doc,
                        badge=badge,
                        accent=v.accent,
                    )
                )

            comp = ComparisonDocument(
                title=spec.title,
                subtitle=spec.summary,
                panes=panes,
                default_scale_mode="wall_clock",
            )
            payload = comp.to_dict()
        else:
            raise ValueError(f"Unsupported build_mode: {spec.build_mode}")

        validate_trace_dict(payload)
        trace_out = output_dir / spec.trace_file
        trace_out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

        manifest_lessons.append(
            {
                "id": spec.id,
                "number": spec.number,
                "title": spec.title,
                "track": spec.track,
                "badge": spec.badge,
                "kind": spec.kind,
                "trace_file": spec.trace_file,
                "default_camera": spec.default_camera,
                "layout_hint": spec.layout_hint,
                "summary": spec.summary,
                "concept_markdown": spec.concept_markdown,
                "what_to_notice": spec.what_to_notice,
                "legend_keys": spec.legend_keys,
                "takeaways": _compute_lesson_takeaways(spec, payload),
                "code_files": code_files,
            }
        )

    manifest = {
        "version": "2.0",
        "lesson_count": len(manifest_lessons),
        "lessons": manifest_lessons,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest
