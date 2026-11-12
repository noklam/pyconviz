"""01. Hello, Concurrent World (asyncio)

Python's asyncio counterpart to divan's first Go concurrency example:
one Queue (channel), one spawned Task, one send, and one receive.
"""

import asyncio


async def sender(ch: asyncio.Queue[int]) -> None:
    await asyncio.sleep(0.025)
    await ch.put(42)
    await asyncio.sleep(0.015)


async def main() -> None:
    ch: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
    task = asyncio.create_task(sender(ch))
    value = await ch.get()
    await task
    assert value == 42


if __name__ == "__main__":
    asyncio.run(main())
