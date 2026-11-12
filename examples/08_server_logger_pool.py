"""08. Async Server + Worker Logger Pool

Connection handler tasks arrive in bursts, writing client metadata into a
shared log channel drained by a 4-worker logger pool and aggregator.
"""

import asyncio

CONNECTIONS = 14
LOG_WORKERS = 4


async def conn_handler(cid: int, log_ch: asyncio.Queue[str | None]) -> None:
    await log_ch.put(f"conn:{cid}")
    await asyncio.sleep(0.024)


async def log_worker(
    work_ch: asyncio.Queue[str | None], results_ch: asyncio.Queue[str | None]
) -> None:
    while True:
        entry = await work_ch.get()
        if entry is None:
            return
        await asyncio.sleep(0.014)
        await results_ch.put(f"ok:{entry}")


async def sink(results_ch: asyncio.Queue[str | None], expected: int) -> None:
    for _ in range(expected):
        await results_ch.get()


async def logger_pool(log_ch: asyncio.Queue[str | None]) -> None:
    work_ch: asyncio.Queue[str | None] = asyncio.Queue()
    results_ch: asyncio.Queue[str | None] = asyncio.Queue()

    workers = [
        asyncio.create_task(log_worker(work_ch, results_ch), name=f"logger #{i}")
        for i in range(1, LOG_WORKERS + 1)
    ]
    agg = asyncio.create_task(sink(results_ch, CONNECTIONS), name="aggregator")

    while True:
        msg = await log_ch.get()
        if msg is None:
            break
        await work_ch.put(msg)

    for _ in range(LOG_WORKERS):
        await work_ch.put(None)
    await asyncio.gather(*workers, agg)


async def server(log_ch: asyncio.Queue[str | None]) -> None:
    handlers = []
    for cid in range(1, CONNECTIONS + 1):
        await asyncio.sleep(0.008)
        handlers.append(
            asyncio.create_task(conn_handler(cid, log_ch), name=f"handler #{cid}")
        )
    await asyncio.gather(*handlers)
    await log_ch.put(None)


async def main() -> None:
    log_ch: asyncio.Queue[str | None] = asyncio.Queue()
    await asyncio.gather(
        asyncio.create_task(logger_pool(log_ch), name="pool"),
        asyncio.create_task(server(log_ch), name="server"),
    )


if __name__ == "__main__":
    asyncio.run(main())
