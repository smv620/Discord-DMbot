"""A campaign's session transcripts on the website (#1501): the list, a read view and the
download, for anyone in the server.

Transcripts are shared (CLAUDE.md, decided 2026-10-05): anyone in the Discord server may read and
download a campaign's, so unlike the recap pages this is not for DMs only. Two database doors
(`dmbot_web_transcript_sessions`, `dmbot_web_transcript_lines`, schema.py) hand over the stored
rows to any signed-in person whose session lists the server; the website's role has no grant on the
transcript tables. What the reader sees is made by the bot's own `transcript/export.py`
(`render`), the same text the Discord download carries: the cleaned version leaves out what
Discord's cleaned file leaves out (DM sidebar lines, off-topic talk and typing) and the
as-heard version is the raw transcript, sidebar lines included (docs/PLAN.md). There is no
second formatter.

Two things the website cannot do as Discord does. It has no Discord access, so a speaker's display
name is the one the bot kept at the session (`transcript_sessions.speaker_names`, #2337); someone
whose name was never kept, or could not be found, is "Speaker 1", "Speaker 2" in the order they
first spoke, and the page says so. A speaker who is a DM of the campaign is marked "(Name, DM)".
And the bot's `{character}` labels come from the memory store, which the website's role cannot
read, so lines carry none.

The plan rule is the bot's (`plan_gate("transcript")`): transcripts belong to the plans with copies,
judged by the campaign's OWNER's plan, and only when plans are enforced. A campaign is named by
`<server>.<campaign>`, the recap's address without its last part; a session by its number.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any

from dmbot import plan_rules
from dmbot.db import Database
from dmbot.entitlements import plan_grant_access
from dmbot.recaps.notify import parse_ref
from dmbot.transcript import export
from dmbot.transcript.models import Line, Lineage, TranscriptSession
from dmbot.web.me import MAX_GUILDS
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

# The website's names for the two versions, in the URL; export's own names go in the file's name.
VERSIONS = {"cleaned": export.CLEANED, "heard": export.AS_HEARD}

SPEAKER = "Speaker"
FALLBACK = re.compile(rf"{SPEAKER} \d+( \(\d+\))?", re.IGNORECASE)
NAMES_NOTE = (
    "DMbot could not find the names of some people, so they are shown as Speaker 1, Speaker 2 and "
    "so on, in the order they first spoke in this session."
)


def _parse(ref: str) -> tuple[int, str]:
    parsed = parse_ref(f"{ref}.0")
    if parsed is None:
        raise RecapError("not_found")
    return parsed[0], parsed[1]


# Unlike the bot's `plan_gate`, which fails open so a slow database never locks a table out of a
# Discord button, a plan that cannot be read here fails (a 500): nothing is shown unchecked.
async def _plan_allows(db: Database, owner: str | None, now: int, *, enforce_plans: bool) -> None:
    """The bot's rule, from the campaign owner's plan: refuses with "not_in_plan". The owner's id
    comes from the door, which has already checked the caller is in the server."""
    if not enforce_plans:
        return
    access = None
    if owner is not None and owner.isascii() and owner.isdecimal():
        _plan, _grant, access = await plan_grant_access(db, int(owner), now)
    if not plan_rules.allowed(plan_rules.RULE_OF["transcript"], access):
        raise RecapError("not_in_plan")


async def list_campaigns(
    db: Database, session: Session, *, now: int, enforce_plans: bool
) -> list[dict[str, Any]]:
    """The campaigns with a saved transcript in every server of this session's list, for anyone
    in them: someone who only plays has no other list of campaigns on the site. Under the plan rule
    (the owner's plan, once per owner), so a campaign whose transcripts would be refused is left
    out. Each has its address (`<server>.<campaign>`), its name, its server's name and a count."""
    out: list[dict[str, Any]] = []
    allowed: dict[str | None, bool] = {}
    async with db.user(session.user_id, session=session.id_hash) as conn:
        found: list[tuple[Any, list[dict[str, Any]]]] = []
        for guild in session.guilds[:MAX_GUILDS]:
            await _scoped(conn, int(guild.id))
            cur = await conn.execute(
                "SELECT dmbot_web_transcript_campaigns(%s) AS found", (int(guild.id),)
            )
            row = await cur.fetchone()
            if row and row["found"]:
                found.append((guild, list(row["found"])))
    for guild, campaigns in found:
        for item in campaigns:
            owner = item.get("owner")
            if owner not in allowed:
                try:
                    await _plan_allows(db, owner, now, enforce_plans=enforce_plans)
                    allowed[owner] = True
                except RecapError:
                    allowed[owner] = False
            if allowed[owner]:
                out.append(
                    {
                        "ref": f"{guild.id}.{item['id']}",
                        "name": item["name"],
                        "serverName": guild.name,
                        "sessions": int(item["sessions"]),
                        "keepDays": _keep_days(item),
                    }
                )
    return out


async def _sessions(
    db: Database, session: Session, ref: str, *, now: int, enforce_plans: bool
) -> tuple[int, str, dict[str, Any]]:
    """(server, campaign, the door's answer: the campaign's name and sessions), or a refusal."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_transcript_sessions(%s, %s) AS found", (guild_id, campaign_id)
        )
        row = await cur.fetchone()
    found = row["found"] if row else None
    if found is None:
        raise RecapError("not_found")
    await _plan_allows(db, found.get("owner"), now, enforce_plans=enforce_plans)
    return guild_id, campaign_id, found


async def list_sessions(
    db: Database, session: Session, ref: str, *, now: int, enforce_plans: bool
) -> dict[str, Any]:
    """The campaign's name and its sessions that have lines, newest first: number, when it started
    and ended (Unix seconds; `endedAt` null while running or if it stopped early) and how many
    lines."""
    _g, _c, found = await _sessions(db, session, ref, now=now, enforce_plans=enforce_plans)
    return {
        "name": found["name"],
        # How long the campaign keeps transcripts (30, 90 or 365 days), or null for forever.
        "keepDays": _keep_days(found),
        "sessions": [
            {
                "number": int(s["number"]),
                "startedAt": int(s["startedAt"]),
                "endedAt": None if s["endedAt"] is None else int(s["endedAt"]),
                "lines": int(s["lines"]),
                # When the transcript was deleted (Unix seconds), else null: the list says so
                # in place of the line count, and the session has nothing to read or save.
                "transcriptsDeletedAt": _deleted_at(s),
            }
            for s in found["sessions"]
        ],
    }


def _keep_days(door: dict[str, Any]) -> int | None:
    days = door.get("keepDays")
    return None if days is None else int(days)


def _deleted_at(session: dict[str, Any]) -> int | None:
    stamp = session.get("transcriptsDeletedAt")
    return None if stamp is None else int(stamp)


def _line(row: dict[str, Any]) -> Line:
    return Line(
        started_ms=int(row["startedMs"]),
        user_id=int(row["userId"]),
        heard=row["heard"],
        text=row["text"],
        duration_ms=int(row["durationMs"]),
        topic=row["topic"],
        sidebar=row["sidebar"] or "",
        lineage=Lineage(
            ref=row["ref"] or "",
            reply_to=row["replyTo"] or "",
            via=row["via"] or "",
            stt=row["stt"] or "",
            model=row["model"] or "",
            prompt=row["prompt"] or "",
            sources=tuple(str(x) for x in row["sources"] or ()),
        ),
        entity=row["entity"] or "",
        typed=bool(row["typed"]),
        image=bool(row.get("image", False)),  # an older door sends none
    )


def speaker_names(
    lines: list[Line], version: str = export.AS_HEARD, stored: Mapping[int, str] | None = None
) -> dict[int, str]:
    """Each person's name in the order they first appear in THIS version: the one kept at the
    session (`stored`; two people with one name tell apart with " (2)"), else "Speaker 1",
    "Speaker 2", ... counted among those without a name. The cleaned version leaves out sidebar
    lines and typing clearly not about the game, so someone who only asked DMbot, or whose typing
    was all hidden, is not listed there: a gap in the numbers would hint at a hidden person."""
    kept = stored or {}
    names: dict[int, str] = {}
    unnamed = 0
    for user_id in export.speaker_order(lines, version):
        name = kept.get(user_id, "")
        # (a real name that looks like the stand-in is no name: it could pass for one)
        if name and not FALLBACK.fullmatch(export.person_name(name)):
            names[user_id] = name
        else:
            unnamed += 1
            names[user_id] = f"{SPEAKER} {unnamed}"
    return dict(export.distinct_names(names))


def has_fallback(lines: list[Line], names: Mapping[int, str]) -> bool:
    """Whether anyone shown in the file has no name of their own (the note at the top says so)."""
    spoke = {line.user_id for line in lines}
    return any(FALLBACK.fullmatch(name) for user, name in names.items() if user in spoke)


# The most lines read for one session, and the biggest text made from them: the bot's own limit for
# a file it sends (ui/transcripts.py FILE_LIMIT). Past either, "too big" and not a half transcript.
MAX_LINES = 200_000
FILE_LIMIT = 10 * 1024 * 1024
# How many of the transcript's lines one answer carries to the reading page ("Show more" asks for
# the next ones).
READ_PAGE = 2000


async def _all_lines(
    db: Database, session: Session, guild_id: int, campaign_id: str, sid: str
) -> list[Line]:
    """Every line of the session, read from the door a page of up to 5,000 at a time (its cap),
    each page in its own short transaction, joined in the order the speech started. The cursor is
    the door's own (started_ms, row), so a page boundary never splits or repeats a line."""
    lines: list[Line] = []
    cursor: dict[str, int] | None = None
    while True:
        async with db.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, guild_id)
            cur = await conn.execute(
                "SELECT dmbot_web_transcript_lines(%s, %s, %s, %s, %s) AS found",
                (
                    guild_id,
                    campaign_id,
                    sid,
                    None if cursor is None else cursor["ms"],
                    None if cursor is None else cursor["id"],
                ),
            )
            row = await cur.fetchone()
        page = row["found"] if row else None
        if page is None:
            raise RecapError("not_found")
        lines.extend(_line(r) for r in page["lines"])
        if len(lines) > MAX_LINES:
            raise RecapError("too_big")
        cursor = page["next"]
        if cursor is None:
            return lines


