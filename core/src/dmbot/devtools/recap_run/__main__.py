"""A recap test run from the command line (#1424), for dev1's quality check before recaps go live.

    python -m dmbot.devtools.recap_run --session GUILD CAMPAIGN SESSION_AT [--audio] [--keep] --yes
    python -m dmbot.devtools.recap_run --golden --yes
    python -m dmbot.devtools.recap_run --transcript FILE --dm NAME [--names FILE] [--audio]
        [--out DIR] --yes

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

`--transcript FILE` (#1969) runs the same thing on a transcript file instead of a stored session,
to test a game played before DMbot was listening or recorded with another tool: DMbot's own
download, plain `Name: words` lines, or WebVTT and SRT (see `transcript_file.py`). `--dm NAME` is
the DM's name as the file spells it; `--names FILE` gives names, `Ka'zeth = Kah-zeth` (how the
voice says it) and `secret: Name` (the recap must never use it). **Consent:** an uploaded file has
no consent records, so before any paid call the tool asks whether everyone in it agreed to be
recorded and to this test, and anything but `yes` stops it. `--yes` covers the cost question and
never this one. This is CLAUDE.md's consent rule applied to the owner's own table, offline.
Nothing is stored: `--keep` is refused with a file, and the file is only read. `--out DIR` keeps
the voice, music and mix mp3s there (the folder is private to the user) and the report prints
which voice tag each cue became. The file and the outputs stay on the server; the tool never
prints the transcript, only the recap the owner reads. **Paste nothing from the output into
GitHub but the scores, counts and credits.**

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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from dmbot import ai_structured
from dmbot.ai import AIModels, AIModelTier, AnthropicClient, Reply
from dmbot.config import ConfigError, parse_ai_models
from dmbot.devtools.costs.prices import UnknownPrice, price_of
from dmbot.devtools.recap_measure.__main__ import beat_hits
from dmbot.devtools.recap_measure.golden import GoldenError, load_golden
from dmbot.devtools.recap_run import review as rv
from dmbot.devtools.recap_run import transcript_file as tf
from dmbot.devtools.recap_run.ephemeral import EphemeralStore
from dmbot.memory.extractor import readable
from dmbot.recaps import beats as bt
from dmbot.recaps import calls as cl
from dmbot.recaps import draft as rd
from dmbot.recaps import fixed_lines
from dmbot.recaps import pipeline as pl
from dmbot.recaps import split as sp
from dmbot.recaps.audio.eleven import ProviderError, check_line
from dmbot.recaps.audio.names import SpokenName
from dmbot.recaps.inputs import NotLoaded
from dmbot.recaps.pipeline import Client
from dmbot.recaps.store import Job, RecapStore
from dmbot.recaps.style import StyleStore
from dmbot.recaps.text import names_a_secret, narration_of, proper_nouns, strip_cues
from dmbot.recaps.text import words as words_of
from dmbot.transcript.improvement import filter_lines
from dmbot.transcription.config import load_transcription_settings

Runner = Callable[[list[str]], Awaitable[int]]

DM_PLACEHOLDER = 1  # the test run has no account to email; this is only a label
REFUSED = (
    "recap_run: this recap names something the DM keeps secret, so it is not printed "
    "(and not kept). Nothing was posted or emailed."
)


# The audio file store checks the ids it is given (a positive server number and a plain campaign
# id) because it guards the live bot's folders. A test run has no real campaign, so it uses these
# fixed placeholders, which are only ever used inside its temporary cache folder (#2062).
PLACEHOLDER_GUILD = 1
PLACEHOLDER_CAMPAIGN = "0" * 32
MESSAGE_MAX = 100  # characters of a ValueError's message in the report


@dataclass(slots=True)
class Counting:
    """Wraps a client to count the calls and tokens it spends (the worker only counts calls)."""

    client: Client
    model: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    async def complete(
        self,
        system: str,
        text: str,
        *,
        max_tokens: int = 8000,
        schema: dict[str, Any] | None = None,
    ) -> Reply:
        self.calls += 1  # counted when made: a call that fails was still a call
        if schema is None:
            reply = await self.client.complete(system, text, max_tokens=max_tokens)
        else:
            reply = await self.client.complete(system, text, max_tokens=max_tokens, schema=schema)
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens
        return reply


@dataclass(slots=True)
class Outcome:
    job: Job
    inputs: pl.Inputs
    fast: Counting
    careful: Counting
    creative: Counting | None = None  # the two-model recap's writer and formatter (#2074)
    formatting: Counting | None = None
    stopped: str = ""  # why it did not finish, in plain words
    notes: list[str] = field(default_factory=list)
    sheets: list[list[dict[str, Any]]] = field(default_factory=list)  # each beat sheet tried
    # (stage, try, the check's problems) for each prose or finish check that failed (#1969)
    failures: list[tuple[str, int, list[str]]] = field(default_factory=list)
    calls: list[cl.CallEvent] = field(default_factory=list)  # each AI call, in order (#2053)
    # With --structured (#2714): how the draft-then-form passes went, counts only; else None
    structured: dict[str, int] | None = None
    # Counts the worker reported (#2738): the away check's first finds. Numbers, never words.
    counts: dict[str, int] = field(default_factory=dict)


def estimate(inputs: pl.Inputs) -> tuple[int, int]:
    """(the most AI calls a recap may make, about how many input tokens the first try needs)."""
    said = readable(inputs.lines)
    chars = sum(len(s.text) for s in said)
    windows = max(1, math.ceil(chars / inputs.window_chars))
    tokens = chars // 4 * 2 + 2500 * 3  # the transcript for beats, the beats for prose, prompts
    return pl.MAX_CALLS + windows - 1, tokens


def count_lines(out: Outcome) -> list[str]:
    """The away check's and the beat sheet's counts (#2738): see `review.count_lines`."""
    return rv.count_lines(out.job, out.counts.get("away_found"), out.counts.get("away_left"))


def split_lines(out: Outcome) -> list[str]:
    """How the opening was split (#2253), counts and ranges only: never a word of a line."""
    split = sp.from_json(out.job.split)
    if split is None:
        return []
    said = readable(out.inputs.lines)
    counts = ", ".join(
        f"{label} {n}" for label in sp.LABELS if (n := len(sp.pick(said, split, label)))
    )
    ranges = ", ".join(f"{p.label} {p.first}-{p.last}" for p in split.parts)
    return [
        "",
        f"Split ({split.method}): {counts}.",
        f"  lines {ranges}"
        + ("; the retelling is UNSURE and was played as play" if split.unsure else ""),
    ]


def dollars(
    price: tuple[float, float] | None, tokens_in: int, tokens_out: int, model: str = "MODEL"
) -> str:
    if price is None:
        return f"no price on file for this model (give --price {model}=IN,OUT)"
    return f"${(tokens_in * price[0] + tokens_out * price[1]) / 1_000_000:.3f}"


async def run_text(
    inputs: pl.Inputs,
    fast: Client,
    careful: Client,
    consenting: Callable[[], Awaitable[Collection[int]]] | None = None,
    *,
    session_at: int = 1,
    models: tuple[str, str] = ("fast", "careful"),
    creative: Client | None = None,
    formatting: Client | None = None,
    creative_models: tuple[str, str] = ("creative", "formatting"),
    critics: bool = False,
    story: pl.Story | None = None,
    start: tuple[str, dict[str, Any]] | None = None,
    reads: str = "beats",
    structured: bool = False,
) -> Outcome:
    """The recap worker on one session, in memory. Returns the job as it ended. With `creative`
    and `formatting` the prose is written and shaped by two models, as live (#2074)."""
    store = EphemeralStore(session_at, DM_PLACEHOLDER)
    if start is not None:  # `--compare`: the second writer begins at the first one's beat sheet
        store.job = replace(store.job, state=start[0], **start[1])
    fast_c, careful_c = Counting(fast, models[0]), Counting(careful, models[1])
    creative_c = Counting(creative, creative_models[0]) if creative is not None else None
    formatting_c = Counting(formatting, creative_models[1]) if formatting is not None else None
    failures: list[tuple[str, int, list[str]]] = []
    calls: list[cl.CallEvent] = []
    counts: dict[str, int] = {}
    notes: list[str] = []
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
        on_problems=lambda stage, attempt, problems: failures.append(
            (stage, attempt + 1, list(problems))
        ),
        on_note=notes.append,
        on_call=calls.append,
        on_count=counts.__setitem__,
        creative=creative_c,
        formatting=formatting_c,
        critics=critics,
        story=story,  # PlotBot's story memory, when a test or tool connects it (#2254)
        reads=reads,  # what the writer reads (#2590)
        structured_split=structured,  # the draft-then-form way for both steps (#2714)
        structured_beats=structured,
    )
    structured_before = ai_structured.STATS.as_dict()
    stopped = ""
    for _ in range(40):  # each pass is one stage; a run never spins
        before = store.job
        await worker.run(PLACEHOLDER_GUILD, PLACEHOLDER_CAMPAIGN, session_at)
        job = store.job
        if job.finished:
            break
        if job.next_at > time.time():  # an AI error: the live job would wait; a test run stops
            stopped = f"a call failed ({job.last_error}); the live job would try again later"
            break
        if job == before:
            stopped = "no progress"
            break
    return Outcome(
        store.job,
        inputs,
        fast_c,
        careful_c,
        creative_c,
        formatting_c,
        stopped,
        notes=notes,
        sheets=store.sheets,
        failures=failures,
        calls=calls,
        counts=counts,
        structured=(
            {k: v - structured_before[k] for k, v in ai_structured.STATS.as_dict().items()}
            if structured
            else None
        ),
    )


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
        dm_ids=inputs.dm_ids,
        transcript_words={w for s in mine for w in words_of(s.text)},
    )
    out.append(("beats cite real lines, no invented names", not problems, "; ".join(problems)))
    prose = rd.Prose(
        fixed_lines.without(job.summary), tuple(job.music)
    )  # (the story, not the lines)
    transcript = "\n".join(s.text for s in mine)
    length = rd.length_for(rd.session_hours(mine, inputs.hours))
    issues = rd.check_prose(
        prose,
        beats=sheet,
        transcript=transcript,
        allowed_names=inputs.allowed_names,
        secret_names=inputs.secret_names,
        previous=inputs.previous,
        setting=inputs.setting,
        length=length,
    )
    out.append(("prose: cues, names, no quotes, no copying", not issues, "; ".join(issues)))
    words = len(
        narration_of(fixed_lines.without(job.summary)).split()
    )  # (the fixed lines are free)
    out.append(
        (
            f"{words} words, {round(100 * words / length.target)}% of the target "
            f"{length.target} (about {length.hours:g} hours)",
            True,  # a number to read, never a pass or fail (owner, 2026-10-24, #2597)
            "",
        )
    )
    out.append((f"{len(kept)} beats kept", len(kept) >= bt.MIN_BEATS, ""))
    out.append(
        (
            f"{len(job.housekeeping)} housekeeping bullet(s) (asked for at most "
            f"{rd.HOUSEKEEPING_BULLETS})",
            True,
            "",
        )
    )
    return out


