"""View 2 (NATIVE ASYNC): Awaiting non-blocking `slow_async_task()`

When an async-native API exists (timers, sockets, async HTTP/DB), the OS tracks
the wait outside Python — ZERO extra threads are created!
"""

import asyncio


async def slow_async_task() -> str:
    """Non-blocking async wait: yields control immediately on the same thread."""
    await asyncio.sleep(0.065)
    return "done"


async def heartbeat(idx: int, ticks: asyncio.Queue[str]) -> None:
    for step in range(1, 5):
        await asyncio.sleep(0.018)
        await ticks.put(f"hb{idx}.{step}")


async def handle_request(ticks: asyncio.Queue[str]) -> None:
    await asyncio.sleep(0.020)
    # NATIVE ASYNC: Pauses cleanly on 1 thread while heartbeats keep ticking
    result = await slow_async_task()
    await ticks.put(result)


async def main() -> None:
    ticks: asyncio.Queue[str] = asyncio.Queue()
    tasks = [
        asyncio.create_task(heartbeat(i, ticks), name=f"heartbeat #{i}")
        for i in range(1, 5)
    ]
    tasks.append(asyncio.create_task(handle_request(ticks), name="request_task"))

    for _ in range(4 * 4 + 1):
        await ticks.get()
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
