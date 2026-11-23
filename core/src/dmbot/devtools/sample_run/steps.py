"""The steps of the sample run (#3547): make the campaign, queue the lore, store the session,
wait for the bot, accept what was found, back up, score, write the recap. Each step takes the
services it uses (the database, the stores, a clock, a way to sleep and to say something), so a
test runs it on fakes and a real database with no AI and no Discord.

The paid work is done by the running bot, the same way the website's upload and import ask for
it: this tool puts the row in the queue and waits, printing counts only. Nothing of the lore or
the session is printed or logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import math
import os
import sys
import time
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from dmbot.campaigns.models import Campaign, CampaignError
from dmbot.campaigns.store import CampaignStore
from dmbot.db import Database
from dmbot.devtools import sample_name
from dmbot.devtools.sample_run.costs import CostLine, recap_line, recap_scores, scoring_cost
from dmbot.devtools.sample_run.plan import (
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

PAID_KINDS = ("paid", "grant", "free")  # a plan, or free access (the owner's accounts and server)
LORE_CHOICES = ("world", "spoilers", "smart")
POLL_S = 5.0
LORE_WAIT_S = 45 * 60
IMPORT_WAIT_S = 90 * 60
SETTLE_WAIT_S = 10 * 60  # how long an old sample's background work is waited for before deleting


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
    guild_id: int | None = None,
) -> None:
    """The campaign's plan is its owner's, so the DM must have one. The tool never grants a plan.
    A server on the free list (`DMBOT_FREE_GUILDS`, #3788) has free access whoever its DM is."""
    if access_of is None:
        from dmbot.entitlements import plan_and_access

        async def access_of(db: Database, dm: int, now: int) -> Any:
            return await plan_and_access(db, dm, now, guild_id=guild_id)

    _plan, access = await access_of(db, dm, now)
    if access.kind not in PAID_KINDS:
        raise SampleError("the DM account has no paid plan or free access; nothing was changed")


async def existing_samples(db: Database, campaigns: CampaignStore, guild: int) -> list[Existing]:
    """The campaigns in this server that carry a sample name (numbered or the old one), with what
    each one holds."""
    out: list[Existing] = []
    for c in await campaigns.list_campaigns(guild):
        if not sample_name.is_sample(c.name):
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
    db: Database,
    campaigns: CampaignStore,
    guild: int,
    dm: int,
    deploy: sample_name.Label | int | None = None,
    version: str = sample_name.TYPED,
) -> list[Existing]:
    """The earlier sample campaigns. Only this deploy's own (the same N, which the run replaces)
    must pass `plan.can_replace`: if it cannot be deleted, the run cannot make a second one with
    that name, so it stops here, before it spends and before anything is changed (#4538). Any
    other campaign the guard refuses is left alone later and counted, so a kept sample someone
    played in never blocks a run. `version`: only this deploy's campaigns of the same version of
    the session are replaced (docs/design/session_names.md, section 8)."""
    found = await existing_samples(db, campaigns, guild)
    if deploy is not None:
        deploy = sample_name.as_label(deploy)
        own = {
            c.id
            for c in await campaigns.list_campaigns(guild)
            if sample_name.deploy_of(c.name) == deploy  # (any run of this deploy and version)
            and sample_name.version_of(c.name) == version
        }
        for old in found:
            why = can_replace(old, dm) if old.campaign_id in own else None
            if why:
                raise SampleError(why)
    return found


DM_BOT_CHANNEL_NEEDED = (
    "DM Bot channel: needed (press ⚙️ Manage DMbot on the new sample campaign; delete the "
    "earlier campaign's channel by hand)"
)


