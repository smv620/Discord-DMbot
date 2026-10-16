"""A recap test run from the command line (#1424), for dev1's quality check before recaps go live.

    python -m dmbot.devtools.recap_run --session GUILD CAMPAIGN SESSION_AT [--audio] [--keep] --yes
    python -m dmbot.devtools.recap_run --golden --yes

It runs the real recap worker (`RecapWorker`: the same windows, checks and grader as the job) on a
stored session, or on the invented golden set, with paid AI calls. It says what it expects to cost
and does nothing without `--yes`. It does not queue, email or post anything, and writes nothing to
`recap_jobs` unless `--keep` is given (then one draft row is made for the DM to see on the
website). The transcript is only read. It prints to the terminal: the recap, the DM's private
campaign, never goes anywhere else, and a recap that names a secret name is not printed at all.

`--audio` also makes the narration and music into a temporary folder and prints the files' lengths
and the credits (needs ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID and ffmpeg). Keys come from the
environment and are never printed. Paste nothing from the output into GitHub but the scores and
counts.

Differences from the live job, on purpose: no plan or daily cap applies (a test run is not a
customer's), the names are read once at the start (the live job reads the secret names again at
each stage), and `--audio` makes the files without the per-call checks (the text was already
checked, and the script is built with the secret names as the second lock). Consent is the live
rule: only yeses for the engine in use count, read again at every stage.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from dmbot.ai import AIModels, AIModelTier, AnthropicClient, Reply
from dmbot.config import ConfigError, parse_ai_models
from dmbot.devtools.costs.prices import UnknownPrice, price_of
from dmbot.devtools.recap_measure.__main__ import beat_hits
from dmbot.devtools.recap_measure.golden import GoldenError, load_golden
from dmbot.devtools.recap_run.ephemeral import EphemeralStore
from dmbot.memory.extractor import Client, readable
from dmbot.recaps import beats as bt
from dmbot.recaps import draft as rd
from dmbot.recaps import pipeline as pl
from dmbot.recaps.store import Job, RecapStore
from dmbot.recaps.text import names_a_secret, narration_of, strip_cues
from dmbot.transcription.config import load_transcription_settings

Runner = Callable[[list[str]], Awaitable[int]]

DM_PLACEHOLDER = 1  # the test run has no account to email; this is only a label
REFUSED = (
    "recap_run: this recap names something the DM keeps secret, so it is not printed "
    "(and not kept). Nothing was posted or emailed."
)


@dataclass(slots=True)
class Counting:
    """Wraps a client to count the calls and tokens it spends (the worker only counts calls)."""

    client: Client
    model: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    async def complete(self, system: str, text: str, *, max_tokens: int = 8000) -> Reply:
        self.calls += 1  # counted when made: a call that fails was still a call
        reply = await self.client.complete(system, text, max_tokens=max_tokens)
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens
        return reply


@dataclass(slots=True)
class Outcome:
    job: Job
    inputs: pl.Inputs
    fast: Counting
    careful: Counting
    stopped: str = ""  # why it did not finish, in plain words
    notes: list[str] = field(default_factory=list)


def estimate(inputs: pl.Inputs) -> tuple[int, int]:
    """(the most AI calls a recap may make, about how many input tokens the first try needs)."""
    said = readable(inputs.lines)
    chars = sum(len(s.text) for s in said)
    windows = max(1, math.ceil(chars / inputs.window_chars))
    tokens = chars // 4 * 2 + 2500 * 3  # the transcript for beats, the beats for prose, prompts
    return pl.MAX_CALLS + windows - 1, tokens


def dollars(price: tuple[float, float] | None, tokens_in: int, tokens_out: int) -> str:
    if price is None:
        return "no price on file for this model (give --price MODEL=IN,OUT)"
    return f"${(tokens_in * price[0] + tokens_out * price[1]) / 1_000_000:.3f}"


async def run_text(
    inputs: pl.Inputs,
    fast: Client,
    careful: Client,
    consenting: Callable[[], Awaitable[Collection[int]]] | None = None,
    *,
    session_at: int = 1,
    models: tuple[str, str] = ("fast", "careful"),
) -> Outcome:
    """The recap worker on one session, in memory. Returns the job as it ended."""
    store = EphemeralStore(session_at, DM_PLACEHOLDER)
    fast_c, careful_c = Counting(fast, models[0]), Counting(careful, models[1])
    speakers = {s.user_id for s in readable(inputs.lines)}

    async def load(gid: int, cid: str, job: Job) -> pl.Inputs:
        return inputs

    async def agreed() -> Collection[int]:
        return await consenting() if consenting is not None else speakers

    async def allowed(gid: int, cid: str, job: Job) -> bool:
        return True  # a test run is not a customer's: no plan applies

    async def nobody(gid: int, cid: str, job: Job) -> None:
        return None  # nothing is emailed

    worker = pl.RecapWorker(
        store,  # type: ignore[arg-type]  # the same methods, held in memory
        fast_c,
        careful_c,
        load=load,
        consenting=lambda gid: agreed,
        allowed=allowed,
        notify=nobody,
    )
    stopped = ""
    for _ in range(40):  # each pass is one stage; a run never spins
        before = store.job
        await worker.run(0, "test-run", session_at)
        job = store.job
        if job.finished:
            break
        if job.next_at > time.time():  # an AI error: the live job would wait; a test run stops
            stopped = f"a call failed ({job.last_error}); the live job would try again later"
            break
        if job == before:
            stopped = "no progress"
            break
    return Outcome(store.job, inputs, fast_c, careful_c, stopped)


def final_checks(job: Job, inputs: pl.Inputs) -> list[tuple[str, bool, str]]:
    """Each check run again on the finished recap, so the report can say every result."""
    said = readable(inputs.lines)
    speakers = set(job.speakers)
    mine = [s for s in said if s.user_id in speakers]
    sheet = bt.from_json(job.beats)
    out: list[tuple[str, bool, str]] = []
    kept, problems = bt.check(
        bt.as_json(sheet),
        lines={s.number: s.text for s in mine},
        speakers={s.number: s.user_id for s in mine},
        allowed_names=inputs.allowed_names,
        secret_names=inputs.secret_names,
    )
    out.append(("beats cite real lines, no invented names", not problems, "; ".join(problems)))
    prose = rd.Prose(job.summary, tuple(job.music))
    transcript = "\n".join(s.text for s in mine)
    issues = rd.check_prose(
        prose,
        beats=sheet,
        transcript=transcript,
        allowed_names=inputs.allowed_names,
        secret_names=inputs.secret_names,
        previous=inputs.previous,
        setting=inputs.setting,
    )
    out.append(("prose: length, cues, names, no quotes, no copying", not issues, "; ".join(issues)))
    words = len(narration_of(job.summary).split())
    out.append((f"{words} words", rd.MIN_WORDS <= words <= rd.MAX_WORDS, ""))
    out.append((f"{len(kept)} beats kept", len(kept) >= bt.MIN_BEATS, ""))
    out.append(
        (
            "housekeeping bullets",
            len(job.housekeeping) <= rd.HOUSEKEEPING_BULLETS,
            f"{len(job.housekeeping)} bullet(s)",
        )
    )
    return out


def names_a_secret_anywhere(job: Job, inputs: pl.Inputs) -> bool:
    text = " ".join([job.title, job.summary, *job.housekeeping, *job.music])
    return names_a_secret(text, inputs.secret_names, inputs.allowed_names)


def report(out: Outcome, prices: dict[str, tuple[float, float]], *, show: bool = True) -> list[str]:
    """The terminal report. A recap that names a secret is refused, not printed."""
    job = out.job
    if names_a_secret_anywhere(job, out.inputs):
        return [REFUSED]
    lines: list[str] = []
    if job.state != "ready":
        lines.append(f"The run did not finish: state {job.state}. {job.last_error or out.stopped}")
    if show and job.summary:
        lines += [
            "",
            rd.heading(job.title, job.game_day),
            "",
            job.summary,
            "",
            "As the table reads it (no cues):",
            strip_cues(job.summary),
            "",
            "Housekeeping:",
            *(f"- {b}" for b in job.housekeeping),
            "",
            "Music prompts:",
            *(f"{i}. {m}" for i, m in enumerate(job.music, start=1)),
        ]
    if job.summary:
        lines += ["", "Checks:"]
        for name, ok, detail in final_checks(job, out.inputs):
            lines.append(
                f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" ({detail})" if detail else "")
            )
    if job.grade:
        weak = rd.weak(job.grade)
        lines += [
            "",
            "Grader: " + ", ".join(f"{k} {v}" for k, v in job.grade.items()),
            "  below the bar: " + ("; ".join(weak) if weak else "nothing"),
        ]
    tokens_in = out.fast.input_tokens + out.careful.input_tokens
    tokens_out = out.fast.output_tokens + out.careful.output_tokens
    spend = dollars(prices.get(out.fast.model), out.fast.input_tokens, out.fast.output_tokens)
    if out.careful.calls:
        spend += " + " + dollars(
            prices.get(out.careful.model), out.careful.input_tokens, out.careful.output_tokens
        )
    lines += [
        "",
        f"Calls: {out.fast.calls + out.careful.calls} ({out.fast.calls} fast, "
        f"{out.careful.calls} careful); tokens in {tokens_in}, out {tokens_out}; spend {spend}",
    ]
    return lines


async def run_audio(
    job: Job,
    inputs: pl.Inputs,
    provider: object,
    files: object,
    *,
    guild_id: int = 0,
    campaign_id: str = "test-run",
    runner: Runner | None = None,
) -> list[str]:
    """The narration and music into a temporary folder: the lengths and the credits. Removed
    afterwards; nothing is kept."""
    from dmbot.recaps.audio import script as sc
    from dmbot.recaps.audio.render import Renderer, estimate_credits, run_ffmpeg

    try:
        script = sc.build(job.title, job.game_day, job.summary, secret_names=inputs.secret_names)
    except sc.SecretInScript:
        return ["audio: not made (the text names a secret)"]
    renderer = Renderer(provider, files, runner=runner or run_ffmpeg)  # type: ignore[arg-type]
    folder = Path(tempfile.mkdtemp(prefix="recap-run-"))
    try:
        made = await renderer.render(
            guild_id, campaign_id, script, into=folder / "out", seed=job.session_at, dictionary=None
        )
        sizes = {p.name: p.stat().st_size for p in (folder / "out").iterdir()}
        return [
            "",
            f"Audio: {made.seconds:.1f} s, {made.credits} credits "
            f"(estimated {estimate_credits(script, ambience_needed=True)})",
            *(f"  {name}: {size // 1024} KB" for name, size in sorted(sizes.items())),
        ]
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="recap_run", description=__doc__)
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument(
        "--session", nargs=3, metavar=("GUILD", "CAMPAIGN", "SESSION_AT"),
        help="a stored session: server id, campaign id, when it started (Unix seconds)",
    )  # fmt: skip
    what.add_argument("--golden", action="store_true", help="the invented golden set")
    parser.add_argument("--audio", action="store_true", help="also make the narration and music")
    parser.add_argument("--keep", action="store_true", help="keep the result as a draft recap row")
    parser.add_argument("--yes", action="store_true", help="spend the money")
    parser.add_argument(
        "--price", action="append", default=[], metavar="MODEL=IN,OUT",
        help="dollars per million tokens, for a model not on file",
    )  # fmt: skip
    args = parser.parse_args(argv)
    if args.golden and (args.audio or args.keep):
        parser.error("--golden takes neither --audio nor --keep")
    for item in args.price:
        try:
            prices_of([item], [])
        except ValueError:
            parser.error(f"--price needs MODEL=IN,OUT in dollars per million tokens, got {item!r}")
    return args


def prices_of(given: Sequence[str], models: Sequence[str]) -> dict[str, tuple[float, float]]:
    """Dollars per million tokens (input, output) for each model: one given, else the checked
    list price on file; never a guess."""
    out: dict[str, tuple[float, float]] = {}
    for item in given:
        model, _, rest = item.partition("=")
        low, _, high = rest.partition(",")
        out[model] = (float(low), float(high))
    for model in models:
        if model not in out:
            try:
                low_p, high_p = price_of(model)
                out[model] = (low_p.dollars, high_p.dollars)
            except UnknownPrice:
                pass
    return out


def clients_from_env() -> tuple[Client, Client, tuple[str, str]] | None:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("recap_run: ANTHROPIC_API_KEY isn't set", file=sys.stderr)
        return None
    try:
        models: AIModels = parse_ai_models(lambda name: os.environ.get(name, ""))[0]
    except ConfigError as exc:
        print(f"recap_run: {exc}", file=sys.stderr)
        return None
    ai = AnthropicClient(key, models)
    return (
        ai.tier(AIModelTier.FAST),
        ai.tier(AIModelTier.CAREFUL),
        (models.of(AIModelTier.FAST), models.of(AIModelTier.CAREFUL)),
    )


async def run_golden(
    fast: Client,
    careful: Client,
    models: tuple[str, str],
    prices: dict[str, tuple[float, float]],
) -> tuple[list[str], bool]:
    """The golden set through the same worker as a live session; the score table."""
    rows = ["session            state    calls  beats  quote  grade"]
    total_in = total_out = 0
    clean = True
    for session in load_golden():
        inputs = pl.Inputs(
            lines=session.lines, dm_ids={1}, allowed_names=list(session.names),
            secret_names=list(session.secret), previous="", day=None,
            window_chars=session.window_chars,
        )  # fmt: skip
        out = await run_text(inputs, fast, careful, models=models)
        job = out.job
        if names_a_secret_anywhere(job, inputs):
            rows.append(f"{session.name:<18} REFUSED: it names a secret")
            clean = False
            continue
        said = readable(session.lines)
        found = beat_hits(session, bt.from_json(job.beats))
        transcript = "\n".join(s.text for s in said)
        narration = narration_of(job.summary)
        leak = int(rd.copies_the_table(narration, transcript) or '"' in narration)
        grade = ",".join(str(v) for v in job.grade.values()) or "-"
        rows.append(
            f"{session.name:<18} {job.state:<8} {out.fast.calls + out.careful.calls:>5}"
            f"  {found}/{len(session.reference):<4} {leak:>5}  {grade}"
        )
        total_in += out.fast.input_tokens + out.careful.input_tokens
        total_out += out.fast.output_tokens + out.careful.output_tokens
        clean = clean and job.state == "ready" and not leak
    spend = dollars(prices.get(models[0]), total_in, total_out)
    rows.append(f"tokens in {total_in}, out {total_out}; spend about {spend} (fast-tier price)")
    return rows, clean


async def main_async(args: argparse.Namespace) -> int:
    got = clients_from_env()
    if got is None:
        return 2
    fast, careful, models = got
    prices = prices_of(args.price, models)
    if args.golden:
        try:
            sessions = load_golden()
        except (OSError, GoldenError) as exc:
            print(f"recap_run: {exc}", file=sys.stderr)
            return 2
        worst = sum(estimate(_golden_inputs(s))[0] for s in sessions)
        print(f"golden set: {len(sessions)} sessions; up to {worst} AI calls.")
        if not args.yes:
            print("Nothing was run. Add --yes to spend the money.")
            return 0
        rows, clean = await run_golden(fast, careful, models, prices)
        print("\n".join(rows))
        return 0 if clean else 1
    return await _session(args, fast, careful, models, prices)


def _golden_inputs(session: object) -> pl.Inputs:
    return pl.Inputs(
        lines=session.lines,  # type: ignore[attr-defined]
        dm_ids={1}, allowed_names=[], secret_names=[], previous="", day=None,
        window_chars=session.window_chars,  # type: ignore[attr-defined]
    )  # fmt: skip


async def _session(
    args: argparse.Namespace,
    fast: Client,
    careful: Client,
    models: tuple[str, str],
    prices: dict[str, tuple[float, float]],
) -> int:
    from dmbot.campaigns.store import CampaignStore
    from dmbot.consent import ConsentStore
    from dmbot.db import Database
    from dmbot.memory.lookup import LookupCache
    from dmbot.memory.store import MemoryStore
    from dmbot.recaps.inputs import load_inputs
    from dmbot.recaps.store import RecapStore
    from dmbot.timebot.store import ClockStore
    from dmbot.transcript.store import TranscriptStore

    try:
        gid, cid, at = int(args.session[0]), args.session[1], int(args.session[2])
    except ValueError:
        print("recap_run: the server and the start time are whole numbers", file=sys.stderr)
        return 2
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("recap_run: DATABASE_URL isn't set", file=sys.stderr)
        return 2
    db = await Database.open(url, migrate=False, max_size=2)
    try:
        recaps = RecapStore(db)
        campaigns = CampaignStore(db)
        inputs = await load_inputs(
            campaigns=campaigns,
            lookup=LookupCache(MemoryStore(db)),
            transcripts=TranscriptStore(db),
            recaps=recaps,
            clocks=ClockStore(db),
            guild_id=gid,
            campaign_id=cid,
            session_at=at,
        )
        if inputs is None:
            print(
                "recap_run: no such session, or the campaign's names can't be read", file=sys.stderr
            )
            return 2
        # The same rule as the live bot: with an outside engine only yeses for it count.
        engine = load_transcription_settings(os.environ).outside_engine
        consent = ConsentStore(db, outside=engine)

        async def agreed() -> Collection[int]:
            return await consent.consenting(gid)  # read again at every stage, like the job

        calls, tokens = estimate(inputs)
        price = prices.get(models[0])
        print(
            f"session of {len(inputs.lines)} lines; up to {calls} AI calls; about {tokens} input "
            f"tokens on the first try ({dollars(price, tokens, 4000)} on the fast tier; a retry "
            "uses the careful tier and costs more)."
            + (" Plus about 7,000 provider credits for --audio." if args.audio else "")
        )
        if not args.yes:
            print("Nothing was run. Add --yes to spend the money.")
            return 0
        out = await run_text(inputs, fast, careful, agreed, session_at=at, models=models)
        lines = report(out, prices)
        if lines == [REFUSED]:
            print(REFUSED)
            return 3
        print("\n".join(lines))  # the paid text report first: nothing below can lose it
        if args.audio and out.job.state == "ready":
            try:
                print("\n".join(await _audio(out)))
            except Exception as exc:  # the provider, ffmpeg: say the kind, never the message
                print(f"audio: failed ({type(exc).__name__}); the text above is unchanged")
        if args.keep and out.job.state == "ready":
            campaign = await campaigns.get(gid, cid)
            assert campaign is not None  # load_inputs found it
            dm = campaign.owner_user_id or min(campaign.dm_user_ids, default=DM_PLACEHOLDER)
            made = await keep(recaps, gid, cid, out, dm)
            print(
                "Kept as a draft the DM can see on the website."
                if made
                else "A recap of that session already exists; not changed."
            )
        return 0 if out.job.state == "ready" else 1
    finally:
        await db.close()


async def _audio(out: Outcome) -> list[str]:
    from dmbot.recaps.audio.eleven import ElevenLabs, HttpTransport
    from dmbot.recaps.audio.files import AudioFiles

    key = os.environ.get("ELEVENLABS_API_KEY", "")
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "")
    if not key or not voice:
        return ["", "audio: not made (ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID aren't both set)"]
    if shutil.which("ffmpeg") is None:
        return ["", "audio: not made (ffmpeg isn't installed here)"]
    http = HttpTransport(key)
    try:
        model = os.environ.get("ELEVENLABS_SPEECH_MODEL") or "eleven_v3"
        provider = ElevenLabs(http, voice_id=voice, speech_model=model)
        # The ambience cache is a temporary folder too, so a run costs what a first run costs.
        with tempfile.TemporaryDirectory(prefix="recap-run-cache-") as cache:
            return await run_audio(out.job, out.inputs, provider, AudioFiles(Path(cache)))
    finally:
        await http.close()


async def keep(recaps: RecapStore, gid: int, cid: str, out: Outcome, dm_user_id: int) -> bool:
    """The finished recap as one draft row, written in one statement as already told (so the
    live worker never takes it and nothing is emailed). False if that session already has a
    recap, which is then left as it is."""
    job = out.job
    return await recaps.add_finished(
        gid, cid, job.session_at, dm_user_id=dm_user_id, title=job.title, summary=job.summary,
        original=job.original, housekeeping=job.housekeeping, music=job.music,
        game_day=job.game_day, speakers=job.speakers, grade=job.grade, beats=job.beats,
    )  # fmt: skip


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
