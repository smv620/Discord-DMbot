"""The steps of the sample run (#3547): make the campaign, queue the lore, store the session,
wait for the bot, accept what was found, back up, score, write the recap. Each step takes the
services it uses (the database, the stores, a clock, a way to sleep and to say something), so a
test runs it on fakes and a real database with no AI and no Discord.

The paid work is done by the running bot, the same way the website's upload and import ask for
it: this tool puts the row in the queue and waits, printing counts only. Nothing of the lore or
the session is printed or logged.
"""

from __future__ import annotations

import contextlib
import io
import math
import os
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from pathlib import Path
from typing import Any

from dmbot.campaigns.models import Campaign, CampaignError
from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database
from dmbot.devtools.sample_run.plan import (
    NAME,
    STEPS,
    Existing,
    SampleError,
    Step,
    can_replace,
)
from dmbot.lore_upload import TEXT_MAX
from dmbot.transcript import import_plan as ip
from dmbot.transcript import importer

Say = Callable[[str], None]
Sleep = Callable[[float], Awaitable[None]]

PAID_KINDS = ("paid", "grant")  # a plan, or free access (the owner's own accounts have it)
LORE_CHOICES = ("world", "spoilers", "smart")
POLL_S = 5.0
LORE_WAIT_S = 45 * 60
IMPORT_WAIT_S = 90 * 60


def campaign_store(db: Database) -> CampaignStore:
    """The campaign store with every backup section registered, as the running bot has it: a
    backup then holds the whole campaign (the answer-key check needs its memory), and deleting a
    campaign clears what each section holds."""
    from dmbot.bot import register_backup_sections
    from dmbot.consent import ConsentStore

    store = CampaignStore(db)
    register_backup_sections(
        store, forget_audio=lambda _guild, _campaign: None, consenting=ConsentStore(db).consenting
    )
    return store


async def require_paid(
    db: Database,
    dm: int,
    now: int,
    access_of: Callable[..., Awaitable[Any]] | None = None,
) -> None:
    """The campaign's plan is its owner's, so the DM must have one. The tool never grants a plan."""
    if access_of is None:
        from dmbot.entitlements import plan_and_access

        access_of = plan_and_access
    _plan, access = await access_of(db, dm, now)
    if access.kind not in PAID_KINDS:
        raise SampleError("the DM account has no paid plan or free access; nothing was changed")


async def existing_samples(db: Database, campaigns: CampaignStore, guild: int) -> list[Existing]:
    """The campaigns in this server that carry the sample's name, with what each one holds."""
    out: list[Existing] = []
    for c in await campaigns.list_campaigns(guild):
        if c.name != NAME:
            continue
        async with db.guild(guild, snapshot=True) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM transcript_sessions WHERE guild_id = %s"
                " AND campaign_id = %s",
                (guild, c.id),
            )
            sessions = int((await cur.fetchone() or {}).get("n", 0))
            cur = await conn.execute(
                "SELECT count(*) AS n FROM session_imports WHERE guild_id = %s"
                " AND campaign_id = %s",
                (guild, c.id),
            )
            imported = int((await cur.fetchone() or {}).get("n", 0))
        out.append(Existing(c.id, c.owner_user_id, c.dm_user_ids, sessions, imported))
    return out


async def check_existing(
    db: Database, campaigns: CampaignStore, guild: int, dm: int
) -> list[Existing]:
    """The earlier sample campaigns, every one checked (`plan.can_replace`) before anything is
    changed. A refusal here changes nothing, which is why the run does it before it spends."""
    found = await existing_samples(db, campaigns, guild)
    for old in found:
        why = can_replace(old, dm)
        if why:
            raise SampleError(why)
    return found


