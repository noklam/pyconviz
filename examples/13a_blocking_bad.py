"""View 1 (TRAP): Calling blocking `slow_task()` directly inside `async def`

Because `slow_task()` never pauses with `await`, it hijacks Python's single
event-loop thread and freezes all 4 concurrent `heartbeat` tasks!
"""

import asyncio
import time


def slow_task() -> str:
    """A synchronous blocking call (e.g. legacy HTTP client or file read)."""
    time.sleep(0.065)
    return "done"


async def heartbeat(idx: int, ticks: asyncio.Queue[str]) -> None:
    for step in range(1, 5):
        await asyncio.sleep(0.018)
        await ticks.put(f"hb{idx}.{step}")


async def handle_request(ticks: asyncio.Queue[str]) -> None:
    await asyncio.sleep(0.020)
    # TRAP: Blocks the single asyncio thread until `slow_task()` finishes!
    result = slow_task()
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
