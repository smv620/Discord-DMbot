"""Two count lines at the end of a sample run (Supervisor's ask on #3545, 2026-11-10): the secret
connections by the writer that made them, and the split-party counts. Counts only, never a name:
the run's output is pasted into a public issue.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dmbot.db import Database


def lines(secret_by_source: Mapping[str, int], split: Mapping[str, int]) -> list[str]:
    """The two lines, in a steady order. Pure."""
    secret = ", ".join(f"{k} {v}" for k, v in sorted(secret_by_source.items())) or "none"
    total = sum(secret_by_source.values())
    return [
        f"Secret connections (confirmed): {total} ({secret})",
        "Split party: " + ", ".join(f"{k} {split.get(k, 0)}" for k in SPLIT_KEYS),
    ]


SPLIT_KEYS = (
    "clock splits",
    "entries hidden from players",
    "names made while split",
    "knowers of hidden entries",
    "private scenes kept",
    "private scenes not private",
)


async def read(db: Database, guild_id: int, campaign_id: str) -> list[str]:
    """The two lines for one campaign, read now."""
    ids = (guild_id, campaign_id)
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT source, count(*) AS n FROM memory_relations WHERE guild_id = %s"
            " AND campaign_id = %s AND status = 'confirmed' AND secret GROUP BY source",
            ids,
        )
        secret = {str(r["source"]): int(r["n"]) for r in await cur.fetchall()}
        split = {
            "clock splits": await _one(
                conn, "SELECT count(*) AS n FROM game_clock_log WHERE guild_id = %s"
                " AND campaign_id = %s AND position IS NOT NULL", ids),
            "entries hidden from players": await _one(
                conn, "SELECT count(*) AS n FROM memory_entities WHERE guild_id = %s"
                " AND campaign_id = %s AND status = 'confirmed' AND hidden_from_players", ids),
            "names made while split": await _one(
                conn, "SELECT count(*) AS n FROM memory_name_sessions s JOIN memory_entities e"
                " ON e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id"
                " AND e.id = s.entity_id WHERE s.guild_id = %s AND s.campaign_id = %s"
                " AND e.hidden_from_players", ids),
            "knowers of hidden entries": await _one(
                conn, "SELECT count(*) AS n FROM memory_knowers k JOIN memory_entities e"
                " ON e.guild_id = k.guild_id AND e.campaign_id = k.campaign_id"
                " AND e.id = k.about_entity_id WHERE k.guild_id = %s AND k.campaign_id = %s"
                " AND e.hidden_from_players", ids),
            "private scenes kept": await _one(
                conn, "SELECT count(*) AS n FROM memory_private_scenes WHERE guild_id = %s"
                " AND campaign_id = %s AND status = 'kept'", ids),
            "private scenes not private": await _one(
                conn, "SELECT count(*) AS n FROM memory_private_scenes WHERE guild_id = %s"
                " AND campaign_id = %s AND status = 'not_private'", ids),
        }  # fmt: skip
    return lines(secret, split)


async def _one(conn: Any, query: str, params: tuple[object, ...]) -> int:
    cur = await conn.execute(query, params)
    row = await cur.fetchone()
    return int(row["n"]) if row is not None else 0