async def fresh_campaign(
    db: Database,
    campaigns: CampaignStore,
    guild: int,
    dm: int,
    *,
    keep_old: bool,
    today: str,
    say: Say,
    found: list[Existing] | None = None,
) -> Campaign:
    """Retire the last run's campaign (deleted, or renamed with `keep_old`) and make a new one,
    paid because the DM owns it. `found`: what `check_existing` returned; asked again when not
    given. The delete re-checks ownership under the campaign's lock (`only_if_owner`)."""
    found = found if found is not None else await check_existing(db, campaigns, guild, dm)
    for old in found:
        if keep_old:
            await _keep(campaigns, guild, old.campaign_id, today)
        else:
            await campaigns.delete(guild, old.campaign_id, only_if_owner=dm)
    say(f"{len(found)} earlier sample campaign(s) {'kept' if keep_old else 'deleted'}")
    try:
        return await campaigns.create(guild, NAME, dm)
    except CampaignError as exc:  # e.g. a campaign whose name differs only in capitals
        raise SampleError(
            "the sample campaign could not be made (a campaign with a look-alike name exists)"
        ) from exc


async def _keep(campaigns: CampaignStore, guild: int, campaign_id: str, today: str) -> None:
    """Rename a kept campaign so the sample's name is free; it is never the exact name again, so
    the tool can never delete it."""
    for n in range(1, 50):
        name = f"{NAME} (kept {today}{'' if n == 1 else f' {n}'})"
        try:
            await campaigns.rename(guild, campaign_id, name)
            return
        except CampaignError:  # the name was taken: try the next
            continue
    raise SampleError("could not find a free name to keep the earlier campaign under")


