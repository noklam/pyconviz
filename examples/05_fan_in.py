"""05. Fan-In Multiplexer

Two producers emit items at different rates (16ms vs 40ms) into `ch`,
while `main` multiplexes all values onto `out` for a downstream `reader`.
"""

import asyncio


async def producer(tag: str, ch: asyncio.Queue[str | None], delay: float, count: int) -> None:
    for i in range(1, count + 1):
        await asyncio.sleep(delay)
        await ch.put(f"{tag}{i}")
    await ch.put(None)


async def reader(out: asyncio.Queue[str | None]) -> None:
    while True:
        item = await out.get()
        if item is None:
            return
        await asyncio.sleep(0.003)


async def main() -> None:
    ch: asyncio.Queue[str | None] = asyncio.Queue()
    out: asyncio.Queue[str | None] = asyncio.Queue()

    fast = asyncio.create_task(producer("A", ch, 0.016, 9), name="fast_prod")
    slow = asyncio.create_task(producer("B", ch, 0.040, 4), name="slow_prod")
    sink = asyncio.create_task(reader(out), name="reader")

    done_producers = 0
    while done_producers < 2:
        val = await ch.get()
        if val is None:
            done_producers += 1
        else:
            await out.put(val)

    await out.put(None)
    await asyncio.gather(fast, slow, sink)


if __name__ == "__main__":
    asyncio.run(main())
