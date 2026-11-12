"""06a. Mixed I/O + Python CPU in Asyncio (1 Thread Holds 1 GIL Across Tasks)

All tasks share ONE OS thread that holds Python's GIL continuously whenever
Python code runs (0% GIL wait, zero cross-thread GIL handoffs).
Tasks switch cooperatively ONLY at `await` — so when `cpu_task` crunches
pure-Python math without `await`, ready `io_task`s must wait for the loop!
"""

import asyncio

IO_WORKERS = 3


def parse_payload(idx: int) -> int:
    """~3ms of Python response parsing (holds the 1 thread's GIL)."""
    acc = idx & 0xFF
    for step in range(85):
        s_mask = step & 0xFF
        for j in range(1100):
            acc = (acc ^ (j & 0xFF) ^ s_mask) & 0xFF
    return acc


def compute_digest() -> int:
    """~52ms of pure-Python CPU math with NO `await` inside."""
    acc = 0x5A
    for step in range(620):
        s_mask = step & 0xFF
        for j in range(2600):
            acc = (acc ^ (j & 0xFF) ^ s_mask) & 0xFF
    return acc


async def io_worker(idx: int, out: asyncio.Queue[str]) -> None:
    # Cooperative yield: OS watches socket/timer outside the GIL
    await asyncio.sleep(0.018 + idx * 0.007)
    digest = parse_payload(idx)
    await out.put(f"io#{idx}:{digest:02x}")


async def cpu_worker(out: asyncio.Queue[str]) -> None:
    await asyncio.sleep(0.002)
    # Runs uninterrupted on the 1 thread (holding 1 GIL) — stalls ready io_tasks!
    digest = compute_digest()
    await asyncio.sleep(0.018)
    await out.put(f"cpu:{digest:02x}")


async def main() -> None:
    out: asyncio.Queue[str] = asyncio.Queue()
    tasks = [
        asyncio.create_task(io_worker(i, out), name=f"io_task #{i}")
        for i in range(1, IO_WORKERS + 1)
    ]
    tasks.append(asyncio.create_task(cpu_worker(out), name="cpu_task"))

    for _ in range(IO_WORKERS + 1):
        await out.get()
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
