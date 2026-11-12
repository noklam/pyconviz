"""06. Workers / Fan-Out Pool

Twelve worker tasks pull 28 jobs from a shared task Queue, distributing
variable I/O wait workloads across the pool.
"""

import asyncio

WORKERS = 12
TASKS = 28


async def worker(tasks_q: asyncio.Queue[int | None], results_q: asyncio.Queue[int]) -> None:
    while True:
        job = await tasks_q.get()
        if job is None:
            return
        await asyncio.sleep(0.012 + (job % 5) * 0.006)
        await results_q.put(job * 10)


async def pool() -> None:
    tasks_q: asyncio.Queue[int | None] = asyncio.Queue(maxsize=4)
    results_q: asyncio.Queue[int] = asyncio.Queue()

    workers = [
        asyncio.create_task(worker(tasks_q, results_q), name=f"worker #{i}")
        for i in range(1, WORKERS + 1)
    ]

    async def collector() -> None:
        for _ in range(TASKS):
            await results_q.get()

    col = asyncio.create_task(collector(), name="collector")

    for job in range(1, TASKS + 1):
        await tasks_q.put(job)
    for _ in range(WORKERS):
        await tasks_q.put(None)

    await asyncio.gather(*workers, col)


async def main() -> None:
    p = asyncio.create_task(pool(), name="pool")
    await p


if __name__ == "__main__":
    asyncio.run(main())
