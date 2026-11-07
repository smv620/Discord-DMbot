"""Importing older sessions from the website (#2105): the preview, the check, the confirm and the
progress of a DM loading transcripts DMbot did not record (Craig's text export, a timestamped
file, or DMbot's own download).

The website's database role has no grant on the transcript or consent tables, so everything goes
through the doors in migration 0136 (`dmbot_import_*`), each held to a DM of the campaign. Who may
keep their lines is decided twice: here, to show the DM what will happen (`import_plan.make_plan`),
and again inside the store door, which drops any line of someone not recorded now whatever it is
sent. Nothing a speaker said is logged, returned in an answer or kept: the files exist only in the
request. The cleaning and the off-topic labelling run afterwards in the bot
(`transcript.import_job`).

Paid plans only, judged by the campaign owner's plan, and never Try It.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from dmbot import fetch, plan_rules
from dmbot import progress as progress_shape
from dmbot.consent import TERMS_VERSION
from dmbot.consent_dm import request_text
from dmbot.db import Conn, Database
from dmbot.entitlements import plan_grant_access
from dmbot.recaps.notify import job_ref, parse_ref
from dmbot.transcript import import_link, importer
from dmbot.transcript import import_plan as ip
from dmbot.transcript import uploader_words as uw
from dmbot.transcript.import_job import MAX_WINDOWS
from dmbot.transcript.topics import WINDOW_LINES
from dmbot.web import import_clock, recap_choices
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

log = logging.getLogger(__name__)

MAX_FILES = 20
MAX_BYTES = 2 * 1024 * 1024
# All the files as JSON text: a quote or newline takes two bytes and a control character six, so
# 2.5x covers the worst text a legal file can hold (a file full of newlines and quotes is 2x);
# the plan around them gets 256 KB. Each file is still held to MAX_BYTES once decoded.
MAX_BODY_BYTES = int(MAX_FILES * MAX_BYTES * 2.5) + 256 * 1024
MAX_SUGGESTED = 100  # labels asked about at once (the door takes no more)
MAX_LABEL = 80  # the table's own limit on a remembered label
MAX_LINES = 20_000  # lines in one file; each is a stored row and a share of an AI call
MAX_LINES_ALL = 60_000  # lines in one import
MAX_SPEAKERS = 50  # labels in one file (each is a lookup)
MAX_SPEAKERS_ALL = 100  # labels in one import
_DATE = re.compile(r"(?<!\d)(\d{4})[-_.](\d{2})[-_.](\d{2})(?!\d)")


@dataclass(frozen=True, slots=True)
class File:
    """One uploaded file, read once: its transcript, or why it can't be used."""

    name: str
    transcript: importer.Transcript | None
    # unreadable, unknown_format, no_speaker_names, too_big, not_txt (a name that is not
    # .txt, .vtt or .srt), empty, too_many_lines or too_many_speakers
    error: str | None
    date: str | None  # a date found in the name or the head of the text, YYYY-MM-DD


def _suggested_date(name: str, text: str) -> str | None:
    for source in (name, text[:600]):
        found = _DATE.search(source)
        if found is not None:
            try:
                return dt.date(int(found[1]), int(found[2]), int(found[3])).isoformat()
            except ValueError:
                continue
    return None


ACCEPTED_NAMES = (".txt", ".vtt", ".srt")  # a caption track is often saved as .vtt or .srt


def read_file(name: str, text: object) -> File:
    """A file from the request. The text is held only for this call."""
    name = str(name)[:200]
    if not name.lower().endswith(ACCEPTED_NAMES):  # (the format is still told by the content)
        return File(name, None, "not_txt", None)
    if not isinstance(text, str):
        return File(name, None, "unreadable", None)
    if len(text.encode("utf-8")) > MAX_BYTES:
        return File(name, None, "too_big", None)
    if not text.strip():
        return File(name, None, "empty", None)
    try:
        transcript = importer.parse(text)
    except importer.TranscriptError as why:
        # the reader's reason, as a code the page puts in plain words (never a word of the file)
        known = (importer.UNKNOWN_FORMAT, importer.NO_SPEAKER_NAMES)
        return File(name, None, why.code if why.code in known else "unreadable", None)
    if len(transcript.utterances) > MAX_LINES:
        return File(name, None, "too_many_lines", None)
    if len(transcript.speakers) > MAX_SPEAKERS:
        return File(name, None, "too_many_speakers", None)
    return File(name, transcript, None, _suggested_date(name, text))


def read_files(
    raw: object, from_links: Sequence[tuple[str, str | None, str | None]] = ()
) -> list[File]:
    """The request's `files` (a list of {name, text}) and the docs read from its `links`
    ((name, text, error code) each), together at most `MAX_FILES`."""
    if raw is None and from_links:
        raw = []
    if not isinstance(raw, list) or not (raw or from_links):
        raise RecapError("bad_files")
    if len(raw) + len(from_links) > MAX_FILES:
        raise RecapError("too_many_files")
    out: list[File] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise RecapError("bad_files")
        out.append(read_file(item["name"], item.get("text")))
    for name, text, error in from_links:  # a doc then follows exactly the uploaded-file path
        out.append(File(name, None, error, None) if error else read_file(name, text))
    if len({f.name for f in out}) != len(out):
        raise RecapError("bad_files")  # two files with one name can't be told apart in a plan
    return out


