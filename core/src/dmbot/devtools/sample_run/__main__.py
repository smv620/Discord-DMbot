"""The whole per-deploy sample run in one command, with no one in Discord (#3547, #3545):

    python -m dmbot.devtools.sample_run --guild GUILD --dm DM_USER_ID \\
        --players ist=ID,jac=ID,eri=ID,owe=ID --lore FILE --session FILE [--keep-old] --yes

It makes the sample campaign, queues the lore the way the website's upload does and waits for the
running bot to read it, accepts what was found, backs up and scores the lore, stores the session
file as an imported one and waits for the bot to clean, scan and read it, accepts again, backs up
and scores both, then writes the recap (no audio). The last thing it prints is one block of counts
and times, ready to paste on #3545.

Only the one campaign called "test of the frostmaiden (sample)" is ever made or deleted, and only
when it is plainly this tool's. Paid AI calls: the most they can be is printed first, and nothing
runs without `--yes`. Nothing of the lore, the session or the recap is printed, logged or kept in
the repository; backups go to a folder outside it. The AI key and database address come from the
environment, as for `recap_run`.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from dmbot.devtools.sample_run import plan, steps
from dmbot.devtools.sample_run.plan import SampleError, Step
from dmbot.transcript import import_plan as ip

if TYPE_CHECKING:
    from dmbot.db import Database

Accept = Callable[["Database", int, str], Awaitable[Mapping[str, int]]]
DEFAULT_BACKUPS = Path("/var/lib/dmbot-sample-backups")
_ON = {"1", "true", "on", "yes"}


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sample_run", description=__doc__)
    parser.add_argument("--guild", type=int, required=True, help="the test server's number")
    parser.add_argument("--dm", type=int, required=True, help="the DM's test account")
    parser.add_argument("--players", required=True, help="label=ACCOUNT pairs: ist=1,jac=2")
    parser.add_argument("--lore", type=Path, required=True, help="the lore text file")
    parser.add_argument("--session", type=Path, required=True, help="the session text file")
    parser.add_argument("--keep-old", action="store_true", help="rename the last run's campaign")
    parser.add_argument("--lore-choice", default="smart", help="world, spoilers or smart")
    parser.add_argument("--date", help="when the past session was held (YYYY-MM-DD)")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUPS)
    parser.add_argument("--commit", help="the deployed commit (default: GIT_COMMIT or git)")
    parser.add_argument("--yes", action="store_true", help="spend the money")
    return parser.parse_args(argv)


def switches(environ: Mapping[str, str]) -> list[str]:
    """The names (never values) of the DMBOT_ switches that are on, for the report."""
    bad = ("KEY", "TOKEN", "SECRET", "PASSWORD", "URL")
    return sorted(
        name
        for name, value in environ.items()
        if name.startswith("DMBOT_")
        and value.strip().casefold() in _ON
        and not any(word in name for word in bad)
    )


def session_start(date: str | None, now: int) -> int:
    """When the past session began: the given day at noon (UTC), or a week ago."""
    if not date:
        return now - 7 * 86400
    try:
        day = dt.date.fromisoformat(date)
    except ValueError as exc:
        raise SampleError("--date is YYYY-MM-DD") from exc
    start = int(dt.datetime(day.year, day.month, day.day, 12, tzinfo=dt.UTC).timestamp())
    if start >= now:
        raise SampleError("--date must be in the past")
    return start


def read_text(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:  # the kind only, never the path's contents
        raise SampleError(f"could not read the {what} file ({type(exc).__name__})") from exc


async def run(
    args: argparse.Namespace,
    *,
    accept: Accept | None = None,
    say: Callable[[str], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    say = say or (lambda line: print(line, flush=True))
    accept = accept or _accept_all()
    now = int(time.time())
    # Everything that can refuse the run without a database comes first, so a mistake costs
    # nothing: the players, the files, the choice, the date.
    players = plan.parse_players(args.players)
    lore_text, session_text = read_text(args.lore, "lore"), read_text(args.session, "session")
    steps.check_lore(lore_text, args.lore_choice)
    started = session_start(args.date, now)
    from dmbot.transcript import importer

    try:
        transcript = importer.parse(session_text)
    except importer.TranscriptError as exc:
        raise SampleError(f"the session file cannot be read ({exc.code})") from exc
    if {s.label for s in ip.speakers(transcript)} - set(players):
        raise SampleError("a speaker in the session file has no --players entry")
    chars = sum(len(u.text) for u in transcript.utterances)
    lore, windows, story, recap = steps.estimate_cost(
        len(lore_text.split()), len(transcript.utterances), chars
    )
    say(plan.Cost(lore, windows, story, recap).line())
    if not args.yes:
        say("Nothing was run. Add --yes to spend the money.")
        return 0
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SampleError("DATABASE_URL isn't set")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SampleError("ANTHROPIC_API_KEY isn't set")
    folder = steps.keep_folder(args.backup_dir)
    from dmbot.consent import ConsentStore
    from dmbot.db import Database
    from dmbot.devtools import common

    commit = common.commit(
        args.commit, tool="sample_run"
    )  # before the run: it cannot mask an error
    db = await Database.open(url, migrate=False, max_size=4)
    done: list[Step] = []
    try:
        campaigns = steps.campaign_store(db)
        today = dt.date.today().isoformat()
        gid, dm = args.guild, args.dm
        # The checks that need the database, still before anything is changed or spent.
        await steps.require_paid(db, dm, now)
        found = await steps.check_existing(db, campaigns, gid, dm)
        ids = set(players.values())
        agreed = await ConsentStore(db).consenting(gid)
        refused = await steps.refused_accounts(db, gid, ids)
        planned = await steps.preflight_session(
            gid, players, session_text, agreed, refused, started_at=started
        )

        async def timed(name: str, work: Awaitable[Mapping[str, int | str]]) -> None:
            began = time.monotonic()
            counts = await work
            done.append(Step(name, time.monotonic() - began, counts))
            say(f"{name}: done")

        campaign = await steps.fresh_campaign(
            db, campaigns, gid, dm, keep_old=args.keep_old, today=today, say=say, found=found
        )
        cid = campaign.id

        async def lore_step() -> Mapping[str, int | str]:
            upload = await steps.queue_lore(
                db, gid, cid, dm, lore_text, choice=args.lore_choice, now=now
            )
            return await steps.wait_lore(db, gid, upload, say=say, sleep=sleep)

        async def accept_step() -> Mapping[str, int | str]:
            return await accept(db, gid, cid)

        async def score_step(label: str, docs: str) -> Mapping[str, int | str]:
            path = await steps.backup(campaigns, gid, cid, folder, label)
            lines = steps.score(path, docs)
            for line in lines:
                say(line)
            return {"lines": len(lines)}

        await timed("lore read", lore_step())
        await timed("accept all (lore)", accept_step())
        await timed("backup and score (lore)", score_step("lore", "lore"))

        async def import_step() -> Mapping[str, int | str]:
            sid = await steps.store_import(db, gid, cid, dm, planned, started_at=started, now=now)
            got = await steps.wait_import(db, gid, sid, say=say, sleep=sleep)
            return {**got, "kept": planned.kept}

        await timed("session import", import_step())
        await timed("accept all (session)", accept_step())
        await timed("backup and score (both)", score_step("both", "both"))

        async def recap_step() -> Mapping[str, int | str]:
            code = await steps.write_recap(gid, cid, started)
            if code != 0:
                raise SampleError(f"the recap was not made (recap_run exit {code})")
            return {"made": 1}

        await timed("recap (no audio)", recap_step())
    finally:
        await db.close()
        for line in plan.report(
            done, commit=commit, switches=switches(os.environ), today=dt.date.today().isoformat()
        ):
            say(line)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(run(parse_args(argv), accept=_accept_all()))
    except SampleError as exc:
        print(f"sample_run: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # the kind only: a database error can quote a row, the lore with it
        print(f"sample_run: stopped by {type(exc).__name__}", file=sys.stderr)
        return 1


def _accept_all() -> Accept:
    """'Accept all' (#3544, `devtools.accept_all`): what the DM would press Yes for, through the
    store's own calls, on the sample campaign only. Counts only come back; a campaign that is not
    the sample (which the tool itself would never make) is a plain refusal."""
    from dmbot.devtools import accept_all
    from dmbot.memory.store import MemoryStore

    async def accept(db: Database, guild: int, campaign: str) -> Mapping[str, int]:
        try:
            got = await accept_all.run(db, guild, campaign, dry_run=False, memory=MemoryStore(db))
        except accept_all.WrongCampaign as exc:
            raise SampleError(str(exc)) from exc
        return {
            "accepted": sum(got.accepted.values()),
            "left waiting": sum(got.waiting.values()),
            "refused by a rule": sum(got.refused.values()),
            "changes": len(got.batches),
        }

    return accept


if __name__ == "__main__":
    raise SystemExit(main())
