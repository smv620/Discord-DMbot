"""An in-memory stand-in for `ImportState` (#4904), for tests of the bot's import steps that have no
database: the same calls and the same take-once rules."""

from __future__ import annotations

from collections.abc import Collection


class FakeImportState:
    def __init__(self) -> None:
        self.counts: dict[tuple[int, str, str], int] = {}
        self.sets: dict[tuple[int, str, str], list[str]] = {}

    async def bump(self, gid: int, cid: str, kind: str, by: int = 1) -> int:
        key = (gid, cid, kind)
        self.counts[key] = self.counts.get(key, 0) + by
        return self.counts[key]

    async def put_count(self, gid: int, cid: str, kind: str, n: int) -> None:
        if n <= 0:
            self.counts.pop((gid, cid, kind), None)
        else:
            self.counts[(gid, cid, kind)] = n

    async def count(self, gid: int, cid: str, kind: str) -> int:
        return self.counts.get((gid, cid, kind), 0)

    async def take_count(self, gid: int, cid: str, kind: str) -> int:
        return self.counts.pop((gid, cid, kind), 0)

    async def counted(self, gid: int, kind: str) -> dict[str, int]:
        return {c: n for (g, c, k), n in self.counts.items() if g == gid and k == kind}

    async def add_strings(self, gid: int, cid: str, kind: str, values: Collection[str]) -> None:
        have = self.sets.setdefault((gid, cid, kind), [])
        have += [v for v in values if v not in have]

    async def take_strings(self, gid: int, cid: str, kind: str) -> list[str]:
        return self.sets.pop((gid, cid, kind), [])

    async def campaigns_with(self, gid: int, kinds: Collection[str]) -> set[str]:
        keys = [*self.counts, *self.sets]
        return {c for (g, c, k) in keys if g == gid and k in kinds}