LINKS_DEADLINE_S = 45  # all the links of one request together, under any proxy's timeout
_links_open = asyncio.Semaphore(2)  # of the reader's 3 places, at most 2 for imports at once


async def read_links(
    raw_files: object, raw_links: object
) -> list[tuple[str, str | None, str | None]]:
    """Each Google Doc link read once, in order, named "Google Doc N.txt" (the export has no
    title). A doc that can't be read carries its error code instead of text; nothing is kept
    and the link is never logged. Links count with the files toward the 20. The uploads are
    checked first (a bad request fetches nothing), the same link is read once, and the whole
    batch has one deadline, so one request can't hold the reader's places for minutes."""
    if raw_links is None:
        return []
    if not isinstance(raw_links, list):
        raise RecapError("bad_files")
    uploads = raw_files if isinstance(raw_files, list) else []
    if raw_files is not None and not isinstance(raw_files, list):
        raise RecapError("bad_files")
    if len(uploads) + len(raw_links) > MAX_FILES:
        raise RecapError("too_many_files")
    names = [i.get("name") if isinstance(i, dict) else None for i in uploads]
    if not all(isinstance(n, str) for n in names) or len(set(names)) != len(names):
        raise RecapError("bad_files")
    named = [f"Google Doc {n}.txt" for n in range(1, len(raw_links) + 1)]
    if set(named) & set(names):
        raise RecapError("bad_files")  # an upload already has a doc's name

    async def one(link: object) -> tuple[str | None, str | None]:
        try:
            async with _links_open:
                return await import_link.read_doc(link), None
        except import_link.DocError as err:
            return None, err.code

    # The same document twice (in any spelling of its address) is read once and the repeat is
    # refused by name, so a copy-paste slip never imports one session twice.
    first: dict[str, int] = {}
    repeated: set[int] = set()
    for n, link in enumerate(raw_links):
        export = fetch.google_doc_export(link) if isinstance(link, str) else None
        key = str(export) if export is not None else None
        if key is None:
            continue
        if key in first:
            repeated.add(n)
        else:
            first[key] = n
    tasks: dict[int, asyncio.Future[tuple[str | None, str | None]]] = {}
    got: dict[int, tuple[str | None, str | None]] = {}
    try:
        for n, link in enumerate(raw_links):
            if n not in repeated:
                tasks[n] = asyncio.ensure_future(one(link))
        if tasks:
            await asyncio.wait(tasks.values(), timeout=LINKS_DEADLINE_S)
        for n, task in tasks.items():
            try:
                got[n] = task.result() if task.done() else (None, import_link.UNREACHABLE)
            except Exception:  # never a 500 from one doc; the link is not logged
                got[n] = (None, import_link.UNREACHABLE)
    finally:
        for task in tasks.values():  # also on a cancelled request: nothing keeps reading
            if not task.done():
                task.cancel()
    return [(name, *got.get(n, (None, import_link.REPEATED_LINK))) for n, name in enumerate(named)]


async def read_files_off_loop(raw: object, raw_links: object = None) -> list[File]:
    """`read_files` on a worker thread: parsing up to 40 MB must not hold up the API's other
    requests. Only after the caller is known to be a DM of the campaign on a paid plan. Google
    Doc links are read first (up to three at a time, the reader's own limit)."""
    from_links = await read_links(raw, raw_links)
    files = await asyncio.to_thread(read_files, raw, from_links)
    total = sum(len(f.transcript.utterances) for f in files if f.transcript)
    if total > MAX_LINES_ALL:
        raise RecapError("too_many_lines")
    if sum(len(f.transcript.speakers) for f in files if f.transcript) > MAX_SPEAKERS_ALL:
        raise RecapError("too_many_speakers")
    return files


@dataclass(frozen=True, slots=True)
class Campaign:
    guild_id: int
    campaign_id: str
    agreed: frozenset[int]  # the server's members who are recorded now (current terms)
    owner: int | None
    # Members who said no (#3305): 'stopped' (had agreed) or 'declined' (never did).
    refused: Mapping[int, str] = field(default_factory=dict)
    # The campaign's speakers without Discord, by lower-cased name -> negative id.
    speakers: Mapping[str, int] = field(default_factory=dict)


def _ref(ref: str) -> tuple[int, str]:
    parsed = parse_ref(f"{ref}.0")
    if parsed is None:
        raise RecapError("not_found")
    return parsed[0], parsed[1]


def ref_parts(ref: str) -> tuple[int, str]:
    """(server id, campaign id) from a campaign's address; RecapError("not_found") if it is not one.
    Reads the address only, so the on/off gate asks nothing of the database."""
    return _ref(ref)


async def _open(conn: Conn, session: Session, ref: str, *, outside: str | None) -> Campaign:
    """The campaign for a DM of it, or a refusal: not in the server (not_found) or not a DM."""
    gid, cid = _ref(ref)
    _guild_of(session, gid)
    await _scoped(conn, gid)
    cur = await conn.execute(
        "SELECT dmbot_import_agreed(%s, %s, %s, %s) AS r", (gid, cid, TERMS_VERSION, outside)
    )
    found = (await cur.fetchone() or {}).get("r")
    if found is None:
        raise RecapError("not_a_dm")
    owner = found.get("owner")
    return Campaign(
        gid,
        cid,
        frozenset(int(u) for u in found["agreed"]),
        int(owner) if isinstance(owner, str) and owner.isdecimal() else None,
        {int(u): str(kind) for u, kind in (found.get("refused") or {}).items()},
        {str(sp["name"]).casefold(): int(sp["id"]) for sp in found.get("speakers") or []},
    )


