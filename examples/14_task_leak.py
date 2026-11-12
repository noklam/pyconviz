"""14. Concurrency Leak (Fire-and-Forget Orphaned Tasks)

When tasks are spawned without structured cancellation (`TaskGroup` or explicit
`cancel()`), a slow or infinite loop keeps running after `main` moves on —
leaving dangling timelines that never rejoin their parent!
"""

import asyncio


async def healthy_worker(idx: int, res_q: asyncio.Queue[str]) -> None:
    await asyncio.sleep(0.025)
    await res_q.put(f"ok#{idx}")


async def leaked_poller(idx: int, ghost_q: asyncio.Queue[str]) -> None:
    # BUG: Infinite loop waiting on a queue nobody ever closes or cancels!
    while True:
        await asyncio.sleep(0.030)
        await ghost_q.put(f"leak#{idx}")


async def main() -> None:
    res_q: asyncio.Queue[str] = asyncio.Queue()
    ghost_q: asyncio.Queue[str] = asyncio.Queue()

    good = [
        asyncio.create_task(healthy_worker(i, res_q), name=f"healthy #{i}")
        for i in range(1, 4)
    ]
    # Spawn 5 fire-and-forget pollers that are never cancelled or awaited
    for i in range(1, 6):
        asyncio.create_task(leaked_poller(i, ghost_q), name=f"orphan #{i}")

    for _ in range(3):
        await res_q.get()
    await asyncio.gather(*good)
    await asyncio.sleep(0.070)


if __name__ == "__main__":
    asyncio.run(main())