async def busy_count(db: Database, guild: int, campaign_id: str) -> int:
    """How many pieces of background work the campaign has running or queued: lore uploads and
    imported sessions that are waiting or working, recap requests waiting or working, and recap
    jobs not yet ready or failed. A number only. Deleting a campaign while these still run makes
    their AI charges find no campaign (#4542); a check and a delete are not atomic, so new work
    can still start in between (this is a test tool)."""
    async with db.guild(guild, snapshot=True) as conn:
        total = 0
        for sql in (
            "SELECT count(*) AS n FROM lore_uploads WHERE guild_id = %s AND campaign_id = %s"
            " AND state IN ('waiting', 'working')",
            "SELECT count(*) AS n FROM session_imports WHERE guild_id = %s AND campaign_id = %s"
            " AND state IN ('waiting', 'working')",
            "SELECT count(*) AS n FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s"
            " AND state NOT IN ('ready', 'failed')",
            "SELECT count(*) AS n FROM recap_requests WHERE guild_id = %s AND campaign_id = %s"
            " AND state IN ('waiting', 'working')",
        ):
            cur = await conn.execute(sql, (guild, campaign_id))
            total += int((await cur.fetchone() or {}).get("n", 0))
    return total


async def wait_settled(
    db: Database,
    guild: int,
    campaign_id: str,
    *,
    sleep: Sleep,
    clock: Callable[[], float] = time.monotonic,
    limit_s: float = SETTLE_WAIT_S,
    poll_s: float = POLL_S,
) -> bool:
    """Wait until the campaign has no background work (`busy_count` is 0), up to `limit_s`. True
    when it settled, False when the wait ran out (the caller says so and deletes anyway: a
    forgotten, stuck job must not block the next deploy's run)."""
    began = clock()
    while await busy_count(db, guild, campaign_id):
        if clock() - began > limit_s:
            return False
        await sleep(poll_s)
    return True


async def clear_for_deploy(
    db: Database,
    campaigns: CampaignStore,
    guild: int,
    dm: int,
    *,
    deploy: sample_name.Label | int,
    say: Say,
    found: list[Existing] | None = None,
    sleep: Sleep | None = None,
    settle_s: float = SETTLE_WAIT_S,
    need: int | None = None,
    free: int | None = None,
    version: str = sample_name.TYPED,
) -> tuple[sample_name.Kept, int]:
    """Make room for this deploy's sample campaign(s) (#4538, #4551): every campaign of the same N
    is deleted first (a re-run never leaves two of one number, runs of a parallel deploy
    included), the oldest deploys beyond the newest `sample_name.KEEP` go with all their runs, and
    old unnumbered ones always go. `found`: what `check_existing` returned (every one already
    passed the guard `plan.can_replace`); asked again when not given. Each delete waits for the
    campaign's background work and re-checks ownership under its lock (`only_if_owner`).

    `need`, `free` (#4551): the runs about to be made and the DM's free paid slots (None: no
    limit). The slots are counted before anything is deleted: while they are short after the
    deletions above, the oldest kept deploys go with all their runs (each through `can_replace`),
    never the newest kept deploy, and the counts line says which. Returns what is deleted and how
    many runs there is room for (`need`, or fewer); none is a `SampleError`, with nothing
    deleted. `version`: the session's version being made; this deploy's campaigns of another
    version are kept."""
    found = (
        found
        if found is not None
        else await check_existing(db, campaigns, guild, dm, deploy, version)
    )
    names = {c.id: c.name for c in await campaigns.list_campaigns(guild)}
    have = [(old.campaign_id, names[old.campaign_id]) for old in found if old.campaign_id in names]
    by_id = {old.campaign_id: old for old in found}
    # Only what the run would delete is checked: a kept sample is never touched. A refusal on
    # this deploy's own campaign stops the run (it cannot be made again); on any other it is
    # left alone and counted in the line.
    deploy = sample_name.as_label(deploy)
    first = sample_name.plan_keep(have, deploy, version=version)
    refused: set[str] = set()
    for campaign_id in first.delete:
        why = can_replace(by_id[campaign_id], dm)
        if why is None:
            continue
        name = names.get(campaign_id, "")
        if sample_name.deploy_of(name) == deploy and sample_name.version_of(name) == version:
            raise SampleError(why)
        refused.add(campaign_id)
    kept = sample_name.plan_keep(have, deploy, protected=refused, version=version)
    allowed = need if need is not None else 1
    if need is not None:
        extra, dropped, allowed = sample_name.slot_drops(
            have,
            kept,
            need,
            free,
            lambda cid: can_replace(by_id[cid], dm) is None,
        )
        if allowed == 0:
            raise SampleError(
                "no free paid campaign slot for even one run, and no older sample to make room "
                "from: nothing was changed"
            )
        if extra:
            kept = replace(
                kept,
                delete=(*kept.delete, *extra),
                keep=tuple(c for c in kept.keep if c not in set(extra)),
                slot_dropped=dropped,
            )
    deleted = 0
    timed_out = 0
    try:
        for campaign_id in kept.delete:
            # Its background AI work is still being charged: wait for it, so the charge finds the
            # campaign (#4542). A job that never settles is not waited on for ever.
            if not await wait_settled(
                db, guild, campaign_id, sleep=sleep or asyncio.sleep, limit_s=settle_s
            ):
                timed_out += 1
            await campaigns.delete(guild, campaign_id, only_if_owner=dm)
            deleted += 1
    except CampaignError:  # (a hand-over just now: the store refuses under its lock)
        say(f"stopped after deleting {deleted} of {len(kept.delete)} old sample campaign(s)")
        raise
    say(sample_name.counts_line(kept))
    if kept.slot_dropped:
        say(sample_name.slot_line(kept.slot_dropped))
    if timed_out:
        say(f"{timed_out} old sample campaign(s) still had background work after the wait: deleted")
    if kept.delete:
        say(f"{len(kept.delete)} old campaign channel(s) to delete by hand")
    return kept, allowed


