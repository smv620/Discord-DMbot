"""The secret-said-aloud question (#1407, docs/STORY_MEMORY.md section 9): a name the DM keeps
secret from the players was said by a player at the table, so it is now in the raw transcript that
everyone may read. After the session DMbot asks the DM, privately, whether to keep it secret.

This is the pure finding: which secret names were said, by whom, how often. It reads no database
and calls no AI. A secret name here is a confirmed other name (alias) flagged secret, including
secret spellings; the main name of an entry is covered when the DM made it secret. Only whole
words count, compared the way names are everywhere (`name_key`: case, accents, apostrophes and
hyphens ignored), and only lines said by players who are recorded now: not the DM's own, not
DMbot's, not anyone who stopped being recorded.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

from dmbot.memory.models import name_key
from dmbot.memory.store import MemoryStore
from dmbot.transcript.models import Line

MAX_ASKED = 5  # names asked about after one session, most said first; the rest next time
MIN_KEY = 3  # a secret "name" shorter than this is too common a word to look for


@dataclass(frozen=True, slots=True)
class SecretName:
    alias_id: str
    text: str


@dataclass(frozen=True, slots=True)
class Said:
    alias_id: str
    text: str
    times: int


def players_lines(lines: Iterable[Line], *, players: Collection[int]) -> list[str]:
    """The words players said at the table: cleaned text of lines by someone in `players` (the
    people recorded now who are not the campaign's DMs), leaving out the sidebar (the DM's
    questions to DMbot and its answers) and anything the off-topic filter hid."""
    return [
        line.text
        for line in lines
        if line.user_id in players and not line.sidebar and line.topic != "off_topic" and line.text
    ]


def spoken(secrets: Sequence[SecretName], texts: Iterable[str]) -> list[Said]:
    """The secret names found in the players' words, most said first (ties by name), at most
    `MAX_ASKED`. A name counts once for each time it appears as whole words."""
    wanted: list[tuple[SecretName, str]] = []
    seen: set[str] = set()
    for secret in secrets:  # one question for a name, however many entries or spellings share it
        key = name_key(secret.text)
        if len(key) >= MIN_KEY and key not in seen:
            seen.add(key)
            wanted.append((secret, key))
    counts: dict[str, int] = {}
    for text in texts:
        # "Auril's frost" is Auril too: a possessive is dropped before names are compared.
        padded = f" {name_key(re.sub(r'[\'’]s\b', '', text))} "
        for secret, key in wanted:
            found = len(re.findall(rf"(?<=\s){re.escape(key)}(?=\s)", padded))
            if found:
                counts[secret.alias_id] = counts.get(secret.alias_id, 0) + found
    by_id = {s.alias_id: s for s, _ in wanted}
    out = [Said(a, by_id[a].text, n) for a, n in counts.items()]
    out.sort(key=lambda s: (-s.times, name_key(s.text), s.alias_id))
    return out[:MAX_ASKED]


async def secret_names(memory: MemoryStore, guild_id: int, campaign_id: str) -> list[SecretName]:
    """This campaign's confirmed secret names (other names and secret spellings, and a main name
    the DM made secret), read for this server and campaign only. A name on an entry that is
    itself rejected or only a guess is not asked about."""
    async with memory._read(guild_id, campaign_id) as scope:
        cur = await scope.conn.execute(
            "SELECT a.id, a.text FROM memory_aliases a JOIN memory_entities e"
            " ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id AND e.id = a.entity_id"
            " WHERE a.guild_id = %s AND a.campaign_id = %s AND a.secret AND a.status = 'confirmed'"
            " AND e.status = 'confirmed' AND e.type <> 'person' ORDER BY a.id",
            scope.ids,
        )
        return [SecretName(str(r["id"]), str(r["text"])) for r in await cur.fetchall()]
