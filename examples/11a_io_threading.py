"""05a. Waiting on I/O with GIL Threading

Even with the GIL enabled, waiting on network or disk I/O releases the GIL!
Ten threads overlap their `slow_io_task()` waits concurrently.
"""

import queue
import threading
import time

WORKERS = 10


def slow_io_task(idx: int) -> str:
    """Simulates waiting on a slow network/database response (releases GIL)."""
    time.sleep(0.045 + (idx % 3) * 0.012)
    return f"ok#{idx}"


def worker(idx: int, out: queue.Queue[str]) -> None:
    acc = sum(i * i for i in range(1500))
    status = slow_io_task(idx)
    out.put(f"{status}:{acc % 97}")


def main() -> None:
    out: queue.Queue[str] = queue.Queue()
    threads = [
        threading.Thread(target=worker, args=(i, out), name=f"io_thread #{i}")
        for i in range(1, WORKERS + 1)
    ]
    for t in threads:
        t.start()
    for _ in range(WORKERS):
        out.get()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
