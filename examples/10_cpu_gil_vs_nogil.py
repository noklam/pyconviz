"""10. CPU-Bound Worker Threads: GIL (PYTHON_GIL=1) vs Free-Threaded (PYTHON_GIL=0)

Four OS threads execute pure-Python integer hashing/math and post digests to
`main`. Under PYTHON_GIL=1, threads take turns holding the GIL while siblings
stall in `waiting_gil`. Under PYTHON_GIL=0 (Python 3.14t), all 4 cores execute
Python bytecode simultaneously in true parallel!
"""

import queue
import threading

WORKERS = 4


def cpu_worker(wid: int, results: queue.Queue[str]) -> None:
    acc = wid & 0xFF
    for step in range(950):
        s_mask = step & 0xFF
        for j in range(3200):
            acc = (acc ^ (j & 0xFF) ^ s_mask) & 0xFF
        if step == 474 or step == 949:
            results.put(f"w{wid}:{acc:02x}")


def main() -> None:
    results: queue.Queue[str] = queue.Queue()
    threads = [
        threading.Thread(target=cpu_worker, args=(i, results), name=f"cpu_worker #{i}")
        for i in range(1, WORKERS + 1)
    ]
    for t in threads:
        t.start()

    for _ in range(WORKERS * 2):
        results.get()

    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