async def free_slots(db: Database, dm: int, now: int, guild: int) -> int | None:
    """The DM's free paid campaign slots, counted the way the plan counts them
    (`campaign_cap.room`): the plan's cap less the campaigns they own, none when there is no cap or
    the server is on the free list (its campaigns take no slot, #3792)."""
    from dmbot import campaign_cap, entitlements

    if guild in entitlements.free_guilds():
        return None
    async with db.user(dm) as conn:
        room = await campaign_cap.room(conn, dm, now)
    return None if room.cap is None else max(0, room.cap - room.owned)


async def make_campaign(
    campaigns: CampaignStore,
    guild: int,
    dm: int,
    *,
    deploy: sample_name.Label | int,
    run: int | None = None,
    say: Say,
    deleted: int = 0,
    of: int | None = None,
    version: str = sample_name.TYPED,
) -> Campaign:
    """Make the sample campaign for this deploy, or for run k of it (#4551), paid because the DM
    owns it. A refusal (no free paid slot, or a campaign with a look-alike name) is one plain
    `SampleError`. `deleted`: how many old ones were deleted just before, for the stop line."""
    try:
        made = await campaigns.create(guild, sample_name.name_for(deploy, run, version), dm)
    except CampaignError as exc:  # no free paid slot, or a name that differs only in capitals
        # Deleting comes first because it frees the paid slot: say how far it got (a count only).
        total = deleted if of is None else of
        say(f"stopped after deleting {deleted} of {total} old sample campaign(s)")
        raise SampleError(
            "the sample campaign could not be made (no free paid campaign slot, or a campaign "
            "with a look-alike name exists)"
        ) from exc
    # This tool has no Discord client, so it cannot make or delete a channel (#4112). The new
    # campaign has no DM Bot channel yet: the bot makes it the way it does for any campaign made
    # before the channel existed, when the DM presses ⚙️ Manage DMbot. A deleted campaign's channel
    # stays in the server and is deleted by hand; its buttons say it cannot find the campaign.
    # Said as one counts-only line, so dev1 does it before checking the main menu.
    say(DM_BOT_CHANNEL_NEEDED)
    return made


