"""The whole per-deploy sample run in one command, with no one in Discord (#3547, #3545):

    python -m dmbot.devtools.sample_run --guild GUILD --dm DM_USER_ID \\
        --deploy LABEL --lore FILE --session FILE --yes

The session's speakers are `player_jac`, `player_ist`, `player_owe` and `player_eri` (players who
are NOT on Discord, imported with the DM's confirmation as the website's import does) and `DM`.
The older `--players ist=ID,jac=ID,eri=ID,owe=ID` (four real test accounts) still works.

It makes the sample campaign, queues the lore the way the website's upload does and waits for the
running bot to read it, accepts what was found, backs up and scores the lore, stores the session
file as an imported one and waits for the bot to clean, scan and read it, accepts again, backs up
and scores both, then writes the recap (no audio). The last thing it prints is one block of counts
and times, ready to paste on #3545.

Only campaigns called "test of the frostmaiden (sample LABEL)" (LABEL is the deploy,
`--deploy LABEL`: a number 1 to 9999 with an optional lowercase letter, 40 or 40b) are ever
made or deleted, and only when they are plainly this tool's. The newest three are kept so the story
graphs can be compared; the same N again replaces that deploy's campaign (#4538). Paid AI calls:
the most they can be is printed first, and nothing runs without `--yes`. Nothing of the lore, the
session or the recap is printed, logged or kept in the repository; backups go to a folder outside
it. The AI key and database address come from the
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

from dmbot.devtools import sample_name
from dmbot.devtools.sample_run import characters, costs, counts, map_step, plan, steps
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
DEFAULT_REPEAT = 5  # Supervisor's ruling on #3545 (2026-11-15)
MAX_USD = 5.0  # the cap a deploy, the same ruling
USD_PER_CREDIT = 0.001  # `credits.MICRO_PER_CREDIT`: 1 credit = 1,000 micro-dollars
# Set aside for the kept run's recap before any run starts: the recap writer is not on the bot's
# credit ledger, so its cost is never measured here; runs 16 and 17 on #3545 cost $0.40 to $0.60
# What one run may cost, set aside for each of the runs that go together (#4551). Measured, not
# guessed (#4652): the bot's AI cost a median of $0.85 a run on deploy 34 ($3.55 for four runs), so
# $1.50 is about 1.7 times that. At $3.00 five runs needed $15 and `--parallel 5` ran one at a time.
RUN_USD = 1.5
RECAP_RESERVE_USD = 0.75
# And for the sample map's one close read (#4423): one careful-model look at one picture
MAP_RESERVE_USD = 0.10
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
    parser.add_argument("--lore", type=Path, help="the lore text file")
    parser.add_argument("--session", type=Path, help="the session text file")
    parser.add_argument(
        "--version",
        choices=tuple(sample_name.VERSIONS),
        default=sample_name.TYPED,
        help="which version of the sample session --session is (docs/design/session_names.md, "
        "section 8): typed (the default, as always), no-capitals (the owner's edit with the "
        "capital-letter clues taken out) or spoken (through speech-to-text). Each is its own "
        'series: its own campaigns ("sample N.k no capitals") and summaries, never mixed',
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=DEFAULT_REPEAT,
        help=f"fresh sample campaigns to run, back to back (default {DEFAULT_REPEAT}, #3545): "
        "each count is then shown as its median, range and spread; the last one is kept",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="how many of the --repeat runs go at the same time (default 1, #4551): each run is "
        'its own campaign, "sample N.k"; the budget for all of them is set aside first',
    )
    parser.add_argument(
        "--run-usd",
        type=float,
        default=RUN_USD,
        help=f"the most one run may cost (default ${RUN_USD:g}), set aside for each run that goes "
        "at the same time before any starts",
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        default=MAX_USD,
        help=f"stop starting runs once the next would pass this (default ${MAX_USD:g} a deploy)",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        help="the last deploy's summary (default: the newest in --backup-dir), to say what changed",
    )
    parser.add_argument(
        "--map",
        type=Path,
        help="the sample map (#4423), on the server only: added to the kept campaign as the DM's "
        "map after the session, read closely by the running bot, scored with map_key_check",
    )
    parser.add_argument(
        "--part2",
        metavar="CAMPAIGN",
        help="part 2 of 2 (#3545): after the owner answered the kept run's questions, what became "
        "of each one; needs only --guild, --dm and this",
    )
    parser.add_argument(
        "--deploy",
        type=str,
        metavar="LABEL",
        help="this deploy's label: a number from 1 to 9999 and, if you like, one lowercase letter "
        '(40, 40b); the campaign is "test of the frostmaiden (sample 40b)", and the newest three '
        "are kept (#4538)",
    )
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
    parser.add_argument(
        "--party-key",
        type=Path,
        help="score the goodbye questions against this key (party_cases_key.json, #4537 6c)",
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


def deploy_label(value: str | None) -> sample_name.Label:
    """The deploy's label from `--deploy LABEL` (40 or 40b), or a refusal that changes nothing
    (#4538, #5057)."""
    if value is None:
        raise SampleError(sample_name.SAY_WHICH)
    try:
        return sample_name.parse_label(value)
    except ValueError as exc:
        raise SampleError(str(exc)) from exc


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
    recap: bool = True,
    metrics: dict[str, float] | None = None,
    snapshot: Path | None = None,
    kept: dict[str, Any] | None = None,
    run_k: int | None = None,
) -> int:
    """One sample run. `recap`: write the recap (only the kept run does, #3545). `metrics`: filled
    with the run's numeric counts, scores, questions waiting and spend, for `--repeat`. `snapshot`:
    where the questions still waiting at the end are written for `--part2`. `kept`: filled with
    the campaign and the session's start, so `--repeat` can write the kept run's recap after.
    `run_k`: this is run k of a parallel deploy (#4551): the deploy's room was made once by the
    caller, and the campaign is "sample N.k"."""
    say = say or (lambda line: print(line, flush=True))
    if args.lore is None or args.session is None:
        raise SampleError("--lore and --session are needed for a run")
    deploy = deploy_label(args.deploy)
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
    lore, windows, story, recap_calls = steps.estimate_cost(
        len(lore_text.split()), len(transcript.utterances), chars
    )
    say(plan.Cost(lore, windows, story, recap_calls if recap else 0).line())
    if not args.accept_all:
        calls = max(args.max_decisions, 0)
        say(
            f"decisions: at most {calls} careful-model calls (#3900),"
            f" about ${decide_mod.estimate(calls):.2f}"
        )
    for line in describe_input(transcript, args.version):  # (counts, and the sample's names only)
        say(line)
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
    spent: list[costs.CostLine] = []  # the recap's own figures, for the block at the end (#4087)
    where: list[str] = []  # the campaign's id once it is known: the ledger is read from it
    decider: list[
        Any
    ] = []  # (the decisions' client is made at their first stage, closed at the end)
    try:
        campaigns, memory = steps.campaign_store(db), MemoryStore(db)
        gid, dm = args.guild, args.dm
        # The checks that need the database, still before anything is changed or spent.
        await steps.require_paid(db, dm, now, guild_id=gid)
        found = await steps.check_existing(
            db, campaigns, gid, dm, None if run_k else deploy, args.version
        )
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

        if run_k is not None:  # (a sibling's campaign is no reason to stop: no replacing here)
            campaign = await steps.make_campaign(
                campaigns, gid, dm, deploy=deploy, run=run_k, say=say, version=args.version
            )
        else:
            campaign = await steps.fresh_campaign(
                db, campaigns, gid, dm, deploy=deploy, say=say, found=found, version=args.version
            )
        cid = campaign.id
        where.append(cid)
        if kept is not None:
            kept.update(guild=gid, campaign=cid, started=started)

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
        # (a run beside others has its own file: they start in the same second, #4551)
        which = f"-{run_k}" if run_k is not None else ""
        record = (
            folder / f"decisions-{dt.datetime.now(dt.UTC).strftime('%Y%m%d-%H%M%S')}{which}.jsonl"
        )

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
            # (#5055 D: a backup does not carry them; read just after it, close enough for a test
            # campaign nobody else touches)
            met = await steps.met_words(db, gid, cid)
            lines = steps.score(path, docs, met)
            for line in lines:
                say(line)
            return {"lines": len(lines), **steps.score_numbers(path, docs, met)}

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
            why: list[str] = []
            scores: dict[str, int | str] = {}
            code = await steps.write_recap(gid, cid, started, spent=spent, why=why, scores=scores)
            if code != 0:
                reason = f": {why[-1]}" if why else " (and no reason was printed)"
                raise SampleError(f"the recap was not made (recap_run exit {code}){reason}")
            return {"made": 1, **scores}  # (the numbers go in the report for #3545, #4556)

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
        order = plan.STEPS if args.accept_all else plan.STEPS_DECIDE
        if not recap:  # (only the kept run writes one: it costs most and is no graph score)
            order = tuple(s for s in order if s != "recap (no audio)")
        actions = {
                "lore read": lore_step,
                "player characters found by the read": found_step,
                **lore_accept,
                "players not on Discord": speakers_step,
                "player characters corrected": correct_step,
                "session import": import_step,
                **both_accept,
                "recap (no audio)": recap_step,
            }  # fmt: skip
        if not recap:
            del actions["recap (no audio)"]
        try:
            await steps.run_steps(actions, done, say, order)
        finally:  # (a run that stopped part way still counts what it did and spent)
            if args.map is not None:
                if recap and len(done) == len(order):
                    # (a single run's map step, once the rest is done: `--repeat` does it after
                    # the runs, on the kept campaign)
                    await _map_on(args, {"guild": gid, "campaign": cid}, folder, say)
                elif recap:
                    # (#4646: a step that does not run says why, never silence; the runs of a
                    # `--repeat` stay quiet here, `_finish` says it for the kept run)
                    map_step.skipped(say, "an earlier step did not finish")
            if metrics is not None or snapshot is not None:
                await _measure(db, memory, gid, cid, done, metrics, snapshot, say)
        # The secret connections by source and the split-party counts (Supervisor, #3545): counts
        # only, read after every step, for dev1's report.
        try:
            for line in await counts.read(db, gid, cid):
                say(line)
        except Exception as exc:  # (a count that can't be read never fails the run)
            say(f"Counts: not read ({type(exc).__name__})")
        # The party's questions and closed stretches (#4537 slice 6c), scored against the party
        # key when one is given (`--party-key`): counts only.
        try:
            given = getattr(args, "party_key", None)
            key = counts.load_party_key(given) if given else None
            for line in await counts.read_party(db, gid, cid, key):
                say(line)
        except Exception as exc:
            say(f"Party counts: not read ({type(exc).__name__})")
    finally:
        # The bot's own AI by step, read here so a run that stopped part way still counts what the
        # bot spent before it stopped (the db is open on every path only until the next line).
        ledger = await _ledger(db, args.guild, where[0], say) if where else []
        await db.close()
        if decider:  # the run's decisions, all stages together: counts and agreement only
            if metrics is not None:
                metrics["spend: decisions ($)"] = round(decider[0].run.cost, 4)
            await decider[1]()
            for line in decider[0].run.lines():
                say(line)
        for line in plan.report(
            done, commit=commit, switches=switches(os.environ), today=dt.date.today().isoformat()
        ):
            say(line)
        if metrics is None:  # (a `--repeat` run already reports its spend in its metrics line)
            lines = [*(ledger or []), *spent]
            if decider and decider[0].run.calls:
                run = decider[0].run
                lines.append(costs.decision_line(run.input_tokens, run.output_tokens, run.cost))
            for line in costs.block(lines, incomplete=ledger is None):
                say(line)
    return 0


