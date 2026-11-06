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
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from dmbot.campaigns.models import Campaign
from dmbot.campaigns.store import CampaignStore
from dmbot.consent import TERMS_VERSION
from dmbot.db import Database
from dmbot.devtools.sample_run.plan import NAME, Existing, SampleError, can_replace
from dmbot.transcript import import_plan as ip
from dmbot.transcript import importer
from dmbot.transcript.store import TranscriptStore

Say = Callable[[str], None]
Sleep = Callable[[float], Awaitable[None]]

PAID_KINDS = ("paid", "grant")  # a plan, or free access (the owner's own accounts have it)
LORE_CHOICES = ("world", "spoilers", "smart")
POLL_S = 5.0
LORE_WAIT_S = 45 * 60
IMPORT_WAIT_S = 90 * 60


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


async def fresh_campaign(
    db: Database,
    campaigns: CampaignStore,
    guild: int,
    dm: int,
    *,
    keep_old: bool,
    today: str,
    say: Say,
) -> Campaign:
    """Retire the last run's campaign (deleted, or renamed with `keep_old`) and make a new one,
    paid because the DM owns it. Refuses, changing nothing, when a campaign with the name is not
    plainly the tool's (`plan.can_replace`)."""
    found = await existing_samples(db, campaigns, guild)
    for old in found:  # every one is checked before anything is changed
        why = can_replace(old, dm)
        if why:
            raise SampleError(why)
    for old in found:
        if keep_old:
            await _keep(campaigns, guild, old.campaign_id, today)
        else:
            await campaigns.delete(guild, old.campaign_id)
    say(f"{len(found)} earlier sample campaign(s) {'kept' if keep_old else 'deleted'}")
    return await campaigns.create(guild, NAME, dm)


async def _keep(campaigns: CampaignStore, guild: int, campaign_id: str, today: str) -> None:
    """Rename a kept campaign so the sample's name is free; it is never the exact name again, so
    the tool can never delete it."""
    for n in range(1, 50):
        name = f"{NAME} (kept {today}{'' if n == 1 else f' {n}'})"
        try:
            await campaigns.rename(guild, campaign_id, name)
            return
        except Exception as exc:  # the name was taken: try the next
            if type(exc).__name__ != "CampaignError":
                raise
    raise SampleError("could not find a free name to keep the earlier campaign under")


async def queue_lore(
    db: Database, guild: int, campaign_id: str, dm: int, text: str, *, choice: str, now: int
) -> str:
    """The lore, queued as the website's upload does (`dmbot_web_lore_add`): the DM's own words,
    the right to use them confirmed by the DM now (recorded as this run), the choice stored."""
    if choice not in LORE_CHOICES:
        raise SampleError("--lore-choice is world, spoilers or smart")
    if not text.strip():
        raise SampleError("the lore file is empty")
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
            raise SampleError(
                "the lore was not read in time: is the bot running and the lore switch on?"
            )
        await sleep(poll_s)


async def store_import(
    db: Database,
    transcripts: TranscriptStore,
    guild: int,
    campaign_id: str,
    dm: int,
    players: Mapping[str, int],
    text: str,
    *,
    started_at: int,
    now: int,
    file_name: str = "sample-session.txt",
) -> tuple[str, int]:
    """Store the session file as an imported session, the way the import page's confirm does
    (`dmbot_import_store`): the speakers mapped to the test accounts, whose lines are kept because
    the DM ticks them (an `uploader_confirmed` record each), then the same row the bot's cleaning
    picks up. Returns (the session's id, the lines kept). A test account that said no refuses it."""
    try:
        transcript = importer.parse(text)
    except importer.TranscriptError as exc:
        raise SampleError(f"the session file cannot be read ({exc.code})") from exc
    labels = {s.label for s in ip.speakers(transcript)}
    unmapped = labels - set(players)
    if unmapped:
        raise SampleError(f"{len(unmapped)} speaker(s) in the file have no --players entry")
    ids = sorted(set(players.values()))
    async with db.guild(guild, snapshot=True) as conn:
        cur = await conn.execute(
            "SELECT 1 FROM consent_refusals WHERE guild_id = %s AND user_id = ANY(%s)",
            (guild, ids),
        )
        if await cur.fetchone() is not None:
            raise SampleError("a test account has said no to being recorded")
    plan = ip.make_plan(
        transcript, dict(players), agreed=(), confirmed=ids, base_ms=started_at * 1000
    )
    if plan.kept == 0:
        raise SampleError("no line of the session file was kept")
    sid = await transcripts.open_session(guild, campaign_id, started_at)
    await transcripts.add_lines(guild, sid, list(plan.lines))
    last = max(line.started_ms + line.duration_ms for line in plan.lines)
    await transcripts.end_session(guild, sid, max(started_at, last // 1000))
    import_id = os.urandom(16).hex()
    per: dict[int, int] = {}
    for line in plan.lines:
        per[line.user_id] = per.get(line.user_id, 0) + 1
    async with db.guild(guild) as conn:
        await conn.execute(
            "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id, file_name,"
            " imported_by, imported_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (guild, campaign_id, sid, import_id, file_name, dm, now),
        )
        for user, lines in per.items():
            await conn.execute(
                "INSERT INTO import_consents (guild_id, campaign_id, id, import_id, user_id,"
                " basis, confirmed_by, confirmed_at, terms_version, lines) VALUES (%s, %s,"
                " replace(gen_random_uuid()::text, '-', ''), %s, %s, 'uploader_confirmed', %s,"
                " %s, %s, %s)",
                (guild, campaign_id, import_id, user, dm, now, TERMS_VERSION, lines),
            )
    return sid, plan.kept


async def _import_row(db: Database, guild: int, session: str) -> dict[str, Any]:
    async with db.guild(guild, snapshot=True) as conn:
        cur = await conn.execute(
            "SELECT state, reason, scanned, scan_finished, step_done, step_total, words_done,"
            " calls_made, story_state, story_part, story_parts FROM session_imports"
            " WHERE guild_id = %s AND session_id = %s",
            (guild, session),
        )
        return dict(await cur.fetchone() or {})


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
    grace_polls: int = 6,
) -> dict[str, int | str]:
    """Wait for the running bot to clean the session, scan its names and read its story, printing
    counts only. The story read may be switched off: once the scan is finished, a story that never
    starts within `grace_polls` polls is taken as not wanted."""
    began, last, quiet = clock(), "", 0
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
            if row["story_state"] == "read":
                break
            quiet = quiet + 1 if row["story_state"] == "" else 0
            if quiet >= grace_polls:
                break
        if clock() - began > limit_s:
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
    folder.mkdir(parents=True, exist_ok=True)
    return folder


async def backup(
    campaigns: CampaignStore, guild: int, campaign_id: str, folder: Path, label: str
) -> Path:
    """The campaign's backup, to a server-only file (private to the account)."""
    from dmbot.campaigns.store import encode_backup

    path = folder / f"sample-{label}.dmbot.json.gz"
    data = encode_backup(await campaigns.export(guild, campaign_id))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return path


def score(path: Path, docs: str) -> list[str]:
    """`graph_key_check --docs DOCS --counts-only` on a backup, as the command does, and its
    counts-only lines back."""
    from dmbot.devtools import graph_key_check

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = graph_key_check.main([str(path), "--docs", docs, "--counts-only"])
    if code != 0:
        raise SampleError(f"the answer-key check could not score the {docs} backup")
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