async def _characters(
    db: Database, session: Session, guild_id: int, campaign_id: str
) -> dict[int, str]:
    """Who plays which character, for the `{character}` labels the Discord file has (a DM never
    has one): from the door that holds the export's rule. None are shown rather than a failure."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_transcript_characters(%s, %s) AS found", (guild_id, campaign_id)
        )
        row = await cur.fetchone()
    found = row["found"] if row else None
    return {int(user): str(name) for user, name in (found or {}).items()}


async def _speakers(
    db: Database, session: Session, guild_id: int, campaign_id: str, sid: str
) -> tuple[dict[int, str], set[int]]:
    """(the names kept at the session, the speakers who are DMs of the campaign), from the door
    that holds them. None are shown rather than a failure, as for the characters."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_transcript_speakers(%s, %s, %s) AS found",
            (guild_id, campaign_id, sid),
        )
        row = await cur.fetchone()
    found = (row["found"] if row else None) or {}
    names = {int(u): str(n) for u, n in (found.get("names") or {}).items() if n}
    return names, {int(u) for u in found.get("dms") or ()}


async def _render(
    db: Database,
    session: Session,
    ref: str,
    number: int,
    version: str,
    *,
    now: int,
    enforce_plans: bool,
) -> tuple[str, str, int | None, bool]:
    """(the file's name, the file's text, when its transcript was deleted, whether anyone is
    shown as "Speaker N"). A deleted session has no lines to build a file from, so its name and
    text are empty and only the time is set."""
    if version not in VERSIONS:
        raise RecapError("not_found")
    guild_id, campaign_id, found = await _sessions(
        db, session, ref, now=now, enforce_plans=enforce_plans
    )
    wanted = next((s for s in found["sessions"] if int(s["number"]) == number), None)
    if wanted is None:
        raise RecapError("not_found")
    deleted = _deleted_at(wanted)
    if deleted is not None:
        return "", "", deleted, False
    lines = await _all_lines(db, session, guild_id, campaign_id, str(wanted["id"]))
    playing = await _characters(db, session, guild_id, campaign_id)
    kept, dms = await _speakers(db, session, guild_id, campaign_id, str(wanted["id"]))
    stored = TranscriptSession(
        id=str(wanted["id"]),
        guild_id=guild_id,
        campaign_id=campaign_id,
        started_at=int(wanted["startedAt"]),
        ended_at=None if wanted["endedAt"] is None else int(wanted["endedAt"]),
        lines=int(wanted["lines"]),
        number=int(wanted["number"]),
        engines=tuple(wanted["engines"]),
    )
    kind = VERSIONS[version]
    shown = speaker_names(lines, kind, kept)
    # Off the event loop, as the bot does: a long session is a lot of text to build.
    text = await asyncio.to_thread(
        export.render,
        found["name"],
        stored,
        lines,
        shown,
        characters=playing,
        dms=dms,
        version=kind,
    )
    if len(text.encode("utf-8")) > FILE_LIMIT:
        raise RecapError("too_big")
    return export.file_name(found["name"], stored, kind), text, None, has_fallback(lines, shown)


