"""Shard settings: which part of Discord this process serves.

Discord splits a bot's servers into shards. Server `g` belongs to shard
`(g >> 22) % shard_count`. One process runs one or more shards; scaling out means
running more processes with different `SHARD_IDS` and the same `SHARD_COUNT`. Today the
default is a single process with one shard.

`ears` (Node) has the same rules in `ears/src/shards.ts`; core refuses an ears whose
shard settings differ, since ears could then not join voice for core's servers.
"""

from __future__ import annotations

from dataclasses import dataclass

SHARD_COUNT_MAX = 4096


class ShardConfigError(ValueError):
    """Bad shard settings. The message says how to fix them."""


@dataclass(frozen=True, slots=True)
class ShardSettings:
    count: int = 1
    ids: tuple[int, ...] = (0,)

    def covers(self, guild_id: int) -> bool:
        return shard_for(guild_id, self.count) in self.ids

    def shard_of(self, guild_id: int) -> int:
        return shard_for(guild_id, self.count)


def shard_for(guild_id: int, shard_count: int) -> int:
    """Discord's rule for which shard a server belongs to."""
    return (guild_id >> 22) % shard_count


def parse_shards(count_raw: str, ids_raw: str) -> ShardSettings:
    """Read SHARD_COUNT (default 1) and SHARD_IDS (default: all shards, e.g. "0,1")."""
    count_raw = count_raw.strip() or "1"
    if not count_raw.isascii() or not count_raw.isdigit():
        raise ShardConfigError(f'SHARD_COUNT must be a whole number, got "{count_raw}".')
    count = int(count_raw)
    if not 1 <= count <= SHARD_COUNT_MAX:
        raise ShardConfigError(f"SHARD_COUNT must be between 1 and {SHARD_COUNT_MAX}.")

    ids_raw = ids_raw.strip()
    if not ids_raw:
        return ShardSettings(count, tuple(range(count)))
    ids: list[int] = []
    for part in ids_raw.split(","):
        part = part.strip()
        if not part.isascii() or not part.isdigit():
            raise ShardConfigError(
                f'SHARD_IDS must be shard numbers separated by commas, like "0,1"; got "{ids_raw}".'
            )
        shard = int(part)
        if shard >= count:
            raise ShardConfigError(
                f"SHARD_IDS has shard {shard}, but SHARD_COUNT is {count} "
                f"(shards are numbered 0 to {count - 1})."
            )
        if shard in ids:
            raise ShardConfigError(f"SHARD_IDS lists shard {shard} twice.")
        ids.append(shard)
    return ShardSettings(count, tuple(sorted(ids)))