async def _ledger(
    db: Database, gid: int, cid: str, say: Callable[[str], None]
) -> list[costs.CostLine] | None:
    """What the bot's own AI work cost this campaign, by kind of job, from the credits ledger
    (#4087). It holds credits, never tokens, so those lines say so. A read that fails never fails
    the run: the line says it was not read and the total says it is incomplete (None). The bot
    flushes its charges a moment after each call, so the very last one can be missing."""
    try:
        async with db.guild(gid) as conn:
            cur = await conn.execute(
                "SELECT job_kind, COALESCE(sum(credits), 0) AS c FROM ai_credit_spend"
                " WHERE guild_id = %s AND campaign_id = %s GROUP BY job_kind",
                (gid, cid),
            )
            rows = await cur.fetchall()
    except Exception as exc:
        say(f"Cost by step: the ledger was not read ({type(exc).__name__})")
        return None
    return costs.ledger_lines({str(r["job_kind"]): int(r["c"]) for r in rows})


async def _measure(
    db: Database,
    memory: Any,
    gid: int,
    cid: str,
    done: Sequence[Step],
    metrics: dict[str, float] | None,
    snapshot: Path | None,
    say: Callable[[str], None],
) -> None:
    """At a run's end (#3545): its numbers for the spread, the questions left for the DM (A), what
    the bot's AI cost it, and for the kept run the snapshot part 2 reads. Counts only are said."""
    from dmbot.consent import ConsentStore
    from dmbot.devtools.sample_run import questions, spread
    from dmbot.memory.backstory_links import BackstoryLinks
    from dmbot.memory.claims import ClaimStore
    from dmbot.questions import counts as question_counts
    from dmbot.transcript.waiting_questions import WaitingQuestionStore

    if metrics is not None:
        for step in done:
            metrics.update(spread.step_metrics(step.name, step.counts))
    try:
        sources = question_counts.Sources(
            db=db, memory=memory, claims=ClaimStore(memory), backstory=BackstoryLinks(db),
            heard=WaitingQuestionStore(db), agreed=ConsentStore(db).consenting,
            recorded=ConsentStore(db).recorded_in,
        )  # fmt: skip
        asked = await questions.asked(sources, gid, cid)
    except Exception as exc:  # (a count that can't be read never fails the run)
        say(f"Questions waiting for the DM: not counted ({type(exc).__name__})")
    else:
        total = sum(asked.values())
        said = ", ".join(f"{k} {v}" for k, v in asked.items()) or "none"
        say(f"Questions waiting for the DM: {total} ({said})")
        if metrics is not None:
            metrics["questions: total"] = total
            metrics.update({f"questions: {k}": v for k, v in asked.items()})
    if metrics is not None:
        try:
            async with db.guild(gid) as conn:
                cur = await conn.execute(
                    "SELECT COALESCE(sum(credits), 0) AS c FROM ai_credit_spend"
                    " WHERE guild_id = %s AND campaign_id = %s",
                    (gid, cid),
                )
                row = await cur.fetchone()
        except Exception as exc:
            say(f"Spend: not read ({type(exc).__name__})")
        else:
            credits = int(row["c"] if row else 0)
            metrics["spend: the bot's AI ($)"] = round(credits * USD_PER_CREDIT, 4)
    if snapshot is not None:
        try:
            questions.save(
                snapshot,
                {"campaign": cid, "guild": gid, **await questions.waiting(db, memory, gid, cid)},
            )
        except Exception as exc:
            say(f"Questions for part 2: not kept ({type(exc).__name__})")
        else:
            say(
                f"Questions kept for part 2 of 2: {snapshot.name} (run --part2 {cid} once answered)"
            )