async def render(
    db: Database,
    session: Session,
    ref: str,
    number: int,
    version: str,
    *,
    now: int,
    enforce_plans: bool,
) -> tuple[str, str]:
    """(the file's name, the file's text) for one session in one version: what Discord's button
    gives, made by `export.render`. A person outside the server, a missing campaign or session, a
    session whose transcript was deleted (nothing to save) and an unknown version are "not
    found"."""
    name, text, deleted, _fallback = await _render(
        db, session, ref, number, version, now=now, enforce_plans=enforce_plans
    )
    if deleted is not None:
        raise RecapError("not_found")
    return name, text


async def read_session(
    db: Database,
    session: Session,
    ref: str,
    number: int,
    version: str,
    *,
    now: int,
    enforce_plans: bool,
    offset: int = 0,
) -> dict[str, Any]:
    """The same text as the download, split for reading: the notes at the top (`notes`) and one
    string per line of the transcript (`lines`), `READ_PAGE` of them from `offset`. `next` is the
    offset of the following page, or null on the last. The whole text is built each time (it is
    one document, with off-topic runs gathered across any boundary), so a page is a slice of it.

    A session whose transcript was deleted answers `transcriptsDeletedAt` (Unix seconds) with no
    lines, so a direct link to it says so rather than failing."""
    name, text, deleted, fallback = await _render(
        db, session, ref, number, version, now=now, enforce_plans=enforce_plans
    )
    if deleted is not None:
        return {
            "number": number,
            "version": version,
            "fileName": "",
            "notes": [],
            "lines": [],
            "next": None,
            "transcriptsDeletedAt": deleted,
        }
    head, _, body = text.partition("\n\n")
    everything = body.rstrip("\n").split("\n") if body.strip() else []
    start = max(0, offset)
    end = start + READ_PAGE
    return {
        "number": number,
        "version": version,
        "fileName": name,
        "notes": [*head.split("\n"), *([NAMES_NOTE] if fallback else [])],
        "lines": everything[start:end],
        "next": end if end < len(everything) else None,
    }
