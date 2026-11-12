"""View 3 (THREAD OFFLOAD): `await asyncio.to_thread(slow_task)`

When you must call a synchronous `slow_task()` that has no `async` version,
`to_thread()` hands it to a background worker thread so the loop stays free!
"""

import asyncio
import time


def slow_task() -> str:
    """Exact same synchronous blocking function from View 1!"""
    time.sleep(0.065)
    return "done"


async def heartbeat(idx: int, ticks: asyncio.Queue[str]) -> None:
    for step in range(1, 5):
        await asyncio.sleep(0.018)
        await ticks.put(f"hb{idx}.{step}")


async def handle_request(ticks: asyncio.Queue[str]) -> None:
    await asyncio.sleep(0.020)
    # OFFLOAD: Spawns helper `thread:slow_task` off the main event loop!
    result = await asyncio.to_thread(slow_task)
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
