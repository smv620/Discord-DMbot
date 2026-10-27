"""Where the text for meaning search comes from (#2664): one `Source` for each kind of owner, each
reading ONE campaign (`memory/embedding.py` runs them).

- **Lines** (`LineSource`): the spoken or typed lines of the campaign's sessions that are about the
  game. Not DMbot's sidebar questions and answers (those are DMbot's own words in a conversation),
  not pictures, not table talk or off-topic talk. The speaker is carried so the run can check, right
  before and right after the call, that the person still agrees to be recorded. A person's words:
  their numbers go with the line (migration 0248).
- **Entries** (`EntitySource`): a confirmed entry's name and the description kept for it, the DM's
  and DMbot's own words about the story. Never a guess still waiting for the DM, and never an entry
  merged into another.

**Secret and DM-only entries are embedded too** (the numbers serve the DM's own search as well), so
every reader of the numbers must filter before anything reaches a player: `meaning.entry_texts(...,
for_players=True)` is the filter, checked live and never from what was true when they were made.

PlotBot's notes, the DM's notes and shared lore are added here as they are found in the store;
each is one more class with a `kind` and a `pieces`.
"""

from __future__ import annotations

from dmbot.db import Database
from dmbot.memory import vectors as vs
from dmbot.memory.embedding import Piece, pieces_of


class LineSource:
    kind = vs.LINE

    def __init__(self, db: Database) -> None:
        self._db = db

    async def pieces(self, guild_id: int, campaign_id: str) -> list[Piece]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT l.id, l.user_id, coalesce(l.text, l.heard) AS text"
                " FROM transcript_lines l JOIN transcript_sessions s"
                "  ON s.id = l.session_id AND s.guild_id = l.guild_id"
                " WHERE l.guild_id = %s AND s.campaign_id = %s AND l.sidebar IS NULL"
                " AND NOT l.image AND l.topic = 'game'"
                " AND char_length(btrim(coalesce(l.text, l.heard))) > 0"
                " ORDER BY l.id",
                (guild_id, campaign_id),
            )
            rows = await cur.fetchall()
        pieces: list[Piece] = []
        for r in rows:
            pieces += pieces_of(vs.LINE, str(r["id"]), r["text"], speaker=int(r["user_id"]))
        return pieces


class EntitySource:
    kind = vs.ASSERTION

    def __init__(self, db: Database) -> None:
        self._db = db

    async def pieces(self, guild_id: int, campaign_id: str) -> list[Piece]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, name, description FROM memory_entities"
                " WHERE guild_id = %s AND campaign_id = %s AND status = 'confirmed'"
                " AND merged_into IS NULL AND char_length(btrim(description)) > 0"
                " ORDER BY id",
                (guild_id, campaign_id),
            )
            rows = await cur.fetchall()
        pieces: list[Piece] = []
        for r in rows:
            pieces += pieces_of(vs.ASSERTION, str(r["id"]), f"{r['name']}: {r['description']}")
        return pieces
