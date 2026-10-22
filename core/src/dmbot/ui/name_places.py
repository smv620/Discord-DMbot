"""The places a suggested name was said, for the review card (#2329): two short windows of the
transcript (the line before, the line with the name, the line after), "More places" for up to five
more, and a link to the session's page on the website.

The words are read from the session's own transcript when the card is drawn, from the pointers
kept with the suggestion (`memory/places.py`); nothing is copied. Table speech only, from people
who agreed (that is all a transcript holds), so the card shows nothing new; it is private to the
DM like the rest of the review. A transcript the retention rule deleted says so, and the count of
how many times it was heard stays."""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

import discord

from dmbot.discord_limits import fit_message
from dmbot.memory.places import SHOWN_AT_FIRST, cut, find_in, trimmed
from dmbot.transcript.export import clock
from dmbot.transcript.store import Around, TranscriptStore
from dmbot.ui.transcripts import display_names

log = logging.getLogger(__name__)

GONE = "The transcript for that session is gone."
BLOCK_MAX = 1100  # what the windows may add to a card: the whole card stays under Discord's 2000
SOMEONE = "Someone"


@dataclass(frozen=True, slots=True)
class Pointer:
    session_id: str
    started_ms: int
    user_id: int


def parse_ref(ref: str) -> Pointer | None:
    """A line reference (`transcript.models.line_ref`) back into its parts."""
    parts = ref.rsplit(":", 2)
    if len(parts) != 3:
        return None
    try:
        return Pointer(parts[0], int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _speak(names: Mapping[int, str], user_id: int) -> str:
    return discord.utils.escape_markdown(names.get(user_id) or SOMEONE)


def _plain(text: str) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))


def window_text(
    header: str,
    around: Around,
    names: Mapping[int, str],
    spellings: Sequence[str],
    *,
    width: int = 120,
) -> str:
    """One window: the session and time, then "speaker: words" for the line before, the line with
    the name in bold, and the line after."""
    user, said = around.line
    said = " ".join(said.split())
    found = find_in(said, spellings)
    if found:
        before, name, after = cut(said, found[0], found[1], width)
        body = f"{_plain(before)}**{_plain(name)}**{_plain(after)}"
    else:  # the line was respelled since: shown without the bold
        body = _plain(trimmed(said, width))
    rows = []
    if around.before:
        earlier = _plain(trimmed(around.before[1], width, from_end=True))
        rows.append(f"{_speak(names, around.before[0])}: {earlier}")
    rows.append(f"{_speak(names, user)}: {body}")
    if around.after:
        rows.append(f"{_speak(names, around.after[0])}: {_plain(trimmed(around.after[1], width))}")
    return f"_{header}_\n" + "\n".join(f"> {row}" for row in rows)


@dataclass(slots=True)
class Places:
    """What the card shows for one suggestion."""

    windows: list[str] = field(default_factory=list)  # the first (up to two) windows
    gone: bool = False  # some transcript is gone: say so once
    more: list[Pointer] = field(default_factory=list)  # kept for "More places"
    link: str = ""  # the session's page on the website, or ""

    def text(self) -> str:
        parts = list(self.windows)
        if self.gone:
            parts.append(GONE)
        block = "\n".join(parts)
        while len(block) > BLOCK_MAX and len(parts) > 1:
            parts.pop(1 if self.gone and len(parts) > 2 else len(parts) - 1)
            block = "\n".join(parts)
        return fit_message(block, limit=BLOCK_MAX)  # (never cut inside a bold name)


class _Sessions:
    """The sessions a card's pointers point into, each read once."""

    def __init__(self, transcripts: TranscriptStore, guild_id: int) -> None:
        self.transcripts = transcripts
        self.guild_id = guild_id
        self.found: dict[str, tuple[int, int, int | None] | None] = {}

    async def get(self, session_id: str) -> tuple[int, int, int | None] | None:
        """(its number, when it started, when its transcript was deleted) or None."""
        if session_id not in self.found:
            session = await self.transcripts.session(self.guild_id, session_id)
            self.found[session_id] = (
                None
                if session is None
                else (session.number, session.started_at, session.deleted_at)
            )
        return self.found[session_id]


async def _windows(
    guild: discord.Guild | None,
    transcripts: TranscriptStore,
    guild_id: int,
    pointers: Sequence[Pointer],
    spellings: Sequence[str],
    sessions: _Sessions,
    agreed: Collection[int],
) -> tuple[list[str], bool, int | None]:
    """(the windows, whether any transcript is gone, the first session's number)."""
    texts: list[str] = []
    gone = False
    first: int | None = None
    arounds: list[tuple[Pointer, Around, int, int]] = []
    for pointer in pointers:
        session = await sessions.get(pointer.session_id)
        if session is None or session[2] is not None:
            gone = True
            continue
        around = await transcripts.around(
            guild_id, pointer.session_id, pointer.started_ms, pointer.user_id, agreed
        )
        if around is None:
            gone = True
            continue
        arounds.append((pointer, around, session[0], session[1]))
        first = session[0] if first is None else first
    ids: set[int] = set()
    for _p, around, _n, _s in arounds:
        ids.update(x[0] for x in (around.before, around.line, around.after) if x)
    names = await display_names(guild, tuple(sorted(ids))) if ids else {}
    for pointer, around, number, started_at in arounds:
        header = f"Session {number}, {clock((pointer.started_ms - started_at * 1000) / 1000)}"
        texts.append(window_text(header, around, names, spellings))
    return texts, gone, first


async def first_places(
    guild: discord.Guild | None,
    transcripts: TranscriptStore,
    guild_id: int,
    campaign_id: str,
    refs: Collection[tuple[str, int | None]],
    spellings: Sequence[str],
    agreed: Collection[int],
    site_url: str = "",
) -> Places:
    """The card's two windows for these pointers (spread across the session, earliest first), and
    what "More places" will show. Never raises: a card without places is still a card."""
    # earliest session first, then in the order spoken
    parsed = [(at or 0, parse_ref(ref)) for ref, at in refs]
    pointers = [p for _at, p in sorted(
        ((at, p) for at, p in parsed if p is not None), key=lambda x: (x[0], x[1].started_ms)
    )]  # fmt: skip
    if not pointers:
        return Places()
    chosen = (
        pointers if len(pointers) <= SHOWN_AT_FIRST else [pointers[0], pointers[len(pointers) // 2]]
    )
    rest = [p for p in pointers if p not in chosen]
    try:
        sessions = _Sessions(transcripts, guild_id)
        texts, gone, number = await _windows(
            guild, transcripts, guild_id, chosen, spellings, sessions, agreed
        )
    except Exception:
        log.exception("Couldn't read where a suggested name was said")
        return Places()
    link = (
        f"{site_url.rstrip('/')}/account/transcripts/{guild_id}.{campaign_id}/{number}"
        if site_url and number is not None
        else ""
    )
    return Places(texts, gone, rest, link)


async def more_places(
    guild: discord.Guild | None,
    transcripts: TranscriptStore,
    guild_id: int,
    pointers: Sequence[Pointer],
    spellings: Sequence[str],
    agreed: Collection[int],
) -> str:
    """Up to five more windows, for "More places": one private message, kept short."""
    try:
        sessions = _Sessions(transcripts, guild_id)
        texts, gone, _ = await _windows(
            guild, transcripts, guild_id, pointers[:5], spellings, sessions, agreed
        )
    except Exception:
        log.exception("Couldn't read more places a suggested name was said")
        return GONE
    parts = [*texts, *([GONE] if gone else [])]
    return fit_message("\n".join(parts), limit=1900) or GONE
