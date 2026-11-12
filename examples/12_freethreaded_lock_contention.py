"""12. Free-Threaded Lock Contention (PYTHON_GIL=0 + threading.Lock)

Removing the GIL unlocks multi-core Python, but shared mutable state still
requires synchronization! Here 4 free-threaded workers run parallel CPU bursts
and then contend on `ledger_lock`, revealing Amdahl's Law & lock handoff arrows.
"""

import queue
import threading

WORKERS = 4


def account_worker(
    wid: int, ledger_lock: threading.Lock, ledger: list[int], done_q: queue.Queue[str]
) -> None:
    for step in range(3):
        # Parallel lock-free local computation on separate CPU cores
        acc = 0
        for i in range(240):
            im = i & 0xFF
            for j in range(1800):
                acc = (acc ^ (j & 0xFF) ^ im ^ wid) & 0xFF

        # Serialized critical section protected by explicit Lock
        with ledger_lock:
            crit = 0
            for j in range(180_000):
                crit = (crit ^ (j & 0xFF)) & 0xFF
            ledger.append(acc ^ crit)

    done_q.put(f"w{wid}:ok")


def main() -> None:
    ledger_lock = threading.Lock()
    ledger: list[int] = []
    done_q: queue.Queue[str] = queue.Queue()

    threads = [
        threading.Thread(
            target=account_worker,
            args=(i, ledger_lock, ledger, done_q),
            name=f"worker #{i}",
        )
        for i in range(1, WORKERS + 1)
    ]
    for t in threads:
        t.start()
    for _ in range(WORKERS):
        done_q.get()
    for t in threads:
        t.join()
    assert len(ledger) == WORKERS * 3


if __name__ == "__main__":
    main()