async def fresh_campaign(
    db: Database,
    campaigns: CampaignStore,
    guild: int,
    dm: int,
    *,
    deploy: sample_name.Label | int,
    say: Say,
    found: list[Existing] | None = None,
    sleep: Sleep | None = None,
    settle_s: float = SETTLE_WAIT_S,
    version: str = sample_name.TYPED,
) -> Campaign:
    """Make this deploy's sample campaign, "test of the frostmaiden (sample N)" (#4538): room is
    made first (`clear_for_deploy`), then the campaign."""
    kept, _allowed = await clear_for_deploy(
        db,
        campaigns,
        guild,
        dm,
        deploy=deploy,
        say=say,
        found=found,
        sleep=sleep,
        settle_s=settle_s,
        version=version,
    )
    return await make_campaign(
        campaigns,
        guild,
        dm,
        deploy=deploy,
        say=say,
        deleted=len(kept.delete),
        of=len(kept.delete),
        version=version,
    )


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
            " calls_made, names_cut FROM lore_uploads WHERE guild_id = %s AND id = %s",
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
                # names past the one-document limit (#5055 A): a loss to the cap, not a miss
                "names cut": int(row["names_cut"]),
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
            " calls_made, story_state, story_part, story_parts, scan_cut, story_held"
            " FROM session_imports"
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
        # (#5055 A) names past the name scan's limit (left out), and new names past the story
        # reader's (held for a later session): kept apart, as they mean different things
        "scan cut": int(row["scan_cut"]),
        "reader held": int(row["story_held"]),
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
    # (the campaign's own id in the name: runs side by side finish in the same second, #4551)
    path = folder / f"sample-{stamp}-{campaign_id[:8]}-{label}.dmbot.json.gz"
    data = encode_backup(await campaigns.export(guild, campaign_id))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return path


def master_key() -> dict[str, object]:
    """The master answer key (lore and session), as `graph_key_check` reads it by default: what
    the AI's decisions are scored against (#3900)."""
    import json

    from dmbot.devtools import graph_key_check

    with graph_key_check.DEFAULT_KEY.open(encoding="utf-8") as f:
        key = json.load(f)
    if not isinstance(key, dict):
        raise SampleError("the answer key is not readable")
    return key


async def met_words(db: Database, guild: int, campaign_id: str) -> list[str]:
    """The words of the campaign's "met but not named" rows (`memory_met`), which a backup does not
    carry, for the key's described rows (#5055 D). The sample's own words, kept on the server: the
    check prints counts only."""
    async with db.guild(guild, snapshot=True) as conn:
        cur = await conn.execute(
            "SELECT what FROM memory_met WHERE guild_id = %s AND campaign_id = %s",
            (guild, campaign_id),
        )
        return [str(r["what"]) for r in await cur.fetchall()]


def score(path: Path, docs: str, met: Sequence[str] = ()) -> list[str]:
    """`graph_key_check --docs DOCS --counts-only` on a backup, as the command does, and its
    counts-only lines back. `met`: the "met but not named" words (`met_words`)."""
    from dmbot.devtools import graph_key_check

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = graph_key_check.main([str(path), "--docs", docs, "--counts-only"], met=met)
    if code != 0:
        # (the checker's own one-line reason: it names a rule, never a name from the backup)
        why = err.getvalue().strip().splitlines()[:1]
        raise SampleError(
            f"the answer-key check could not score the {docs} backup"
            + (f": {why[0]}" if why else "")
        )
    return out.getvalue().splitlines()


def score_numbers(path: Path, docs: str, met: Sequence[str] = ()) -> dict[str, int]:
    """The answer-key check's totals as numbers (#3545's spread): entries and ties found, missing,
    wrong and waiting over every group, the extras, and the described rows found (#5055 D).
    Counts only."""
    from collections import Counter

    from dmbot.campaigns.store import decode_backup
    from dmbot.devtools import graph_key_check as gkc

    summary = gkc.summarize(gkc.check(decode_backup(path.read_bytes()), master_key(), docs, met))
    out: dict[str, int] = {}
    for part in ("entries", "ties"):
        total: Counter[str] = Counter()
        for counts in summary[part].values():
            total.update(counts)
        out.update({
            f"{part} found": total[gkc.FOUND],
            f"{part} wrong": total[gkc.WRONG],
            f"{part} waiting": total[gkc.WAITING],
            f"{part} missing": total[gkc.MISSING],
            f"{part} must missed": total["must_missed"],
        })  # fmt: skip
    out["extra entries"] = sum(summary["extra_entries"].values())
    out["extra ties"] = int(summary["extra_ties"])
    out["entries wrong shape"] = int(summary["wrong_shape"])  # (docs/design/session_names.md, 8)
    described = summary["described"]
    if described.get("total"):  # (#5055 D: not name misses, so a line of their own)
        out["described found"] = described.get(gkc.AS_ENTRY, 0) + described.get(gkc.ON_MET_LIST, 0)
    return out