async def _plan_allows(db: Database, owner: int | None, now: int, *, enforce_plans: bool) -> None:
    """Paid plans only, from the campaign owner's plan: a plan that works but has no copies is
    Try It (`try_it_campaign`); no plan, or an ended one, is `not_on_a_paid_plan`."""
    if not enforce_plans:
        return
    access = None
    if owner is not None:
        _plan, _grant, access = await plan_grant_access(db, owner, now)
    if plan_rules.allowed(plan_rules.RULE_OF["transcript"], access):
        return
    raise RecapError(
        "try_it_campaign" if access is not None and access.works else "not_on_a_paid_plan"
    )


async def recap_facts(
    db: Database,
    session: Session,
    ref: str,
    c: Campaign,
    *,
    site_recaps_on: bool,
    now: int,
    enforce_plans: bool,
) -> dict[str, Any]:
    """Whether a recap can be written for this campaign, for the import page's tick (#2225): the
    same gates the bot runs for an imported session (`_import_recap`), in the same order, read
    without spending anything. `recapsPossible`; when it is false, `recapsWhy` is one of `off`
    (switched off for the server), `campaign_off` (the DM turned recaps off), `plan` (the owner's
    plan has none). The bot's fourth reason, a campaign with no DM, cannot happen here: the
    person asking is a DM of the campaign, and the door checked it. The page only learns whether to
    offer the tick: a check that cannot be made (a database hiccup) hides it, with no reason,
    rather than failing the import page; and the bot's own engine being absent is not known here,
    so a tick may still be offered that the bot does not honour (it says so on the DM screen).
    `recapsWhy` is absent when a recap is possible."""
    try:
        return await _recap_facts(
            db, session, ref, c, site_recaps_on=site_recaps_on, now=now, enforce_plans=enforce_plans
        )
    except Exception:
        log.exception("Import: couldn't tell whether a recap can be written")
        return {"recapsPossible": False}


async def _recap_facts(
    db: Database,
    session: Session,
    ref: str,
    c: Campaign,
    *,
    site_recaps_on: bool,
    now: int,
    enforce_plans: bool,
) -> dict[str, Any]:
    if not site_recaps_on:
        return {"recapsPossible": False, "recapsWhy": "off"}
    choices = await recap_choices.read_choices(
        db, session, ref, now=now, enforce_plans=enforce_plans
    )
    if not choices["text"]:
        return {"recapsPossible": False, "recapsWhy": "campaign_off"}
    if enforce_plans:
        access = None
        if c.owner is not None:  # (a campaign with no owner has no plan, as for the bot)
            _plan, _grant, access = await plan_grant_access(db, c.owner, now)
        if not plan_rules.allowed(plan_rules.RULE_OF["recap"], access):
            return {"recapsPossible": False, "recapsWhy": "plan"}
    return {"recapsPossible": True}


def estimate(files: Sequence[File], kept: Mapping[str, int] | None = None) -> dict[str, int]:
    """Roughly how many fast-tier calls the off-topic labelling will make: one for each window of
    lines (`topics.WINDOW_LINES`). The cleaning of names makes no call."""
    calls = 0
    sessions = 0
    for f in files:
        if f.transcript is None:
            continue
        lines = len(f.transcript.utterances) if kept is None else kept.get(f.name, 0)
        if lines:
            sessions += 1
            calls += min(math.ceil(lines / WINDOW_LINES), MAX_WINDOWS)
    return {"sessions": sessions, "fastCalls": calls}


def _display(label: str) -> str:
    """ "Cerric (stormy#1234)" is "cerric": the name people use, case ignored."""
    return label.split(" (", 1)[0].strip().casefold()


async def _members(conn: Conn, c: Campaign, q: str, outside: str | None) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT dmbot_import_members(%s, %s, %s, %s, %s) AS r",
        (c.guild_id, c.campaign_id, q[:60], TERMS_VERSION, outside),
    )
    found = (await cur.fetchone() or {}).get("r")
    out = []
    for member in found or []:
        kind = member.pop("refused", None)  # said no: the page greys them out (#3305)
        out.append({**member, "blocked": BLOCKED_WORDS.get(kind) if kind else None})
    return out


def consent_words(*, cloud: bool, company: str | None) -> dict[str, Any]:
    """The words the uploader confirms for each speaker (#3305): the same consent request a
    player sees in Discord, which says the whole server can read transcripts, and the version of
    the wording. The page sends the version back with the import and a stale one is refused.
    Once `UPLOADER_WORDS_LIVE` is on these are the uploader's own words
    (`transcript.uploader_words`) and the tick's words, `{name}` filled in by the page."""
    if uw.UPLOADER_WORDS_LIVE:  # the uploader's own words, with their own version (#3305)
        return {"version": uw.version(), "text": uw.TEXT, "tick": uw.TICK}
    return {
        "version": TERMS_VERSION,
        "text": request_text("this server", voice=None, dm=None, cloud=cloud, company=company),
    }