def _cost(metrics: Mapping[str, float]) -> float:
    return metrics.get("spend: the bot's AI ($)", 0.0) + metrics.get("spend: decisions ($)", 0.0)


async def run_many(args: argparse.Namespace, **kw: Any) -> int:
    """`--repeat N` (#3545): N fresh sample campaigns, back to back, then one summary of every
    count as its median, range and spread, the noise of each step, and what changed since the last
    deploy's summary. Each run replaces the sample campaign, so the last one made is the one kept:
    each run writes the question snapshot (the last written is the kept run's), and the recap is
    written once, after the runs, for the kept campaign. The recap's cost is set aside first; a run
    is then started only while what was spent plus the dearest run so far still fits `--max-usd`.
    A run that fails stops the runs; the summary of those done is still printed and saved."""
    from dmbot.devtools import common

    say: Callable[[str], None] = kw.get("say") or (lambda line: print(line, flush=True))
    check_compare(args)  # (before anything is spent)
    n = max(1, args.repeat)
    if not args.yes or n == 1:
        return await run(args, **kw)
    if args.parallel > 1:
        return await run_parallel(args, n, **kw)
    folder = steps.keep_folder(args.backup_dir)
    commit = common.commit(args.commit, tool="sample_run")  # (before any money is spent)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    snap = folder / f"questions-{stamp}.json"
    budget = args.max_usd - RECAP_RESERVE_USD - (MAP_RESERVE_USD if args.map else 0.0)
    if budget <= 0:
        raise SampleError(f"--max-usd must leave room for the recap (${RECAP_RESERVE_USD:g})")
    runs: list[dict[str, float]] = []
    kept: dict[str, Any] = {}
    spent = dearest = 0.0
    failed: str | None = None
    for i in range(1, n + 1):
        if runs and spent + dearest > budget:
            say(f"Stopped after {len(runs)} runs: another would pass ${args.max_usd:g}.")
            break
        say(f"=== run {i} of {n} ===")
        metrics: dict[str, float] = {}
        try:
            await run(args, recap=False, metrics=metrics, snapshot=snap, kept=kept, **kw)
        except Exception as exc:  # (the kind only; the summary of the runs done still comes)
            failed = type(exc).__name__ if not isinstance(exc, SampleError) else str(exc)
            say(f"run {i} stopped: {failed}")
        if metrics:
            runs.append(metrics)
            spent += _cost(metrics)
            dearest = max(dearest, _cost(metrics))
            say(f"run {i}: about ${_cost(metrics):.2f}; ${spent:.2f} so far")
        if failed:
            break
    return await _finish(args, say, kept, failed, runs, spent, folder, stamp, commit)


