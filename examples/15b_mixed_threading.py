"""06b. Mixed I/O + Python CPU in Threading (1 GIL Lock Passed Across Threads)

Identical workload with OS threads under standard Python (`PYTHON_GIL=1`).
`io_thread`s drop the GIL while waiting on OS I/O; when their I/O finishes,
CPython preempts `cpu_thread` at the switch interval and passes the 1 GIL Lock
across threads so I/O responses finish right away — still 1 CPU thread at a time!
"""

import queue
import threading
import time

IO_WORKERS = 3


def parse_payload(idx: int) -> int:
    """~3ms of Python response parsing (requires holding the GIL)."""
    acc = idx & 0xFF
    for step in range(85):
        s_mask = step & 0xFF
        for j in range(1100):
            acc = (acc ^ (j & 0xFF) ^ s_mask) & 0xFF
    return acc


def compute_digest() -> int:
    """Exact same ~52ms pure-Python CPU math as 15a_mixed_asyncio.py!"""
    acc = 0x5A
    for step in range(620):
        s_mask = step & 0xFF
        for j in range(2600):
            acc = (acc ^ (j & 0xFF) ^ s_mask) & 0xFF
    return acc


def io_worker(idx: int, out: queue.Queue[str]) -> None:
    # Blocking OS syscall drops the GIL so `cpu_thread` runs concurrently
    time.sleep(0.018 + idx * 0.007)
    # Re-acquires GIL (preempting `cpu_thread`) to parse & enqueue immediately!
    digest = parse_payload(idx)
    out.put(f"io#{idx}:{digest:02x}")


def cpu_worker(out: queue.Queue[str]) -> None:
    time.sleep(0.002)
    # Preempted briefly whenever a woken `io_thread` takes a GIL slice
    digest = compute_digest()
    time.sleep(0.018)
    out.put(f"cpu:{digest:02x}")


def main() -> None:
    out: queue.Queue[str] = queue.Queue()
    threads = [
        threading.Thread(target=io_worker, args=(i, out), name=f"io_thread #{i}")
        for i in range(1, IO_WORKERS + 1)
    ]
    threads.append(
        threading.Thread(target=cpu_worker, args=(out,), name="cpu_thread")
    )
    for t in threads:
        t.start()

    for _ in range(IO_WORKERS + 1):
        out.get()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