def explain_sheets(out: Outcome) -> list[str]:
    """Why each beat sheet failed its checks (#1997): the check's own words, how many beats were
    kept against the least a recap needs, and the names it did not accept. This is the owner's
    private run on their own terminal, so a name from the sheet may print; a line of the
    transcript never does."""
    said = list(readable(out.inputs.lines))
    speakers = set(out.job.speakers) or {s.user_id for s in said}
    mine = [s for s in said if s.user_id in speakers]
    everything = {w for s in mine for w in words_of(s.text)}  # the speakers used, like the job
    rows: list[str] = []
    for number, sheet in enumerate(out.sheets, start=1):
        kept, problems = bt.check(
            sheet,
            lines={s.number: s.text for s in mine},
            speakers={s.number: s.user_id for s in mine},
            allowed_names=out.inputs.allowed_names,
            secret_names=out.inputs.secret_names,
            dm_ids=out.inputs.dm_ids,
            transcript_words=everything,
        )
        rows.append(
            f"Beat sheet, try {number}: {len(sheet)} beats written, {len(kept)} kept "
            f"(a recap needs at least {bt.MIN_BEATS})."
        )
        rows += [f"  problem: {p}" for p in problems] or ["  no problem found on this check"]
        by_number = {s.number: s.text for s in mine}
        unknown: list[str] = []
        for beat in sheet:
            cited = beat.get("lines")
            numbers = [n for n in (cited if isinstance(cited, list) else []) if n in by_number]
            said_words = {w for n in numbers for w in words_of(by_number[n])}
            who = beat.get("who")
            names = [
                *proper_nouns(str(beat.get("what", ""))),
                *(str(w) for w in (who if isinstance(who, list) else [])),
            ]
            unknown += bt.unsaid_names(names, said_words, out.inputs.allowed_names, everything)
        if unknown:
            rows.append("  names it did not accept: " + ", ".join(dict.fromkeys(unknown)))
    return rows