def fit_parallel(budget: float, run_usd: float, wanted: int) -> int:
    """How many runs may go at the same time: `wanted`, or fewer when the money set aside for each
    (`run_usd`, the most one run may cost) for all of them does not fit `budget` (#4551). 0 when
    not even one fits."""
    if run_usd <= 0:
        raise SampleError("--run-usd must be more than 0")
    return max(0, min(wanted, int(budget // run_usd)))


async def _make_room(
    args: argparse.Namespace, deploy: sample_name.Label, say: Callable[[str], None], need: int = 1
) -> int:
    """Once, before the runs of a parallel deploy start (#4551): the plan check, and every sample
    campaign of this deploy (a re-run) and the oldest beyond the newest three deleted, as a single
    run does. The runs then each make their own campaign, "sample N.k"."""
    from dmbot import entitlements
    from dmbot.db import Database

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SampleError("DATABASE_URL isn't set")
    try:
        entitlements.configure_free_users(
            entitlements.parse_free_users(os.environ.get("DMBOT_FREE_USERS", ""))
        )
        entitlements.configure_free_guilds(
            entitlements.parse_free_guilds(os.environ.get("DMBOT_FREE_GUILDS", ""))
        )
    except ValueError as exc:
        raise SampleError(str(exc)) from exc
    db = await Database.open(url, migrate=False, max_size=2)
    try:
        campaigns = steps.campaign_store(db)
        await steps.require_paid(db, args.dm, int(time.time()), guild_id=args.guild)
        found = await steps.check_existing(db, campaigns, args.guild, args.dm, deploy, args.version)
        free = await steps.free_slots(db, args.dm, int(time.time()), args.guild)
        _kept, allowed = await steps.clear_for_deploy(
            db, campaigns, args.guild, args.dm, deploy=deploy, say=say, found=found,
            need=need, free=free, version=args.version,
        )  # fmt: skip
        return allowed
    finally:
        await db.close()


async def run_parallel(args: argparse.Namespace, n: int, **kw: Any) -> int:
    """`--repeat N --parallel K` (#4551): the runs go K at a time, each its own campaign
    ("sample N.k"), all of them kept (a deploy counts once toward the newest three, with all its
    runs). The money is set aside up front: K runs go together only when K times `--run-usd` fits
    what `--max-usd` leaves after the recap; otherwise as many as fit, said in a line. Each next
    batch is sized from the dearest run so far. A run that fails is counted and its siblings carry
    on; no new batch starts after a failure. Nothing is renamed or deleted after scoring."""
    from dmbot.devtools import common

    say: Callable[[str], None] = kw.pop("say", None) or (lambda line: print(line, flush=True))
    folder = steps.keep_folder(args.backup_dir)
    commit = common.commit(args.commit, tool="sample_run")  # (before any money is spent)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    budget = args.max_usd - RECAP_RESERVE_USD - (MAP_RESERVE_USD if args.map else 0.0)
    if budget <= 0:
        raise SampleError(f"--max-usd must leave room for the recap (${RECAP_RESERVE_USD:g})")
    wanted = min(args.parallel, n)
    width = fit_parallel(budget, args.run_usd, wanted)
    if width == 0:
        raise SampleError(
            f"--max-usd leaves ${budget:.2f} after the recap: not enough for one run "
            f"(${args.run_usd:g} set aside each, --run-usd)"
        )
    if width < wanted:
        need = wanted * args.run_usd + RECAP_RESERVE_USD + (MAP_RESERVE_USD if args.map else 0.0)
        say(
            f"Only {width} at once fit ${args.max_usd:g}; --max-usd {need:.2f} would fit "
            f"{wanted}. Going {width} at a time."
        )
    deploy = deploy_label(args.deploy)
    # The paid slots are counted before anything is deleted or made, like the money: older kept
    # deploys go (all their runs, never the newest) until the runs fit, or fewer runs are made.
    allowed = await _make_room(args, deploy, say, n)
    if allowed < n:
        say(f"Only {allowed} of {n} runs have a paid campaign slot: running {allowed}.")
        n = allowed
    runs: list[dict[str, float]] = []
    kept_by_run: dict[int, dict[str, Any]] = {}
    spent = dearest = 0.0
    failures: list[str] = []
    todo = list(range(1, n + 1))

    async def one(k: int) -> tuple[int, dict[str, float], str | None]:
        metrics: dict[str, float] = {}
        kept_k: dict[str, Any] = {}
        kept_by_run[k] = kept_k

        def prefixed(line: str) -> None:
            say(f"[run {k}] {line}")

        try:
            await run(
                args, recap=False, metrics=metrics, kept=kept_k, run_k=k, say=prefixed,
                snapshot=folder / f"questions-{stamp}-{k}.json", **kw,
            )  # fmt: skip
        except Exception as exc:  # (the kind only; the other runs go on)
            why = type(exc).__name__ if not isinstance(exc, SampleError) else str(exc)
            prefixed(f"stopped: {why}")
            return k, metrics, why
        return k, metrics, None

    while todo and not failures:
        size = width
        if runs:  # (sized from the dearest run so far, not the allowance)
            size = fit_parallel(
                budget - spent, dearest or args.run_usd, width
            )  # (0: no spend read)
            if size == 0:
                say(f"Stopped after {len(runs)} runs: another would pass ${args.max_usd:g}.")
                break
        batch, todo = todo[:size], todo[size:]
        say(f"=== runs {', '.join(str(k) for k in batch)} of {n}, together ===")
        for k, metrics, why in await asyncio.gather(*(one(k) for k in batch)):
            if metrics:
                spent += _cost(metrics)  # (a failed run's money is spent all the same)
                dearest = max(dearest, _cost(metrics))
                say(f"run {k}: about ${_cost(metrics):.2f}; ${spent:.2f} so far")
                if not why:  # (half a run's counts would spoil the median and the spread)
                    runs.append(metrics)
            if why:
                failures.append(why)
    if failures:
        say(f"{len(failures)} run(s) stopped; the summary of the {len(runs)} finished follows.")
    first = next((kd for _, kd in sorted(kept_by_run.items()) if kd.get("campaign")), {})
    return await _finish(
        args, say, first, failures[0] if failures else None, runs, spent, folder, stamp, commit
    )


async def _finish(
    args: argparse.Namespace,
    say: Callable[[str], None],
    kept: Mapping[str, Any],
    failed: str | None,
    runs: list[dict[str, float]],
    spent: float,
    folder: Path,
    stamp: str,
    commit: str,
) -> int:
    """After the runs: the kept run's recap and map, then the one summary (`--repeat`, with or
    without `--parallel`)."""
    from dmbot.devtools.sample_run import spread

    if kept and not failed:
        say("=== recap of the kept run (no audio) ===")
        why: list[str] = []
        scores: dict[str, int | str] = {}
        code = await steps.write_recap(
            kept["guild"], kept["campaign"], kept["started"], why=why, scores=scores
        )
        if code == 0:
            say("recap: made")
            for label, value in scores.items():  # (numbers only, for #3545, #4556)
                say(f"{label}: {value}")
        else:
            reason = why[-1] if why else "no reason was printed"
            say(f"recap: not made (recap_run exit {code}); {reason}")
        if args.map is not None:
            await _map_on(args, kept, folder, say)
    elif args.map is not None:  # (#4646)
        map_step.skipped(say, "a run failed, so none was kept" if failed else "no run was kept")
    stats = spread.summarize(runs)
    version = summary_version(args)
    say(
        f"Sample summary, commit {commit}, {version} session: {len(runs)} runs, about"
        f" ${spent:.2f} (#3545)"
    )
    for line in spread.render(stats):
        say(line)
    for name in spread.sure_flags(stats):
        say(f"SURE STEP VARIED with the same input, a bug to file: {name}")
    before = args.compare or _newest(folder, summary_glob(args.version))
    if before is not None:
        old_commit, old = spread.load(before)
        moved = spread.compare(stats, old)
        say(f"Since {old_commit or 'the last summary'}: " + ("nothing moved" if not moved else ""))
        for line in moved:
            say(line)
    if runs:
        spread.save(
            folder / summary_file(args.version, stamp), stats, commit=commit, version=args.version
        )
    return 1 if failed else 0


def describe_input(transcript: Any, version: str) -> list[str]:
    """How the session file writes the session key's names (docs/design/session_names.md,
    section 8): a description of the input, beside the score, never a score. For a version other
    than typed, the names still written with a capital mid-sentence are listed (the public
    sample's names), since the edit was meant to remove every one."""
    import json

    from dmbot.devtools import graph_key_check
    from dmbot.devtools.sample_run import input_shape

    path = graph_key_check.DEFAULT_KEY.parent / "session_key.json"
    try:
        key = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # (the kind only)
        return [f"Session file: the session key could not be read ({type(exc).__name__})"]
    got = input_shape.shape((u.text for u in transcript.utterances), input_shape.key_names(key))
    lines = [got.line()]
    if version != sample_name.TYPED and got.capitalized:
        lines.append(
            "Still capitalized mid-sentence in this version (check the file): "
            + ", ".join(got.capitalized)
        )
    return lines


def check_compare(args: argparse.Namespace) -> None:
    """An explicit `--compare` must be a summary of the same version of the session: refused here,
    before any run spends, never after (docs/design/session_names.md, section 8)."""
    if args.compare is None:
        return
    from dmbot.devtools.sample_run import spread

    try:
        theirs = spread.version_of(args.compare)
    except (OSError, ValueError) as exc:  # (the kind only: never the file's contents)
        raise SampleError(f"--compare cannot be read ({type(exc).__name__})") from exc
    if theirs != args.version:
        raise SampleError(
            f"--compare is a summary of the {theirs} session, not the {args.version} one:"
            " the versions are never mixed"
        )


def summary_version(args: argparse.Namespace) -> str:
    """The version's words for the summary line: "typed", "no capitals", "spoken"."""
    return sample_name.VERSIONS[args.version] or sample_name.TYPED


def summary_file(version: str, stamp: str) -> str:
    """The summary's file name: the typed one keeps the name it always had (`summary-STAMP.json`),
    so the last typed summary before this change is still compared with; another version's
    carries its name (`summary-no-capitals-STAMP.json`), so each series compares only with its
    own (docs/design/session_names.md, section 8: "numbers are never mixed across versions")."""
    if version == sample_name.TYPED:
        return f"summary-{stamp}.json"
    return f"summary-{version}-{stamp}.json"


def summary_glob(version: str) -> str:
    """The pattern for this version's summaries: a stamp starts with a digit, a version's name
    with a letter, so the typed series never picks up another's."""
    if version == sample_name.TYPED:
        return "summary-[0-9]*.json"
    return f"summary-{version}-[0-9]*.json"


async def _map_on(
    args: argparse.Namespace, kept: Mapping[str, Any], folder: Path, say: Callable[[str], None]
) -> None:
    """The sample map step (#4423) on the kept campaign: never fails the run, says why it
    skipped."""
    from dmbot.db import Database
    from dmbot.memory.store import MemoryStore

    say("=== the sample map ===")
    db = await Database.open(os.environ.get("DATABASE_URL", ""), migrate=False, max_size=2)
    try:
        await map_step.run(
            db, MemoryStore(db), int(kept["guild"]), str(kept["campaign"]), args.dm, args.map,
            folder, say=say,
        )  # fmt: skip
    except Exception as exc:  # (the kind only: the map step never fails the run)
        say(f"map: stopped ({type(exc).__name__})")
    finally:
        await db.close()


def _newest(folder: Path, pattern: str) -> Path | None:
    found = sorted(folder.glob(pattern))
    return found[-1] if found else None


async def part2(args: argparse.Namespace, say: Callable[[str], None] | None = None) -> int:
    """Part 2 of 2 (#3545): after the owner answered the kept run's questions in Discord, what
    became of each one, by kind, and the campaign scored against the master key again. Reads the
    newest snapshot made for that campaign; changes nothing but the backup file it writes."""
    from dmbot.db import Database
    from dmbot.devtools import accept_all
    from dmbot.devtools.sample_run import questions

    say = say or (lambda line: print(line, flush=True))
    folder = steps.keep_folder(args.backup_dir)
    snap = None
    for path in sorted(folder.glob("questions-*.json"), reverse=True):
        data = questions.load(path)
        if data and data.get("campaign") == args.part2 and data.get("guild") == args.guild:
            snap = data
            break
    if snap is None:
        raise SampleError(
            "no question snapshot for that campaign: was it a --repeat run's kept one?"
        )
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SampleError("DATABASE_URL isn't set")
    db = await Database.open(url, migrate=False, max_size=2)
    try:
        try:
            await accept_all.check_campaign(db, args.guild, args.part2)
        except accept_all.WrongCampaign as exc:
            raise SampleError(str(exc)) from exc
        result = questions.classify(snap, await questions.read_state(db, args.guild, args.part2))
        say("Part 2 of 2: what the DM answered (#3545), counts only")
        for line in questions.lines(result) or ["nothing was waiting"]:
            say(line)
        path = await steps.backup(
            steps.campaign_store(db), args.guild, args.part2, folder, "answered"
        )
        met = await steps.met_words(db, args.guild, args.part2)
        for line in steps.score(path, "both", met):
            say(line)
    finally:
        await db.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.part2:
            return asyncio.run(part2(args))
        return asyncio.run(run_many(args, accept=_accept_all()))
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
        memory, tally = MemoryStore(db), auto.Tally()
        kept_so_far = len(await memory.entities(guild, campaign, statuses=("confirmed",)))
        if not connections_only:
            await auto.accept_names(memory, guild, campaign, tally, kind_of=background_kind)
            await auto.accept_other_names(memory, guild, campaign, tally)
        await auto.accept_connections(memory, BackstoryLinks(db), guild, campaign, tally)
        # the story threads the lore reader suggested (#4453): kept for the score only, as the
        # bot never keeps one by itself; the key check counts them from the backup
        threads = (
            0 if connections_only else await accept_all.keep_threads(db, memory, guild, campaign)
        )
        # and the progress a session read on the campaign's threads (#4522 5c): kept for the
        # score only too, after the threads, in the order the 📜 card asks them
        # (what no longer fit the thread is left out by the keep: counted, so a reader's
        # drop in quality shows in the counts)
        progress_waiting = (
            0 if connections_only else await accept_all.progress_waiting(db, guild, campaign)
        )
        progress = (
            0 if connections_only else await accept_all.keep_progress(db, memory, guild, campaign)
        )
        counts = {
            "accepted": sum(tally.accepted.values()),
            "left waiting": sum(tally.waiting.values()),
            "refused by a rule": sum(tally.refused.values()),
            "changes": len(tally.batches),
        }
        if connections_only:  # (after the AI's answers: only "accepted" is read, names untouched)
            return counts
        # #4382: what "left waiting" is, so a run says it without SQL. A lore upload keeps what
        # DMbot is sure of by itself (`add_sure`, unless the DM asked to be asked first), so this
        # step often accepts 0. "names kept so far": every kept entry before this step (the
        # upload's sure names and every earlier step's; "The party" too, once the bot has filled
        # it in at a start-up). The connections' reasons add up to "waiting connections".
        return {
            **counts,
            "names kept so far": kept_so_far,
            "story threads kept for the score": threads,
            "thread progress kept for the score": progress,
            "thread progress left out": progress_waiting - progress,
            "waiting names": tally.waiting[auto.NAMES],
            "waiting other names": tally.waiting[auto.OTHER_NAMES],
            "waiting connections": tally.waiting[auto.CONNECTIONS],
            **{f"connections {why}": tally.waiting_why[why] for why in auto.WHY_WAITING},
        }

    return sure


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
