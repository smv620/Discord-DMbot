"""Who-learned-what proposals in campaign backups (an `ExportSection`; #2996, as the deeds' are).

A backup holds the whole campaign, including what the DM has not yet answered, so a restored
campaign still has its waiting reveals. A row is ids only: the character, the statement, how (a
check's name or the teller's id), line numbers (never lines' words), the people whose lines it came
from, the session, and the prompt and model it was made with. Rows from the file are untrusted:
the section is refused whole if any is damaged. A waiting row whose speaker is not recorded in the
server it is restored into is deleted by the same consent check that wipes claims, the first time
it would be shown (`RevealProposals.purge_stopped`). An older backup has no such section and
restores with none.
"""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.memory.models import is_id
from dmbot.memory.reveal_reading import CHECKS

DAMAGED = "This backup file is damaged (bad who-learned-what proposals)."
ROWS_MAX = 5_000
INT64_MAX = 2**63 - 1
STATUSES = ("proposed", "kept", "dismissed")
KINDS = ("entry", "property", "connection", "alias")
_KEYS = {
    "id", "character_id", "statement_kind", "statement_id", "how", "check_name", "teller_id",
    "lines", "speaker_ids", "session_started_at", "prompt_version", "model", "status",
    "created_at", "decided_at",
}  # fmt: skip
_ORDER = sorted(_KEYS)

Row = tuple[Any, ...]


def _int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high  # (a JSON true is not a number)


def _text(value: object, low: int, high: int) -> bool:
    return isinstance(value, str) and low <= len(value) <= high


def _how_fits(r: dict[str, Any]) -> bool:
    """A check names a listed check and no teller; a tell names a teller (not the learner) and no
    check: the table's own rules, checked before the insert so a bad file is refused whole."""
    if r["how"] == "check":
        return r["check_name"] in CHECKS and r["teller_id"] is None
    return (
        r["how"] == "tell"
        and r["check_name"] == ""
        and is_id(r["teller_id"])
        and r["teller_id"] != r["character_id"]
    )


def _checked(rows: object) -> list[Row]:
    """The rows as tuples, or CampaignError(DAMAGED) on anything out of shape. Pure."""
    if not isinstance(rows, list) or len(rows) > ROWS_MAX:
        raise CampaignError(DAMAGED)
    out: list[Row] = []
    ids: set[str] = set()
    once: set[tuple[Any, ...]] = set()  # (the table's one-per-session rule, so a file can't break it)
    for r in rows:
        if not (
            isinstance(r, dict)
            and set(r) == _KEYS
            and is_id(r["id"])
            and r["id"] not in ids
            and is_id(r["character_id"])
            and r["statement_kind"] in KINDS
            and is_id(r["statement_id"])
            and _how_fits(r)
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
            and (r["status"] == "kept" or len(r["speaker_ids"]) >= 1)
            and _int(r["created_at"], 1, INT64_MAX)
            and (r["decided_at"] is None or _int(r["decided_at"], 1, INT64_MAX))
        ):
            raise CampaignError(DAMAGED)
        key = (r["session_started_at"], r["character_id"], r["statement_kind"], r["statement_id"])
        if key in once:
            raise CampaignError(DAMAGED)
        once.add(key)
        ids.add(r["id"])
        out.append(tuple(r[k] for k in _ORDER))
    return out


def _is_checked(rows: object) -> bool:
    return isinstance(rows, list) and all(isinstance(r, tuple) for r in rows)


class RevealProposalsSection:
    name = "memory_reveal_proposals"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT id, character_id, statement_kind, statement_id, how, check_name, teller_id,"
            " lines, speaker_ids, session_started_at, prompt_version, model, status, created_at,"
            " decided_at FROM memory_reveal_proposals"
            # (past the cap, the rows still waiting for the DM are kept first, then the newest)
            " WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY status = 'proposed' DESC, created_at DESC, id LIMIT %s",
            (guild_id, campaign_id, ROWS_MAX),
        )
        return [
            {
                "id": r["id"], "character_id": r["character_id"],
                "statement_kind": r["statement_kind"], "statement_id": r["statement_id"],
                "how": r["how"], "check_name": r["check_name"], "teller_id": r["teller_id"],
                "lines": list(r["lines"]), "speaker_ids": [int(u) for u in r["speaker_ids"]],
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
        columns = ", ".join(_ORDER)
        async with conn.cursor() as cursor:
            await cursor.executemany(
                f"INSERT INTO memory_reveal_proposals (guild_id, campaign_id, {columns})"
                f" VALUES (%s, %s, {', '.join(['%s'] * len(_ORDER))})",
                [(guild_id, campaign_id, *(r[at[k]] for k in _ORDER)) for r in checked],
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM memory_reveal_proposals WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


__all__ = ["ROWS_MAX", "RevealProposalsSection"]
