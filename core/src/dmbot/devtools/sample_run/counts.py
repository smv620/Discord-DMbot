"""Count lines at the end of a sample run (Supervisor's ask on #3545, 2026-11-10): the secret
connections by the writer that made them, the split-party counts, and the party's questions and
closed stretches (#4537 slice 6c). Counts only, never a name: the run's output is pasted into a
public issue. With a party key (`party_key`), the goodbye questions are scored against it by name
here, and only the counts leave.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from dmbot.db import Database
from dmbot.memory.models import name_key


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


PARTY_KINDS = ("join", "back", "leave", "died")


def party_line(asked: Mapping[str, int], closed: int) -> str:
    """The party's questions by kind and the stretches closed (#4537 slice 6c). Nothing closes a
    stretch but the DM's answer, so a run with no answers must show 0 closed. Pure."""
    kinds = ", ".join(f"{k} {asked.get(k, 0)}" for k in PARTY_KINDS)
    return f"Party: questions {kinds}; stretches closed {closed}"


def party_key_line(asked: Collection[str], key: Mapping[str, Any]) -> str:
    """The goodbye questions asked (`asked`: the characters' names) scored against a party key's
    `leave` (expected) and `must_not_leave` lists, by name key: found, missed, and false (asked
    about one the key says never to; one in neither list counts as extra). Counts only. Pure."""
    found = {name_key(n) for n in asked}
    expected = {name_key(n) for n in key.get("leave", [])}
    never = {name_key(n) for n in key.get("must_not_leave", [])}
    return (
        f"Party key: goodbye questions {len(found & expected)} of {len(expected)} found,"
        f" {len(expected - found)} missed, {len(found & never)} false,"
        f" {len(found - expected - never)} extra"
    )


def load_party_key(path: Path) -> dict[str, Any]:
    """A party key file (`party_cases_key.json`)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("leave"), list):
        raise ValueError('a party key has a "leave" list')
    return data


async def read_party(
    db: Database, guild_id: int, campaign_id: str, key: Mapping[str, Any] | None = None
) -> list[str]:
    """The party line, and the key's line when a key is given, for one campaign, read now."""
    ids = (guild_id, campaign_id)
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT kind, count(*) AS n FROM memory_join_questions WHERE guild_id = %s"
            " AND campaign_id = %s GROUP BY kind",
            ids,
        )
        asked = {str(r["kind"]): int(r["n"]) for r in await cur.fetchall()}
        closed = await _one(
            conn, "SELECT count(*) AS n FROM memory_party_spans WHERE guild_id = %s"
            " AND campaign_id = %s AND to_at IS NOT NULL", ids)  # fmt: skip
        out = [party_line(asked, closed)]
        if key is not None:
            cur = await conn.execute(
                "SELECT e.name FROM memory_join_questions q JOIN memory_entities e"
                " ON e.guild_id = q.guild_id AND e.campaign_id = q.campaign_id"
                " AND e.id = q.entity_id WHERE q.guild_id = %s AND q.campaign_id = %s"
                " AND q.kind = 'leave'",
                ids,
            )
            out.append(party_key_line([str(r["name"]) for r in await cur.fetchall()], key))
    return out


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
