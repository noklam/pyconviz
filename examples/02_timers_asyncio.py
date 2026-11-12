"""02. Recurrent Timers (asyncio)

Builds a channel-style timer helper that spawns a short-lived task, sleeps,
and emits a tick on its own Queue. Running 16 timers sequentially forms a
3D spiral staircase around `main`.
"""

import asyncio


def timer(delay: float) -> asyncio.Queue[int]:
    ch: asyncio.Queue[int] = asyncio.Queue(maxsize=1)

    async def tick() -> None:
        await asyncio.sleep(delay)
        await ch.put(1)

    asyncio.create_task(tick())
    return ch


async def main() -> None:
    for _ in range(16):
        ch = timer(0.012)
        await ch.get()


if __name__ == "__main__":
    asyncio.run(main())
