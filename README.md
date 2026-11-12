# Python Concurrency Visualized (`pyconvis`)

An interactive **3D Spacetime Visual Guide** for Python concurrency & parallelism—covering **`threading` with the GIL**, **Free-Threaded Python (`3.14t` No-GIL)**, and **`asyncio`**—inspired by Ivan Danilyuk's [*Visualizing Concurrency in Go*](https://divan.dev/posts/go_concurrency_visualize/).

Every scene in the guide is a **real execution trace** of Python code, presented in plain English for anyone who knows Python basics.

---

## Progressive 7-Lesson Story Arc

### 1. Foundations — How to Read 3D Spacetime
1. **How to Read Spacetime: `asyncio` vs `threading`** — Time flows downward (`0ms → end`), `main` branches out to start a helper, and a pink arrow passes `42` back through a Queue.
2. **Waiting Over Time: The Timer Spiral** — 16 sequential timers carving a spiral staircase; introduces **3D View `[1]`**, **Top-Down `[2]`**, and **Side Time `[3]`**.
3. **Fair Turn-Taking: 12-Player Ping-Pong Ring** — Why 12 workers sharing one Queue pass a counter in a neat FIFO circle instead of bouncing randomly.

### 2. Python Threads & The GIL
4. **Why Python Threads Don't Speed Up CPU Work (The GIL vs No-GIL `3.14t`)** — Side-by-side real trace of 4 threads doing pure-Python math with `PYTHON_GIL=1` (1 thread green at a time, 3 waiting in orange) vs `PYTHON_GIL=0` (all 4 cores glowing green in parallel, **~4× faster**).
5. **When Python Threads DO Work: Waiting on I/O vs `asyncio`** — Why waiting on network/disk I/O drops the GIL so threads overlap waits—and how `asyncio` achieves the exact same speed on **1 single thread**.
6. **Without the GIL: Shared Data Still Needs a `Lock`** — How `with lock:` protects shared lists in free-threaded Python while re-introducing single-file lock handoffs.

### 3. Asyncio: Waiting vs Offloading
7. **Blocking `asyncio`: Trap vs. Native `await` vs. `to_thread` Offload (3-Way Compare)** — Side-by-side comparison of:
   - **1. Trap (`slow_task()`)**: Freezes the single-threaded event loop (`Blocking Asyncio Loop!`), stalling all 4 heartbeats.
   - **2. Native Async (`await slow_async_task()`)**: OS watches the wait—**zero extra threads** needed.
   - **3. Thread Offload (`await asyncio.to_thread(slow_task)`)**: Spawns a visible helper OS thread (`thread:slow_task`) to run legacy synchronous code without freezing the event loop.

---

## Launch the Interactive Guide

```bash
cd /Users/noklam.chan/nok/python-async
.venv/bin/pyconvis serve --port 8765 --open
```

## Developer & CLI Commands

```bash
# Regenerate all 7 real curriculum traces
.venv/bin/pyconvis build-examples

# Rebuild static frontend bundle
cd web && npm run build && cp -R dist/. ../src/pyconvis/static/

# Run test suite & OpenSpec strict validation
.venv/bin/pytest -v
openspec validate --all --strict
```
