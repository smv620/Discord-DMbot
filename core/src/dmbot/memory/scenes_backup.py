"""Scenes of questions in campaign backups (an `ExportSection`; #3596).

A scene is the AI's short telling of who, where and what, made from lines a backup does not hold,
so without this a restored campaign's waiting questions would lose it for good. A row is the
subject (an entry or a claim, by id), the scene, how many lines it was made from, the people whose
lines they were, the session, and the prompt and model. Like the rest of a backup it holds the DM's
secrets (a backup is for the DM and warned about) and never reaches a player. Rows from the file are
untrusted: the section is refused whole if any is damaged. A scene whose question is no longer open
in the restored campaign, or whose speaker is not recorded in the server it is restored into, is
deleted by the same sweep and consent check that guard every read (`Scenes.of`). An older backup has
no such section and restores with none.
"""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.memory.models import is_id
from dmbot.memory.scene_reading import SCENE_MAX

DAMAGED = "This backup file is damaged (bad scenes)."
ROWS_MAX = 20_000
INT64_MAX = 2**63 - 1
KINDS = ("entity", "claim")
_KEYS = (
    "subject_kind", "subject_id", "scene", "lines_n", "speaker_ids", "session_started_at",
    "prompt_version", "model", "created_at",
)  # fmt: skip

Row = tuple[Any, ...]


def _int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high  # (a JSON true is not a number)


def _text(value: object, low: int, high: int) -> bool:
    return isinstance(value, str) and low <= len(value) <= high


def _checked(rows: object) -> list[Row]:
    """The rows as tuples, or CampaignError(DAMAGED) on anything out of shape. Pure."""
    if not isinstance(rows, list) or len(rows) > ROWS_MAX:
        raise CampaignError(DAMAGED)
    out: list[Row] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        if not (
            isinstance(r, dict)
            and set(r) == set(_KEYS)
            and r["subject_kind"] in KINDS
            and is_id(r["subject_id"])
            and (r["subject_kind"], r["subject_id"]) not in seen
            and _text(r["scene"], 1, SCENE_MAX)
            and _int(r["lines_n"], 0, 1_000_000)
            and isinstance(r["speaker_ids"], list)
            and len(r["speaker_ids"]) <= 100
            and all(_int(u, 1, INT64_MAX) for u in r["speaker_ids"])
            and (r["session_started_at"] is None or _int(r["session_started_at"], 1, INT64_MAX))
            and _text(r["prompt_version"], 1, 40)
            and _text(r["model"], 0, 80)
            and _int(r["created_at"], 1, INT64_MAX)
        ):
            raise CampaignError(DAMAGED)
        seen.add((r["subject_kind"], r["subject_id"]))
        out.append(tuple(r[k] for k in _KEYS))
    return out


def _is_checked(rows: object) -> bool:
    return isinstance(rows, list) and all(isinstance(r, tuple) for r in rows)


class ScenesSection:
    name = "memory_scenes"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT subject_kind, subject_id, scene, lines_n, speaker_ids, session_started_at,"
            " prompt_version, model, created_at FROM memory_scenes"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY subject_kind, subject_id LIMIT %s",
            (guild_id, campaign_id, ROWS_MAX),
        )
        return [
            {
                "subject_kind": r["subject_kind"],
                "subject_id": r["subject_id"],
                "scene": r["scene"],
                "lines_n": int(r["lines_n"]),
                "speaker_ids": [int(u) for u in r["speaker_ids"]],
                "session_started_at": None
                if r["session_started_at"] is None
                else int(r["session_started_at"]),
                "prompt_version": r["prompt_version"],
                "model": r["model"],
                "created_at": int(r["created_at"]),
            }
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[Row]:
        """Pure; the store runs it in a worker thread before its transaction."""
        return _checked(rows)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = list(rows) if _is_checked(rows) else _checked(rows)
        if not checked:
            return
        async with conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO memory_scenes (guild_id, campaign_id, subject_kind, subject_id,"
                " scene, lines_n, speaker_ids, session_started_at, prompt_version, model,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(guild_id, campaign_id, *row) for row in checked],
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM memory_scenes WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


__all__ = ["ROWS_MAX", "ScenesSection"]
