"""07. Workers of Workers (Hierarchical Fan-Out Tree)

Four top-level workers each spawn three subworkers to process batches of
subtasks, forming a 2-level concurrent tree in 3D space.
"""

import asyncio

WORKERS = 4
SUBWORKERS = 3
BATCHES = 8
SUBTASKS = 6


async def subworker(sub_q: asyncio.Queue[int | None]) -> None:
    while True:
        item = await sub_q.get()
        if item is None:
            return
        await asyncio.sleep(0.010 + (item % 3) * 0.004)


async def worker(w_idx: int, tasks_q: asyncio.Queue[int | None]) -> None:
    while True:
        batch = await tasks_q.get()
        if batch is None:
            return
        sub_q: asyncio.Queue[int | None] = asyncio.Queue()
        subs = [
            asyncio.create_task(subworker(sub_q), name=f"sub #{w_idx}.{s}")
            for s in range(1, SUBWORKERS + 1)
        ]
        for k in range(1, SUBTASKS + 1):
            await sub_q.put(batch * 10 + k)
        for _ in range(SUBWORKERS):
            await sub_q.put(None)
        await asyncio.gather(*subs)


async def main() -> None:
    tasks_q: asyncio.Queue[int | None] = asyncio.Queue()
    workers = [
        asyncio.create_task(worker(i, tasks_q), name=f"worker #{i}")
        for i in range(1, WORKERS + 1)
    ]
    for b in range(1, BATCHES + 1):
        await tasks_q.put(b)
    for _ in range(WORKERS):
        await tasks_q.put(None)
    await asyncio.gather(*workers)


if __name__ == "__main__":
    asyncio.run(main())