async def write_recap(
    guild: int,
    campaign_id: str,
    session_at: int,
    *,
    run: Callable[[list[str]], Awaitable[int]] | None = None,
    spent: list[CostLine] | None = None,
    why: list[str] | None = None,
    scores: dict[str, int | str] | None = None,
) -> int:
    """The recap, with no audio, by the same code as `recap_run --session ... --yes`. Its report
    holds the recap's words, so it is swallowed: only the exit code comes back (0: made). `spent`
    (#4087) gets the recap's tokens and dollars, read from its one "tokens in N, out N; spend $X"
    line and nothing else of the report. `scores` (#4556): the recap is also scored (`--score`: two
    more AI calls, the must-have counts and the four storytelling numbers), and the numbers go in
    `scores`, never a word of the recap."""
    if run is None:
        from dmbot.devtools.recap_run import __main__ as rr

        async def run(argv: list[str]) -> int:
            return await rr.main_async(rr.parse_args(argv))

    argv = ["--session", str(guild), campaign_id, str(session_at), "--yes"]
    if scores is not None:
        argv.append("--score")
    swallowed, complaints = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(swallowed), contextlib.redirect_stderr(complaints):
            code = await run(argv)
    finally:
        # The tool's own "recap_run: ..." lines say why and hold none of the recap's words (the
        # stdout report does, which is why it is swallowed): pass them on (#4543). Anything else
        # on stderr (a warning, a traceback) shows as it did before.
        for said in complaints.getvalue().splitlines():
            if said.startswith("recap_run:"):
                if why is not None:
                    why.append(said.strip())
            else:
                print(said, file=sys.stderr)
    if spent is not None and (line := recap_line(swallowed.getvalue())) is not None:
        spent.append(line)
    if scores is not None and code == 0:
        scores.update(recap_scores(swallowed.getvalue()))
        if spent is not None and (extra := scoring_cost(swallowed.getvalue())) is not None:
            spent.append(extra)  # (the scoring's own calls, #4556: the recap's line has not them)
    return code


def estimate_cost(
    lore_words: int, session_lines: int, session_chars: int
) -> tuple[int, int, int, int]:
    """(lore calls, import windows, story batches, recap calls): the most each can use, from the
    two files' sizes, for the line printed before `--yes`."""
    from dmbot.recaps import pipeline as pl
    from dmbot.recaps.beats import WINDOW_CHARS
    from dmbot.transcript.import_job import MAX_WINDOWS
    from dmbot.transcript.topics import WINDOW_LINES

    lore = 7 + math.ceil(lore_words / 1500)  # (one more since the story threads' read, #4453)
    windows = min(MAX_WINDOWS, math.ceil(session_lines / WINDOW_LINES))
    story = max(1, math.ceil(session_chars / 20_000))
    recap = pl.MAX_CALLS + max(1, math.ceil(session_chars / WINDOW_CHARS)) - 1
    return lore, windows, story, recap


async def run_steps(
    actions: Mapping[str, Callable[[], Awaitable[Mapping[str, int | str]]]],
    done: list[Step],
    say: Say,
    order: Sequence[str] = STEPS,
) -> None:
    """Run the actions in `order` (`plan.STEPS`, or `plan.STEPS_DECIDE` since #3900), timing each
    and adding it to `done` as it finishes (so a stop part way still reports what was done). The
    actions must be exactly the steps."""
    if set(actions) != set(order) or len(order) != len(set(order)):
        raise SampleError("the run's actions are not the run's steps")
    for name in order:
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
# session for the DM for the length of the call and removes it at the end (its server list is made
# up for the one server: this tool already holds the database, so it asks Discord for nothing).
#
# Two things to know. The players' speakers are made one step BEFORE the import (their characters
# need the numbers, and the import's cleaning needs the characters), so a refused or failed import
# leaves four named speakers with no confirmation record in the sample campaign; the next run
# deletes that campaign. And "the uploader confirmed" is recorded for the sample's made-up players
# and, if it has not agreed in Discord, the DM: the run only ever touches the one sample campaign.


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
