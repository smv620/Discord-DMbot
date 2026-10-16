"""A campaign's session transcripts on the website (#1501): the list, a read view and the
download, for anyone in the server.

Transcripts are shared (CLAUDE.md, decided 2026-10-05): anyone in the Discord server may read and
download a campaign's, so unlike the recap pages this is not for DMs only. Two database doors
(`dmbot_web_transcript_sessions`, `dmbot_web_transcript_lines`, schema.py) hand over the stored
rows to any signed-in person whose session lists the server; the website's role has no grant on the
transcript tables. What the reader sees is made by the bot's own `transcript/export.py`
(`render`), the same text the Discord download carries: the cleaned version leaves out what
Discord's cleaned file leaves out (DM sidebar lines, typed chat, off-topic talk) and the
as-heard version is the raw transcript, sidebar lines included (docs/PLAN.md). There is no
second formatter.

Two things the website cannot do as Discord does. It has no Discord access, so a speaker's display
name is not known: each person who spoke is "Speaker 1", "Speaker 2" in the order they first
spoke in the session, and the file says so. And the bot's `{character}` labels come from the memory
store, which the website's role cannot read, so lines carry none.

The plan rule is the bot's (`plan_gate("transcript")`): transcripts belong to the plans with copies,
judged by the campaign's OWNER's plan, and only when plans are enforced. A campaign is named by
`<server>.<campaign>`, the recap's address without its last part; a session by its number.
"""

from __future__ import annotations

import asyncio
from typing import Any

from dmbot import plan_rules
from dmbot.db import Database
from dmbot.entitlements import plan_grant_access
from dmbot.recaps.notify import parse_ref
from dmbot.transcript import export
from dmbot.transcript.models import QUESTION_KINDS, Line, Lineage, TranscriptSession
from dmbot.web.me import MAX_GUILDS
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

# The website's names for the two versions, in the URL; export's own names go in the file's name.
VERSIONS = {"cleaned": export.CLEANED, "heard": export.AS_HEARD}

SPEAKER = "Speaker"
NAMES_NOTE = (
    "On the website, people are shown as Speaker 1, Speaker 2 and so on, in the order they first "
    "spoke in this session. Discord's download has their names."
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
        "sessions": [
            {
                "number": int(s["number"]),
                "startedAt": int(s["startedAt"]),
                "endedAt": None if s["endedAt"] is None else int(s["endedAt"]),
                "lines": int(s["lines"]),
            }
            for s in found["sessions"]
        ],
    }


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
    )


def speaker_names(lines: list[Line], version: str = export.AS_HEARD) -> dict[int, str]:
    """ "Speaker 1", "Speaker 2", ... in the order each person first appears in THIS version. The
    cleaned version leaves out typed chat and sidebar lines, so someone who only typed or asked
    DMbot is not numbered there: a gap in the numbers would hint at a hidden person."""
    names: dict[int, str] = {}
    for line in sorted(lines, key=lambda x: x.started_ms):
        if line.sidebar and line.sidebar not in QUESTION_KINDS:
            continue  # DMbot's own answers are shown as DMbot, not as a person
        if version == export.CLEANED and (line.sidebar or line.typed):
            continue
        names.setdefault(line.user_id, f"{SPEAKER} {len(names) + 1}")
    return names


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
    gives, made by `export.render`. A person outside the server, a missing campaign or session and
    an unknown version are "not found"."""
    if version not in VERSIONS:
        raise RecapError("not_found")
    guild_id, campaign_id, found = await _sessions(
        db, session, ref, now=now, enforce_plans=enforce_plans
    )
    wanted = next((s for s in found["sessions"] if int(s["number"]) == number), None)
    if wanted is None:
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_transcript_lines(%s, %s, %s) AS found",
            (guild_id, campaign_id, wanted["id"]),
        )
        row = await cur.fetchone()
    rows = row["found"] if row else None
    if rows is None:
        raise RecapError("not_found")
    lines = [_line(r) for r in rows]
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
    # Off the event loop, as the bot does: a long session is a lot of text to build.
    text = await asyncio.to_thread(
        export.render, found["name"], stored, lines, speaker_names(lines, kind), version=kind
    )
    return export.file_name(found["name"], stored, kind), text


async def read_session(
    db: Database,
    session: Session,
    ref: str,
    number: int,
    version: str,
    *,
    now: int,
    enforce_plans: bool,
) -> dict[str, Any]:
    """The same text as the download, split for reading: the notes at the top (`notes`) and one
    string per line of the transcript (`lines`)."""
    name, text = await render(
        db, session, ref, number, version, now=now, enforce_plans=enforce_plans
    )
    head, _, body = text.partition("\n\n")
    return {
        "number": number,
        "version": version,
        "fileName": name,
        "notes": [*head.split("\n"), NAMES_NOTE],
        "lines": body.rstrip("\n").split("\n") if body.strip() else [],
    }
