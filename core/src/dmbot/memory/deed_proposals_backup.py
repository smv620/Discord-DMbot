"""Deed proposals in campaign backups (an `ExportSection`; #3175).

A backup holds the whole campaign, including what the DM has not yet answered, so a restored
campaign still has its waiting deeds. A row is the model's short telling, the names as said, the
effect step, line numbers (never lines' words), the people whose lines it came from, the session,
and the prompt and model it was made with. Like the rest of a backup it holds the DM's secrets
(a backup is for the DM and is warned about); nothing here ever reaches a player's screen. Rows
from the file are untrusted: the section is refused whole if any is damaged. A waiting row whose
speaker is not recorded in the server it is restored into is deleted by the same consent check
that wipes claims, the first time it would be shown (`DeedProposals.purge_stopped`). An older
backup has no such section and restores with none.
"""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.memory.models import DEED_TEXT_MAX, DEED_VIEWERS_MAX, NAME_MAX, is_id
from dmbot.memory.standing import DEED_SCORE

DAMAGED = "This backup file is damaged (bad deed proposals)."
ROWS_MAX = 5_000
INT64_MAX = 2**63 - 1
STATUSES = ("proposed", "recorded", "dismissed")
_KEYS = {
    "id", "doer", "text", "viewers", "effect", "lines", "speaker_ids", "session_started_at",
    "prompt_version", "model", "status", "created_at", "decided_at",
}  # fmt: skip

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
    ids: set[str] = set()
    for r in rows:
        if not (
            isinstance(r, dict)
            and set(r) == _KEYS
            and is_id(r["id"])
            and r["id"] not in ids
            and _text(r["doer"], 1, NAME_MAX)
            and _text(r["text"], 1, DEED_TEXT_MAX)
            and isinstance(r["viewers"], list)
            and 1 <= len(r["viewers"]) <= DEED_VIEWERS_MAX
            and all(_text(v, 1, NAME_MAX) for v in r["viewers"])
            and r["effect"] in DEED_SCORE
            and isinstance(r["lines"], list)
            and len(r["lines"]) <= 10
            and all(_text(n, 1, 12) for n in r["lines"])
            and isinstance(r["speaker_ids"], list)
            and len(r["speaker_ids"]) <= 100
            and all(_int(u, 1, INT64_MAX) for u in r["speaker_ids"])
            and (r["session_started_at"] is None or _int(r["session_started_at"], 1, INT64_MAX))
            and _text(r["prompt_version"], 1, 40)
            and _text(r["model"], 0, 80)
            and r["status"] in STATUSES
            and _int(r["created_at"], 1, INT64_MAX)
            and (r["decided_at"] is None or _int(r["decided_at"], 1, INT64_MAX))
        ):
            raise CampaignError(DAMAGED)
        ids.add(r["id"])
        out.append(tuple(r[k] for k in sorted(_KEYS)))
    return out


def _is_checked(rows: object) -> bool:
    return isinstance(rows, list) and all(isinstance(r, tuple) for r in rows)


_ORDER = sorted(_KEYS)


class DeedProposalsSection:
    name = "memory_deed_proposals"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT id, doer, text, viewers, effect, lines, speaker_ids, session_started_at,"
            " prompt_version, model, status, created_at, decided_at FROM memory_deed_proposals"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY created_at, id LIMIT %s",
            (guild_id, campaign_id, ROWS_MAX),
        )
        return [
            {
                "id": r["id"], "doer": r["doer"], "text": r["text"], "viewers": list(r["viewers"]),
                "effect": r["effect"], "lines": list(r["lines"]),
                "speaker_ids": [int(u) for u in r["speaker_ids"]],
                "session_started_at": None
                if r["session_started_at"] is None
                else int(r["session_started_at"]),
                "prompt_version": r["prompt_version"], "model": r["model"], "status": r["status"],
                "created_at": int(r["created_at"]),
                "decided_at": None if r["decided_at"] is None else int(r["decided_at"]),
            }
            for r in await cur.fetchall()
        ]  # fmt: skip

    def check(self, rows: list[Any]) -> list[Row]:
        """Pure; the store runs it in a worker thread before its transaction."""
        return _checked(rows)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = list(rows) if _is_checked(rows) else _checked(rows)
        if not checked:
            return
        at = {k: i for i, k in enumerate(_ORDER)}
        async with conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO memory_deed_proposals (guild_id, campaign_id, id, doer, text,"
                " viewers, effect, lines, speaker_ids, session_started_at, prompt_version, model,"
                " status, created_at, decided_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        guild_id, campaign_id, r[at["id"]], r[at["doer"]], r[at["text"]],
                        r[at["viewers"]], r[at["effect"]], r[at["lines"]], r[at["speaker_ids"]],
                        r[at["session_started_at"]], r[at["prompt_version"]], r[at["model"]],
                        r[at["status"]], r[at["created_at"]], r[at["decided_at"]],
                    )
                    for r in checked
                ],
            )  # fmt: skip

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM memory_deed_proposals WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


__all__ = ["ROWS_MAX", "DeedProposalsSection"]
