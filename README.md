# pyconvis — Visualizing Concurrency & Parallelism in Python

Interactive **3D WebGL Spacetime Visualizer & Real-Execution Tracer** for Python **`threading` (with GIL)**, **Free-Threaded Python (`3.13t` / `3.14t` No-GIL)**, and **`asyncio`**—inspired by Ivan Danilyuk's [*Visualizing Concurrency in Go*](https://divan.dev/posts/go_concurrency_visualize/).

Every visualization is generated from **real execution traces** of Python code (never simulated animations).

- **Read the interactive blog post:** [Visualizing Python Concurrency: Threads, the GIL, Free-Threaded 3.14t, and Asyncio](https://noklam.com/posts/visualizing-python-concurrency/)

---

## Quick Start (Run Locally with Live Code Runner)

Requires [uv](https://docs.astral.sh/uv/) (recommended so you can install free-threaded Python `3.14t` in one command).

```bash
# 1. Clone and install dependencies
git clone https://github.com/noklam/pyconviz.git
cd pyconviz
uv sync

# Optional (recommended): install free-threaded Python 3.14t for GIL=1 vs GIL=0 comparisons
uv python install 3.14t

# 2. Launch the interactive 3D Lab + "Run Live" Python playground
uv run pyconvis serve --port 8765 --open
```

Opening `http://127.0.0.1:8765` gives you:
- **Lesson Guide & Python Code tabs**: Walk through the 7 progressive visual lessons in 3D, Top-Down (`[2]`), and Side Time (`[3]`) views.
- **Run Live tab** *(local server only)*: Edit any `asyncio` or `threading` Python snippet directly in the browser, choose **Auto**, **Compare `GIL=1` vs `GIL=0` (`3.14t`)**, **`PYTHON_GIL=1`**, or **`PYTHON_GIL=0`**, and click **Run in 3D** to trace and visualize your code immediately!

---

## Command-Line Usage (`pyconvis`)

### 1. Trace any Python script (`asyncio` or `threading`)
```bash
# Run and open directly in the 3D visualizer
uv run pyconvis trace examples/04_ping_pong_ring.py --layout ring --serve --open

# Or export trace JSON to a file
uv run pyconvis trace my_script.py -o trace.json
```

### 2. Side-by-Side `PYTHON_GIL=1` vs Free-Threaded `PYTHON_GIL=0` Comparison
Runs your threaded Python script twice in isolated subprocesses on CPython `3.14t` (`PYTHON_GIL=1` vs `PYTHON_GIL=0`) and opens a synchronized Real-Time Scale comparison:
```bash
uv run pyconvis compare-gil examples/10_cpu_gil_vs_nogil.py --serve --open
```

### 3. Serve a saved `.json` trace
```bash
uv run pyconvis serve trace.json --port 8765 --open
```

---

## Included 7-Lesson Progressive Guide

1. **How to Read Spacetime (`asyncio` vs `Thread`)** — Time flows downward (`0ms → end`), `main` spawns a helper, and queue messages fly across in 3D.
2. **Waiting Over Time (Timer Spiral)** — 16 sequential timers demonstrating **3D `[1]`**, **Top-Down `[2]`**, and **Side Time `[3]`** camera projections.
3. **Fair Turn-Taking (12-Player Ping-Pong Ring)** — Why 12 workers sharing one Queue pass messages in a deterministic FIFO circle.
4. **Why Python Threads Don't Speed Up CPU Work (The GIL vs `3.14t` No-GIL)** — Side-by-side real trace showing 1-thread GIL turn-taking vs 4-core parallel execution (**~4× faster**).
5. **When Python Threads DO Work (Waiting on I/O vs `asyncio`)** — Why I/O drops the GIL, and how `asyncio` overlaps 10 slow waits on **1 single thread**.
6. **Without the GIL: Shared Data Still Needs a `Lock`** — Parallel CPU execution vs single-file `with lock:` critical sections and lock handoff arrows.
7. **Blocking `asyncio`: Trap vs. Native `await` vs. `to_thread` Offload (3-Way Compare)** — Comparing a blocking `slow_task()` (freezes the loop), native `await slow_async_task()` (`0` extra threads), and `await asyncio.to_thread(slow_task)` (`+1` helper OS thread).

---

## Rebuilding Web Assets & Running Tests

```bash
# Run Python tracer & CLI test suite
uv run --extra dev pytest -v

# Regenerate the 7 built-in real execution traces
uv run pyconvis build-examples

# Rebuild frontend bundle (local repo mode with Run Live tab)
cd web
npm install
npm run build
cp -R dist/. ../src/pyconvis/static/
```