def check_lore(text: str, choice: str) -> None:
    """What the website's door refuses, asked before anything is spent: the choice, an empty file
    and a file over the column's limit (whose text a database error would otherwise print)."""
    if choice not in LORE_CHOICES:
        raise SampleError("--lore-choice is world, spoilers or smart")
    if not text.strip():
        raise SampleError("the lore file is empty")
    if len(text) > TEXT_MAX:
        raise SampleError(f"the lore file is over the upload limit of {TEXT_MAX:,} characters")
    if "\x00" in text or any(0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        raise SampleError("the lore file holds characters the upload cannot take")


async def queue_lore(
    db: Database, guild: int, campaign_id: str, dm: int, text: str, *, choice: str, now: int
) -> str:
    """The lore, queued as the website's upload does (`dmbot_web_lore_add`): the DM's own words,
    the right to use them confirmed by the DM now (recorded as this run), the choice stored."""
    check_lore(text, choice)
    async with db.guild(guild) as conn:
        cur = await conn.execute(
            "INSERT INTO lore_uploads (guild_id, campaign_id, id, user_id, source, text, choice,"
            " rights_by, rights_at, created_at, updated_at) VALUES (%s, %s,"
            " replace(gen_random_uuid()::text, '-', ''), %s, 'file', %s, %s, %s, %s, %s, %s)"
            " RETURNING id",
            (guild, campaign_id, dm, text, choice, dm, now, now, now),
        )
        row = await cur.fetchone()
    assert row is not None
    return str(row["id"])


async def _lore_row(db: Database, guild: int, upload: str) -> dict[str, Any]:
    async with db.guild(guild, snapshot=True) as conn:
        cur = await conn.execute(
            "SELECT state, stage, error, done, total, names_found, links_found, words_done,"
            " calls_made FROM lore_uploads WHERE guild_id = %s AND id = %s",
            (guild, upload),
        )
        return dict(await cur.fetchone() or {})


async def _stop_lore(db: Database, guild: int, upload: str) -> None:
    """The tool gave up waiting: take the upload out of the queue (and blank its words, as an
    upload nobody took is), so the bot does not go on spending after the tool has said it failed."""
    async with db.guild(guild) as conn:
        await conn.execute(
            "UPDATE lore_uploads SET state = 'failed', error = 'not_taken', text = '', link = ''"
            " WHERE guild_id = %s AND id = %s AND state = 'waiting'",
            (guild, upload),
        )


async def wait_lore(
    db: Database,
    guild: int,
    upload: str,
    *,
    say: Say,
    sleep: Sleep,
    clock: Callable[[], float] = time.monotonic,
    limit_s: float = LORE_WAIT_S,
    poll_s: float = POLL_S,
) -> dict[str, int | str]:
    """Wait for the running bot to read the upload, printing the counts as they move. Counts
    only: the stage, done of total, the words and calls so far."""
    began, last = clock(), ""
    while True:
        row = await _lore_row(db, guild, upload)
        if not row:
            raise SampleError("the lore upload is gone")
        seen = (
            f"lore {row['state']}/{row['stage']} {row['done']}/{row['total']}"
            f" words {row['words_done']} calls {row['calls_made']}"
        )
        if seen != last:
            say(seen)
            last = seen
        if row["state"] == "done":
            return {
                "names": int(row["names_found"]),
                "connections": int(row["links_found"]),
                "words": int(row["words_done"]),
                "calls": int(row["calls_made"]),
            }
        if row["state"] == "failed":
            raise SampleError(f"the lore read failed ({row['error'] or 'no reason'})")
        if clock() - began > limit_s:
            await _stop_lore(db, guild, upload)
            raise SampleError(
                "the lore was not read in time: is the bot running and the lore switch on?"
            )
        await sleep(poll_s)


async def preflight_session(
    guild: int,
    players: Mapping[str, int],
    text: str,
    agreed: Collection[int],
    refused: Collection[int],
    *,
    started_at: int,
) -> ip.Plan:
    """Everything that can refuse the session file, before anything is spent: it must read, every
    speaker must have an account in `--players`, every test account must have agreed in Discord
    (the recap reads lines by that agreement, so a run without it could not make its recap) and
    none may have said no, and some line must be kept. Returns the plan the import stores."""
    try:
        transcript = importer.parse(text)
    except importer.TranscriptError as exc:
        raise SampleError(f"the session file cannot be read ({exc.code})") from exc
    labels = {s.label for s in ip.speakers(transcript)}
    unmapped = labels - set(players)
    if unmapped:
        raise SampleError(f"{len(unmapped)} speaker(s) in the file have no --players entry")
    ids = set(players.values())
    if ids & set(refused):
        raise SampleError("a test account has said no to being recorded")
    silent = ids - set(agreed)
    if silent:
        raise SampleError(
            f"{len(silent)} test account(s) have not agreed to being recorded in Discord yet"
        )
    plan = ip.make_plan(transcript, dict(players), agreed=ids, base_ms=started_at * 1000)
    if plan.kept == 0:
        raise SampleError("no line of the session file was kept")
    return plan


async def refused_accounts(db: Database, guild: int, ids: Collection[int]) -> set[int]:
    async with db.guild(guild, snapshot=True) as conn:
        cur = await conn.execute(
            "SELECT user_id FROM consent_refusals WHERE guild_id = %s AND user_id = ANY(%s)",
            (guild, sorted(ids)),
        )
        return {int(r["user_id"]) for r in await cur.fetchall()}


async def store_import(
    db: Database,
    guild: int,
    campaign_id: str,
    dm: int,
    plan: ip.Plan,
    *,
    started_at: int,
    now: int,
    file_name: str = "sample-session.txt",
) -> str:
    """Store the planned session as an imported one, in ONE transaction like the import door
    (the import page's store door): the session, its lines, and the `session_imports` row the bot's
    cleaning picks up. Either all of it is there or none, so a stop half way never leaves a
    session the guard would take for a played one. The test accounts have agreed, so no
    uploader confirmation is recorded. Returns the session's id."""
    sid = os.urandom(16).hex()
    import_id = os.urandom(16).hex()
    last = max(line.started_ms + line.duration_ms for line in plan.lines)
    speakers = sorted({line.user_id for line in plan.lines})
    async with db.guild(guild) as conn:
        await conn.execute(
            "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, ended_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            (sid, guild, campaign_id, started_at, max(started_at, last // 1000)),
        )
        await conn.execute(
            "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard,"
            " duration_ms) SELECT %s, %s, l.ms, l.uid, l.heard, l.dur FROM unnest("
            "%s::bigint[], %s::bigint[], %s::text[], %s::int[]) AS l(ms, uid, heard, dur)",
            (
                guild,
                sid,
                [x.started_ms for x in plan.lines],
                [x.user_id for x in plan.lines],
                [x.heard for x in plan.lines],
                [x.duration_ms for x in plan.lines],
            ),
        )
        await conn.execute(
            "UPDATE transcript_sessions SET line_count = %s, speakers = %s WHERE id = %s",
            (plan.kept, speakers, sid),
        )
        await conn.execute(
            "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id, file_name,"
            " imported_by, imported_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (guild, campaign_id, sid, import_id, file_name, dm, now),
        )
    return sid


async def _import_row(db: Database, guild: int, session: str) -> dict[str, Any]:
    async with db.guild(guild, snapshot=True) as conn:
        cur = await conn.execute(
            "SELECT state, reason, scanned, scan_finished, step_done, step_total, words_done,"
            " calls_made, story_state, story_part, story_parts FROM session_imports"
            " WHERE guild_id = %s AND session_id = %s",
            (guild, session),
        )
        return dict(await cur.fetchone() or {})


async def _stop_import(db: Database, guild: int, session: str) -> None:
    """The tool gave up waiting: stop the import where it is, so the bot does not go on spending."""
    async with db.guild(guild) as conn:
        await conn.execute(
            "UPDATE session_imports SET state = 'failed', reason = 'stopped' WHERE guild_id = %s"
            " AND session_id = %s AND state IN ('waiting', 'working')",
            (guild, session),
        )


STORY_GRACE_S = 180.0  # more than the import rounds' gap (30 s) and a sweep to pick the story up


async def wait_import(
    db: Database,
    guild: int,
    session: str,
    *,
    say: Say,
    sleep: Sleep,
    clock: Callable[[], float] = time.monotonic,
    limit_s: float = IMPORT_WAIT_S,
    poll_s: float = POLL_S,
    grace_s: float = STORY_GRACE_S,
) -> dict[str, int | str]:
    """Wait for the running bot to clean the session, scan its names and read its story, printing
    counts only. The story read may be switched off: once the scan is finished, a story that has
    not started within `grace_s` is taken as not wanted. A story read stopped by the daily limit
    or a failed batch ("waiting") is said so, not waited on for ever."""
    began, last, scanned_at = clock(), "", None
    while True:
        row = await _import_row(db, guild, session)
        if not row:
            raise SampleError("the imported session is gone")
        seen = (
            f"import {row['state']} lines {row['step_done']}/{row['step_total']} words"
            f" {row['words_done']} calls {row['calls_made']} scan {int(row['scan_finished'])}"
            f" story {row['story_state'] or '-'} {row['story_part']}/{row['story_parts']}"
        )
        if seen != last:
            say(seen)
            last = seen
        if row["state"] == "failed":
            raise SampleError(f"cleaning the session failed ({row['reason'] or 'no reason'})")
        if row["state"] == "done" and row["scan_finished"]:
            scanned_at = clock() if scanned_at is None else scanned_at
            if row["story_state"] == "read":
                break
            if row["story_state"] == "" and clock() - scanned_at >= grace_s:
                break
            if row["story_state"] == "waiting" and clock() - scanned_at >= grace_s:
                await _stop_import(db, guild, session)
                raise SampleError(
                    "the story read is waiting (the daily limit, or a batch failed): "
                    "run again later; the numbers would be of a half-built graph"
                )
        if clock() - began > limit_s:
            await _stop_import(db, guild, session)
            raise SampleError("the session was not finished in time: is the bot running?")
        await sleep(poll_s)
    return {
        "lines": int(row["step_total"]),
        "words": int(row["words_done"]),
        "calls": int(row["calls_made"]),
        "story": str(row["story_state"] or "not read"),
    }


def keep_folder(folder: Path) -> Path:
    """Where backups may go: a folder outside the checkout (a backup holds the whole campaign,
    secrets included, and the repository is public)."""
    from dmbot.devtools.common import REPO

    folder = folder.expanduser().resolve()
    if folder == REPO or REPO in folder.parents:
        raise SampleError("--backup-dir must be outside the repository")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


async def backup(
    campaigns: CampaignStore, guild: int, campaign_id: str, folder: Path, label: str
) -> Path:
    """The campaign's backup, to a server-only file (private to the account)."""
    from dmbot.campaigns.store import encode_backup

    stamp = time.strftime("%Y%m%d-%H%M%S")  # a new file each run: an earlier backup is kept
    path = folder / f"sample-{stamp}-{label}.dmbot.json.gz"
    data = encode_backup(await campaigns.export(guild, campaign_id))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return path


def score(path: Path, docs: str) -> list[str]:
    """`graph_key_check --docs DOCS --counts-only` on a backup, as the command does, and its
    counts-only lines back."""
    from dmbot.devtools import graph_key_check

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = graph_key_check.main([str(path), "--docs", docs, "--counts-only"])
    if code != 0:
        # (the checker's own one-line reason: it names a rule, never a name from the backup)
        why = err.getvalue().strip().splitlines()[:1]
        raise SampleError(
            f"the answer-key check could not score the {docs} backup"
            + (f": {why[0]}" if why else "")
        )
    return out.getvalue().splitlines()


async def write_recap(
    guild: int,
    campaign_id: str,
    session_at: int,
    *,
    run: Callable[[list[str]], Awaitable[int]] | None = None,
) -> int:
    """The recap, with no audio, by the same code as `recap_run --session ... --yes`. Its report
    holds the recap's words, so it is swallowed: only the exit code comes back (0: made)."""
    if run is None:
        from dmbot.devtools.recap_run import __main__ as rr

        async def run(argv: list[str]) -> int:
            return await rr.main_async(rr.parse_args(argv))

    argv = ["--session", str(guild), campaign_id, str(session_at), "--yes"]
    with contextlib.redirect_stdout(io.StringIO()):
        return await run(argv)


def estimate_cost(
    lore_words: int, session_lines: int, session_chars: int
) -> tuple[int, int, int, int]:
    """(lore calls, import windows, story batches, recap calls): the most each can use, from the
    two files' sizes, for the line printed before `--yes`."""
    from dmbot.recaps import pipeline as pl
    from dmbot.recaps.beats import WINDOW_CHARS
    from dmbot.transcript.import_job import MAX_WINDOWS
    from dmbot.transcript.topics import WINDOW_LINES

    lore = 6 + math.ceil(lore_words / 1500)
    windows = min(MAX_WINDOWS, math.ceil(session_lines / WINDOW_LINES))
    story = max(1, math.ceil(session_chars / 20_000))
    recap = pl.MAX_CALLS + max(1, math.ceil(session_chars / WINDOW_CHARS)) - 1
    return lore, windows, story, recap


async def run_steps(
    actions: Mapping[str, Callable[[], Awaitable[Mapping[str, int | str]]]],
    done: list[Step],
    say: Say,
) -> None:
    """Run the actions in `plan.STEPS` order, timing each and adding it to `done` as it finishes
    (so a stop part way still reports what was done). The actions must be exactly the steps."""
    if set(actions) != set(STEPS):
        raise SampleError("the run's actions are not the run's steps")
    for name in STEPS:
        began = time.monotonic()
        counts = await actions[name]()
        done.append(Step(name, time.monotonic() - began, counts))
        say(f"{name}: done")


# ---- players who are not on Discord (#3690) ------------------------------------------------------
#
# The session's speakers are imported the way the website's import takes a speaker without Discord:
# through the import's own code (`dmbot.web.imports`), so the speaker rows and the uploader's
# confirmation (`import_consents`) are written by the same doors, with the same checks, as for any
# import, never by this tool. The doors only answer a signed-in DM, so the tool opens a website
# session for the DM for the length of the call and removes it at the end.


@contextlib.asynccontextmanager
async def site_session(db: Database, dm: int, guild: int, *, now: int) -> Any:
    """A website session for the DM that lives only inside this block: the doors' own check of who
    is asking and which server they manage. The row is removed when the block ends, even on an
    error."""
    import json
    import secrets

    from dmbot.web.discord import DiscordGuild
    from dmbot.web.sessions import Session, hash_token

    token = secrets.token_urlsafe(32)
    hashed = hash_token(token)
    try:
        async with db.user(dm) as conn:
            await conn.execute(
                "INSERT INTO web_sessions (id_hash, user_id, created_at, expires_at, guilds,"
                " display_name) VALUES (%s, %s, %s, %s, %s::jsonb, '')",
                (
                    hashed,
                    dm,
                    now,
                    now + 3600,
                    json.dumps([{"id": str(guild), "name": "sample", "manage": True}]),
                ),
            )
    except Exception as exc:  # the kind only: a database error can quote a row
        raise SampleError(
            f"the DM has no website account to import through ({type(exc).__name__})"
        ) from exc
    try:
        yield Session(hashed, dm, "", (DiscordGuild(guild, "sample", True),), now + 3600, now)
    finally:
        async with db.user(dm) as conn:
            await conn.execute("DELETE FROM web_sessions WHERE id_hash = %s", (hashed,))


def speaker_names(transcript: importer.Transcript, labels: Collection[str]) -> dict[str, str]:
    """Each label's name as the import reads it (the part before any "(username)")."""
    from dmbot.web.imports import label_parts

    return {label: (label_parts(label)[0] or label.strip()) for label in labels}


async def make_speakers(
    db: Database, session: Any, guild: int, campaign_id: str, names: Collection[str]
) -> dict[str, int]:
    """Make the players' speakers without Discord with the import's own door, the same one the
    website's import stores them with (the same name is the same speaker, so the import that
    follows finds them). Returns name -> the speaker's negative number."""
    from dmbot.web.recaps import _scoped

    made: dict[str, int] = {}
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild)
        for name in sorted(names):
            cur = await conn.execute(
                "SELECT dmbot_import_speaker(%s, %s, %s) AS r", (guild, campaign_id, name)
            )
            speaker = (await cur.fetchone() or {}).get("r")
            if not isinstance(speaker, int) or speaker >= 0:
                raise SampleError("a player not on Discord could not be made")
            made[name] = speaker
    return made


