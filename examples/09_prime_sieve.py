"""09. Concurrent Prime Sieve (Sieve of Eratosthenes)

One of the most iconic concurrent algorithms: `generate` streams integers
2, 3, 4, ... down a dynamically daisy-chained pipeline of prime filter tasks.
Switch to Top-Down View to see discovered primes radiating back to `main`!
"""

import asyncio

PRIME_COUNT = 9


async def generate(ch: asyncio.Queue[int]) -> None:
    n = 2
    while True:
        await asyncio.sleep(0.004)
        await ch.put(n)
        if n > 32:
            await ch.put(-1)
            return
        n += 1


async def prime_filter(
    in_ch: asyncio.Queue[int], out_ch: asyncio.Queue[int], prime: int
) -> None:
    while True:
        val = await in_ch.get()
        if val < 0:
            await out_ch.put(-1)
            return
        if val % prime != 0:
            await asyncio.sleep(0.002)
            await out_ch.put(val)


async def main() -> None:
    ch: asyncio.Queue[int] = asyncio.Queue(maxsize=2)
    stages: list[asyncio.Task[None]] = [
        asyncio.create_task(generate(ch), name="generate")
    ]
    primes: list[int] = []

    for _ in range(PRIME_COUNT):
        prime = await ch.get()
        if prime < 0:
            break
        primes.append(prime)
        next_ch: asyncio.Queue[int] = asyncio.Queue(maxsize=2)
        stages.append(
            asyncio.create_task(
                prime_filter(ch, next_ch, prime), name=f"filter p={prime}"
            )
        )
        ch = next_ch

    # Drain remaining numbers until sentinel cascades through all filters
    while (await ch.get()) >= 0:
        pass
    await asyncio.gather(*stages)


if __name__ == "__main__":
    asyncio.run(main())