async def speakers(
    db: Database, session: Session, ref: str, *, outside: str | None, now: int, enforce_plans: bool
) -> dict[str, Any]:
    """The campaign's speakers without Discord: id (a negative number as text, opaque to the
    page), name and how many lines each has. Names and counts only. DMs only."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        c = await _open(conn, session, ref, outside=outside)
    await _plan_allows(db, c.owner, now, enforce_plans=enforce_plans)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        c = await _open(conn, session, ref, outside=outside)
        cur = await conn.execute(
            "SELECT dmbot_import_speakers(%s, %s) AS r", (c.guild_id, c.campaign_id)
        )
        found = (await cur.fetchone() or {}).get("r")
    if found is None:
        raise RecapError("not_a_dm")
    return {"speakers": list(found)}


SPEAKER_ID = re.compile(r"^-?[1-9][0-9]{0,18}$")
REMOVAL_ID = re.compile(r"^[0-9a-f]{32}$")
# What a failed removal says, as the code the page turns into words.
REMOVAL_REASONS = {"failed"}


async def remove_speaker(
    db: Database, session: Session, ref: str, speaker: str, *, outside: str | None,
    now: int, enforce_plans: bool,
) -> dict[str, Any]:  # fmt: skip
    """Ask the bot to remove one speaker's lines from this campaign (#3305): `{status, id}`.
    DMs only, a speaker of this campaign only. Not held to a paid plan, on purpose: taking
    someone's words out is never something to put behind a payment. The same id again while one is
    open for the speaker. `nothing` when a member has no lines here, `unknown_speaker` for a number
    that is nobody's here, `too_soon` past twenty requests an hour."""
    if not SPEAKER_ID.match(speaker) or abs(int(speaker)) > 2**63 - 1:
        raise RecapError("unknown_speaker")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        c = await _open(conn, session, ref, outside=outside)
        cur = await conn.execute(
            "SELECT dmbot_speaker_remove(%s, %s, %s) AS r",
            (c.guild_id, c.campaign_id, int(speaker)),
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_a_dm")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return {"status": "queued", "id": str(got["id"])}


async def speaker_removal(
    db: Database, session: Session, ref: str, removal_id: str, *, outside: str | None,
    now: int | None = None,
) -> dict[str, Any]:  # fmt: skip
    """Where a removal stands, in the shared progress shape, with the counts once it is done.
    A single transaction in the bot, so no "n of N": the stage, how long it has run and when it
    last moved. Counts only, never a word of a transcript. DMs only."""
    if not REMOVAL_ID.match(removal_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        c = await _open(conn, session, ref, outside=outside)
        cur = await conn.execute(
            "SELECT dmbot_speaker_removal_read(%s, %s, %s) AS r",
            (c.guild_id, c.campaign_id, removal_id),
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_found")
    reason = got["error"] or None
    shape = progress_shape.shape(
        got["stage"],
        started_at=int(got["createdAt"]),
        updated_at=int(got["updatedAt"]),
        now=int(time.time()) if now is None else now,
        reason=reason if reason in REMOVAL_REASONS else ("failed" if reason else None),
    )
    done = got["state"] == "done"
    return {
        **shape,
        "speaker": got["speaker"],
        "lines": int(got["lines"]) if done else None,
        "sessions": int(got["sessions"]) if done else None,
    }


async def authorize(
    db: Database, session: Session, ref: str, *, outside: str | None, now: int, enforce_plans: bool
) -> None:
    """The DM and the plan, from the address alone: asked before a request's body is read, so a
    person who may not import never makes the API read or decode 40 MB."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        c = await _open(conn, session, ref, outside=outside)
    await _plan_allows(db, c.owner, now, enforce_plans=enforce_plans)


async def search_members(
    db: Database,
    session: Session,
    ref: str,
    q: str,
    *,
    outside: str | None,
    now: int,
    enforce_plans: bool,
) -> dict[str, Any]:
    async with db.user(session.user_id, session=session.id_hash) as conn:
        c = await _open(conn, session, ref, outside=outside)
    await _plan_allows(db, c.owner, now, enforce_plans=enforce_plans)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        c = await _open(conn, session, ref, outside=outside)
        members = await _members(conn, c, q, outside)
    return {"members": members}


async def _remembered(
    conn: Conn, c: Campaign, put: Mapping[str, int] | None, outside: str | None
) -> dict[str, str]:
    cur = await conn.execute(
        "SELECT dmbot_import_mapping(%s, %s, %s::jsonb, %s, %s) AS r",
        (c.guild_id, c.campaign_id, json.dumps(put) if put else None, TERMS_VERSION, outside),
    )
    return dict((await cur.fetchone() or {}).get("r") or {})


MAX_PLAYS = 100


def plays_of(raw: object, remembered: Mapping[str, int]) -> list[dict[str, str]]:
    """The DM's "plays which character" choices, {label: character name}, as [{user, name}]:
    for the labels this import maps to a member only, so a dropped label or one the request
    never mapped is ignored. The bot makes each a link later (the website cannot write story
    memory); a name that is not a character nobody plays is dropped by the door."""
    if not isinstance(raw, dict):
        return []
    out: list[dict[str, str]] = []
    for label, name in raw.items():
        member = remembered.get(label) if isinstance(label, str) else None
        if member is None or not isinstance(name, str) or not name.strip() or len(name) > 100:
            continue
        out.append({"user": str(member), "name": name.strip()})
        if len(out) >= MAX_PLAYS:
            break
    return out


async def _plays_chosen(
    conn: Conn, c: Campaign, wanted: list[dict[str, str]], outside: str | None
) -> None:
    if not wanted:
        return
    await conn.execute(
        "SELECT dmbot_import_plays(%s, %s, %s::jsonb, %s, %s) AS r",
        (c.guild_id, c.campaign_id, json.dumps(wanted), TERMS_VERSION, outside),
    )


def _file_view(f: File) -> dict[str, Any]:
    return {
        "name": f.name,
        "format": f.transcript.format if f.transcript else None,
        "error": f.error,
        "suggestedDate": f.date,
        "lineCount": len(f.transcript.utterances) if f.transcript else 0,
        "leftOut": f.transcript.cut_out if f.transcript else 0,
    }


async def preview(
    db: Database,
    session: Session,
    ref: str,
    raw_files: object,
    *,
    links: object = None,
    outside: str | None,
    now: int,
    enforce_plans: bool,
    site_recaps_on: bool = False,  # (false hides the tick: the app always passes the real switch)
) -> dict[str, Any]:
    """What is in the files: format, date, speakers with their line counts and a suggested member
    for each, and whether a recap can be written for the campaign. Stores nothing."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        c = await _open(conn, session, ref, outside=outside)
    await _plan_allows(db, c.owner, now, enforce_plans=enforce_plans)
    files = await read_files_off_loop(raw_files, links)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        c = await _open(conn, session, ref, outside=outside)
        remembered = await _remembered(conn, c, None, outside)
        labels = list(
            dict.fromkeys(
                s.label
                for f in files
                if f.transcript is not None
                for s in ip.speakers(f.transcript)
            )
        )
        guesses = await _suggest(conn, c, labels, remembered, outside)
        out = []
        for f in files:
            view = _file_view(f)
            view["speakers"] = []
            if f.transcript is not None:
                for s in ip.speakers(f.transcript):
                    member, character = guesses.get(s.label, (None, None))
                    who = member.get("userId") if isinstance(member, dict) else None
                    refused = (
                        c.refused.get(int(who))
                        if isinstance(who, str) and who.isdecimal()
                        else None
                    )
                    shown = label_parts(s.label)[0] or s.label.strip()
                    known = c.speakers.get(shown.casefold())
                    view["speakers"].append(
                        {
                            "label": s.label,
                            "lines": s.lines,
                            "suggested": member,
                            "suggestedCharacter": character,
                            # Only for a member who said no; the page greys them out (#3305).
                            "blocked": BLOCKED_WORDS.get(refused) if refused else None,
                            # A speaker without Discord this campaign already has by that name.
                            "speaker": (
                                {"id": str(known), "name": shown} if known is not None else None
                            ),
                        }
                    )
            out.append(view)
    facts = await recap_facts(
        db, session, ref, c, site_recaps_on=site_recaps_on, now=now, enforce_plans=enforce_plans
    )
    return {"files": out, "estimate": estimate(files), **facts}


def label_parts(label: str) -> tuple[str, str]:
    """("Cerric (stormy#1234)") is ("Cerric", "stormy"): the display part and the username part of
    Craig's "Display (username)", the old "#1234" tag left off. A label with no brackets has no
    username part."""
    shown, _, rest = label.partition(" (")
    user = rest.rstrip(")").split("#", 1)[0].strip() if rest.endswith(")") else ""
    return shown.strip(), user


async def _suggest(
    conn: Conn,
    c: Campaign,
    labels: Sequence[str],
    remembered: Mapping[str, str],
    outside: str | None,
) -> dict[str, tuple[dict[str, Any] | None, dict[str, str] | None]]:
    """A member and a character to suggest for each label, never decided: the member remembered
    from an earlier import, else the one whose Discord username is the label's username part, else
    whose display name or nickname is its name, else the one the table already calls that. The
    character is the one the member already plays, else a confirmed character with the label's
    name. The database does the matching in one read-only door (migration 0161), so the DM's
    campaign names never leave it. The DM confirms every one."""
    wanted = []
    for label in labels[:MAX_SUGGESTED]:
        shown, user = label_parts(label)
        entry: dict[str, str] = {"label": label, "user": user, "shown": shown}
        if label in remembered:
            entry["member"] = remembered[label]
        wanted.append(entry)
    cur = await conn.execute(
        "SELECT dmbot_import_suggest(%s, %s, %s::jsonb, %s, %s) AS r",
        (c.guild_id, c.campaign_id, json.dumps(wanted), TERMS_VERSION, outside),
    )
    found = (await cur.fetchone() or {}).get("r") or {}
    return {
        label: (guess.get("member"), guess.get("character"))
        for label, guess in found.items()
        if isinstance(guess, dict)
    }


# One code for both kinds of no: the page may say only that this person said no. "stopped" would
# tell the uploader they once agreed, "declined" that they were asked (#3305, ux-critic).
BLOCKED_WORDS = {"stopped": "said_no", "declined": "said_no"}
NEW_SPEAKER_BASE = -(10**12)  # placeholder ids for speakers not made yet; real ones are small


@dataclass(frozen=True, slots=True)
class Choices:
    """What the uploader chose for each speaker label (#3305). `mapping`: label -> member id,
    `ip.DROP`, or a placeholder (below `NEW_SPEAKER_BASE`) for a speaker without Discord that is
    made when the import is stored. `ticked`: the member ids the uploader confirmed. `new`:
    placeholder -> the name to make it with."""

    mapping: Mapping[str, int | str | None]
    ticked: frozenset[int] = frozenset()
    new: Mapping[int, str] = field(default_factory=dict)

    @property
    def confirmed(self) -> frozenset[int]:
        return self.ticked | frozenset(self.new)


def _member_id(who: object) -> int:
    if (
        isinstance(who, str)
        and who.isascii()
        and who.isdecimal()
        and len(who) <= 19
        and 0 < int(who) < 2**63
    ):
        return int(who)
    raise RecapError("bad_mapping")


def choices_of(raw: object) -> Choices:
    """The request's choices. A label maps to a member id as text (as before), `"drop"`/null, or an
    object: `{"member": id}`, `{"confirmed": true, "member"?: id}` (no member: a speaker without
    Discord, kept by the label's name), or `{"leave_out": true}`. A tick on a speaker with no
    member that is not `true` is `not_ticked`."""
    if not isinstance(raw, dict):
        raise RecapError("bad_mapping")
    mapping: dict[str, int | str | None] = {}
    ticked: set[int] = set()
    new: dict[int, str] = {}
    for label, who in raw.items():
        if not isinstance(label, str) or len(label) > MAX_LABEL:
            raise RecapError("bad_mapping")
        if who == ip.DROP or who is None:
            mapping[label] = ip.DROP
        elif isinstance(who, dict):
            if who.get("leave_out") is True:
                mapping[label] = ip.DROP
            elif "member" in who and who.get("confirmed") is not True:
                mapping[label] = _member_id(who["member"])
            elif who.get("confirmed") is True and "member" in who:
                member = _member_id(who["member"])
                mapping[label] = member
                ticked.add(member)
            elif who.get("confirmed") is True:
                name = label_parts(label)[0] or label.strip()
                placeholder = NEW_SPEAKER_BASE - len(new)
                mapping[label] = placeholder
                new[placeholder] = name
            else:
                raise RecapError("not_ticked")
        else:
            mapping[label] = _member_id(who)
    return Choices(mapping, frozenset(ticked), new)


def refuse(choices: Choices, c: Campaign) -> None:
    """Checked on the server, whatever the page showed: nobody who said no (Stop recording me, or
    turned DMbot down) can be ticked in."""
    if any(member in c.refused for member in choices.ticked):
        raise RecapError("blocked")


def _plans(files: Sequence[File], choices: Choices, c: Campaign) -> dict[str, ip.Plan]:
    return {
        f.name: ip.make_plan(
            f.transcript, choices.mapping, agreed=c.agreed, confirmed=choices.confirmed
        )
        for f in files
        if f.transcript is not None
    }


async def check(
    db: Database,
    session: Session,
    ref: str,
    raw_files: object,
    raw_mapping: object,
    *,
    links: object = None,
    outside: str | None,
    now: int,
    enforce_plans: bool,
) -> dict[str, Any]:
    """The mapping applied to the files, without storing: per speaker, the lines kept and the
    lines dropped by reason."""
    choices = choices_of(raw_mapping)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        c = await _open(conn, session, ref, outside=outside)
    refuse(choices, c)
    await _plan_allows(db, c.owner, now, enforce_plans=enforce_plans)
    files = await read_files_off_loop(raw_files, links)
    plans = _plans(files, choices, c)
    return {
        "files": [
            {
                **{k: v for k, v in _file_view(f).items() if k in ("name", "error")},
                "leftOut": plans[f.name].left_out if f.name in plans else 0,
                "speakers": [
                    {"label": r.label, "kept": r.kept, "dropped": r.dropped}
                    for r in (plans[f.name].speakers if f.name in plans else ())
                ],
            }
            for f in files
        ],
        "estimate": estimate(files, {n: p.kept for n, p in plans.items()}),
    }


def _started_at(date: object, order: object, now: int) -> int:
    """The session's start (Unix seconds) from the DM's date: 12:00 UTC of that day (so a DM
    anywhere on earth sees the day they meant), plus the order within the day in seconds, so two
    files on one day stay in the DM's order. A session can't be on a day that has not happened
    yet (#3483): the page uses the DM's own calendar, so the server allows one day ahead of UTC
    (a DM east of UTC is never refused today) and refuses anything later (`future_date`)."""
    if not isinstance(date, str):
        raise RecapError("bad_date")
    try:
        day = dt.date.fromisoformat(date)
    except ValueError:
        raise RecapError("bad_date") from None
    if not dt.date(2000, 1, 1) <= day <= dt.date(2100, 1, 1):
        raise RecapError("bad_date")
    if day > dt.datetime.fromtimestamp(now, dt.UTC).date() + dt.timedelta(days=1):
        raise RecapError("future_date")
    extra = (
        order if isinstance(order, int) and not isinstance(order, bool) and 0 <= order < 3600 else 0
    )
    noon = dt.datetime(day.year, day.month, day.day, 12, tzinfo=dt.UTC)
    return int(noon.timestamp()) + extra


async def confirm(
    db: Database,
    session: Session,
    ref: str,
    raw_files: object,
    raw_plan: object,
    *,
    links: object = None,
    outside: str | None,
    now: int,
    enforce_plans: bool,
    site_recaps_on: bool = False,
) -> dict[str, Any]:
    """Store the sessions. Everything is checked before anything is stored, and all the files are
    stored in one transaction: all of them, or none."""
    if not isinstance(raw_plan, dict):
        raise RecapError("bad_mapping")
    choices = choices_of(raw_plan.get("mapping"))
    # The words the uploader saw when ticking: a stale version is refused so the page shows the
    # current ones (#3305). Nothing to confirm, nothing to check.
    if (choices.ticked or choices.new) and raw_plan.get("consentVersion") != uw.version():
        raise RecapError("stale_consent")
    entries = raw_plan.get("sessions")
    if not isinstance(entries, list) or not entries:
        raise RecapError("bad_date")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        c = await _open(conn, session, ref, outside=outside)
    refuse(choices, c)
    await _plan_allows(db, c.owner, now, enforce_plans=enforce_plans)
    # Asked before anything is stored: an answer that cannot be had never fails a stored import.
    facts = await recap_facts(
        db, session, ref, c, site_recaps_on=site_recaps_on, now=now, enforce_plans=enforce_plans
    )
    files = await read_files_off_loop(raw_files, links)
    if len(entries) > len(files):
        raise RecapError("bad_files")  # one session for each file, no more
    by_name = {f.name: f for f in files}
    plans = _plans(files, choices, c)
    chosen: list[tuple[File, ip.Plan, int]] = []
    recaps_on: dict[str, bool] = {}  # the DM's "Write recaps" choice for each file (#2106)
    named: set[str] = set()
    for entry in entries:
        name = entry.get("file") if isinstance(entry, dict) else None
        found = by_name.get(name) if isinstance(name, str) else None
        if found is None or found.transcript is None or found.name in named:
            raise RecapError("bad_files")
        named.add(found.name)
        plan = plans[found.name]
        if plan.kept == 0:
            raise RecapError("no_lines_kept")
        chosen.append((found, plan, _started_at(entry.get("date"), entry.get("order"), now)))
        recaps_on[found.name] = entry.get("writeRecap") is True  # only a plain true counts
    batch = uuid.uuid4().hex
    stored: list[dict[str, str | bool]] = []
    async with db.user(session.user_id, session=session.id_hash) as conn:
        c = await _open(conn, session, ref, outside=outside)
        refuse(choices, c)  # asked again inside the transaction that stores
        # Speakers without Discord are made here, in the same transaction as the lines, so a failed
        # import leaves none behind. The same name is the same speaker (the door finds it).
        made: dict[int, int] = {}
        for placeholder, name in choices.new.items():
            cur = await conn.execute(
                "SELECT dmbot_import_speaker(%s, %s, %s) AS r", (c.guild_id, c.campaign_id, name)
            )
            speaker = (await cur.fetchone() or {}).get("r")
            if not isinstance(speaker, int) or speaker >= 0:
                raise RecapError("bad_mapping")
            made[placeholder] = speaker
        ticked = sorted(choices.ticked | frozenset(made.values()))
        for f, plan, started in chosen:
            lines = [
                {
                    "ms": line.started_ms,
                    "user": made.get(line.user_id, line.user_id),
                    "text": line.text,
                    "dur": line.duration_ms,
                }
                for line in plan.lines
            ]
            cur = await conn.execute(
                "SELECT dmbot_import_store("
                "%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s) AS r",
                (
                    c.guild_id,
                    c.campaign_id,
                    batch,
                    f.name,
                    started,
                    json.dumps(lines),
                    TERMS_VERSION,
                    outside,
                    json.dumps([str(t) for t in ticked]),
                    uw.version(),  # the words the uploader saw, kept with each tick
                ),
            )
            done = (await cur.fetchone() or {}).get("r")
            if done is None:
                raise RecapError("not_a_dm")
            if done.get("error") == "blocked":
                raise RecapError("blocked")
            if done.get("error"):
                raise RecapError("bad_mapping")
            if not done["session"]:
                raise RecapError("no_lines_kept")
            if recaps_on.get(f.name):
                cur = await conn.execute(
                    "SELECT dmbot_import_write_recap(%s, %s, %s, TRUE) AS r",
                    (c.guild_id, c.campaign_id, done["session"]),
                )
                if not (await cur.fetchone() or {}).get("r"):
                    raise RecapError("not_a_dm")
            stored.append(
                {"file": f.name, "state": "waiting", "writeRecap": recaps_on.get(f.name, False)}
            )
        labels = {s.label for f, _, _ in chosen if f.transcript for s in ip.speakers(f.transcript)}
        remembered = {
            k: v
            for k, v in choices.mapping.items()
            if isinstance(v, int) and v > 0 and k in labels  # (a member; never a speaker)
        }
        await _remembered(conn, c, remembered, outside)
        await _plays_chosen(conn, c, plays_of(raw_plan.get("plays"), remembered), outside)
    return {
        "importId": batch,
        "sessions": stored,
        "estimate": estimate([f for f, _, _ in chosen], {f.name: p.kept for f, p, _ in chosen}),
        **facts,
    }


# The progress stages in which a session has a recap, or one held for the DM's answers (#2240).
# They follow the stage list in `progress` and the SQL's `stage` CASE; the tests pin them.
RECAP_STAGES = frozenset(
    ("waiting_for_names", "starting_recap", "writing_recap", "recap_ready", "recap_failed")
)


def _queued(s: dict[str, Any]) -> bool:
    """Whether the session is waiting its turn rather than working: cleaning goes in the order the
    files were loaded, name scans and recaps in the order of the dates, behind every earlier
    session, so a later session that is ready can sit still while an earlier one moves."""
    stage = s["stage"]
    return (
        (stage == "cleaning" and s["state"] == "waiting")
        or (stage == "checking_names" and not s["scanned"])
        or stage == "starting_recap"
        or (stage == "writing_recap" and s["recapState"] == "queued")
    )


def _moved_of(s: dict[str, Any]) -> int:
    """When the session last moved: its own row, and at the recap stages the recap job's."""
    moved = int(s["moved"])
    if s["stage"] in RECAP_STAGES:
        moved = max(moved, int(s["recapMoved"]))
    return max(moved, int(s["imported"]))


def _shapes(found: list[dict[str, Any]], now: int) -> list[dict[str, Any]]:
    """Each session's progress in the shared shape. A session waiting its turn takes the newest
    movement of the whole import as its own, so it is never called stalled while the import is
    moving; an import where nothing has moved stalls, queued sessions included."""
    newest = max((_moved_of(s) for s in found), default=0)
    out = []
    for s in found:
        cleaning = s["stage"] == "cleaning"
        out.append(
            progress_shape.with_counts(
                progress_shape.shape(
                    s["stage"],
                    started_at=int(s["imported"]),
                    updated_at=max(newest, _moved_of(s)) if _queued(s) else _moved_of(s),
                    now=now,
                    done=int(s["stepDone"]) if cleaning else None,
                    total=int(s["stepTotal"]) if cleaning else None,
                    unit=progress_shape.UNITS.get(s["stage"]),
                    # Only a failure carries the job's own stored reason (a code).
                    reason=(s["reason"] or None) if s["stage"] == "failed" else None,
                ),
                words=int(s.get("wordsDone") or 0),
                calls=int(s.get("callsMade") or 0),
            )
        )
    return out


# (the story read's stages are its own list, not part of the SQL stage CASE above)
_STORY_STAGES = {
    "reading": "reading_connections",
    "waiting": "waiting_for_limit",
    "read": "connections_read",
}


def _story_shape(story: dict[str, Any] | None, now: int) -> dict[str, Any] | None:
    """The story read of one imported session (#3307) in the shared shape: reading (batch n of
    m, how long it has run, when it last moved), waiting (the daily limit or a failed batch
    stopped it; it carries on by itself) or read. None until it starts. Counts and times only."""
    stage = _STORY_STAGES.get(str((story or {}).get("state") or ""))
    if story is None or stage is None:
        return None
    reading = stage == "reading_connections"
    return progress_shape.shape(
        stage,
        started_at=int(story["started"]),
        updated_at=int(story["moved"]),
        now=now,
        done=int(story["part"]) if reading else None,
        total=int(story["parts"]) if reading else None,
        unit=progress_shape.UNITS.get(stage),
    )


async def progress(
    db: Database,
    session: Session,
    ref: str,
    import_id: str,
    *,
    outside: str | None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{32}", import_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        c = await _open(conn, session, ref, outside=outside)
        cur = await conn.execute(
            "SELECT dmbot_import_progress(%s, %s, %s) AS r", (c.guild_id, c.campaign_id, import_id)
        )
        found = (await cur.fetchone() or {}).get("r")
        clocks = {
            r["file"]: r
            for r in await import_clock.read_rows(conn, c.guild_id, c.campaign_id, import_id)
        }
    if not found:
        raise RecapError("not_found")
    now = int(time.time())
    parts = _shapes(found, now)
    return {
        # The whole import in the one shared shape (#2564), then each session's own `progress`.
        # A session's `state` stays the stored one (waiting, working, done, failed); the shape's
        # `state` is running, stalled, failed or done.
        "import": progress_shape.whole(parts, now=now),
        "sessions": [
            {
                "file": s["file"],
                "date": dt.datetime.fromtimestamp(int(s["date"]), dt.UTC).date().isoformat(),
                "state": s["state"],
                "reason": s["reason"] or None,
                # Where it has got to, for the page's progress: cleaning, checking_names,
                # finding_names (held while names are found, nothing to answer yet),
                # waiting_for_names, starting_recap, writing_recap, recap_ready, recap_failed,
                # recap_not_written, done or failed.
                "stage": s["stage"],
                "namesToAnswer": int(s["namesToAnswer"]),
                "namesUnchecked": bool(s["namesUnchecked"]),
                "progress": part,
                # The offer to set the game clock where this session ended (#3199), or None.
                "clock": import_clock.shown(clocks.get(s["file"])),
                # The story read (the connections found in what was said) in the same shape, or
                # None when it has not started (#3307).
                "story": _story_shape(s.get("story"), now),
                # Its own recap's reference, for the stages that have a recap or a held one: the
                # page then links the session's recap, not the list (#2240). The session time is
                # the door's `date`; the campaign is the one opened above (never another's).
                "recap": (
                    job_ref(c.guild_id, c.campaign_id, int(s["date"]))
                    if s["stage"] in RECAP_STAGES
                    else None
                ),
            }
            for s, part in zip(found, parts, strict=True)
        ],
    }
