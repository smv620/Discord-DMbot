"""What the background job keeps for a character's card (#5050): the one sentence on who they are,
and where and when they were first met and last mentioned. A row for each character, made again by
the job when the facts change or a new session names them, never when a card is opened.

Only ids, times, line numbers and DMbot's own words: the sentence and the scenes are DMbot's short
descriptions (never a quote of what anyone said). It is made again from the facts and the
transcripts, so it is not in a backup (`campaigns/tables.py`). One campaign's rows only: every
read and write is scoped to its server and campaign.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from dmbot.db import Database

SCENE_MAX = 300  # the column's limit
WHO_MAX = 600  # the column's limit (the card cuts the sentence shorter, `card_lines.WHO_MAX`)


@dataclass(frozen=True, slots=True)
class Mention:
    """Where a character was first met or last mentioned: the session (by its start), the line
    (`Said.number`) and DMbot's description of what was happening ("" until it has been written)."""

    session_at: int
    line: int
    scene: str = ""


@dataclass(frozen=True, slots=True)
class Summary:
    who: str | None  # None until the job has written it
    facts_key: str | None  # a hash of the facts the sentence was made from
    made_at: int | None
    first: Mention | None
    last: Mention | None
    scanned_at: int | None  # when the first and last were last worked out


class CardSummaries:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def get(self, guild_id: int, campaign_id: str, entity_id: str) -> Summary | None:
        found = await self.get_many(guild_id, campaign_id, [entity_id])
        return found.get(entity_id)

    async def get_many(
        self, guild_id: int, campaign_id: str, entity_ids: Sequence[str]
    ) -> dict[str, Summary]:
        """The rows kept for these characters of this campaign; a character with none is absent."""
        if not entity_ids:
            return {}
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT * FROM memory_card_summaries"
                " WHERE guild_id = %s AND campaign_id = %s AND entity_id = ANY(%s)",
                (guild_id, campaign_id, list(entity_ids)),
            )
            return {str(r["entity_id"]): _summary(r) for r in await cur.fetchall()}

    async def put_who(
        self, guild_id: int, campaign_id: str, entity_id: str, who: str, facts_key: str
    ) -> bool:
        """Keep the sentence and the hash of the facts it was made from; the first and last
        mention stay as they are. False when the character is gone (a merge or a removal)."""
        text = " ".join(who.split())[:WHO_MAX]
        if not text:
            return False
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO memory_card_summaries"
                " (guild_id, campaign_id, entity_id, who, facts_key, made_at)"
                " SELECT %s, %s, e.id, %s, %s, %s FROM memory_entities e"
                " WHERE e.guild_id = %s AND e.campaign_id = %s AND e.id = %s"
                " ON CONFLICT (guild_id, campaign_id, entity_id) DO UPDATE SET"
                " who = EXCLUDED.who, facts_key = EXCLUDED.facts_key, made_at = EXCLUDED.made_at",
                (
                    guild_id,
                    campaign_id,
                    text,
                    facts_key,
                    int(self._now()),
                    guild_id,
                    campaign_id,
                    entity_id,
                ),
            )
            return bool(cur.rowcount)

    async def put_mentions(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        first: Mention | None,
        last: Mention | None,
    ) -> bool:
        """Keep where the character was first met and last mentioned (None: never, at the table);
        the sentence stays as it is. False when the character is gone."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO memory_card_summaries"
                " (guild_id, campaign_id, entity_id, first_session, first_line, first_scene,"
                " last_session, last_line, last_scene, scanned_at)"
                " SELECT %s, %s, e.id, %s, %s, %s, %s, %s, %s, %s FROM memory_entities e"
                " WHERE e.guild_id = %s AND e.campaign_id = %s AND e.id = %s"
                " ON CONFLICT (guild_id, campaign_id, entity_id) DO UPDATE SET"
                " first_session = EXCLUDED.first_session, first_line = EXCLUDED.first_line,"
                " first_scene = EXCLUDED.first_scene, last_session = EXCLUDED.last_session,"
                " last_line = EXCLUDED.last_line, last_scene = EXCLUDED.last_scene,"
                " scanned_at = EXCLUDED.scanned_at",
                (
                    guild_id,
                    campaign_id,
                    first.session_at if first else None,
                    first.line if first else None,
                    _scene(first),
                    last.session_at if last else None,
                    last.line if last else None,
                    _scene(last),
                    int(self._now()),
                    guild_id,
                    campaign_id,
                    entity_id,
                ),
            )
            return bool(cur.rowcount)

    async def put_scene(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        *,
        which: str,
        scene: str,
    ) -> bool:
        """Write the scene of the first or the last mention (`which`: "first" or "last") once the
        job has made it, on a row whose mention is already kept; the line it describes is
        unchanged. False when there is no such mention (it moved meanwhile) or no row."""
        if which not in ("first", "last"):
            raise ValueError("which is first or last")
        text = " ".join(scene.split())[:SCENE_MAX]
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"UPDATE memory_card_summaries SET {which}_scene = %s"
                f" WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s"
                f" AND {which}_session IS NOT NULL",
                (text, guild_id, campaign_id, entity_id),
            )
            return bool(cur.rowcount)


def _scene(mention: Mention | None) -> str | None:
    if mention is None or not mention.scene.strip():
        return None
    return " ".join(mention.scene.split())[:SCENE_MAX]


def _mention(session: object, line: object, scene: object) -> Mention | None:
    if session is None or line is None:
        return None
    return Mention(int(str(session)), int(str(line)), str(scene or ""))


def _summary(row: dict[str, object]) -> Summary:
    made = row["made_at"]
    scanned = row["scanned_at"]
    who = row["who"]
    key = row["facts_key"]
    return Summary(
        who=None if who is None else str(who),
        facts_key=None if key is None else str(key),
        made_at=None if made is None else int(str(made)),
        first=_mention(row["first_session"], row["first_line"], row["first_scene"]),
        last=_mention(row["last_session"], row["last_line"], row["last_scene"]),
        scanned_at=None if scanned is None else int(str(scanned)),
    )
