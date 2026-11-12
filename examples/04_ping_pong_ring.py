"""04. Ping-Pong Ring (12 Players — FIFO Queue Order)

Twelve player tasks wait on the same asyncio.Queue. Because asyncio.Queue wakes
waiting getters in strict FIFO deque order, the ball travels around the ring
in deterministic sequential order! Switch to Top-Down View to see the polygon.
"""

import asyncio

PLAYERS = 12
RALLIES = 24


async def player(idx: int, table: asyncio.Queue[int]) -> None:
    while True:
        ball = await table.get()
        if ball < 0:
            # Pass stop token to next player in FIFO order
            await table.put(-1)
            return
        ball += 1
        await asyncio.sleep(0.008)
        if ball >= RALLIES:
            await table.put(-1)
            return
        await table.put(ball)


async def main() -> None:
    table: asyncio.Queue[int] = asyncio.Queue()
    tasks = []
    for i in range(1, PLAYERS + 1):
        t = asyncio.create_task(player(i, table), name=f"player #{i}")
        tasks.append(t)
        # Let each task register in the Queue's FIFO waiter deque in order
        await asyncio.sleep(0.001)

    await table.put(0)
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
