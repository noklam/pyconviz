"""13b. Fix: Offloading Blocking Work with asyncio.to_thread (GOOD)

Wrapping synchronous blocking work in `await asyncio.to_thread(...)` yields the
event loop immediately so all 4 heartbeat tasks keep firing right on schedule!
"""

import asyncio
import time


async def heartbeat(idx: int, ticks: asyncio.Queue[str]) -> None:
    for step in range(1, 5):
        await asyncio.sleep(0.018)
        await ticks.put(f"hb{idx}.{step}")


async def polite_offloaded_report(ticks: asyncio.Queue[str]) -> None:
    await asyncio.sleep(0.020)
    # FIX: Offload blocking work to worker thread pool without stalling the loop
    await asyncio.to_thread(time.sleep, 0.065)
    await ticks.put("report_done")


async def main() -> None:
    ticks: asyncio.Queue[str] = asyncio.Queue()
    tasks = [
        asyncio.create_task(heartbeat(i, ticks), name=f"heartbeat #{i}")
        for i in range(1, 5)
    ]
    tasks.append(
        asyncio.create_task(polite_offloaded_report(ticks), name="offloaded_task")
    )

    for _ in range(4 * 4 + 1):
        await ticks.get()
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
