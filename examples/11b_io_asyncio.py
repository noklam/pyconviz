"""05b. Waiting on I/O with Single-Threaded Asyncio

Achieves the exact same overlapping I/O speed as 10 OS threads, but all on
1 single thread using `await slow_async_io_task()`.
"""

import asyncio

WORKERS = 10


async def slow_async_io_task(idx: int) -> str:
    """Non-blocking async I/O wait: pauses cleanly on the single event loop."""
    await asyncio.sleep(0.045 + (idx % 3) * 0.012)
    return f"ok#{idx}"


async def worker(idx: int, out: asyncio.Queue[str]) -> None:
    acc = sum(i * i for i in range(1500))
    status = await slow_async_io_task(idx)
    await out.put(f"{status}:{acc % 97}")


async def main() -> None:
    out: asyncio.Queue[str] = asyncio.Queue()
    tasks = [
        asyncio.create_task(worker(i, out), name=f"io_task #{i}")
        for i in range(1, WORKERS + 1)
    ]
    for _ in range(WORKERS):
        await out.get()
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