STAGE_WORDS = {
    "queued": "beat sheet",
    "beats": "beat sheet",
    "checked": "prose",
    "prose": "title and housekeeping",
}  # a job's state when a check failed -> the step that was being checked


def explain_failures(out: Outcome) -> list[str]:
    """Why the prose or the finishing step failed its checks (#1969): the check's own words, for
    each try. They are fixed sentences and the recap's own names, never a line of the transcript;
    a problem that holds a name the DM keeps secret is not printed."""
    secret = [n.casefold() for n in out.inputs.secret_names]
    rows: list[str] = []
    for stage, attempt, problems in out.failures:
        rows.append(
            f"Step {STAGE_WORDS.get(stage, stage)}, try {attempt}: {len(problems)} problem(s)."
        )
        for problem in problems:
            held = any(name and name in problem.casefold() for name in secret)
            rows.append(
                "  problem: " + ("(it names something the DM keeps secret)" if held else problem)
            )
    return rows


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
        if job.to_confirm:
            lines += ["", "Facts to be confirmed:", *(f"- {n}" for n in job.to_confirm)]
    if job.summary:
        lines += ["", "Checks:"]
        for name, ok, detail in final_checks(job, out.inputs):
            lines.append(
                f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" ({detail})" if detail else "")
            )
    if job.state != "ready" and out.sheets:
        lines += ["", *explain_sheets(out)]
    if job.state != "ready" and out.failures:
        lines += ["", *explain_failures(out)]
    lines += split_lines(out)
    lines += count_lines(out)
    lines += cl.section(out.calls)  # counts and fixed words only (#2053)
    if out.notes:
        lines += ["", *(f"Note: {n}" for n in out.notes)]
    if job.grade:
        weak = rd.weak(job.grade)
        lines += [
            "",
            "Grader: " + ", ".join(f"{k} {v}" for k, v in job.grade.items()),
            "  below the bar: " + ("; ".join(weak) if weak else "nothing"),
        ]
    counted = [
        ("fast", out.fast),
        ("careful", out.careful),
        *(
            (name, c)
            for name, c in (("creative", out.creative), ("formatting", out.formatting))
            if c is not None
        ),
    ]
    tokens_in = sum(c.input_tokens for _, c in counted)
    tokens_out = sum(c.output_tokens for _, c in counted)
    spend = dollars(
        prices.get(out.fast.model), out.fast.input_tokens, out.fast.output_tokens, out.fast.model
    )
    for _, c in counted[1:]:
        if c.calls:
            spend += " + " + dollars(prices.get(c.model), c.input_tokens, c.output_tokens, c.model)
    split = ", ".join(f"{c.calls} {name}" for name, c in counted if name == "fast" or c.calls)
    lines += [
        "",
        "Models: " + ", ".join(f"{name} {c.model}" for name, c in counted),
        f"Calls: {sum(c.calls for _, c in counted)} ({split}); "
        f"tokens in {tokens_in}, out {tokens_out}; spend {spend}",
    ]
    if out.structured is not None:  # counts only, for the comparison with the one-call way (#2714)
        lines.append(
            "Structured: " + ", ".join(f"{k} {v}" for k, v in out.structured.items() if v)
            if any(out.structured.values())
            else "Structured: no step used it (nothing to split or write)"
        )
    return lines


