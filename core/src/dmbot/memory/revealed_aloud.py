"""Names the table has now said (#2404, docs/PLAN.md, "Players View / Full View"):
a name the story is holding back from the players (a backstory name, or one the DM added by hand)
becomes revealed when someone at the table says it or types it. Pure code, no AI.

Only whole names count, compared the way names are everywhere (`spelling_key`: case, accents,
apostrophes and hyphens ignored), by the name or any of its other names. Only lines of people who
are recorded now count, the DM's own table speech included; never the DM sidebar (the DM's
questions to DMbot and its answers), DMbot's own messages, the DM screen or the backstory text. A
🤫 secret spelling is never looked for here: a secret said aloud has its own question.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from dmbot.memory.models import spelling_key
from dmbot.memory.secret_aloud import MIN_KEY
from dmbot.memory.store import MemoryStore
from dmbot.transcript.models import Line

MAX_LISTED = 10  # names the report lists; the rest are counted


@dataclass(frozen=True, slots=True)
class Unrevealed:
    entity_id: str
    name: str
    spellings: tuple[str, ...]  # its name and every other, non-secret name


def table_lines(lines: Iterable[Line], *, recorded: Collection[int]) -> list[str]:
    """The words said or typed at the table: lines by someone in `recorded`, never the sidebar.
    Both the cleaned and the as-heard words, since the cleaner may have respelled a name."""
    out: list[str] = []
    for line in lines:
        if line.user_id not in recorded or line.sidebar:
            continue
        if line.text:
            out.append(line.text)
        if line.heard and line.heard.strip() != line.text.strip():
            out.append(line.heard)
    return out


def said(names: Iterable[Unrevealed], texts: Iterable[str]) -> list[Unrevealed]:
    """The held-back names the table said, in the order of `names`. A name counts when it, or one
    of its other names, appears as whole words in any line."""
    wanted: list[tuple[Unrevealed, list[re.Pattern[str]]]] = []
    for item in names:
        keys = {spelling_key(s) for s in item.spellings}
        patterns = [re.compile(rf"(?<=\s){re.escape(k)}(?=\s)") for k in keys if len(k) >= MIN_KEY]
        if patterns:
            wanted.append((item, patterns))
    found: dict[str, Unrevealed] = {}
    for text in texts:
        padded = f" {spelling_key(text)} "
        for item, patterns in wanted:
            if item.entity_id not in found and any(p.search(padded) for p in patterns):
                found[item.entity_id] = item
    return [item for item, _ in wanted if item.entity_id in found]


async def unrevealed_names(
    memory: MemoryStore, guild_id: int, campaign_id: str
) -> list[Unrevealed]:
    """This campaign's confirmed entries the players haven't been shown, with the names they may
    be said by (never a 🤫 spelling, a clue, a person, a player's character or the party). Any of
    its other names said reveals the entry: the DM held the entry back, not one spelling. Read for
    this server and campaign only."""
    async with memory._read(guild_id, campaign_id) as scope:
        cur = await scope.conn.execute(
            "SELECT e.id, e.name, a.text FROM memory_entities e JOIN memory_aliases a"
            " ON a.guild_id = e.guild_id AND a.campaign_id = e.campaign_id AND a.entity_id = e.id"
            " WHERE e.guild_id = %s AND e.campaign_id = %s AND e.status = 'confirmed'"
            " AND e.hidden_from_players AND NOT e.audience_limited AND NOT e.party"
            " AND e.type NOT IN ('person', 'clue') AND e.role IS DISTINCT FROM 'player_character'"
            " AND e.played_by IS NULL AND a.status = 'confirmed' AND NOT a.secret"
            " ORDER BY e.id, a.id",
            scope.ids,
        )
        rows = await cur.fetchall()
    by_entity: dict[str, tuple[str, list[str]]] = {}
    for r in rows:
        by_entity.setdefault(str(r["id"]), (str(r["name"]), []))[1].append(str(r["text"]))
    return [Unrevealed(i, name, tuple(texts)) for i, (name, texts) in by_entity.items()]