async def speaker_ids(db: Database, guild: int, campaign_id: str) -> dict[str, int]:
    """The campaign's speakers without Discord: lower-cased name -> negative number."""
    async with db.guild(guild, snapshot=True) as conn:
        cur = await conn.execute(
            "SELECT user_id, name FROM campaign_speakers WHERE guild_id = %s AND campaign_id = %s",
            (guild, campaign_id),
        )
        return {str(r["name"]).casefold(): int(r["user_id"]) for r in await cur.fetchall()}


async def import_through_site(
    db: Database,
    session: Any,
    guild: int,
    campaign_id: str,
    text: str,
    mapping: Mapping[str, Any],
    *,
    started_at: int,
    now: int,
    file_name: str = "sample-session.txt",
) -> str:
    """Store the session with the website's own `confirm`: the players without Discord are ticked
    in by the uploader (the DM), which records the confirmation as the import records it. Returns
    the session's id. `mapping`: label -> the choice object the page sends."""
    import datetime as dt

    from dmbot.transcript import uploader_words as uw
    from dmbot.web import imports
    from dmbot.web.recaps import RecapError

    day = dt.datetime.fromtimestamp(started_at, dt.UTC).date().isoformat()
    try:
        done = await imports.confirm(
            db,
            session,
            f"{guild}.{campaign_id}",
            [{"name": file_name, "text": text}],
            {
                "mapping": dict(mapping),
                "consentVersion": uw.version(),
                "sessions": [{"file": file_name, "date": day, "order": 0, "writeRecap": False}],
            },
            outside=None,
            now=now,
            enforce_plans=False,
        )
    except RecapError as exc:
        raise SampleError(f"the import was refused ({exc})") from exc
    async with db.guild(guild, snapshot=True) as conn:
        cur = await conn.execute(
            "SELECT session_id FROM session_imports WHERE guild_id = %s AND campaign_id = %s"
            " AND import_id = %s",
            (guild, campaign_id, done["importId"]),
        )
        row = await cur.fetchone()
    if row is None:
        raise SampleError("the import was not stored")
    return str(row["session_id"])