def _copy_out(source: Path, into: Path) -> None:
    """The mp3s, copied to a folder only this user can open (the owner's recap is private)."""
    into.mkdir(parents=True, exist_ok=True, mode=0o700)
    into.chmod(0o700)  # (mkdir's mode is narrowed by the umask and ignored for an existing folder)
    for path in sorted(source.iterdir()):
        if not path.is_file():  # (the kept parts of a small change live in a folder, #2630)
            continue
        target = into / path.name
        shutil.copyfile(path, target)
        target.chmod(0o600)


async def run_audio(
    job: Job,
    inputs: pl.Inputs,
    provider: object,
    files: object,
    *,
    guild_id: int = PLACEHOLDER_GUILD,
    campaign_id: str = PLACEHOLDER_CAMPAIGN,
    runner: Runner | None = None,
    voice_tags: bool = True,
    names: Sequence[SpokenName] = (),
    keep_in: Path | None = None,
) -> list[str]:
    """The narration and music into a temporary folder: the lengths and the credits. Removed
    afterwards; nothing is kept unless `keep_in` is given (#1969), when the mp3s are copied
    there. `names`: the names with a "said as" spelling become the pronunciation dictionary."""
    from dmbot.recaps.audio import names as nm
    from dmbot.recaps.audio import script as sc
    from dmbot.recaps.audio.render import Renderer, estimate_credits, run_ffmpeg

    try:
        script = sc.build(
            job.title,
            job.game_day,
            job.summary,
            secret_names=inputs.secret_names,
            allowed_names=inputs.allowed_names,
            emotion_tags=voice_tags,
        )
    except sc.SecretInScript as exc:  # the rule's word only, never the name or the match
        return [f"audio: not made (the text names a secret: {exc.rule})"]
    renderer = Renderer(provider, files, runner=runner or run_ffmpeg)  # type: ignore[arg-type]
    folder = Path(tempfile.mkdtemp(prefix="recap-run-"))
    try:
        dictionary = None
        spelled = nm.rules(names)
        if spelled:  # secret names never get a rule: `rules` leaves them and what holds them out
            dictionary = await provider.dictionary("recap-run", spelled)  # type: ignore[attr-defined]
        try:
            made = await renderer.render(
                guild_id,
                campaign_id,
                script,
                into=folder / "out",
                seed=job.session_at,
                dictionary=dictionary,
            )
        finally:
            if dictionary is not None:  # a test run leaves nothing at the company, even on failure
                await provider.forget_dictionary(dictionary.dictionary_id)  # type: ignore[attr-defined]
        sizes = {p.name: p.stat().st_size for p in (folder / "out").iterdir()}
        kept: list[str] = []
        if keep_in is not None:
            await asyncio.to_thread(_copy_out, folder / "out", keep_in)
            kept = ["", f"Kept in {keep_in} (on this server only; not for GitHub)."]
        return [
            "",
            f"Voice mood tags: {'on' if voice_tags else 'off'} (DMBOT_RECAP_VOICE_TAGS)",
            f"Audio: {made.seconds:.1f} s, {made.credits} credits "
            f"(estimated {estimate_credits(script, ambience_needed=True)})",
            *(f"  {name}: {size // 1024} KB" for name, size in sorted(sizes.items())),
            *([f"Pronunciation rules: {len(spelled)}"] if names else []),
            *kept,
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
    what.add_argument(
        "--transcript", metavar="FILE",
        help="a transcript file (DMbot's download, 'Name: words' lines, WebVTT or SRT)",
    )  # fmt: skip
    parser.add_argument(
        "--dm", metavar="NAME", help="with --transcript: the DM, as the file spells it"
    )
    parser.add_argument(
        "--names", metavar="FILE",
        help="with --transcript: names, 'Ka'zeth = Kah-zeth', and 'secret: Name' lines",
    )  # fmt: skip
    what.add_argument(
        "--review", metavar="DIR",
        help="rebuild review.html from a folder a test run made (no paid call, no --yes)",
    )  # fmt: skip
    parser.add_argument(
        "--hours", type=float, metavar="H",
        help="how long the session ran, in hours: the recap's length follows it (default: from "
        "the file's times, or for a file with none, estimated from the words)",
    )  # fmt: skip
    parser.add_argument(
        "--out", metavar="DIR",
        help="with --session or --transcript: keep recap.txt, report.txt and review.html here, "
        "and with --audio the mp3s",
    )  # fmt: skip
    parser.add_argument(
        "--no-critics", action="store_true",
        help="leave out the critics loop (#2255), to compare with the recap before it",
    )  # fmt: skip
    parser.add_argument(
        "--compare", action="store_true",
        help="with --session or --transcript: the same beats written by Claude and by Gemini, "
        "saved blind as A.txt and B.txt in --out with the key apart (a Gemini writer needs "
        "--gemini-model and --paid-gemini-key; claude,claude needs neither)",
    )  # fmt: skip
    parser.add_argument("--gemini-model", metavar="NAME", help="with --compare: the Gemini model")
    parser.add_argument(
        "--paid-gemini-key", action="store_true",
        help="with --compare: I confirm GEMINI_API_KEY belongs to a Google account with billing "
        "on (the free tier lets Google use what is sent)",
    )  # fmt: skip
    parser.add_argument(
        "--writers", metavar="A,B", default="claude,gemini",
        help="with --compare: who writes each draft, claude or gemini (default claude,gemini; "
        "gemini,gemini compares what Gemini reads)",
    )  # fmt: skip
    parser.add_argument(
        "--reads", metavar="WHAT", default="beats",
        help="what the writer reads (#2590): beats (default) or transcript (the cleaned transcript "
        "with PlotBot's guide). With --compare, one word for both or two (A,B) in --writers order",
    )  # fmt: skip
    parser.add_argument(
        "--claude-model", metavar="NAME",
        help="with --compare: the Claude writer (default: the creative model on file)",
    )  # fmt: skip
    parser.add_argument(
        "--structured", action="store_true",
        help="with --session or --transcript only (not --golden, --compare or --review): ask the "
        "split and the beat sheet the structured way (a strong model drafts, a fast one fills the "
        "form, #2714); the report adds the counts. To compare, run the session once without it "
        "and once with it",
    )  # fmt: skip
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
    if args.transcript:
        if args.keep:
            parser.error(
                "--keep can't be used with --transcript: a transcript file is never stored"
            )
        if not args.dm:
            parser.error("--transcript needs --dm NAME, the DM's name as the file spells it")
    elif args.dm or args.names:
        parser.error("--dm and --names go with --transcript")
    if args.compare:
        if args.golden or args.review or args.audio or args.keep:
            parser.error(
                "--compare goes with --session or --transcript, and takes no --audio or --keep"
            )
        gemini_writes = "gemini" in [w.strip() for w in args.writers.split(",")]
        if not args.out:
            parser.error("--compare needs --out DIR (for the drafts and the key)")
        if gemini_writes and not args.gemini_model:
            parser.error("--compare with a Gemini writer needs --gemini-model")
        if gemini_writes and not args.paid_gemini_key:
            parser.error(
                "--compare sends the session to Google: add --paid-gemini-key to confirm the key "
                "is on a billing-enabled account (the free tier lets Google use what is sent)"
            )
    elif args.gemini_model or args.claude_model or args.paid_gemini_key:
        parser.error("--gemini-model, --claude-model and --paid-gemini-key go with --compare")
    elif args.writers != "claude,gemini":
        parser.error("--writers goes with --compare")
    args.reads_each = parse_reads(args.reads, parser.error)
    if args.structured and (args.golden or args.compare or args.review):
        parser.error(
            "--structured goes with --session or --transcript, not --golden, --compare or --review"
        )
    if args.out and not (args.transcript or args.compare or args.session):
        parser.error("--out goes with --session, --transcript or --compare")
    if args.hours is not None and not 0.25 <= args.hours <= 24:
        parser.error("--hours is the session's length in hours, from 0.25 to 24")
    if args.hours is not None and args.golden:
        parser.error("--golden takes no --hours")
    if args.review and (args.audio or args.keep or args.price or args.yes):
        parser.error(
            "--review only rebuilds the page: it takes no --audio, --keep, --price or --yes"
        )
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


_OPEN_CLIENTS: list[AnthropicClient] = []


async def _close_clients() -> None:
    while _OPEN_CLIENTS:
        await _OPEN_CLIENTS.pop().close()


_WRITERS: dict[str, Any] = {}


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
    _OPEN_CLIENTS.append(ai)  # closed when the run ends, so no "unclosed session" warning (#1997)
    # The recap's writer and formatter, as live (#2074): passed on to every `run_text` below.
    _WRITERS.update(
        creative=ai.tier(AIModelTier.CREATIVE),
        formatting=ai.tier(AIModelTier.FORMATTING),
        creative_models=(models.of(AIModelTier.CREATIVE), models.of(AIModelTier.FORMATTING)),
    )
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
        out = await run_text(inputs, fast, careful, models=models, **_WRITERS)
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
    """Run the tool. `_WRITERS` is set for this run only and put back after it, so a run in the
    same process (the tests) never leaves the critics, or a model choice, on for the next one."""
    before = dict(_WRITERS)
    try:
        return await _main_async(args)
    finally:
        _WRITERS.clear()
        _WRITERS.update(before)


async def _main_async(args: argparse.Namespace) -> int:
    if args.review:
        return review_folder(Path(args.review))
    got = clients_from_env()
    if got is None:
        return 2
    fast, careful, models = got
    _WRITERS["critics"] = not args.no_critics  # as live (#2255): the critics read each draft
    prices = prices_of(args.price, (*models, *_WRITERS.get("creative_models", ())))
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
    if args.transcript:
        return await _transcript(args, fast, careful, models, prices)
    return await _session(args, fast, careful, models, prices)


def review_folder(folder: Path) -> int:
    """`--review DIR`: rebuild the page from a folder. No key, no paid call."""
    if not folder.is_dir():
        print(f"recap_run: {folder} is not a folder", file=sys.stderr)
        return 2
    target = rv.write_page(folder)
    print(f"Wrote {target}. Open it in a browser (it needs no network).")
    return 0


def save_text(folder: Path, out: Outcome, printed: Sequence[str], *, names_given: bool) -> None:
    """`--out`: the recap and the report as files only this user can read (#2011). The caller
    has already refused a recap that names a secret, so nothing here is checked twice."""
    rv.private_dir(folder)
    job = out.job
    if job.summary and job.state == "ready":  # a recap that failed its checks is not kept
        rv.write_private(
            folder / rv.RECAP_FILE,
            rv.recap_text(
                job.title,
                job.summary,
                list(job.housekeeping),
                list(job.music),
                voice_tags=voice_tags_on(),
                names_given=names_given,
                to_confirm=list(job.to_confirm),
            ),
        )
    rv.write_private(folder / rv.REPORT_FILE, "\n".join(printed) + "\n")


def audio_failure(exc: Exception) -> list[str]:
    """The lines for an audio step that failed (#2042). A voice-company failure says its kind,
    the HTTP status and which call, then what to check. A `ValueError` also says its message:
    the audio file store's are DMbot's own fixed words ("not a campaign"; #2062). Anything else
    only its type, never its message, which could hold text that was sent."""
    if isinstance(exc, ProviderError):
        return [
            f"audio: failed ({exc.describe()}); the text above is unchanged",
            f"  to check: {check_line(exc)}",
        ]
    if isinstance(exc, ValueError):
        return [
            f"audio: failed (ValueError: {str(exc)[:MESSAGE_MAX]}); the text above is unchanged"
        ]
    return [f"audio: failed ({type(exc).__name__}); the text above is unchanged"]


def _golden_inputs(session: object) -> pl.Inputs:
    return pl.Inputs(
        lines=session.lines,  # type: ignore[attr-defined]
        dm_ids={1}, allowed_names=[], secret_names=[], previous="", day=None,
        window_chars=session.window_chars,  # type: ignore[attr-defined]
    )  # fmt: skip


async def inputs_for(db: Any, gid: int, cid: str, at: int) -> pl.Inputs:
    """The recap inputs of one stored session, read the way the bot reads them (every store on
    this one database, each read scoped to the server by `Database.guild`). Raises `NotLoaded` with
    the reason (#2680)."""
    from dmbot.campaigns.store import CampaignStore
    from dmbot.memory.lookup import LookupCache
    from dmbot.memory.store import MemoryStore
    from dmbot.recaps.inputs import load_inputs_or_why
    from dmbot.recaps.store import RecapStore
    from dmbot.timebot.store import ClockStore
    from dmbot.transcript.store import TranscriptStore

    return await load_inputs_or_why(
        campaigns=CampaignStore(db),
        lookup=LookupCache(MemoryStore(db)),
        transcripts=TranscriptStore(db),
        recaps=RecapStore(db),
        clocks=ClockStore(db),
        guild_id=gid,
        campaign_id=cid,
        session_at=at,
        styles=StyleStore(db),
    )


def not_loaded_line(why: NotLoaded, gid: int, cid: str, at: int) -> str:
    """Which of the three it was, in words that say what to look at. Numbers only: no name or
    word of a transcript."""
    if why.why == "campaign":
        return f"no campaign {cid} in server {gid}"
    if why.why == "names":
        return f"the campaign {cid}'s names can't be read (see the log above for why)"
    near = ", ".join(str(t) for t in why.started) or "none"
    # (when several sessions share a start time, the count is the largest; harmless)
    if why.lines_in_it is not None:  # the session is there, but there is nothing to read in it
        return (
            f"campaign {cid} has a session that began at {at}, but with {why.lines_in_it} stored "
            f"lines (a session with none is not listed; was its transcript copied, or deleted?)"
        )
    return (
        f"campaign {cid} has no stored session that began at {at}; its newest sessions with "
        f"lines began at: {near}"
    )


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
    from dmbot.recaps.inputs import NotLoaded
    from dmbot.recaps.store import RecapStore

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
        try:
            inputs = await inputs_for(db, gid, cid, at)
        except NotLoaded as why:
            print(f"recap_run: {not_loaded_line(why, gid, cid, at)}", file=sys.stderr)
            return 2
        # The improvement opt-out (#1609): this is a test run on real words, not a table, so
        # the lines of anyone who opted out are left out, read now.
        # The earlier recap (`previous`) is derived from everyone's words, so it is left out
        # of a test run rather than carry an opted-out person's words along.
        # (the earlier session's beats are left out for the same reason; its words only compare)
        inputs = replace(
            inputs,
            lines=await filter_lines(db, inputs.lines),
            previous="",
            earlier=replace(inputs.earlier, beats=()) if inputs.earlier is not None else None,
        )
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
        if args.out:
            rv.clear_old(Path(args.out))  # nothing of an earlier run is left to be mixed in
        if args.compare:
            return await _compare(args, inputs, fast, careful, models, prices, agreed, at)
        out = await run_text(
            inputs, fast, careful, agreed, session_at=at, models=models,
            reads=args.reads_each[0], structured=args.structured, **_WRITERS,
        )  # fmt: skip
        lines = report(out, prices)
        if lines == [REFUSED]:
            print(REFUSED)
            return 3
        print("\n".join(lines))  # the paid text report first: nothing below can lose it
        folder = Path(args.out) if args.out else None
        if folder is not None:  # the text is saved before the audio can fail
            save_text(folder, out, lines, names_given=False)
        if args.audio and out.job.state == "ready":
            try:
                print("\n".join(await _audio(out, keep_in=folder)))
            except Exception as exc:  # the provider, ffmpeg: say the kind, never the message
                print("\n".join(audio_failure(exc)))
        if folder is not None:
            print(f"\nReview page: {rv.write_page(folder)} (open it in a browser).")
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


def parse_reads(raw: str, fail: Callable[[str], Any]) -> tuple[str, str]:
    """`--reads` (#2590): "beats", "transcript", or two of them for the two sides."""
    words = [w.strip() for w in raw.split(",")]
    if len(words) == 1:
        words *= 2
    if len(words) != 2 or any(w not in ("beats", "transcript") for w in words):
        fail("--reads is beats or transcript (with --compare, one for each side: beats,transcript)")
    return words[0], words[1]


async def _compare(
    args: argparse.Namespace,
    inputs: pl.Inputs,
    fast: Client,
    careful: Client,
    models: tuple[str, str],
    prices: dict[str, tuple[float, float]],
    consenting: Callable[[], Awaitable[Collection[int]]] | None,
    session_at: int,
) -> int:
    """`--compare` (#2575): Claude and Gemini write from the same beats; two blind drafts."""
    from dmbot.ai import DEFAULT_MODELS
    from dmbot.devtools.recap_run import compare as cmp

    all_models = parse_ai_models(lambda name: os.environ.get(name, ""))[0]
    claude_model = args.claude_model or DEFAULT_MODELS.creative
    which = [w.strip() for w in args.writers.split(",")]
    if len(which) != 2 or any(w not in ("claude", "gemini") for w in which):
        print(
            "recap_run: --writers is two of claude, gemini (for example claude,gemini)",
            file=sys.stderr,
        )
        return 2
    writers = cmp.writers_from_env(
        lambda name: os.environ.get(name, ""),
        all_models,
        claude_model,
        args.gemini_model or "",
        register=_OPEN_CLIENTS.append,
        which=which,
        reads=args.reads_each,
    )
    if writers is None:
        return 2
    shown = {
        **prices,
        **prices_of(
            args.price, [claude_model, *([args.gemini_model] if args.gemini_model else [])]
        ),
    }
    return await cmp.run_compare(
        run_text,
        inputs,
        fast,
        careful,
        consenting,
        session_at=session_at,
        models=models,
        writers=writers,
        folder=Path(args.out),
        prices=shown,
        checks_of=lambda out: final_checks(out.job, out.inputs) if out.job.summary else [],
        leaks=lambda out: names_a_secret_anywhere(out.job, out.inputs),
        voice_tags=voice_tags_on(),
    )


def read_transcript_inputs(
    args: argparse.Namespace,
) -> tuple[pl.Inputs, tf.Transcript, list[SpokenName]]:
    """The file, the DM and the names as the recap worker's inputs. Raises `tf.TranscriptError`
    (and `OSError` for a file that can't be read) with plain words and never a line of the file."""
    transcript = tf.parse(Path(args.transcript).read_text(encoding="utf-8-sig"))
    dm = tf.dm_id(transcript, args.dm)
    names = tf.parse_names(Path(args.names).read_text(encoding="utf-8-sig")) if args.names else []
    allowed = [n.text for n in names if not n.secret] + list(transcript.characters)
    secret = [n.text for n in names if n.secret]
    secret_keys = {s.casefold() for s in secret}
    hours = args.hours
    if hours is None and not transcript.timed:  # a Craig file: no times, so from the words
        hours = rd.estimated_hours(sum(len(u.text.split()) for u in transcript.utterances))
    inputs = pl.Inputs(
        lines=tf.lines_of(transcript),
        dm_ids={dm},
        allowed_names=[a for a in dict.fromkeys(allowed) if a.casefold() not in secret_keys],
        secret_names=secret,
        previous="",
        day=None,
        hours=hours,
    )
    return inputs, transcript, names


async def _transcript(
    args: argparse.Namespace,
    fast: Client,
    careful: Client,
    models: tuple[str, str],
    prices: dict[str, tuple[float, float]],
    *,
    ask: Callable[[str], str] = input,
) -> int:
    """`--transcript`: the file through the same worker as a stored session, in memory only."""
    try:
        inputs, transcript, names = read_transcript_inputs(args)
    except (OSError, tf.TranscriptError) as exc:
        print(f"recap_run: {exc}", file=sys.stderr)
        return 2
    calls, tokens = estimate(inputs)
    price = prices.get(models[0])
    print(_length_line(args, transcript, inputs))
    print(
        f"{transcript.format} file: {len(inputs.lines)} lines from {len(transcript.speakers)} "
        f"speakers{_tolerated(transcript)}; up to {calls} AI calls; about {tokens} input tokens on "
        "the first try "
        f"({dollars(price, tokens, 4000)} on the fast tier; a retry uses the careful tier and "
        "costs more)." + (" Plus about 7,000 provider credits for --audio." if args.audio else "")
    )
    if not args.yes:
        print("Nothing was run. Add --yes to spend the money.")
        return 0
    # An uploaded file has no consent records. This is asked once, before any paid call, and
    # --yes never answers it (CLAUDE.md: never process words from people who have not agreed).
    try:
        answer = ask(tf.CONSENT_QUESTION)
    except EOFError:  # nobody is there to answer (a pipe, a scheduled run): that is a no
        answer = ""
    if not tf.consent_given(answer):
        print("recap_run: stopped. Nothing was sent to any service.")
        return 4
    folder = Path(args.out) if args.out else None
    if folder is not None and folder.is_dir():
        rv.clear_old(folder)  # nothing of an earlier run is left to be mixed into this one
    if args.compare:
        return await _compare(args, inputs, fast, careful, models, prices, None, 1)
    out = await run_text(
        inputs, fast, careful, session_at=1, models=models, reads=args.reads_each[0],
        structured=args.structured, **_WRITERS,
    )  # fmt: skip
    lines = report(out, prices)
    if lines == [REFUSED]:
        print(REFUSED)
        return 3
    print("\n".join(lines))  # the paid text report first: nothing below can lose it
    printed = list(lines)
    if out.job.state == "ready":
        cues = _cue_lines(out)
        print("\n".join(cues))
        printed += cues
        if folder is not None:  # the text is saved before the audio can fail
            save_text(folder, out, printed, names_given=bool(names))
        if args.audio:
            try:
                made = await _audio(out, names=names, keep_in=folder)
            except Exception as exc:  # the provider, ffmpeg: say the kind, never the message
                made = audio_failure(exc)
            print("\n".join(made))
            printed += made
    if folder is not None:
        save_text(folder, out, printed, names_given=bool(names))
        print(f"\nReview page: {rv.write_page(folder)} (open it in a browser).")
    return 0 if out.job.state == "ready" else 1


def _length_line(args: argparse.Namespace, transcript: tf.Transcript, inputs: pl.Inputs) -> str:
    """How long the session was and so how long the recap will be (#2013)."""
    said = readable(inputs.lines)
    hours = rd.session_hours(said, inputs.hours)
    length = rd.length_for(hours)
    how = (
        "from --hours"
        if args.hours is not None
        else "estimated from the words; give --hours to set it"
        if not transcript.timed
        else "from the file's times"
    )
    return (
        f"length: about {hours:g} hours ({how}); the writer is asked for about {length.target} "
        "words (a target, not a check)."
    )


def _tolerated(transcript: tf.Transcript) -> str:
    """What the plain reader let through, as counts only: " (skipped 2 header lines, joined 5
    wrapped lines, skipped 80 cut-out lines)". Never the text."""
    notes = []
    if transcript.skipped:
        notes.append(f"skipped {transcript.skipped} header lines")
    if transcript.joined:
        notes.append(f"joined {transcript.joined} wrapped lines")
    if transcript.cut_out:
        notes.append(f"skipped {transcript.cut_out} cut-out lines")
    return f" ({', '.join(notes)})" if notes else ""


def voice_tags_on() -> bool:
    """DMBOT_RECAP_VOICE_TAGS as the bot reads it (on unless 0/false/off)."""
    return os.environ.get("DMBOT_RECAP_VOICE_TAGS", "1").lower() in ("1", "true", "on")


def _cue_lines(out: Outcome) -> list[str]:
    from dmbot.recaps.audio import script as sc

    try:
        script = sc.build(
            out.job.title,
            out.job.game_day,
            out.job.summary,
            secret_names=out.inputs.secret_names,
            allowed_names=out.inputs.allowed_names,
            emotion_tags=voice_tags_on(),  # the cue map must show what the audio will say
        )
    except sc.SecretInScript:
        return []
    rows = tf.cue_map(out.job.summary, script)
    return ["", f"Cues ({len(rows)}):", *rows] if rows else ["", "Cues: none in this recap"]


async def _audio(
    out: Outcome, *, names: Sequence[SpokenName] = (), keep_in: Path | None = None
) -> list[str]:
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
            tags = voice_tags_on()
            return await run_audio(
                out.job,
                out.inputs,
                provider,
                AudioFiles(Path(cache)),
                voice_tags=tags,
                names=names,
                keep_in=keep_in,
            )
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


async def _run(args: argparse.Namespace) -> int:
    try:
        return await main_async(args)
    finally:
        await _close_clients()


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_run(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
