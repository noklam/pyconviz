"""03. Ping-Pong (2 Players)

Two concurrent player tasks pass an incrementing integer 'ball' back and forth
across a shared table Queue.
"""

import asyncio


async def player(table: asyncio.Queue[int], stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            ball = await asyncio.wait_for(table.get(), timeout=0.08)
        except TimeoutError:
            return
        if stop.is_set():
            return
        ball += 1
        await asyncio.sleep(0.018)
        await table.put(ball)


async def main() -> None:
    table: asyncio.Queue[int] = asyncio.Queue()
    stop = asyncio.Event()
    p1 = asyncio.create_task(player(table, stop), name="player #1")
    p2 = asyncio.create_task(player(table, stop), name="player #2")

    await table.put(0)
    await asyncio.sleep(0.16)
    stop.set()
    # Drain ball if resting on table so players exit cleanly
    while not table.empty():
        table.get_nowait()
    await asyncio.gather(p1, p2, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
