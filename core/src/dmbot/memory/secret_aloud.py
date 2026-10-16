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
    """The words players said at the table: lines by someone in `players` (the people recorded now
    who are not the campaign's DMs), leaving out the sidebar (the DM's questions to DMbot and its
    answers). Off-topic lines are kept: the raw transcript shows them too, so a secret muttered
    there is just as out. Both the cleaned and the as-heard words are read, since the cleaner may
    have respelled a name; a line where they differ counts as two, which only affects the order."""
    out: list[str] = []
    for line in lines:
        if line.user_id not in players or line.sidebar:
            continue
        if line.text:
            out.append(line.text)
        if line.heard and line.heard.strip() != line.text.strip():
            out.append(line.heard)
    return out


_POSSESSIVE = re.compile(r"['’]s\b")


def _key(text: str) -> str:
    """A name or a stretch of speech as compared here: "Auril's frost" is Auril too, so a
    possessive goes before the usual `name_key` (on both sides, or "Dragon's Rest" never meets
    itself)."""
    return name_key(_POSSESSIVE.sub("", text))


def spoken(secrets: Sequence[SecretName], texts: Iterable[str]) -> list[Said]:
    """The secret names found in the players' words, most said first (ties by name). A name counts
    once for each time it appears as whole words."""
    wanted: list[tuple[SecretName, str, re.Pattern[str]]] = []
    seen: set[str] = set()
    for secret in secrets:  # one question for a name, however many entries or spellings share it
        key = _key(secret.text)
        if len(key) >= MIN_KEY and key not in seen:
            seen.add(key)
            wanted.append((secret, key, re.compile(rf"(?<=\s){re.escape(key)}(?=\s)")))
    counts: dict[str, int] = {}
    for text in texts:
        padded = f" {_key(text)} "
        for secret, key, pattern in wanted:
            if key not in padded:  # cheap test first: most lines name no secret
                continue
            found = len(pattern.findall(padded))
            if found:
                counts[secret.alias_id] = counts.get(secret.alias_id, 0) + found
    by_id = {s.alias_id: s for s, _, _ in wanted}
    out = [Said(a, by_id[a].text, n) for a, n in counts.items()]
    out.sort(key=lambda s: (-s.times, name_key(s.text), s.alias_id))
    return out  # all of them; the caller asks about the first `MAX_ASKED` and says there are more


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
