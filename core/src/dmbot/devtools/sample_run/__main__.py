"""The whole per-deploy sample run in one command, with no one in Discord (#3547, #3545):

    python -m dmbot.devtools.sample_run --guild GUILD --dm DM_USER_ID \\
        --lore FILE --session FILE [--keep-old] --yes

The session's speakers are `player_jac`, `player_ist`, `player_owe` and `player_eri` (players who
are NOT on Discord, imported with the DM's confirmation as the website's import does) and `DM`.
The older `--players ist=ID,jac=ID,eri=ID,owe=ID` (four real test accounts) still works.

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
from typing import TYPE_CHECKING, Any, Protocol

from dmbot.devtools.sample_run import characters, counts, plan, steps
from dmbot.devtools.sample_run import decide as decide_mod
from dmbot.devtools.sample_run.plan import SampleError, Step
from dmbot.transcript import import_plan as ip

if TYPE_CHECKING:
    from dmbot.db import Database

Accept = Callable[["Database", int, str], Awaitable[Mapping[str, int]]]


class Sure(Protocol):
    def __call__(
        self, db: Database, guild: int, campaign: str, *, connections_only: bool = False
    ) -> Awaitable[Mapping[str, int]]: ...


# The careful model's client and how to close it (#3900); a test passes a stand-in.
DecideClient = Callable[[Mapping[str, str]], tuple[Any, Callable[[], Awaitable[None]]]]
DEFAULT_BACKUPS = Path("/var/lib/dmbot-sample-backups")
_ON = {"1", "true", "on", "yes"}


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sample_run", description=__doc__)
    parser.add_argument("--guild", type=int, required=True, help="the test server's number")
    parser.add_argument("--dm", type=int, required=True, help="the DM's test account")
    parser.add_argument(
        "--players",
        default="",
        help="optional label=ACCOUNT pairs (ist=1,jac=2) for players who ARE on Discord; "
        "without it the players are not on Discord (player_jac, ..., and DM)",
    )
    parser.add_argument("--lore", type=Path, required=True, help="the lore text file")
    parser.add_argument("--session", type=Path, required=True, help="the session text file")
    parser.add_argument("--keep-old", action="store_true", help="rename the last run's campaign")
    parser.add_argument("--lore-choice", default="smart", help="world, spoilers or smart")
    parser.add_argument("--date", help="when the past session was held (YYYY-MM-DD)")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUPS)
    parser.add_argument("--commit", help="the deployed commit (default: GIT_COMMIT or git)")
    parser.add_argument(
        "--max-decisions",
        type=int,
        default=decide_mod.DEFAULT_MAX_DECISIONS,
        help="the most careful-model decisions in the whole run (#3900); past it, items wait",
    )
    parser.add_argument(
        "--accept-all",
        action="store_true",
        help="the old way: accept everything (#3544) instead of sure, then the AI's decisions",
    )
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
    sure: Sure | None = None,
    decide_client: DecideClient | None = None,
    say: Callable[[str], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    say = say or (lambda line: print(line, flush=True))
    accept = accept or _accept_all()
    sure = sure or _sure()
    decide_client = decide_client or _careful_client
    now = int(time.time())
    # Everything that can refuse the run without a database comes first, so a mistake costs
    # nothing: the players, the files, the choice, the date.
    off = not args.players  # the players are not on Discord (#3690)
    players = {} if off else plan.parse_players(args.players)
    if not off:
        plan.require_character_players(players)
    lore_text, session_text = read_text(args.lore, "lore"), read_text(args.session, "session")
    steps.check_lore(lore_text, args.lore_choice)
    started = session_start(args.date, now)
    from dmbot.transcript import importer

    try:
        transcript = importer.parse(session_text)
    except importer.TranscriptError as exc:
        raise SampleError(f"the session file cannot be read ({exc.code})") from exc
    labels = {s.label for s in ip.speakers(transcript)}
    roles: dict[str, str] = {}
    dm_label: str | None = None
    if off:
        roles, dm_label = plan.off_discord_roles(labels, steps.speaker_names(transcript, labels))
        missing = {key for key, _ in plan.OFF_DISCORD} - set(roles.values())
        if missing:
            raise SampleError("the session file has no lines from one of the four players")
    elif labels - set(players):
        raise SampleError("a speaker in the session file has no --players entry")
    chars = sum(len(u.text) for u in transcript.utterances)
    lore, windows, story, recap = steps.estimate_cost(
        len(lore_text.split()), len(transcript.utterances), chars
    )
    say(plan.Cost(lore, windows, story, recap).line())
    if not args.accept_all:
        calls = max(args.max_decisions, 0)
        say(
            f"decisions: at most {calls} careful-model calls (#3900),"
            f" about ${decide_mod.estimate(calls):.2f}"
        )
    if not args.yes:
        say("Nothing was run. Add --yes to spend the money.")
        return 0
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SampleError("DATABASE_URL isn't set")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SampleError("ANTHROPIC_API_KEY isn't set")
    # The free lists, as the running bot reads them (#771, #3788): the plan check below knows the
    # owner's own accounts and test server only if they are configured in this process too.
    from dmbot import entitlements

    try:
        entitlements.configure_free_users(
            entitlements.parse_free_users(os.environ.get("DMBOT_FREE_USERS", ""))
        )
        entitlements.configure_free_guilds(
            entitlements.parse_free_guilds(os.environ.get("DMBOT_FREE_GUILDS", ""))
        )
    except ValueError as exc:
        raise SampleError(str(exc)) from exc
    folder = steps.keep_folder(args.backup_dir)
    from dmbot.consent import ConsentStore
    from dmbot.db import Database
    from dmbot.devtools import common
    from dmbot.memory.store import MemoryStore

    commit = common.commit(
        args.commit, tool="sample_run"
    )  # before the run: it cannot mask an error
    db = await Database.open(url, migrate=False, max_size=4)
    done: list[Step] = []
    decider: list[
        Any
    ] = []  # (the decisions' client is made at their first stage, closed at the end)
    try:
        campaigns, memory = steps.campaign_store(db), MemoryStore(db)
        today = dt.date.today().isoformat()
        gid, dm = args.guild, args.dm
        # The checks that need the database, still before anything is changed or spent.
        await steps.require_paid(db, dm, now, guild_id=gid)
        found = await steps.check_existing(db, campaigns, gid, dm)
        ids = set(players.values()) if not off else {dm}
        agreed = await ConsentStore(db).consenting(gid)
        refused = await steps.refused_accounts(db, gid, ids)
        planned = None
        if off:
            if dm in refused:
                raise SampleError("the DM account has said no to being recorded")
        else:
            planned = await steps.preflight_session(
                gid, players, session_text, agreed, refused, started_at=started
            )

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

        docs = (lore_text, session_text)
        # One asker for the whole run (#3900): the cap, the counts and the cost add up across the
        # lore and session stages, and the record is this run's own file (stamped, owner-only).
        record = folder / f"decisions-{dt.datetime.now(dt.UTC).strftime('%Y%m%d-%H%M%S')}.jsonl"

        async def sure_step() -> Mapping[str, int | str]:
            return await sure(db, gid, cid)

        async def session_sure_step() -> Mapping[str, int | str]:
            """DMbot's own matches from the import's who step (#3602), read before anything else
            decides them, then what DMbot is sure of, as for the lore."""
            from dmbot.devtools import accept_all
            from dmbot.devtools.sample_run import judge

            try:
                line = await decide_mod.own_matches(
                    db, memory, judge.Key(steps.master_key()), gid, cid
                )
            except accept_all.WrongCampaign as exc:
                raise SampleError(str(exc)) from exc
            say(line)
            return await sure(db, gid, cid)

        async def decide_step(label: str) -> Mapping[str, int | str]:
            """The careful model's answers for what is still waiting (#3900), then the connections
            those answers made ready, kept as the sure step keeps them."""
            from dmbot.consent import ConsentStore as Consents
            from dmbot.devtools import accept_all
            from dmbot.devtools.sample_run import judge
            from dmbot.memory.backstory_links import BackstoryLinks
            from dmbot.memory.claims import ClaimStore

            if not decider:
                ai, close = decide_client(os.environ)
                asker = decide_mod.Asker(
                    ai, str(getattr(ai, "model", "")),
                    decide_mod.Budget(max(args.max_decisions, 0)), decide_mod.Run(), record,
                )  # fmt: skip
                decider.extend([asker, close])
            asker = decider[0]
            asker.stage = label
            calls, cost = asker.run.calls, asker.run.cost
            try:
                await decide_mod.run(
                    db, memory, BackstoryLinks(db), ClaimStore(memory), asker,
                    judge.Key(steps.master_key()), gid, cid, docs=docs,
                    opted_in=await Consents(db).recorded_in(gid, cid), dm=dm,
                )  # fmt: skip
            except accept_all.WrongCampaign as exc:
                raise SampleError(str(exc)) from exc
            after = await sure(db, gid, cid, connections_only=True)
            return {
                "calls": asker.run.calls - calls,
                "cost": f"${asker.run.cost - cost:.2f}",
                "connections kept after": after.get("accepted", 0),
            }

        async def score_step(label: str, docs: str) -> Mapping[str, int | str]:
            path = await steps.backup(campaigns, gid, cid, folder, label)
            lines = steps.score(path, docs)
            for line in lines:
                say(line)
            return {"lines": len(lines)}

        async def found_step() -> Mapping[str, int | str]:
            n = await characters.found_by_the_read(db, memory, gid, cid)
            say(f"player characters found by the read: {n} of 4")
            return {"found by the read": f"{n} of 4"}

        shown = steps.speaker_names(transcript, labels)
        player_names = sorted({shown[label] for label in roles})

        made: dict[str, int] = {}  # the players' speaker numbers, by the name the import reads

        async def speakers_step() -> Mapping[str, int | str]:
            """The four players are made as speakers without Discord, by the import's own door,
            before their characters are set (the cleaning of the import needs them)."""
            if not off:
                return {"on Discord": 1}
            async with steps.site_session(db, dm, gid, now=int(time.time())) as site:
                made.update(await steps.make_speakers(db, site, gid, cid, player_names))
            return {"made": len(made)}

        async def correct_step() -> Mapping[str, int | str]:
            who = players
            if off:  # label key (ist...) -> the speaker's negative number
                who = {roles[label]: made[shown[label]] for label in roles}
            got = await characters.correct_player_characters(db, memory, gid, cid, who)
            return {"corrected": got.corrected, "of those made": got.made}

        async def import_step() -> Mapping[str, int | str]:
            if not off:
                assert planned is not None
                sid = await steps.store_import(
                    db, gid, cid, dm, planned, started_at=started, now=now
                )
                got = await steps.wait_import(db, gid, sid, say=say, sleep=sleep)
                return {**got, "kept": planned.kept}
            choice: dict[str, object] = {label: {"confirmed": True} for label in roles}
            if dm_label is not None:  # the DM is a real account: agreed, else ticked in
                who_dm: dict[str, object] = {"member": str(dm)}
                if dm not in agreed:
                    who_dm["confirmed"] = True
                choice[dm_label] = who_dm
            fresh = int(
                time.time()
            )  # (the lore wait was long: a website session must not be stale)
            async with steps.site_session(db, dm, gid, now=fresh) as site:
                sid = await steps.import_through_site(
                    db, site, gid, cid, session_text, choice, started_at=started, now=fresh
                )
            got = await steps.wait_import(db, gid, sid, say=say, sleep=sleep)
            return {**got, "players not on Discord": len(roles)}

        async def recap_step() -> Mapping[str, int | str]:
            code = await steps.write_recap(gid, cid, started)
            if code != 0:
                raise SampleError(f"the recap was not made (recap_run exit {code})")
            return {"made": 1}

        if args.accept_all:  # the old way (#3544), for comparison
            lore_accept: dict[str, Callable[[], Awaitable[Mapping[str, int | str]]]] = {
                "accept all (lore)": accept_step,
                "backup and score (lore)": lambda: score_step("lore", "lore"),
            }
            both_accept: dict[str, Callable[[], Awaitable[Mapping[str, int | str]]]] = {
                "accept all (session)": accept_step,
                "backup and score (both)": lambda: score_step("both", "both"),
            }
        else:  # three columns, never mixed (#3900): sure, then the AI's decisions; the rest left
            lore_accept = {
                "sure (lore)": sure_step,
                "backup and score (lore, sure)": lambda: score_step("lore-sure", "lore"),
                "AI decides (lore)": lambda: decide_step("lore"),
                "backup and score (lore, sure + AI)": lambda: score_step("lore", "lore"),
            }
            both_accept = {
                "sure (session)": session_sure_step,
                "backup and score (both, sure)": lambda: score_step("both-sure", "both"),
                "AI decides (session)": lambda: decide_step("session"),
                "backup and score (both, sure + AI)": lambda: score_step("both", "both"),
            }
        await steps.run_steps(
            {
                "lore read": lore_step,
                "player characters found by the read": found_step,
                **lore_accept,
                "players not on Discord": speakers_step,
                "player characters corrected": correct_step,
                "session import": import_step,
                **both_accept,
                "recap (no audio)": recap_step,
            },
            done,
            say,
            plan.STEPS if args.accept_all else plan.STEPS_DECIDE,
        )
        # The secret connections by source and the split-party counts (Supervisor, #3545): counts
        # only, read after every step, for dev1's report.
        try:
            for line in await counts.read(db, gid, cid):
                say(line)
        except Exception as exc:  # (a count that can't be read never fails the run)
            say(f"Counts: not read ({type(exc).__name__})")
    finally:
        await db.close()
        if decider:  # the run's decisions, all stages together: counts and agreement only
            await decider[1]()
            for line in decider[0].run.lines():
                say(line)
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


def _sure() -> Sure:
    """What DMbot is sure of, added as an upload adds it (#3878, `memory.auto_accept` narrowed): the
    "sure" column (#3900). `connections_only`: just the connections that are ready now (after the
    AI's answers named their ends). Counts only; refuses any campaign but the sample."""
    from dmbot.devtools import accept_all
    from dmbot.memory import auto_accept as auto
    from dmbot.memory.backstory_links import BackstoryLinks
    from dmbot.memory.store import MemoryStore
    from dmbot.ui.names import background_kind

    async def sure(
        db: Database, guild: int, campaign: str, *, connections_only: bool = False
    ) -> Mapping[str, int]:
        try:
            await accept_all.check_campaign(db, guild, campaign)
        except accept_all.WrongCampaign as exc:
            raise SampleError(str(exc)) from exc
        memory, tally, links = MemoryStore(db), auto.Tally(), BackstoryLinks(db)
        kept_before = len(await memory.entities(guild, campaign, statuses=("confirmed",)))
        if not connections_only:
            await auto.accept_names(memory, guild, campaign, tally, kind_of=background_kind)
            await auto.accept_other_names(memory, guild, campaign, tally)
        await auto.accept_connections(memory, links, guild, campaign, tally)
        return {
            "accepted": sum(tally.accepted.values()),
            "left waiting": sum(tally.waiting.values()),
            "refused by a rule": sum(tally.refused.values()),
            "changes": len(tally.batches),
            # #4382: what "left waiting" is, so a run says it without SQL. A lore upload keeps what
            # DMbot is sure of by itself (`add_sure`, unless the DM asked to be asked first), so
            # this step often accepts 0: "names kept before" says the upload did it.
            "names kept before": kept_before,
            **await _waiting_by_kind(memory, links, guild, campaign, tally),
        }

    return sure


async def _waiting_by_kind(
    memory: Any, links: Any, guild: int, campaign: str, tally: Any
) -> dict[str, int]:
    """The sure step's "left waiting", split (#4382): names, other names and connections, and the
    connections by why they wait (an end that isn't a kept entry yet, worked out by DMbot, or a
    hand-over: each a question for the DM; the AI step decides none of these). Counts only."""
    from dmbot.memory.auto_accept import CONNECTIONS, NAMES, OTHER_NAMES

    onto = await memory.ontology(guild, campaign)
    symmetric = {p.key for p in onto.predicates.values() if p.symmetric}
    still = await links.waiting(guild, campaign, symmetric=symmetric)
    return {
        "waiting names": tally.waiting[NAMES],
        "waiting other names": tally.waiting[OTHER_NAMES],
        "waiting connections": tally.waiting[CONNECTIONS],
        "connections: an end not kept": sum(1 for w in still if not w.ready),
        "connections: worked out": sum(1 for w in still if w.ready and w.inferred),
        "connections: a hand-over": sum(
            1 for w in still if w.ready and not w.inferred and w.hand_over
        ),
    }


def _careful_client(environ: Mapping[str, str]) -> tuple[Any, Callable[[], Awaitable[None]]]:
    """The careful tier, as the bot's own settings name it, for the decisions (#3900)."""
    from dmbot.ai import AIModelTier, AnthropicClient, Feature
    from dmbot.config import ConfigError, parse_ai_models

    try:
        models, _ = parse_ai_models(lambda name: environ.get(name, ""))
    except ConfigError as exc:
        raise SampleError(str(exc)) from exc
    # never a quiet fall back to a smaller model: the record says "careful" (#3900)
    client = AnthropicClient(environ.get("ANTHROPIC_API_KEY", ""), models, fallback=False)
    tier = client.tier(AIModelTier.CAREFUL, Feature.NAMES)
    return tier, client.close


if __name__ == "__main__":
    raise SystemExit(main())
