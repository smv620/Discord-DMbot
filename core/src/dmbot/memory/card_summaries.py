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
from typing import Literal

from dmbot.db import Database
from dmbot.memory import meaning

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

    @property
    def hidden(self) -> bool:
        """The job ran and kept no sentence because the players may not see this entry (a row made
        by `put_nothing`): its card line says DMbot writes it only from what the players may know,
        never "knows little" (a canned sentence is kept for that) and never "still writing" (no
        row at all, or a row with no `made_at`)."""
        return self.who is None and self.made_at is not None


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

    async def parts(
        self, guild_id: int, campaign_id: str, entity_ids: Sequence[str]
    ) -> dict[str, dict[str, object]]:
        """What the campaign holds about each of these confirmed, unmerged entries that is safe for
        the players to read (`dmbot_entity_parts`: it leaves out what the DM keeps secret and what
        the players don't know yet): the card's sentence is built from this and nothing else, so
        the same sentence is safe for the players' view later. One campaign's entries only."""
        if not entity_ids:
            return {}
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, name, dmbot_entity_parts(guild_id, campaign_id, id) AS parts"
                " FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                " AND id = ANY(%s) AND status = 'confirmed' AND merged_into IS NULL",
                (guild_id, campaign_id, list(entity_ids)),
            )
            # (the function gives nothing for an entry with no description: only its name is known)
            return {
                str(r["id"]): dict(r["parts"])
                if r["parts"]
                else {"name": r["name"], "description": "", "aliases": [], "links": []}
                for r in await cur.fetchall()
            }

    async def player_facts(
        self, guild_id: int, campaign_id: str, entity_id: str
    ) -> tuple[str, dict[str, object], str] | Literal[False] | None:
        """What the players may be told about one entry, for the card's sentence: (name, parts,
        text), or None when the entry may not be shown to the players (the same rule as
        `meaning.entry_texts(for_players=True)`: held by the table, not a person's card, not a
        story-planning kind, not unnamed, its name not a secret), or False when it is not a
        confirmed entry of this campaign at all. The text is "" for an entry with no description."""
        found = (await self.parts(guild_id, campaign_id, [entity_id])).get(entity_id)
        if found is None:
            return False
        shown = await meaning.entry_texts(
            self._db, guild_id, campaign_id, [entity_id], for_players=True, described_only=False
        )
        if entity_id not in shown:
            return None
        return str(found.get("name") or ""), found, shown[entity_id]

    async def put_nothing(
        self, guild_id: int, campaign_id: str, entity_id: str, facts_key: str
    ) -> bool:
        """The job ran and there is no sentence to keep (the entry may not be shown to the
        players): the sentence is cleared, so one made before it was hidden does not stay, and the
        row reads `hidden`: the card says DMbot writes this line only from what the players may
        know, which is neither "knows little" (a real sentence is kept for that) nor "still
        writing" (no row yet). False when the entry is gone."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO memory_card_summaries"
                " (guild_id, campaign_id, entity_id, who, facts_key, made_at)"
                " SELECT %s, %s, e.id, NULL, %s, %s FROM memory_entities e"
                " WHERE e.guild_id = %s AND e.campaign_id = %s AND e.id = %s"
                " ON CONFLICT (guild_id, campaign_id, entity_id) DO UPDATE SET"
                " who = NULL, facts_key = EXCLUDED.facts_key, made_at = EXCLUDED.made_at",
                (
                    guild_id,
                    campaign_id,
                    facts_key,
                    int(self._now()),
                    guild_id,
                    campaign_id,
                    entity_id,
                ),
            )
            return bool(cur.rowcount)

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
