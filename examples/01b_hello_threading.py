"""01b. Hello, Concurrent World (threading)

Same single-message handoff implemented with an OS Thread and queue.Queue.
"""

import queue
import threading
import time


def sender(ch: queue.Queue[int]) -> None:
    time.sleep(0.025)
    ch.put(42)
    time.sleep(0.015)


def main() -> None:
    ch: queue.Queue[int] = queue.Queue(maxsize=1)
    t = threading.Thread(target=sender, args=(ch,))
    t.start()
    value = ch.get()
    t.join()
    assert value == 42


if __name__ == "__main__":
    main()
