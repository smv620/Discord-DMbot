"""The recap job's worker (#1382): takes one queued job and runs its stages, each saved before the
next, so a restart picks up where it stopped.

    queued → beats      the AI reads the cleaned transcript and returns a cited beat sheet
    beats → checked     code cuts every beat that cites no real line or names what its lines do not
    checked → prose     the AI writes the recap from the beat sheet only; code checks it; a cheap
                        grader scores it against a fixed rubric
    prose → housekeeping  the title and the housekeeping bullets, each citing a beat
    housekeeping → ready  the text is done and the DM is emailed; the audio, if wanted, is made
                          afterwards by `dmbot.recaps.audio.worker` and never delays it

A check that fails retries the stage once on the careful tier, told what was wrong; a second
failure fails the job with a plain reason. A call that errors (the AI service, the network) waits
and tries again, three times, with a longer wait each time. A job has a ceiling on AI calls, and a
campaign has one for each day. Consent is read at every stage and after every answer: if anyone
whose lines were used has stopped being recorded, the work starts again without them (CLAUDE.md,
consent, after every async step). The log gets counts only, never a recap.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from dmbot import ai_structured
from dmbot.ai import OUTPUT_FLOOR, AIOutOfFunds, Reply, max_input
from dmbot.memory.extractor import Said, readable
from dmbot.recaps import away, fixed_lines
from dmbot.recaps import beats as bt
from dmbot.recaps import calls as cl
from dmbot.recaps import critics as cr
from dmbot.recaps import draft as rd
from dmbot.recaps import motives as mo
from dmbot.recaps import significance as sg
from dmbot.recaps import split as sp
from dmbot.recaps import unknown_names as un
from dmbot.recaps.store import Job, RecapStore
from dmbot.recaps.text import CUE, names_a_secret, narration_of, strip_cues, words
from dmbot.transcript.models import Line

log = logging.getLogger(__name__)

# AI calls for one recap: three stages, a retry each (two for the prose), the checks, and since
# #2074 the formatting call after each prose try (up to 3, and one re-run of the formatter): 13 + 4,
# and since #2253 the one call that splits the opening (talk, catch-up, last time's recap, play):
# and the one careful-tier call where PlotBot marks what matters (#2254): 19 for a run with no
# restarts; each restart (at most two) makes those calls again. With the critics loop (#2255) a
# recap may read its draft four times (PlotBot, the motive check) and write it three times more (the
# writer, the formatter, the fact check, the grader): about 30 more calls, so the ceiling is five
# times what it was: 95.
MAX_CALLS = 95
PROSE_LAST_ATTEMPT = 2  # the prose stage has tries 0, 1 and 2: a long real session needs the third
# AI calls for one campaign's recaps in a day (a Try again is a new recap). A clean recap is eight
# calls now (split, beats, notes, write, format, facts, grade, title; the first only for a session
# with an earlier one), so 56 keeps the seven a day that 40 gave five-call recaps (#2074).
DAILY_CALLS = 280  # (five times the 56 of before the critics loop, #2255)
BACKOFF_S = (60, 240, 960)
MAX_RESTARTS = 2  # a stopped player restarts the work; not for ever
DAY_S = 86400

NOTHING = "No one who agreed to be recorded spoke this session, so there was nothing to recap."
TOO_LITTLE = "There wasn't enough said to tell as a story."
FAILED_CHECKS = (
    "DMbot couldn't write this one so that every detail came from what was said, so it wrote "
    "nothing. You can try again."
)
CUT_OFF = "DMbot's answer was cut off before it was finished. You can try again."
SERVICE = "The writing service wasn't available. You can try again."
FUNDS = "DMbot's writing service is unavailable right now. We've been told. Try again later."
CAP = "This recap has hit its limit for now. You can try again tomorrow."
DAILY = "This campaign has used up today's recap writing. Try again tomorrow."
NAMES_WAIT_S = 30 * 60  # the most step 1 waits for the name pass; then it goes on without it
NAMES_RETRY_S = 30  # how often it looks again
# The note on a recap held for the name pass (#2628): an explicit marker, so the page shows
# "checking the names" with the time it has taken and when it last moved, and never a silent wait.
CHECKING_NAMES = "🔎 Checking the names in the session before the recap is written."
NO_NAMES = (
    "DMbot couldn't check your campaign's secret names, so it wrote nothing to be safe. "
    "Try again in a minute."
)
NO_COUNT = (
    "DMbot couldn't check this month's recap allowance, so it didn't write the recap, to be "
    "safe. Press Try again in a minute."
)
STOPPED = "Players stopped being recorded while this was written, so nothing was left to tell."
# Reasons where trying again would fail the same way: the email offers no retry for these.
NO_RETRY = (NOTHING, TOO_LITTLE, STOPPED)


@dataclass(frozen=True, slots=True)
class Inputs:
    """What the stages read, loaded fresh for a job: the campaign's own data only."""

    lines: Sequence[Line]
    dm_ids: Collection[int]
    allowed_names: Collection[str]  # confirmed, not secret
    secret_names: Collection[str]  # read fresh
    previous: str  # the earlier posted recap without its cues: the bridge
    day: int | None
    setting: str | None = None  # a campaign has no setting name yet; none is ever written in
    window_chars: int = bt.WINDOW_CHARS  # a test shrinks it to get several windows
    hours: float | None = None  # hours played when known (recap_run); else from the lines' times
    # The campaign's example recaps, for the writer's tone only (#2075): the writer's call and the
    # copy check read them; the beats and the fact check never do.
    style: Sequence[str] = ()
    notes: Sequence[str] = ()  # the DM's standing style notes, oldest first (#2076)
    # What the DM corrected in this one recap (#2076): confirmed facts for it alone, never style.
    corrections: Sequence[str] = ()
    # The campaign's display name, for the recap's fixed opening and closing lines (#2253).
    campaign_name: str = ""
    ending: int = 0  # which of the ten closing lines this recap takes (fixed_lines.ENDINGS)
    # What the campaign's previous session was about, to find a retelling of it at the start of
    # this one (#2253). None when there is no earlier session (the first, or the first imported):
    # then nothing is a recap and the split stage makes no call.
    earlier: sp.Earlier | None = None
    # How connected each name is in The truth, hidden connections too (#2411): numbers by name,
    # used only to rank which beats the writer keeps. No connection and no fact is in it.
    importance: Mapping[str, float] = field(default_factory=dict)
    # Who is who, from the story memory (#2573): another spelling -> the name (a character the
    # speech to text heard as a stranger), every player character, and each player's characters.
    aliases: Mapping[str, str] = field(default_factory=dict)
    pcs: Sequence[str] = ()
    party: Mapping[int, Sequence[str]] = field(default_factory=dict)
    # The names of the story threads still open (quests, mysteries, hooks), for PlotBot's guide to
    # the writer that reads the transcript (#2590). Names only.
    threads: Sequence[str] = ()
    # The campaign's earlier accepted recaps, newest version of each, oldest first, without their
    # cues and fixed lines (#2954). The writer gets as many as fit, whole, newest kept; with none,
    # the bridge (`previous`) is the only earlier recap.
    story: Sequence[str] = ()
    # A 🤫 spelling and its own entry's public names (every name of each end): a sentence naming
    # both says who is who, so a draft that does is refused (#2591).
    identity_ties: Sequence[tuple[tuple[str, ...], tuple[str, ...]]] = ()
    # The campaign's 🤫 relations, as the same pairs: the writer is told which pairs must not be
    # connected, and the DM gets a note on a sentence that names both. Never a refusal (#2591).
    relation_ties: Sequence[tuple[tuple[str, ...], tuple[str, ...]]] = ()
    # The name the table uses for a character with a longer name (#2627): full name -> familiar
    # name, told to the writer beside the full one. Never a secret name; never a check.
    familiar: Mapping[str, str] = field(default_factory=dict)


# A rough size of a token in characters, to decide whether a transcript fits (#2590). Low on
# purpose: names and rare words take more tokens than plain English, so it errs towards "too long".
CHARS_PER_TOKEN = 3
TRANSCRIPT_SHARE = 0.5  # of the smaller input room, for the transcript and the guide
TRANSCRIPT_TOKENS_MAX = 60_000  # and never more than this, whatever the window (cost per retry)
STORY_CHARS_MAX = (
    12_000  # the earlier recaps given whole, newest kept: a long campaign stays bounded
)

UNMATCHED = "Couldn't write a recap that matched the session."
# Problems a draft may still have after the fact check's flagged sentences were cut out of it (the
# flagged sentence may have been the ending): only the ending (#2741). Every other check stays.
CUT_MAY_LEAVE = frozenset({"ending"})
# What the DM reads on the recap when the fact check took out its last lines, or could not be had.
ENDING_CUT_NOTE = (
    "The last lines were taken out because the session didn't say them. Check the ending."
)
NOT_FACT_CHECKED_NOTE = (
    "This recap was not fact-checked: the check could not be had. Read it against the session."
)
DM_TOLD = (ENDING_CUT_NOTE, NOT_FACT_CHECKED_NOTE)  # kept through the critics (#2741)


def hard_after_cut(problems: Sequence[str]) -> list[str]:
    """The problems of a draft that had flagged sentences cut out that still count: all but the
    ending (`CUT_MAY_LEAVE`). One rule for every place a cut draft is checked (#2741)."""
    return [p for p in problems if cl.problem_kind(p) not in CUT_MAY_LEAVE]


# Why a try fails when the formatter changed the written words twice (#2074); its kind is "shape".
SHAPE_FAILED = "answer with the mood and music lines in the form asked for, and keep every word"
FACTS_TOKENS = 800  # the fact check's room: sentence and beat numbers, no text
FACTS_ROOM_MAX = (
    16_000  # after doubling too: a run of one-word sentences stays under the output limit
)
FACTS_TOKENS_PER_SENTENCE = 60  # one flag as JSON: the sentence number, the kind, the beat numbers


def _kinds_line(counts: Mapping[str, int]) -> str:
    """`ordinary_word 2, unknown 1`: fixed words and counts, never the names (#2741)."""
    return ", ".join(f"{k} {n}" for k, n in sorted(counts.items()))


def facts_room(sentences: int) -> int:
    """The fact check's output room: never below the old 800, and a number pair for each sentence
    of a long recap (#2345)."""
    return min(max(FACTS_TOKENS, FACTS_TOKENS_PER_SENTENCE * sentences + 400), FACTS_ROOM_MAX // 2)


def plot_room(sentences: int) -> int:
    """PlotBot's output room: never below the old 1200, and more for a long recap, whose changes
    are many and each one names its sentence (#2365). A cut answer is asked for again with twice
    this."""
    return min(max(PLOT_TOKENS, PLOT_TOKENS_PER_SENTENCE * sentences + 600), PLOT_ROOM_MAX // 2)


GRADE_TOKENS = 200  # the grader's room: a few numbers
FINISH_TOKENS = 900  # room for a title and twelve short bullets as JSON
SPLIT_ERRORS_MAX = 2  # service errors at the split before it is done without the AI
SIG_TOKENS = 1500  # room for up to twelve notes and four details as JSON
SIG_RESERVE = 6  # calls kept for the writer, formatter, facts, grader, title and a retry
SIG_ERRORS_MAX = 2  # service errors at PlotBot's notes before the recap goes on without them
SPLIT_TOKENS = 200  # room for up to three labelled ends as JSON
PLOT_TOKENS = 1200  # room for up to eight changes as JSON (the least: see `plot_room`)
PLOT_TOKENS_PER_SENTENCE = 50  # a change names its sentence, its kind and the reason in words
PLOT_ROOM_MAX = 12_000  # after doubling too
APPROVED_TITLE_CALLS = 3  # what the title step of a draft approved unchecked may add to the day
CRITIC_RESERVE = 5  # calls kept for the title, the housekeeping and a retry once the loop is on
CRITIC_REVISE_CALLS = 5  # what one revision may need: write, format, facts, grade and a spare
CRITIC_ERRORS_MAX = 2  # service errors in a try of the loop before a draft not yet read waits
# Why the loop stopped before any draft was read: the recap then waits for its check (below),
# or fails when the job's own ceiling is the reason, and is never shipped unchecked or cut.
STOP_ERRORS, STOP_CEILING, STOP_TALLY = "service errors", "job ceiling", "day's tally"
# The revision could not be had or checked (cut off, declined, or its fact check unavailable): ours,
# like a service error, so the recap waits for its check and never fails for it (#2370).
STOP_REVISION = "revision unavailable"
CHECK_TALLY_RETRY_S = 3600  # and this long after the day's tally was reached (it is a rolling day)
CHECK_WAIT = cr.CHECK_WAIT  # (the words live with the loop's other fixed wording)


def more_room(tokens: int, attempt: int) -> int:
    """A retry gets half as much room again for each try, so a draft that was cut off at the limit
    (structured outputs cannot promise the shape of a cut answer) has room to finish (#2039)."""
    return tokens + tokens * attempt // 2


def unusable(reply: Reply) -> str:
    """Why a structured answer cannot be read at all, in the words of the retry; "" if it can.
    The shape holds only for an answer that finished and was not declined."""
    if reply.refused:
        return "the answer was declined: write it again from the beats"
    if reply.cut:
        return "the answer was cut off: write it again"
    return ""


class Client(Protocol):
    """The AI client a recap job uses: like the extractor's, plus `schema` for structured
    outputs (#2039): the answer text is then JSON of that shape."""

    async def complete(
        self,
        system: str,
        text: str,
        *,
        max_tokens: int = ...,
        schema: dict[str, Any] | None = ...,
    ) -> Reply: ...


Load = Callable[[int, str, Job], Awaitable["Inputs | None"]]
Consenting = Callable[[], Awaitable[Collection[int]]]
# (server, campaign, job, names in the beats) -> the confirmed story memory around them (#2254)
Story = Callable[[int, str, Job, Sequence[str]], Awaitable[Sequence[sg.Memory]]]
Notify = Callable[[int, str, Job], Awaitable[None]]  # (server, campaign, job): ready or failed


class _Stopped(Exception):
    """A call made for the structured-output adapter failed. `RecapWorker._call` has already put
    the job waiting or failed, so the step only returns."""


class _Counted:
    """A tier for the structured-output adapter whose every call goes through the worker's `_call`:
    counted before it is made, against the recap's call ceiling and the day's tally, with the same
    handling of an out-of-funds or failed call (#2714)."""

    def __init__(
        self,
        worker: RecapWorker,
        gid: int,
        cid: str,
        job: Job,
        client: Client,
        stage: str = "split",
    ) -> None:
        self._worker, self._gid, self._cid, self._job, self._client = worker, gid, cid, job, client
        self._stage = stage

    async def complete(
        self,
        system: str,
        text: str,
        *,
        max_tokens: int = 8000,
        schema: dict[str, Any] | None = None,
    ) -> Reply:
        w = self._worker
        fresh = await w._store.get(self._gid, self._cid, self._job.session_at)
        if fresh is not None and fresh.calls >= w._ceiling(fresh):
            # The step may make several calls where it made one: the limit holds between them
            await w._fail(self._gid, self._cid, self._job, CAP)
            raise _Stopped
        reply = await w._call(
            self._gid, self._cid, self._job, self._client, system, text, max_tokens,
            schema=schema, stage=self._stage,
        )  # fmt: skip
        if reply is None:
            ai_structured.STATS.errors += (
                1  # (`_call` swallows the error: the adapter cannot count it)
            )
            raise _Stopped
        return reply


class RecapWorker:
    def __init__(
        self,
        store: RecapStore,
        fast: Client,
        careful: Client,
        *,
        load: Load,
        consenting: Callable[[int], Consenting],
        allowed: Callable[[int, str, Job], Awaitable[bool]],
        notify: Notify,
        unchecked: Notify | None = None,
        allowance: Callable[[int, str, Job], Awaitable[str | None]] | None = None,
        names_ready: Callable[[int, str, Job], Awaitable[bool]] | None = None,
        clock: Callable[[], float] = time.time,
        on_problems: Callable[[str, int, Sequence[str]], None] | None = None,
        on_note: Callable[[str], None] | None = None,
        on_call: Callable[[cl.CallEvent], None] | None = None,
        on_count: Callable[[str, int], None] | None = None,
        creative: Client | None = None,
        formatting: Client | None = None,
        story: Story | None = None,
        critics: bool = False,
        reads: str = rd.BEATS,
        structured_split: bool = False,
        structured_beats: bool = False,
    ) -> None:
        # Two models write the prose (#2074): `creative` writes it as plain text, `formatting`
        # cuts it into the schema's sections without changing a word. With either missing, the one
        # structured call of before writes and shapes it at once (tests and small tools).
        self._story = story
        # What the writer reads (#2590): the beat sheet, or the cleaned transcript with PlotBot's
        # guide. A recap that is too long for the writer's model falls back to the beats, once,
        # remembered here for the job until it ends.
        self._reads = reads
        # The opening's split by a draft and a form (#2714, RECAP_STRUCTURED_OUTPUT_SPLIT).
        self._structured_split = structured_split
        self._structured_beats = structured_beats
        self._beats_instead: set[tuple[int, str, int]] = set()
        self._heard: dict[tuple[int, str, int], str] = {}  # the transcript the writer was shown
        # the sheet, threads and earlier recaps the writer was shown (#2954): the checks read them
        self._given: dict[tuple[int, str, int], rd.Given] = {}
        self._critics = critics  # the critics loop after the prose (#2255); off for small tools
        self._creative = creative
        self._formatting = formatting
        self._two = creative is not None and formatting is not None
        self._draft_stage = "write" if self._two else "prose"  # the stage the draft's call is in
        self._on_problems = on_problems  # (stage, try, problems): a test run reports them (#1969)
        self._on_note = on_note  # plain notes a test run puts in its report (#2013)
        self._on_count = on_count  # (name, number): counts a test run reports, never words (#2738)
        # Each AI call as it returns, for a test run's "Calls" section (#2053): counts and fixed
        # words only. The worker fills in the event as the answer is checked.
        self._on_call = on_call
        self._last: dict[tuple[int, str, int, str], cl.CallEvent] = {}
        # The last rejected draft of each job and stage, so a retry can send it back (#2026). Kept
        # in memory only: after a restart the retry just goes without it. Dropped when the stage
        # passes or the job fails.
        # What the DM is told about the draft that is about to stand (the last lines taken out, a
        # fact check that could not be had), kept until the draft is accepted (#2741).
        self._dm_notes: dict[tuple[int, str, int], list[str]] = {}
        self._rejected_prose: dict[tuple[int, str, int], rd.Prose] = {}
        self._rejected_finish: dict[tuple[int, str, int], tuple[str, list[str]]] = {}
        # A draft that passed every check but was graded below the bar, kept while its revision is
        # tried (in memory, like the rejected drafts): if the revision fails the checks, this draft
        # stands instead of the whole recap failing (#2008, test 7: a revision came back 173 words
        # and the third try was cut off, though the first draft had passed).
        self._fallback: dict[
            tuple[int, str, int], tuple[rd.Prose, dict[str, int], cl.CallEvent | None]
        ] = {}
        # The fact check's flags on a kept draft (#2064), and the jobs whose draft is already the
        # one revision the check allows. In memory, like the drafts: after a restart the check
        # just runs again.
        self._flags: dict[tuple[int, str, int], rd.Facts] = {}
        self._revised: set[tuple[int, str, int]] = set()
        # The writer's text, kept while the formatter's call failed and the job waits (#2074), so
        # the expensive write is not paid for twice.
        self._written: dict[tuple[int, str, int], str] = {}
        self._store = store
        self._fast = fast
        self._careful = careful
        self._load = load
        self._consenting = consenting
        self._allowed = allowed
        self._notify = notify
        self._unchecked = unchecked  # tells the DM once that a recap could not be checked
        self._allowance = allowance
        self._names_ready = names_ready  # PlotBot's name pass on the transcript is done (#2628)
        self._now = clock

    async def run(self, guild_id: int, campaign_id: str, session_at: int) -> Job | None:
        """Work one job until it is ready, has failed, or must wait. None if another worker
        has it (or it is not due)."""
        if not await self._store.lease(guild_id, campaign_id, session_at):
            return None
        job: Job | None = None
        try:
            while True:
                job = await self._store.get(guild_id, campaign_id, session_at)
                if job is None or job.finished or job.status != "draft":
                    break
                if job.next_at > self._now():
                    break
                before = self._mark(job)
                job = await self._step(guild_id, campaign_id, job)
                if job is None or job.finished or before == self._mark(job):
                    break  # finished, or no progress: never spin
        finally:
            await self._store.release(guild_id, campaign_id, session_at)
        if job is not None and job.finished and job.emailed_at is None and job.status == "draft":
            await self._tell(guild_id, campaign_id, job)
        return job

    @staticmethod
    def _ceiling(job: Job) -> int:
        """AI calls one recap may make: the usual, and one more for each extra window."""
        return MAX_CALLS + max(0, job.window_count - 1)

    @staticmethod
    def _mark(job: Job) -> tuple[object, ...]:
        """Everything a step can change, to tell whether it got anywhere."""
        return (
            job.state,
            job.attempt,
            job.errors,
            job.next_at,
            job.calls,
            job.emailed_at,
            job.restarts,
            job.problems,
            job.window_done,
            job.split is None,
            repr(job.critics),
        )

    async def _tell(self, gid: int, cid: str, job: Job) -> None:
        # Marked first, so a crash in the mail never sends two; the mailer also sends each
        # kind once.
        await self._store.update(gid, cid, job.session_at, emailed_at=int(self._now()))
        try:
            await self._notify(gid, cid, job)
        except Exception:
            log.exception("Recap: the email step failed")

    # --- one step -----------------------------------------------------------------------

    async def _step(self, gid: int, cid: str, job: Job) -> Job | None:
        retry = job.state == "queued" and job.emailed_at is not None
        if retry and self._allowance is not None:
            # Try again counts as a new recap against the month, checked before anything of the
            # old one is wiped: a refusal leaves the DM's recap as it was.
            try:
                over = await self._allowance(gid, cid, job)
            except Exception as exc:
                log.warning("Recap: couldn't read the month's count (%s)", type(exc).__name__)
                return await self._wait(gid, cid, job, NO_COUNT, error=type(exc).__name__)
            if over:
                await self._store.give_back_try(gid, cid, job.session_at)
                await self._store.update(
                    gid, cid, job.session_at,
                    state="ready" if job.summary else "failed", last_error=over,
                )  # fmt: skip
                return await self._store.get(gid, cid, job.session_at)
        if retry:
            # The DM pressed Try again (the page set the job back to queued): a fresh recap, with
            # a fresh share of calls and a fresh email. The day's tally (`spent`) is kept, and
            # this comes before the ceiling, which is what a failed-at-the-ceiling job needs.
            await self._store.update(
                gid, cid, job.session_at, calls=0, emailed_at=None, summary="", original="",
                title="", housekeeping=[], music=[], beats=[], speakers=[], grade={},
                attempt=0, errors=0, restarts=0, next_at=0, problems=[], last_error="",
                window_done=0, window_count=0, to_confirm=[], names_state=0, split=None,
                critics=None,
            )  # fmt: skip
            await self._store.reset_audio(gid, cid, job.session_at)  # a new recap, new audio
            return await self._store.get(gid, cid, job.session_at)
        if not await self._allowed(gid, cid, job):
            return await self._fail(gid, cid, job, "recaps aren't on for this campaign")
        if await self._waiting_for_names(gid, cid, job):
            return await self._store.get(gid, cid, job.session_at)
        first = job.state == "queued" and job.calls == 0 and job.split is None
        if self._allowance is not None and first:
            # The monthly allowance (#1428), before the first paid call of this recap. A count
            # that can't be read spends nothing: the job waits, and fails if it keeps failing.
            try:
                over = await self._allowance(gid, cid, job)
            except Exception as exc:
                log.warning("Recap: couldn't read the month's count (%s)", type(exc).__name__)
                return await self._wait(gid, cid, job, NO_COUNT, error=type(exc).__name__)
            if over:
                return await self._fail(gid, cid, job, over)
        if job.calls >= self._ceiling(job):
            return await self._fail(gid, cid, job, CAP)
        loop = cr.loop_from_json(job.critics)
        in_loop = job.state == "prose" and self._critics and not loop.done
        spent_today = await self._store.calls_since(gid, cid, int(self._now()) - DAY_S)
        # (the loop stops by itself, below; and a draft the DM approved unchecked only has its
        # title step left, which the waiting tries' calls in the day's tally must not fail, so it
        # has a small fixed allowance of its own over the day's limit, still counted in it, #2305)
        limit = DAILY_CALLS + (APPROVED_TITLE_CALLS if loop.approved_at else 0)
        if spent_today >= limit and not in_loop:
            return await self._fail(gid, cid, job, DAILY)
        inputs = await self._load(gid, cid, job)
        if inputs is None:
            return await self._wait(gid, cid, job, NO_NAMES)
        consenting = self._consenting(gid)
        if job.speakers and not set(job.speakers) <= set(await consenting()):
            return await self._restart(gid, cid, job)
        stage = job.state
        if stage == "queued":
            return await self._beats(gid, cid, job, inputs, consenting)
        if stage == "beats":
            return await self._check_beats(gid, cid, job, inputs, consenting)
        if stage == "checked":
            return await self._prose(gid, cid, job, inputs, consenting)
        if stage == "prose":
            if self._critics and not cr.loop_from_json(job.critics).done:
                return await self._critique(gid, cid, job, inputs, consenting)
            return await self._finish(gid, cid, job, inputs, consenting)
        return await self._fail(gid, cid, job, "unknown stage")

    async def _waiting_for_names(self, gid: int, cid: str, job: Job) -> bool:
        """Step 1 waits for PlotBot's name pass on the transcript (#2628), so the beats, the recap
        and the narration all use the list's spellings. A short, bounded wait that costs nothing
        and fails nothing: the pass marks the session done whatever happens, and after
        `NAMES_WAIT_S` the recap goes on regardless. While it waits the job says so
        (`CHECKING_NAMES`, and each look moves its time). True when the job was put off."""
        if self._names_ready is None or job.state != "queued" or job.calls > 0:
            return False
        waiting = int(self._now()) - job.created_at < NAMES_WAIT_S
        ready = False
        if waiting:
            try:
                ready = await self._names_ready(gid, cid, job)
            except Exception:
                log.exception("Recap: couldn't tell whether the names are checked")
                ready = True  # a pass that cannot be read must not hold the recap back
        if ready or not waiting:
            if job.last_error == CHECKING_NAMES:  # the wait is over: the note goes
                await self._store.update(gid, cid, job.session_at, last_error="")
            return False
        await self._store.update(
            gid, cid, job.session_at, next_at=int(self._now()) + NAMES_RETRY_S,
            last_error=CHECKING_NAMES,
        )  # fmt: skip
        return True

    async def _call(
        self,
        gid: int,
        cid: str,
        job: Job,
        client: Client,
        system: str,
        text: str,
        max_tokens: int,
        schema: dict[str, Any] | None = None,
        stage: str = "",
        soft: bool = False,
        free: bool = False,
    ) -> Reply | None:
        """One AI call, counted before it is made. None if it failed (the job is then waiting or
        failed already: the caller returns). `soft`: a call of the critics loop (#2255), whose
        failure never fails a recap that is already written: it only waits, and after
        `CRITIC_ERRORS_MAX` of them a draft not yet read waits for its check (`_hold`). `free`: a
        call of a recap that waits for its check, which has its own small budget
        (`cr.wait_budget` a try: a bound the loop is built to meet, checked by a test): counted in
        the day's tally (`spent`, dated) but not against the job's
        own calls, so waiting can never fail the job."""
        # Counted from the stored job, not the one handed in: a try can make several calls (the
        # writer, then the formatter and its re-run, #2074), and each one must count.
        now = await self._store.get(gid, cid, job.session_at) or job
        calls, spent = now.calls + (0 if free else 1), now.spent + 1
        await self._store.update(gid, cid, job.session_at, calls=calls, spent=spent)
        try:
            if schema is None:
                reply = await client.complete(system, text, max_tokens=max_tokens)
            else:  # structured outputs (#2039): the answer is JSON of this shape
                reply = await client.complete(system, text, max_tokens=max_tokens, schema=schema)
        except AIOutOfFunds:
            await self._fail(gid, cid, job, FUNDS)
            return None
        except Exception as exc:  # the AI service, the network: a wait, never fatal to the bot
            log.warning("Recap: a call failed (%s)", type(exc).__name__)  # the type, not the text
            if soft:
                await self._soft_wait(gid, cid, job, calls, spent, type(exc).__name__)
                return None
            await self._wait(
                gid,
                cid,
                job,
                SERVICE,
                calls=calls,
                spent=spent,
                error=type(exc).__name__,
            )
            return None
        self._announce(gid, cid, job, client, max_tokens, reply, stage=stage)
        return reply

    def _announce(
        self,
        gid: int,
        cid: str,
        job: Job,
        client: Client,
        limit: int,
        reply: Reply,
        *,
        stage: str = "",
        grade: dict[str, int] | None = None,
    ) -> None:
        """Tell the observer about a call that returned (#2053). Nothing else reads this."""
        if self._on_call is None:
            return
        stage = stage or cl.STAGE_OF_STATE.get(job.state, job.state)
        stop = reply.stop or ("refusal" if reply.refused else "max_tokens" if reply.cut else "")
        stop = stop if stop in cl.STOPS else "other"  # fixed words only, whatever the service says
        event = cl.CallEvent(
            stage,
            job.attempt + 1,
            self._tier_name(client),
            reply.input_tokens,
            reply.output_tokens,
            limit,
            stop,
            grade=dict(grade or {}),
        )
        self._last[(gid, cid, job.session_at, stage)] = event
        self._on_call(event)

    def _tier_name(self, client: Client) -> str:
        """The tier's name for the report: the fixed words, never a model."""
        for name, known in (
            ("creative", self._creative),
            ("formatting", self._formatting),
            ("fast", self._fast),
        ):
            if known is not None and client is known:
                return name
        return "careful"

    def _annotate(self, gid: int, cid: str, job: Job, stage: str, **fields: Any) -> None:
        """Add what a check found to the latest call of that stage."""
        event = self._last.get((gid, cid, job.session_at, stage))
        if event is not None:  # (a value given as a function is worked out only when observed)
            for name, value in fields.items():
                setattr(event, name, value() if callable(value) else value)

    def _client(self, job: Job) -> Client:
        return self._fast if job.attempt == 0 else self._careful

    # --- stage 1: the beat sheet --------------------------------------------------------

    async def _beats(
        self, gid: int, cid: str, job: Job, inputs: Inputs, consenting: Consenting
    ) -> Job | None:
        """One window of the transcript for each call, the whole session in order (a short one
        is a single window). Each window's beats are saved before the next is read, so a restart
        carries on at the next window."""
        agreed = set(await consenting())
        said = [s for s in readable(inputs.lines) if s.user_id in agreed]
        if not said:
            return await self._fail(gid, cid, job, NOTHING)
        if job.split is None and job.window_done == 0:
            return await self._split(gid, cid, job, inputs, said, consenting)
        split = sp.from_json(job.split) or sp.all_play(said)  # (a job begun before #2253: all play)
        # The beats come from the play alone; the table's retelling of earlier sessions goes with
        # them as labelled context, and the talk is left out (#2253).
        play = sp.pick(said, split, sp.PLAY) or said
        rows = [
            (str(s.number), "DM" if s.user_id in inputs.dm_ids else "Player", s.text) for s in play
        ]
        retold = " ".join(s.text for s in sp.pick(said, split, sp.RECAP))
        parts = bt.windows(rows, inputs.window_chars)
        k = job.window_done
        if k >= len(parts):  # the transcript is shorter than when this began: begin again
            return await self._restart(gid, cid, job)
        text = bt.message(parts[k], inputs.allowed_names, retold)
        if job.problems:
            text += (
                "\nYour last sheet was rejected: " + "; ".join(job.problems) + ". Write it again."
            )
        # (everyone whose words went to an AI for this recap: the play, the retelling, and the
        # catching up the housekeeping step reads; the talk went nowhere)
        sent = {s.user_id for s in sp.pick(said, split, sp.PLAY, sp.RECAP, sp.CATCHUP)}
        parsed = None
        if self._structured_beats:
            # A strong model drafts the sheet, a fast one puts it in the schema, and `bt.parse`
            # decides, as below. When that gives nothing the one call below is made, as before: the
            # new way only ever adds a try (#2714).
            try:
                tried = await ai_structured.structured(
                    bt.draft_system(k + 1, len(parts)),
                    text,
                    bt.SCHEMA,
                    bt.parse,
                    draft=_Counted(self, gid, cid, job, self._careful, "beats"),
                    fmt=_Counted(self, gid, cid, job, self._fast, "beats"),
                    escalate=_Counted(self, gid, cid, job, self._careful, "beats"),
                    # (the floor, doubled on a retry after a cut, as `bt.room` does)
                    draft_tokens=OUTPUT_FLOOR << min(job.attempt, 2),
                    format_tokens=OUTPUT_FLOOR << min(job.attempt, 2),
                    stage="beats",
                )
            except _Stopped:  # a call failed: `_call` has put the job waiting or failed
                return await self._store.get(gid, cid, job.session_at)
            if not sent <= set(await consenting()):  # after it
                return await self._restart(gid, cid, job)
            if tried.status == ai_structured.CUT:
                return await self._beats_cut(gid, cid, job, OUTPUT_FLOOR << min(job.attempt, 2))
            parsed = tried.value
        if parsed is None:
            raw = await self._call(
                gid,
                cid,
                job,
                self._client(job),
                bt.system(k + 1, len(parts)),
                text,
                bt.room(job.attempt),
            )
            if raw is None:
                return await self._store.get(gid, cid, job.session_at)
            if raw.cut:  # never read half a sheet: once more with twice the room (#2341)
                return await self._beats_cut(gid, cid, job, bt.room(job.attempt))
            if not sent <= set(await consenting()):  # again, after the answer
                return await self._restart(gid, cid, job)
            parsed = bt.parse(raw.text)
        if parsed is None:
            self._annotate(gid, cid, job, "beats", checks=["shape"])
            return await self._retry_or_fail(gid, cid, job, ["answer in the JSON form asked for"])
        beats = [*(job.beats or []), *parsed]
        last = k + 1 == len(parts)
        await self._store.update(
            gid,
            cid,
            job.session_at,
            state="beats" if last else "queued",
            beats=bt.merge([beats]) if last else beats,
            speakers=sorted(sent),
            window_count=len(parts),
            window_done=k + 1,
            errors=0,
            last_error="",
        )
        return await self._store.get(gid, cid, job.session_at)

    async def _beats_cut(self, gid: int, cid: str, job: Job, room: int) -> Job | None:
        """The beat sheet was cut off: once more with twice the room, then say so (#2341)."""
        log.info("Recap: the beat sheet was cut off at %d tokens (try %d)", room, job.attempt + 1)
        self._annotate(gid, cid, job, "beats", checks=["cut off"])
        return await self._retry_or_fail(
            gid, cid, job, ["the answer was cut off: write it again"], cut_off=True
        )

    async def _split(
        self,
        gid: int,
        cid: str,
        job: Job,
        inputs: Inputs,
        said: Sequence[Said],
        consenting: Consenting,
    ) -> Job | None:
        """Mark the opening's parts: talk, catching up, last time's retelling and play (#2253).
        With no earlier session there is nothing to retell, so no call and all play. Otherwise one
        fast-tier call labels the opening and code checks the answer against the earlier session's
        words; an answer it cannot read falls back to strong evidence only, else all play, and a
        line is never dropped on a guess."""
        split = sp.all_play(said)
        earlier = inputs.earlier
        if earlier is not None and job.errors >= SPLIT_ERRORS_MAX:
            # The labelling is optional: after repeated service errors the recap goes on with
            # what code alone can tell, rather than fail for it.
            split = sp.without_ai(said, earlier, inputs.previous)
        elif earlier is not None:
            region = sp.opening_of(said)
            rows = [
                (str(s.number), "DM" if s.user_id in inputs.dm_ids else "Player", s.text)
                for s in region
            ]
            message = sp.message(rows, earlier, inputs.previous)
            labelled = None
            if self._structured_split:
                # A strong model drafts the labels, a fast one puts them in the schema, and the same
                # check as below decides. When that gives nothing the one call below is made, as
                # before: the new way only ever adds a try (#2714). A try is usually one more call,
                # two when the form is redone on the stronger tier, three when the one call
                # follows both.
                try:
                    tried = await ai_structured.structured(
                        sp.draft_system(),
                        message,
                        sp.SCHEMA,
                        sp.parse,
                        draft=_Counted(self, gid, cid, job, self._careful),
                        fmt=_Counted(self, gid, cid, job, self._fast),
                        escalate=_Counted(self, gid, cid, job, self._careful),
                        stage="split",
                    )
                except _Stopped:  # a call failed: `_call` has put the job waiting or failed
                    return await self._store.get(gid, cid, job.session_at)
                labelled = tried.value
                if not {s.user_id for s in region} <= set(await consenting()):  # after it
                    return await self._restart(gid, cid, job)
            if labelled is None:
                raw = await self._call(
                    gid,
                    cid,
                    job,
                    self._fast,
                    sp.system(),
                    message,
                    SPLIT_TOKENS,
                    schema=sp.SCHEMA,
                    stage="split",
                )
                if raw is None:
                    return await self._store.get(gid, cid, job.session_at)
                if not {s.user_id for s in region} <= set(await consenting()):  # after it
                    return await self._restart(gid, cid, job)
                labelled = None if unusable(raw) else sp.parse(raw.text)
            if labelled is None:
                split = sp.without_ai(said, earlier, inputs.previous)
            else:
                split = sp.build(said, labelled, earlier, inputs.previous)
            self._annotate(gid, cid, job, "split", checks=[] if labelled is not None else ["shape"])
        # Counts only: how many lines went to each part, never a word of them.
        log.info(
            "Recap: split by %s into %s%s",
            split.method,
            {label: len(sp.pick(said, split, label)) for label in sp.LABELS},
            ", the retelling unsure" if split.unsure else "",
        )
        # (stored with a clean error count: the split's service errors are not the beats' budget)
        await self._store.update(
            gid, cid, job.session_at, split=split.to_json(), errors=0, next_at=0, last_error=""
        )
        return await self._store.get(gid, cid, job.session_at)

    async def _check_beats(
        self, gid: int, cid: str, job: Job, inputs: Inputs, consenting: Consenting
    ) -> Job | None:
        said = self._said(inputs, set(job.speakers))
        # A beat may rest only on play lines: one citing only the table's retelling, catching up or
        # talk has no real line and is cut here (#2253). Names are known from every line.
        play = self._parts(job, said)[0]
        kept, problems = bt.check(
            [b for b in (job.beats or []) if isinstance(b, dict)],
            lines={s.number: s.text for s in play},
            speakers={s.number: s.user_id for s in play},
            allowed_names=inputs.allowed_names,
            secret_names=inputs.secret_names,
            dm_ids=inputs.dm_ids,
            transcript_words={w for s in said for w in words(s.text)},
            aliases=inputs.aliases,
            party=inputs.party,
            pcs=inputs.pcs,
            ordinary_words=rd.ordinary("\n".join(s.text for s in said)),
        )
        usable = len(kept) >= bt.MIN_BEATS and not any("in no beat" in p for p in problems)
        # (a long session reads in windows; the check is of the whole sheet: it marks the last)
        self._annotate(gid, cid, job, "beats", checks=lambda: cl.kinds(problems))
        if not usable:
            if len(kept) < bt.MIN_BEATS and job.attempt >= 1:
                return await self._fail(gid, cid, job, TOO_LITTLE)
            return await self._back_to_start(gid, cid, job, problems)
        log.info("Recap: %d beat(s) kept, %d problem(s)", len(kept), len(problems))  # counts only
        marked = await self._significance(gid, cid, job, inputs, kept, consenting)
        if not isinstance(marked, list):
            return (
                marked  # the call failed or the speakers changed: the job is waiting or begun again
            )
        kept = marked
        await self._store.update(
            gid,
            cid,
            job.session_at,
            state="checked",
            beats=bt.as_json(kept),
            attempt=0,
            errors=0,
            problems=[],
        )
        return await self._store.get(gid, cid, job.session_at)

    async def _significance(
        self,
        gid: int,
        cid: str,
        job: Job,
        inputs: Inputs,
        kept: list[bt.Beat],
        consenting: Consenting,
    ) -> list[bt.Beat] | Job | None:
        """PlotBot marks which beats matter and adds the few left-out details that tie to the story
        so far (#2254). Optional: with nothing to tie to, no way to read the memory, a service that
        keeps failing, or an answer that cannot be read, the recap goes on with the beats as they
        are. Returns the beats to store, or the job when the step must stop (waiting, restarted)."""
        if self._story is None or job.errors >= SIG_ERRORS_MAX:
            return kept
        if (
            job.calls >= self._ceiling(job) - SIG_RESERVE
        ):  # optional: never spend what the rest needs
            return kept
        said = self._said(inputs, set(job.speakers))
        play, retold, _ = self._parts(job, said)
        seeds = sorted({n for b in kept for n in (*b.who, b.where) if n})[:40]
        try:
            memory = list(await self._story(gid, cid, job, seeds))
        except Exception as exc:  # the memory is optional context: the type, never the text
            log.warning("Recap: couldn't read the story memory (%s)", type(exc).__name__)
            memory = []
        if not sg.worth_asking(memory, inputs.previous):
            return kept
        rows = [
            (str(s.number), "DM" if s.user_id in inputs.dm_ids else "Player", s.text) for s in play
        ]
        raw = await self._call(
            gid,
            cid,
            job,
            self._careful,
            sg.system(),
            sg.message(kept, memory, inputs.previous, rows, retold),
            SIG_TOKENS,
            schema=sg.SCHEMA,
            stage="significance",
        )
        if raw is None:
            return await self._store.get(gid, cid, job.session_at)
        # (the people whose words were in the call: the play lines sent, and the retelling shown)
        split = sp.from_json(job.split) or sp.all_play(said)
        sent = {s.user_id for s in play} | {s.user_id for s in sp.pick(said, split, sp.RECAP)}
        if not sent <= set(await consenting()):  # again, after the answer
            return await self._restart(gid, cid, job)
        parsed = None if unusable(raw) else sg.parse(raw.text)
        if parsed is None:
            self._annotate(gid, cid, job, "significance", checks=["shape"])
            return kept
        play_lines = {s.number: s.text for s in play}
        vocabulary = sg.known_words(
            kept, memory, inputs.previous, list(play_lines.values()), retold
        )
        notes, details, cut = sg.check(
            *parsed,
            beats=kept,
            memory=memory,
            has_recap=bool(inputs.previous.strip()),
            play_lines=play_lines,
            known_words=vocabulary,
            allowed_names=inputs.allowed_names,
            secret_names=inputs.secret_names,
        )
        self._annotate(gid, cid, job, "significance", checks=lambda: cl.kinds(cut))
        log.info("Recap: %d note(s), %d detail(s), %d cut", len(notes), len(details), len(cut))
        return sg.apply(kept, notes, details)

    @staticmethod
    def _said(inputs: Inputs, speakers: set[int]) -> list[Said]:
        return [s for s in readable(inputs.lines) if s.user_id in speakers]

    @staticmethod
    def _parts(job: Job, said: Sequence[Said]) -> tuple[list[Said], str, str]:
        """(the play lines, the table's retelling, its catching up) from the stored split. A job
        with none (begun before #2253) is all play."""
        split = sp.from_json(job.split) or sp.all_play(said)
        play = sp.pick(said, split, sp.PLAY) or list(said)
        retold = " ".join(s.text for s in sp.pick(said, split, sp.RECAP))
        catchup = " ".join(s.text for s in sp.pick(said, split, sp.CATCHUP))
        return play, retold, catchup

    async def _back_to_start(
        self, gid: int, cid: str, job: Job, problems: Sequence[str]
    ) -> Job | None:
        """The sheet failed its checks: the stage is run again, once, on the careful tier."""
        if job.attempt >= 1:
            return await self._fail(gid, cid, job, FAILED_CHECKS)
        await self._store.update(
            gid,
            cid,
            job.session_at,
            state="queued",
            beats=[],
            window_done=0,
            window_count=0,
            attempt=job.attempt + 1,
            problems=list(problems),
        )
        return await self._store.get(gid, cid, job.session_at)

    # --- stage 2: the prose -------------------------------------------------------------

    async def _context_for(
        self,
        gid: int,
        cid: str,
        job: Job,
        inputs: Inputs,
        play: Sequence[Said],
        shown: Sequence[bt.Beat],
    ) -> tuple[str, rd.Given]:
        """The material the writer gets besides the beat sheet (#2954): the cleaned transcript, the
        who-and-what lines, the open threads and the earlier recaps. The transcript is the play's
        cleaned lines of the people recorded now, in the order spoken, each with its speaker as the
        character, the DM or a number: never a player's name, the raw words, the DM sidebar or a
        table question. They are limited to what the writer's model takes: the earlier recaps are
        dropped oldest first, whole, to fit; a transcript that alone is too long for the model is
        left out (the beats and the rest are still given), with a plain log line."""
        key = (gid, cid, job.session_at)
        first = [
            u for u in dict.fromkeys(x.user_id for x in inputs.lines) if u not in inputs.dm_ids
        ]
        # A line that names a secret never goes to the writer (the beat sheet already leaves such
        # beats out): the writer then cannot see a name the players have not been shown.
        rows = [
            (self._who(inputs, s.user_id, first), s.text)
            for s in play
            if not names_a_secret(s.text, inputs.secret_names, inputs.allowed_names)
        ]
        rows = self._without_near_secrets(rows, inputs)
        block = rd.transcript_block(rows)
        sheet_text = await self._who_and_what(gid, cid, job, inputs, shown)
        # Thread names are the DM's own list and carry no secret flag: the only guard is the secret
        # names filter, and the writer is told a thread never adds an event (the DM reviews the
        # recap before the table hears it).
        threads = rd.open_threads(inputs.threads, inputs.allowed_names, inputs.secret_names)
        limit = self._transcript_limit() * CHARS_PER_TOKEN
        extra = sum(len(x) for x in (*sheet_text, *threads))
        if key in self._beats_instead:  # said once for this job; a retry does not say it again
            block = ""
        elif len(block) + extra > limit:
            self._beats_instead.add(key)
            log.info(
                "Recap: the transcript is about %d tokens, over the writer model's limit of %d: "
                "this recap is written without it",
                len(block) // CHARS_PER_TOKEN,
                self._transcript_limit(),
            )
            if self._on_note is not None:
                self._on_note(
                    "Check: this session was too long for DMbot to read in full, "
                    "so the recap is built from the key moments only."
                )
            block = ""
        # An earlier recap that names something now secret is left out whole: a name made secret
        # after a recap was accepted must not leave the process in a prompt.
        earlier = [
            r
            for r in inputs.story
            if not names_a_secret(r, inputs.secret_names, inputs.allowed_names)
        ]
        if len(earlier) < len(inputs.story):
            log.info(
                "Recap: %d earlier recap(s) left out (they name a secret)",
                len(inputs.story) - len(earlier),
            )
        story = tuple(
            rd.trim_story(earlier, min(STORY_CHARS_MAX, max(0, limit - len(block) - extra)))
        )
        return block, rd.Given(sheet_text, threads, story)

    @staticmethod
    def _without_near_secrets(rows: list[tuple[str, str]], inputs: Inputs) -> list[tuple[str, str]]:
        """The lines minus any that holds a word spelled close to a secret name (a misheard
        "Silas Vain" for a secret "Silas Vane"): the same test that refuses such a word in a
        draft, run line by line."""
        if not inputs.secret_names or not rows:
            return rows
        from dmbot.recaps.unknown_names import respell

        names = sorted(inputs.allowed_names)
        return [
            (who, text)
            for who, text in rows
            if not respell(
                text,
                names=names,
                secret_names=inputs.secret_names,
                known_words=set(),
                ordinary_words=set(),
            ).refused
        ]

    def _given_args(self, gid: int, cid: str, job: Job) -> dict[str, Any]:
        """The sheet and the earlier recaps the writer was given, as `check_prose` takes them."""
        given = self._given.get((gid, cid, job.session_at), rd.Given())
        return {"who_and_what": given.who_and_what, "story": given.story}

    async def _who_and_what(
        self, gid: int, cid: str, job: Job, inputs: Inputs, shown: Sequence[bt.Beat]
    ) -> tuple[str, ...]:
        """What the players know of the names in the beats, one line each (#2950): the players'
        view of story memory around the beats' names (kind, role and the connections the players
        know), with no note of the DM's, nothing secret, and nothing the players have not been
        shown. Empty when there is no story memory to read or it fails: the writer then explains
        only from the transcript and the earlier recaps."""
        if self._story is None:
            return ()
        seeds = sorted({n for b in shown for n in (*b.who, b.where) if n})[:40]
        if not seeds:
            return ()
        try:
            memory = list(await self._story(gid, cid, job, seeds))
        except Exception as exc:  # optional context: the type, never the text
            log.warning("Recap: couldn't read the story memory (%s)", type(exc).__name__)
            return ()
        return rd.sheet_lines(
            [(m.text, m.known) for m in memory], inputs.allowed_names, inputs.secret_names
        )

    def _transcript_limit(self) -> int:
        """The most tokens of transcript and guide the writer's model takes, leaving room for the
        instructions, the names and the answer."""
        # The fact check reads the same lines on the careful model, so the smaller of the two rules.
        writer = str(getattr(self._creative or self._careful, "model", ""))
        checker = str(getattr(self._careful, "model", ""))
        return int(
            min(
                max_input(writer) * TRANSCRIPT_SHARE,
                max_input(checker) * TRANSCRIPT_SHARE,
                TRANSCRIPT_TOKENS_MAX,
            )
        )

    @staticmethod
    def _who(inputs: Inputs, user_id: int, order: Sequence[int] | None = None) -> str:
        """How a speaker is shown to the writer: "DM", the character the player plays (one, or
        several joined), else "Player N" by who spoke first. Never a player's name. `order`: the
        players in the order they first spoke, worked out once for a whole transcript."""
        if user_id in inputs.dm_ids:
            return "DM"
        for player, characters in inputs.party.items():
            if player == user_id and characters:
                return "/".join(characters)
        if order is None:
            order = [u for u in dict.fromkeys(x.user_id for x in inputs.lines)]
            order = [u for u in order if u not in inputs.dm_ids]
        return f"Player {order.index(user_id) + 1}" if user_id in order else "Player"

    async def _prose(
        self, gid: int, cid: str, job: Job, inputs: Inputs, consenting: Consenting
    ) -> Job | None:
        sheet = bt.from_json(job.beats)
        key = (gid, cid, job.session_at)
        if len(sheet) < bt.MIN_BEATS:
            return await self._fail(gid, cid, job, TOO_LITTLE)
        # The recap's length follows the session's hours (#2013). The writer is shown at most
        # `length.beats` of a long sheet; the checks still use all of it, so a name from any beat
        # is known.
        said = self._said(inputs, set(job.speakers))
        play, retold, _ = self._parts(job, said)
        # (the hours are the play's: the talk and the retelling are not session time)
        length = rd.length_for(rd.session_hours(play, inputs.hours))
        shown = rd.pick_beats(sheet, length.beats, importance=inputs.importance)
        heard, given = await self._context_for(gid, cid, job, inputs, play, shown)
        message = rd.prose_message(
            shown,
            inputs.previous,
            [n for n in inputs.allowed_names if n not in inputs.aliases],  # (#2573)
            inputs.day,
            retold,
            ending=sheet,
            transcript=heard,
            apart=rd.apart_pairs(inputs.relation_ties, inputs.allowed_names),
            familiar=inputs.familiar,
            who_and_what=given.who_and_what,
            threads=given.threads,
            story=given.story,
        )
        self._given[key] = given  # the checks read the same material
        if heard:
            self._heard[key] = heard  # the fact check reads the same lines
        else:
            self._heard.pop(key, None)
        if job.problems:
            message += rd.prose_retry(
                job.problems, self._rejected_prose.get(key), length, plain=self._two
            )
        stage = self._draft_stage
        kept = self._written.pop(key, None) if self._two else None  # the writer's text, kept
        raw: Reply | None = None
        if kept is None:
            raw = await self._call(
                gid,
                cid,
                job,
                self._creative if self._creative is not None and self._two else self._client(job),
                rd.prose_system(
                    inputs.setting,
                    length,
                    plain=self._two,
                    style=inputs.style,
                    notes=inputs.notes,
                    corrections=inputs.corrections,
                ),
                message,
                more_room(rd.write_room(length) if self._two else length.max_tokens, job.attempt),
                schema=None if self._two else rd.prose_schema(),
                stage=stage if self._two else "",
            )
            if raw is None:
                return await self._store.get(gid, cid, job.session_at)
        if not set(job.speakers) <= set(await consenting()):
            return await self._restart(gid, cid, job)
        if raw is not None and (why := unusable(raw)):
            self._annotate(gid, cid, job, stage, checks=cl.kinds([why]))
            return await self._prose_failed(gid, cid, job, [why], inputs, sheet, length)
        if self._two:
            written = kept if kept is not None else (raw.text if raw is not None else "")
            # Nothing to shape (no narration at all) fails the try without paying the formatter.
            shaped: rd.Prose | str = (
                await self._shape(gid, cid, job, written, length)
                if rd.written_tokens(written)
                else SHAPE_FAILED
            )
            if isinstance(shaped, str):  # why it failed ("" : the call failed, the job is waiting)
                if not shaped:
                    self._written[key] = written  # the next round shapes it, without a new write
                    return await self._store.get(gid, cid, job.session_at)
                self._rejected_prose.pop(key, None)  # an older draft does not go with this problem
                self._annotate(gid, cid, job, stage, checks=cl.kinds([shaped]))
                return await self._prose_failed(gid, cid, job, [shaped], inputs, sheet, length)
            prose: rd.Prose | None = shaped
        else:
            assert raw is not None
            prose = rd.parse_prose(raw.text)
        if prose is None:
            self._annotate(gid, cid, job, stage, checks=["shape"])
            return await self._prose_failed(
                gid, cid, job, ["answer with the sections JSON asked for"], inputs, sheet, length
            )
        fixed = rd.fix_prose(prose, length.sections)  # the mechanical form, in code (#2013)
        if fixed != prose:
            log.info(
                "Recap: put right in code: %d to %d sections, %d to %d music prompts",
                len(CUE.findall(prose.summary)),
                len(CUE.findall(fixed.summary)),
                len(prose.music),
                len(fixed.music),
            )
        prose, refused = rd.respelled(
            fixed,
            beats=sheet,
            allowed_names=inputs.allowed_names,
            secret_names=inputs.secret_names,
            previous=inputs.previous,
            setting=inputs.setting,
            transcript="\n".join(s.text for s in said),
            aliases=inputs.aliases,
        )  # a misheard spelling back to its name; what is too like a secret refused (#2591)
        transcript = "\n".join(s.text for s in said)
        self._annotate(gid, cid, job, stage, words=lambda: len(words(narration_of(prose.summary))))

        draft: rd.Prose = prose  # (the closure below reads this one; `prose` may be cut later)

        name_counts: dict[str, int] = {}

        def checked(last_try: bool) -> list[str]:
            name_counts.clear()
            return rd.check_prose(
                draft,
                beats=sheet,
                transcript=transcript,
                allowed_names=inputs.allowed_names,
                secret_names=inputs.secret_names,
                previous=inputs.previous,
                setting=inputs.setting,
                length=length,
                last=last_try,
                refused=refused,
                style=inputs.style,
                corrections=inputs.corrections,
                who_and_what=given.who_and_what,
                story=given.story,
                pcs=inputs.pcs,
                identity_ties=inputs.identity_ties,
                name_counts=name_counts,
                aliases=inputs.aliases,
            )

        problems = checked(False)
        if problems and job.attempt >= PROSE_LAST_ATTEMPT and not checked(True):
            # A draft that passes everything but a doubtful scene check stands on the last try.
            problems = []
        count = len(words(narration_of(prose.summary)))
        self._annotate(
            gid, cid, job, stage, checks=lambda: cl.kinds(problems), names=lambda: dict(name_counts)
        )
        if problems:
            if name_counts:
                log.info(
                    "Recap: names outside the beat sheet, by kind: %s", _kinds_line(name_counts)
                )
            log.info(
                "Recap: a draft failed %d check(s): %s (%d words, %d wanted, %d section(s) "
                "of at most %d, about %.2f hours)",
                len(problems),
                ", ".join(cl.kinds(problems)),
                count,
                length.target,
                len(CUE.findall(prose.summary)),
                length.sections,
                length.hours,
            )  # names and counts only
            self._keep_rejected(
                key, prose, inputs, [prose.summary, *prose.music], near_secret=bool(refused)
            )
            return await self._prose_failed(gid, cid, job, problems, inputs, sheet, length)
        try:
            facts, asked = await self._facts(
                gid, cid, job,
                rd.pick_beats(sheet, length.beats, importance=inputs.importance), prose,
                {*inputs.allowed_names, *(w for b in sheet for w in b.who)},
                inputs.corrections, False, consenting,
            )  # fmt: skip
        except AIOutOfFunds:
            return await self._fail(gid, cid, job, FUNDS)
        if not set(job.speakers) <= set(await consenting()):
            # (the fact check sends nothing and answers None when a speaker stopped, #2064: that is
            # a recap to start over, never one to stand "not fact-checked")
            return await self._restart(gid, cid, job)
        if not asked and key not in self._flags:
            # The fact check could not be had (a failed, unreadable or cut-off answer even after
            # its retry): the recap stands, marked, never passed as if nothing were flagged.
            log.info("Recap: not fact-checked (the check could not be had)")
            self._dm_notes.setdefault(key, []).append(NOT_FACT_CHECKED_NOTE)
        if not asked and key in self._flags:
            # The revision cannot be checked (the call failed or was unreadable): do not ship it
            # unverified; the first draft stands with its flagged sentences cut.
            return await self._prose_failed(
                gid, cid, job, ["the fact check of the revision could not be had"],
                inputs, sheet, length,
            )  # fmt: skip
        if facts is not None and not facts.clean:
            if key in self._fallback and key not in self._flags:
                # an earlier clean draft stands; this revision, flagged, does not
                return await self._prose_failed(
                    gid, cid, job, rd.facts_problems(facts, prose.summary, with_text=False),
                    inputs, sheet, length,
                )  # fmt: skip
            if key not in self._revised and job.attempt < PROSE_LAST_ATTEMPT:
                # One revision, told which sentences and which beats (#2064); the draft is kept
                # so that, if the revision fails its checks, this one stands with the flagged
                # sentences cut.
                problems = rd.facts_problems(facts, prose.summary)
                self._revised.add(key)
                self._flags[key] = facts
                self._fallback[key] = (prose, {}, self._last.get((*key, stage)))
                self._keep_rejected(key, prose, inputs, [prose.summary, *prose.music])
                if self._on_problems is not None:  # (a test report never shows the draft's words)
                    self._on_problems(
                        job.state,
                        job.attempt,
                        rd.facts_problems(facts, prose.summary, with_text=False),
                    )
                await self._store.update(
                    gid, cid, job.session_at, attempt=job.attempt + 1, problems=problems
                )
                return await self._store.get(gid, cid, job.session_at)
            cut, why = await self._cut(
                prose,
                facts,
                sheet,
                inputs,
                transcript,
                length,
                given=self._given.get((gid, cid, job.session_at)),
            )
            if cut is None:
                return await self._fail(gid, cid, job, UNMATCHED, check=why, ours=True)
            self._tell_if_unended(key, cut, sheet)
            prose = cut
        self._flags.pop(key, None)
        self._revised.discard(key)
        try:
            grade = await self._grade(gid, cid, job, sheet, prose.summary)
        except AIOutOfFunds:
            return await self._fail(gid, cid, job, FUNDS)
        if facts is not None and grade:
            grade["faithful"] = facts.score()
        weak = rd.weak(grade) if grade else []
        if weak and job.attempt == 0:  # below the bar: one more try, told what fell short
            self._keep_rejected(key, prose, inputs, [prose.summary, *prose.music])
            self._fallback[key] = (prose, grade, self._last.get((*key, stage)))
            await self._store.update(
                gid, cid, job.session_at, attempt=1, problems=weak, grade=grade
            )
            return await self._store.get(gid, cid, job.session_at)
        self._fallback.pop(key, None)
        return await self._accept_prose(
            gid, cid, job, key, prose, grade, inputs, sheet, transcript,
            self._last.get((*key, stage)), cl.FINAL,
        )  # fmt: skip

    async def _shape(
        self,
        gid: int,
        cid: str,
        job: Job,
        written: str,
        length: rd.Length,
        soft: bool = False,
        free: bool = False,
    ) -> rd.Prose | str:
        """The written text cut into the schema's sections by the formatting model, or why it
        could not be (#2074). The check is in code: the formatted narration's words must be the
        written text's words, in order. A mismatch runs the formatter once more; a second one
        fails the try as "shape". The text is never repaired in code. "" means a call failed and
        the job is waiting or failed already."""
        assert self._formatting is not None
        for _ in range(2):
            reply = await self._call(
                gid,
                cid,
                job,
                self._formatting,
                rd.FORMAT_SYSTEM,
                rd.format_message(written),
                more_room(length.max_tokens, job.attempt),
                schema=rd.prose_schema(),
                stage="format",
                soft=soft,
                free=free,
            )
            if reply is None:
                return ""
            ok = not unusable(reply)
            prose = rd.parse_prose(reply.text) if ok else None
            if prose is not None and rd.same_words(prose, written):
                self._annotate(gid, cid, job, "format", checks=[])
                return prose
            self._annotate(gid, cid, job, "format", checks=["shape"])
        return SHAPE_FAILED

    async def _prose_failed(
        self,
        gid: int,
        cid: str,
        job: Job,
        problems: Sequence[str],
        inputs: Inputs,
        sheet: Sequence[bt.Beat],
        length: rd.Length,
    ) -> Job | None:
        """The prose stage's try failed. After a draft that passed but graded weak, a revision
        that fails its checks (cut off, declined, unreadable, empty) leaves that draft
        standing, checked again against the names as they are now (the DM may have made one
        secret since); otherwise the usual retry, and the last try's failure fails the job. A
        service error or running out of credit during the revision still fails the job."""
        key = (gid, cid, job.session_at)
        kept = self._fallback.pop(key, None)
        if kept is not None:
            prose, grade, kept_event = kept
            transcript = "\n".join(s.text for s in self._said(inputs, set(job.speakers)))
            prose, refused = rd.respelled(
                prose,
                beats=sheet,
                allowed_names=inputs.allowed_names,
                secret_names=inputs.secret_names,
                previous=inputs.previous,
                setting=inputs.setting,
                transcript=transcript,
                aliases=inputs.aliases,
            )
            # (a kept draft whose flagged ending was already cut stands without it: #2741)
            if not hard_after_cut(
                rd.check_prose(
                    prose,
                    beats=sheet,
                    transcript=transcript,
                    allowed_names=inputs.allowed_names,
                    secret_names=inputs.secret_names,
                    **self._given_args(gid, cid, job),
                    previous=inputs.previous,
                    setting=inputs.setting,
                    length=length,
                    last=True,
                    refused=refused,
                    style=inputs.style,
                    corrections=inputs.corrections,
                    pcs=inputs.pcs,
                    identity_ties=inputs.identity_ties,
                )
            ):
                flags = self._flags.pop(key, None)
                self._revised.discard(key)
                if flags is not None:  # the kept draft still has its flagged sentences: cut them
                    cut, why = await self._cut(
                        prose,
                        flags,
                        sheet,
                        inputs,
                        transcript,
                        length,
                        given=self._given.get((gid, cid, job.session_at)),
                    )
                    if cut is None:
                        return await self._fail(gid, cid, job, UNMATCHED, check=why, ours=True)
                    self._tell_if_unended(key, cut, sheet)
                    prose = cut
                    try:
                        grade = await self._grade(gid, cid, job, sheet, prose.summary)
                    except AIOutOfFunds:
                        return await self._fail(gid, cid, job, FUNDS)
                    if grade:
                        grade["faithful"] = flags.score()
                if self._on_problems is not None:
                    self._on_problems(job.state, job.attempt, problems)
                log.info(
                    "Recap: the revision failed %d check(s) (%s); the first draft stands",
                    len(problems),
                    ", ".join(cl.kinds(problems)),
                )
                if self._on_note is not None:
                    weak = ", ".join(rd.weak(grade)) or "a score"
                    self._on_note(
                        "The revised recap failed a check, so the first draft stands: it passed "
                        "every check, and "
                        + (
                            "the fact check flagged sentences in it (cut)."
                            if flags is not None
                            else f"the grader scored it below the bar ({weak})."
                        )
                    )
                return await self._accept_prose(
                    gid, cid, job, key, prose, grade, inputs, sheet, transcript, kept_event,
                    "kept after a failed revision",
                )  # fmt: skip
        return await self._retry_or_fail(gid, cid, job, problems, last_attempt=PROSE_LAST_ATTEMPT)

    async def _accept_prose(
        self,
        gid: int,
        cid: str,
        job: Job,
        key: tuple[int, str, int],
        prose: rd.Prose,
        grade: dict[str, int],
        inputs: Inputs,
        sheet: Sequence[bt.Beat],
        transcript: str,
        event: cl.CallEvent | None,
        outcome: str,
    ) -> Job | None:
        """The prose stage's draft is the recap's: store it and go on to the next stage.
        `event`, `outcome`: which call made it and how it came to stand (the test report)."""
        if event is not None:
            event.outcome = outcome
        self._rejected_prose.pop(key, None)
        told = self._dm_notes.pop(key, [])
        extra: dict[str, Any] = {}
        if told:  # kept with the critics' own notes, which the page already shows the DM
            loop = cr.loop_from_json(job.critics)
            extra["critics"] = replace(
                loop, notes=tuple(dict.fromkeys([*loop.notes, *told]))
            ).to_json()
        log.info("Recap: graded %s", ",".join(f"{k}={v}" for k, v in sorted(grade.items())))
        await self._store.update(
            gid,
            cid,
            job.session_at,
            state="prose",
            summary=prose.summary,
            original=prose.summary,
            music=list(prose.music),
            to_confirm=rd.kept_names(
                prose.summary,
                beats=sheet,
                transcript=transcript,
                allowed_names=inputs.allowed_names,
                secret_names=inputs.secret_names,
                previous=inputs.previous,
                setting=inputs.setting,
            )[: un.MAX_TO_CONFIRM],
            game_day=inputs.day,
            grade=grade,
            attempt=0,
            errors=0,
            problems=[],
            last_error="",
            **extra,
        )
        return await self._store.get(gid, cid, job.session_at)

    async def _facts(
        self,
        gid: int,
        cid: str,
        job: Job,
        shown: Sequence[bt.Beat],
        prose: rd.Prose,
        names: Collection[str] = (),
        corrections: Sequence[str] = (),
        free: bool = False,
        consenting: Consenting | None = None,
    ) -> tuple[rd.Facts | None, bool]:
        """The fact check's flags with the compass directions found in code added (#2067): the
        rule is checked in code, not only asked of the AI, so it holds when the AI's check fails.
        The second value says whether the AI's check was had at all (a revision without it is not
        shipped unchecked, whatever the code found)."""
        key = (gid, cid, job.session_at, "facts")
        before = self._last.get(key)
        asked = await self._ask_facts(gid, cid, job, shown, prose, corrections, free, consenting)
        facts = rd.with_directions(asked, prose.summary, names)
        if facts is not asked and facts is not None and self._last.get(key) is not before:
            self._annotate(gid, cid, job, "facts", facts=facts.counts())  # (this try's own event)
        return facts, asked is not None

    async def _ask_facts(
        self,
        gid: int,
        cid: str,
        job: Job,
        shown: Sequence[bt.Beat],
        prose: rd.Prose,
        corrections: Sequence[str] = (),
        free: bool = False,
        consenting: Consenting | None = None,
    ) -> rd.Facts | None:
        """The fact check (#2064): the careful tier reads the numbered sentences against the
        beats the writer was shown and answers with numbers and fixed kinds, never text. None when
        it cannot be had (a failed or unreadable call): like the grader, it never stops a recap
        that passed every check. `free`: a try of a recap that waits for its check, off the job's
        calls and its ceiling (#2370)."""
        step = 0 if free else 1
        fresh = await self._store.get(gid, cid, job.session_at) or job
        if not free and fresh.calls >= self._ceiling(fresh) - 1:  # the last is the title's
            return None
        await self._store.update(
            gid, cid, job.session_at, calls=fresh.calls + step, spent=fresh.spent + 1
        )
        # Room for a number pair for every sentence, not a fixed 800: a long recap's answer was
        # cut off, unreadable, and the check then skipped (#2345). A cut-off answer is asked for
        # once more with twice the room.
        room = facts_room(len(rd.sentence_spans(prose.summary)))
        reply: Reply | None = None
        for again in (False, True):
            if again:
                fresh = await self._store.get(gid, cid, job.session_at) or job
                if not free and fresh.calls >= self._ceiling(fresh) - 1:
                    break  # (the room reported stays the one the cut answer had)
                room = min(room * 2, FACTS_ROOM_MAX)
                await self._store.update(
                    gid, cid, job.session_at, calls=fresh.calls + step, spent=fresh.spent + 1
                )
            try:
                heard = self._heard.get((gid, cid, job.session_at), "")
                # The transcript goes to the AI again here, so consent is read right before it (a
                # player may have stopped since the writer saw it): nothing is sent, and the
                # caller's own check then starts the recap over.
                if (
                    heard
                    and consenting is not None
                    and not set(job.speakers) <= set(await consenting())
                ):
                    self._heard.pop((gid, cid, job.session_at), None)
                    return None
                given = self._given.get((gid, cid, job.session_at), rd.Given())
                reply = await self._careful.complete(
                    rd.FACTS_SYSTEM,
                    rd.facts_message(
                        shown,
                        prose.summary,
                        corrections,
                        heard,
                        who_and_what=given.who_and_what,
                        story=given.story,
                    ),
                    max_tokens=room,
                    schema=rd.facts_schema(),
                )
            except AIOutOfFunds:
                raise
            except Exception as exc:
                log.warning("Recap: the fact check failed (%s)", type(exc).__name__)
                return None
            if not reply.cut:
                break
            log.info("Recap: the fact check was cut off at %d tokens", room)
        assert reply is not None
        facts = None
        if not unusable(reply):
            facts = rd.parse_facts(
                reply.text, len(rd.sentence_spans(prose.summary)), {b.number for b in shown}
            )
        self._announce(gid, cid, job, self._careful, room, reply, stage="facts")
        self._annotate(
            gid, cid, job, "facts",
            checks=(["cut off" if reply.cut else "shape"] if facts is None else []),
            facts=facts.counts() if facts is not None else {},
        )  # fmt: skip
        return facts

    def _tell_if_unended(
        self, key: tuple[int, str, int], cut: rd.Prose, sheet: Sequence[bt.Beat]
    ) -> None:
        """The DM is told when a cut took the ending off the draft that stands (#2741): it is kept,
        and the page says to check it."""
        narration = narration_of(cut.summary)
        if sheet and not rd.ends_on(narration, sheet[-2:]):
            self._dm_notes.setdefault(key, []).append(ENDING_CUT_NOTE)

    async def _cut(
        self,
        prose: rd.Prose,
        facts: rd.Facts,
        sheet: Sequence[bt.Beat],
        inputs: Inputs,
        transcript: str,
        length: rd.Length,
        *,
        keep_unended: bool = True,
        given: rd.Given | None = None,
    ) -> tuple[rd.Prose | None, str]:
        """The draft without the sentences still flagged, done in code, and ""; or None and the
        kinds of check the rest still fails (a fixed word each, never the text), so a failure says
        why. A few flagged sentences cost the recap those sentences, not the whole recap (#2741):
        what is left is held to every check that is about the words (a secret, a name that is not
        allowed, a copy, quotation marks, an empty recap, the layout) but not to the one the cut
        itself causes, ending where the session stopped, because the flagged sentence may well
        have been the ending (logged, and noted in a test report). `keep_unended=False`: a
        revision that lost its ending is still refused, so a better draft that has one stands."""
        cut = rd.cut_sentences(prose, [n for n, _ in facts.unsupported])
        if self._on_note is not None and facts.unsupported:
            self._on_note(
                f"Cut {len(facts.unsupported)} sentence(s) the fact check still flagged "
                "after the one revision."
            )
        problems = rd.check_prose(
            cut,
            beats=sheet,
            transcript=transcript,
            allowed_names=inputs.allowed_names,
            secret_names=inputs.secret_names,
            who_and_what=(given or rd.Given()).who_and_what,
            story=(given or rd.Given()).story,
            previous=inputs.previous,
            setting=inputs.setting,
            length=length,
            last=True,
            refused=[],
            style=inputs.style,
            corrections=inputs.corrections,
            pcs=inputs.pcs,
            identity_ties=inputs.identity_ties,
        )
        hard = problems if not keep_unended else hard_after_cut(problems)
        if hard:
            return None, ", ".join(cl.kinds(hard))
        if problems:
            log.info("Recap: the fact check cut the sentence that ended the recap; it stands")
            if self._on_note is not None:
                self._on_note(
                    "The recap no longer ends exactly where the session stopped: the sentence "
                    "that did was cut by the fact check."
                )
        return cut, ""

    async def _grade(
        self, gid: int, cid: str, job: Job, sheet: Sequence[bt.Beat], summary: str,
        free: bool = False,
    ) -> dict[str, int]:  # fmt: skip
        """The grader's numbers, or none: a grader that fails never stops a recap that passed
        every check. `free`: a try of a recap that waits, off the job's calls (#2370)."""
        fresh = await self._store.get(gid, cid, job.session_at) or job
        if not free and fresh.calls >= self._ceiling(fresh):
            return {}  # the job's ceiling holds for the grader too
        await self._store.update(
            gid, cid, job.session_at, calls=fresh.calls + (0 if free else 1), spent=fresh.spent + 1
        )
        try:
            reply = await self._fast.complete(
                rd.GRADE_SYSTEM, rd.grade_message(sheet, summary), max_tokens=GRADE_TOKENS
            )
        except AIOutOfFunds:
            raise  # the next stage would only hit it too
        except Exception as exc:
            log.warning("Recap: the grader failed (%s)", type(exc).__name__)
            return {}
        grade = rd.parse_grade(reply.text) or {}
        self._announce(gid, cid, job, self._fast, GRADE_TOKENS, reply, stage="grade", grade=grade)
        return grade

    # --- stage 2b: the critics, until they agree (#2255) ------------------------------

    async def _critique(
        self, gid: int, cid: str, job: Job, inputs: Inputs, consenting: Consenting
    ) -> Job | None:
        """One step of the critics loop on the draft the prose stage accepted. The draft is read
        (the motive check, then PlotBot, and the grader's marks it already has); if every critic
        passes the loop is done; if not, the writer revises with all their changes at once and the
        revision is read in its turn, at most `cr.MAX_ROUNDS` readings in all. Each step is saved
        before the next, so a restart never repays for one that finished. A service that keeps
        failing, or the day's tally within reach, makes a draft not yet read wait for its check
        (`_hold`); one that was read keeps the best reading made. What is never shipped (a motive
        nobody said, a secret PlotBot found) is cut in code at the end, or the job fails as it
        does for any unfixable check."""
        loop = cr.loop_from_json(job.critics)
        today = await self._store.calls_since(gid, cid, int(self._now()) - DAY_S)
        if job.errors >= CRITIC_ERRORS_MAX:
            return await self._stop_critics(gid, cid, job, loop, inputs, STOP_ERRORS)
        # (not for a recap that waits: its tries are off the job's calls, so this ceiling can only
        # be the one it was already at when it began to wait, and waiting never fails it, #2370)
        if loop.waits == 0 and job.calls >= self._ceiling(job) - CRITIC_RESERVE:
            return await self._stop_critics(gid, cid, job, loop, inputs, STOP_CEILING)
        if today >= DAILY_CALLS - CRITIC_RESERVE:  # what the title step needs is kept
            return await self._stop_critics(gid, cid, job, loop, inputs, STOP_TALLY)
        sheet = bt.from_json(job.beats)
        prose = rd.Prose(job.summary, tuple(job.music))
        sentences = rd.sentences_of(prose.summary)
        said = self._said(inputs, set(job.speakers))
        play, retold, _ = self._parts(job, said)
        if loop.pending is None:
            return await self._read(
                gid, cid, job, inputs, consenting, loop, sheet, sentences, play, retold
            )
        if not loop.pending:  # every critic passed
            log.info("Recap: the critics agree after %d reading(s)", loop.round)
            unchecked = (
                () if job.grade else (cr.STYLE_UNCHECKED,)
            )  # (the grader failed on this one)
            # What a reader who was away could still not follow, after the writer's one fix: a
            # note for the DM, never another round (#2680).
            sentences_now, left = self._away(job.summary, sheet, inputs)
            self._count("away_left", len(left))
            return await self._save_loop(
                gid, cid, job,
                replace(
                    loop,
                    done=True,
                    notes=self._told(loop) + unchecked + away.notes(left, sentences_now),
                ),
            )  # fmt: skip
        room = self._ceiling(job) - CRITIC_RESERVE - CRITIC_REVISE_CALLS
        # (a revision that waits for its try is off the job's calls, so the room is not looked at)
        if loop.round >= cr.MAX_ROUNDS or (job.calls > room and loop.waits == 0):
            return await self._stop_critics(gid, cid, job, loop, inputs, "no agreement")
        return await self._revise(
            gid, cid, job, inputs, consenting, loop, sheet, prose, play, retold
        )

    def _count(self, name: str, number: int) -> None:
        """A count for a test run's report (#2738); a callback that fails never fails the recap."""
        if self._on_count is None:
            return
        try:
            self._on_count(name, number)
        except Exception:
            log.exception("Recap: a test run's count hook failed")

    @staticmethod
    def _told(loop: cr.Loop) -> tuple[str, ...]:
        """The notes `_accept_prose` told the DM about the draft that stands (the last lines cut,
        no fact check), which the critics' own notes must not replace (#2741)."""
        return tuple(n for n in DM_TOLD if n in loop.notes)

    def _away_notes(
        self, summary: str, sheet: Sequence[bt.Beat], inputs: Inputs
    ) -> tuple[str, ...]:
        """The DM's notes for what a reader who was away could not follow in this draft."""
        sentences, found = self._away(summary, sheet, inputs)
        return away.notes(found, sentences)

    @staticmethod
    def _away(
        summary: str, sheet: Sequence[bt.Beat], inputs: Inputs
    ) -> tuple[list[str], list[away.Finding]]:
        """A draft's sentences and what a reader who was away could not follow in it (#2680).
        Pure and free: no call, so it never touches the job's ceiling or the day's tally."""
        sentences = rd.sentences_of(summary)
        present = sorted({n for b in sheet for n in b.present})
        found = away.findings(
            sentences,
            names=inputs.allowed_names,
            aliases=inputs.aliases,
            previous=inputs.previous,
            present=present,
        )
        return sentences, found

    async def _save_loop(self, gid: int, cid: str, job: Job, loop: cr.Loop) -> Job | None:
        # (errors back to none: a step of the loop got through)
        # A step got through: a "waiting for its check" note is over, and so is the waiting,
        # whichever step it was (#2370); only `_hold` counts a wait.
        loop = replace(loop, waits=0, since=0, told=False)
        await self._store.update(
            gid, cid, job.session_at, critics=loop.to_json(), errors=0, last_error=""
        )
        return await self._store.get(gid, cid, job.session_at)

    async def _read(
        self,
        gid: int,
        cid: str,
        job: Job,
        inputs: Inputs,
        consenting: Consenting,
        loop: cr.Loop,
        sheet: Sequence[bt.Beat],
        sentences: Sequence[str],
        play: Sequence[Said],
        retold: str,
    ) -> Job | None:
        """The critics read the draft: the motive check, then PlotBot, and the style marks the
        draft was graded with. The motive check's result is saved before PlotBot is asked, so a
        PlotBot call that fails does not make the next try pay for it again."""
        lines = {s.number: s.text for s in play}
        waiting = loop.waits > 0  # a try of a recap that waits: its own small budget of calls
        if loop.motives is None:
            asked = mo.candidates(sentences)
            found: list[cr.Change] = []
            batches = mo.batches(asked)
            # (a waiting try's budget covers its batches and PlotBot: `cr.wait_budget`)
            for batch in batches:
                raw = await self._call(
                    gid, cid, job, self._fast, mo.SYSTEM, mo.message(batch, lines, inputs.previous),
                    mo.answer_tokens(len(batch)), schema=mo.SCHEMA, stage="motives", soft=True,
                    free=waiting,
                )  # fmt: skip
                if raw is None:
                    return await self._store.get(gid, cid, job.session_at)
                if not set(job.speakers) <= set(await consenting()):  # again, after the answer
                    return await self._restart(gid, cid, job)
                answer = None if unusable(raw) else mo.parse(raw.text)
                if answer is None or not mo.complete(answer, batch):
                    # neither agreement nor a finding (not readable, or a sentence left out)
                    return await self._unreadable(gid, cid, job, "motives")
                bad = mo.failures(batch, answer, lines, inputs.previous)
                found += [cr.Change("motive", mo.instruction(f), True, f.number) for f in bad]
            self._annotate(gid, cid, job, "motives", checks=["motive"] if found else [])
            # A provisional best for the first draft only, right away, so that if PlotBot cannot be
            # reached a motive nobody said is still cut (or fails the job) and never ships. A later
            # draft that PlotBot has not read never takes the place of one that it has.
            first = cr.best_from(job.summary, job.music, job.grade, found)
            loop = replace(
                loop,
                motives=tuple(found),
                best=first if loop.best is None else loop.best,
            )
            await self._save_loop(gid, cid, job, loop)
            job = await self._store.get(gid, cid, job.session_at) or job
        plot: list[cr.Change] = []
        memory: list[sg.Memory] = []
        if self._story is not None:
            seeds = sorted({n for b in sheet for n in (*b.who, b.where) if n})[:40]
            try:
                memory = list(await self._story(gid, cid, job, seeds))
            except Exception as exc:  # optional context: the type, never the text
                log.warning("Recap: couldn't read the story memory (%s)", type(exc).__name__)
        if sg.worth_asking(memory, inputs.previous):
            room = plot_room(len(sentences))
            message = cr.plot_message(
                sheet, memory, inputs.previous, sentences, self._given_args(gid, cid, job)["story"]
            )
            raw = await self._call(
                gid, cid, job, self._careful, cr.plot_system(), message,
                room, schema=cr.PLOT_SCHEMA, stage="plotbot", soft=True, free=waiting,
            )  # fmt: skip
            if raw is not None and raw.cut and not raw.refused:
                # Cut off at the limit: asked once more with twice the room (#2365), as the fact
                # check is (#2345). A second cut ends as any unreadable answer does: it waits.
                # The retry is one more call, so the job's ceiling is looked at again first (the
                # check at the top of the step is before the first call): a recap that waits for
                # its check is off the job's calls, one that does not must keep the reserve.
                fresh = await self._store.get(gid, cid, job.session_at) or job
                if waiting or fresh.calls < self._ceiling(fresh) - CRITIC_RESERVE:
                    log.info("Recap: PlotBot's answer was cut off at %d tokens", room)
                    room *= 2  # (`plot_room` is at most half of PLOT_ROOM_MAX)
                    raw = await self._call(
                        gid, cid, job, self._careful, cr.plot_system(), message,
                        room, schema=cr.PLOT_SCHEMA, stage="plotbot", soft=True, free=waiting,
                    )  # fmt: skip
            if raw is None:
                return await self._store.get(gid, cid, job.session_at)
            if not set(job.speakers) <= set(await consenting()):
                return await self._restart(gid, cid, job)
            parsed = None if unusable(raw) else cr.parse_plot(raw.text)
            if parsed is None:  # an answer that cannot be read is not "nothing to change"
                # Said as what it was, so the report shows the cause: declined, cut off, or an
                # answer in the wrong shape (#2365).
                kind = "declined" if raw.refused else "cut off" if raw.cut else "shape"
                self._annotate(gid, cid, job, "plotbot", checks=[kind])
                return await self._unreadable(gid, cid, job, "plotbot")
            if parsed is not None:
                vocabulary = sg.known_words(
                    sheet, memory, inputs.previous, [s.text for s in play], retold
                )
                plot, cut = cr.plot_changes(
                    parsed,
                    sentences=sentences,
                    memory=memory,
                    has_recap=bool(inputs.previous.strip()),
                    known_words=vocabulary,
                    allowed_names=inputs.allowed_names,
                    secret_names=inputs.secret_names,
                )
                self._annotate(gid, cid, job, "plotbot", checks=lambda: cl.kinds(cut))
        style = cr.style_changes(rd.weak(job.grade)) if job.grade else []
        # A reader who was away (#2680): the writer is asked once, on the first reading; whatever
        # is left after its fix is a note for the DM (`_away` again at the end), not another round.
        asked_away: list[cr.Change] = []
        if loop.round == 0:
            sentences_now, found_away = self._away(job.summary, sheet, inputs)
            asked_away = away.changes(found_away, sentences_now)
            log.info(
                "Recap: the away-reader check found %d: %s",
                len(found_away), sorted({f.kind for f in found_away}),
            )  # fmt: skip
            self._count("away_found", len(found_away))
        changes = [*(loop.motives or ()), *plot, *style, *asked_away]
        hard, soft = cr.count(changes)
        log.info("Recap: critics reading %d: %d hard, %d soft", loop.round + 1, hard, soft)
        best = cr.best_from(job.summary, job.music, job.grade, changes)
        # (the same draft's provisional best, from the motive check alone, is replaced by this
        # fuller one; any other draft's is kept only if it failed less)
        same = loop.best is not None and loop.best.summary == job.summary
        return await self._save_loop(
            gid,
            cid,
            job,
            replace(
                loop,
                round=loop.round + 1,
                pending=tuple(changes),
                motives=None,
                best=best if same or best.better_than(loop.best) else loop.best,
            ),
        )

    async def _revise(
        self,
        gid: int,
        cid: str,
        job: Job,
        inputs: Inputs,
        consenting: Consenting,
        loop: cr.Loop,
        sheet: Sequence[bt.Beat],
        prose: rd.Prose,
        play: Sequence[Said],
        retold: str,
    ) -> Job | None:
        """The writer revises the draft with all the critics' changes at once. The revision has to
        pass the same checks as any draft (code, then the fact check; a flagged sentence is cut,
        and one that still fails the checks is no draft), and is graded again. One that does not
        pass is not used: the loop stops and the best draft so far goes on."""
        key = (gid, cid, job.session_at)
        assert loop.pending
        length = rd.length_for(rd.session_hours(play, inputs.hours))
        shown = rd.pick_beats(sheet, length.beats, importance=inputs.importance)
        # the revision reads what the first draft read (#2954), so it is not a worse writer
        heard, given = await self._context_for(gid, cid, job, inputs, play, shown)
        self._given[key] = given
        if heard:
            self._heard[key] = heard
        else:
            self._heard.pop(key, None)
        message = rd.prose_message(
            shown,
            inputs.previous,
            [n for n in inputs.allowed_names if n not in inputs.aliases],  # (#2573)
            inputs.day,
            retold,
            ending=sheet,
            transcript=heard,
            apart=rd.apart_pairs(inputs.relation_ties, inputs.allowed_names),
            familiar=inputs.familiar,
            who_and_what=given.who_and_what,
            threads=given.threads,
            story=given.story,
        ) + rd.prose_retry(cr.writer_notes(loop.pending), prose, length, plain=self._two)
        attempt = 1  # the careful writer's room, as for a retry
        kept = self._written.pop(key, None)  # the writer's text, if only the formatter failed
        raw = None if kept is not None else await self._call(
            gid, cid, job,
            self._creative if self._creative is not None and self._two else self._careful,
            rd.prose_system(
                inputs.setting, length, plain=self._two, style=inputs.style,
                notes=inputs.notes, corrections=inputs.corrections,
            ),
            message,
            more_room(rd.write_room(length) if self._two else length.max_tokens, attempt),
            schema=None if self._two else rd.prose_schema(),
            stage="revise",
            soft=True,
            free=loop.waits > 0,  # a try of a recap that waits has its own budget (#2370)
        )  # fmt: skip
        if raw is None and kept is None:
            return await self._store.get(gid, cid, job.session_at)
        if not set(job.speakers) <= set(await consenting()):
            return await self._restart(gid, cid, job)
        revised: rd.Prose | None = None
        unavailable = kept is None and raw is not None and bool(unusable(raw))  # cut or declined
        if kept is not None or (raw is not None and not unusable(raw)):
            written = kept if kept is not None else (raw.text if raw is not None else "")
            if self._two:
                shaped = await self._shape(
                    gid, cid, job, written, length, soft=True, free=loop.waits > 0
                )
                if shaped == "":  # the formatter's call failed; the loop will try again
                    self._written[key] = written  # without paying for the writing twice
                    return await self._store.get(gid, cid, job.session_at)
                revised = None if isinstance(shaped, str) else shaped
            else:
                revised = rd.parse_prose(written)
        transcript = "\n".join(s.text for s in self._said(inputs, set(job.speakers)))
        grade: dict[str, int] = {}
        if revised is not None:
            revised, refused = rd.respelled(
                rd.fix_prose(revised, length.sections),
                beats=sheet,
                allowed_names=inputs.allowed_names,
                secret_names=inputs.secret_names,
                previous=inputs.previous,
                setting=inputs.setting,
                transcript=transcript,
                aliases=inputs.aliases,
            )
            if rd.check_prose(
                revised, beats=sheet, transcript=transcript,
                allowed_names=inputs.allowed_names, secret_names=inputs.secret_names,
**self._given_args(gid, cid, job),
                previous=inputs.previous, setting=inputs.setting, length=length, last=True,
                refused=refused, style=inputs.style, corrections=inputs.corrections,
                pcs=inputs.pcs,
                identity_ties=inputs.identity_ties,
            ):  # fmt: skip
                revised = None
        if revised is not None:
            try:
                facts, asked = await self._facts(
                    gid, cid, job,
                    rd.pick_beats(sheet, length.beats, importance=inputs.importance), revised,
                    {*inputs.allowed_names, *(w for b in sheet for w in b.who)},
                    inputs.corrections, loop.waits > 0, consenting,
                )  # fmt: skip
            except AIOutOfFunds:
                return await self._fail(gid, cid, job, FUNDS)
            if not asked:  # a revision that could not be checked is not shipped
                revised = None
                unavailable = True
            elif facts is not None and not facts.clean:
                revised, _ = await self._cut(
                    revised,
                    facts,
                    sheet,
                    inputs,
                    transcript,
                    length,
                    keep_unended=False,
                    given=given,
                )
            if revised is not None:
                try:
                    grade = await self._grade(gid, cid, job, sheet, revised.summary, loop.waits > 0)
                except AIOutOfFunds:
                    return await self._fail(gid, cid, job, FUNDS)
                if facts is not None and grade:
                    grade["faithful"] = facts.score()
        if not set(job.speakers) <= set(await consenting()):  # after the last of the AI calls
            return await self._restart(gid, cid, job)
        if revised is None:
            log.info("Recap: a critics revision did not pass the checks; the best draft stands")
            why = STOP_REVISION if unavailable else "revision failed"
            return await self._stop_critics(gid, cid, job, loop, inputs, why)
        await self._store.update(
            gid, cid, job.session_at,
            summary=revised.summary, original=revised.summary, music=list(revised.music),
            grade=grade, errors=0,
            to_confirm=self._to_confirm(revised.summary, sheet, transcript, inputs),
            critics=replace(
                loop, pending=None, motives=None, waits=0, since=0, told=False,
                # (the new text passed its own ending check and was fact-checked: those notes were
                # about the text it replaces)
                notes=tuple(n for n in loop.notes if n not in DM_TOLD),
            ).to_json(),
        )  # fmt: skip
        self._forget_draft(key)
        return await self._store.get(gid, cid, job.session_at)

    @staticmethod
    def _to_confirm(
        summary: str, sheet: Sequence[bt.Beat], transcript: str, inputs: Inputs
    ) -> list[str]:
        return rd.kept_names(
            summary,
            beats=sheet,
            transcript=transcript,
            allowed_names=inputs.allowed_names,
            secret_names=inputs.secret_names,
            previous=inputs.previous,
            setting=inputs.setting,
        )[: un.MAX_TO_CONFIRM]

    @staticmethod
    def _tie_notes(job: Job, finish: rd.Finish, inputs: Inputs) -> dict[str, Any]:
        """The DM's notes on sentences that name both ends of a 🤫 relation (#2591), put with the
        critics' soft notes, which only the DM's page shows. Nothing when there are none."""
        found = rd.tie_notes(
            "\n".join([strip_cues(job.summary), finish.title, *finish.housekeeping]),
            inputs.relation_ties,
        )
        if not found:
            return {}
        loop = cr.loop_from_json(job.critics)
        return {"critics": replace(loop, notes=(*dict.fromkeys((*loop.notes, *found)),)).to_json()}

    def _forget_draft(self, key: tuple[int, str, int]) -> None:
        self._rejected_prose.pop(key, None)
        self._fallback.pop(key, None)
        self._flags.pop(key, None)
        self._revised.discard(key)
        self._written.pop(key, None)

    async def _hold(self, gid: int, cid: str, job: Job, loop: cr.Loop, why: str) -> Job | None:
        """No draft could be read (the service, or the day's tally): the recap waits for its check
        and is never cut, shipped unchecked or failed for waiting. The wait grows (15 minutes, an
        hour, 4 hours, then every 12 hours; minutes when the check failed on our side, #2596) and
        its tries have a small budget of their own (`cr.wait_budget`: a reading's batches and
        PlotBot's two calls, or a revision's `cr.WAIT_REVISE_CALLS`; kept out of the job's calls
        and its ceiling). After 3 days the DM is told once that DMbot hasn't been able to finish
        checking it and keeps trying; players never see an unchecked draft."""
        now = int(self._now())
        since = loop.since or now
        # A cut or unreadable answer, or the service erroring, is ours to retry within minutes
        # (#2596); only the day's tally is a rolling day, and waits as long as it needs.
        delay = cr.wait_for(loop.waits, soon=why != STOP_TALLY)
        if why == STOP_TALLY:
            delay = max(cr.wait_for(loop.waits), CHECK_TALLY_RETRY_S)
        told = loop.told
        if not told and now - since >= cr.UNCHECKED_AFTER_S:
            told = True
            if self._unchecked is not None:
                try:
                    await self._unchecked(gid, cid, job)
                except Exception:
                    log.exception("Recap: the unchecked notice failed")
        log.info(
            "Recap: the check waits (%s), wait %d; tried again in %d s", why, loop.waits, delay
        )
        await self._store.update(
            gid, cid, job.session_at,
            critics=replace(loop, waits=loop.waits + 1, since=since, told=told).to_json(),
            errors=0, next_at=now + delay, last_error=CHECK_WAIT,
        )  # fmt: skip
        return await self._store.get(gid, cid, job.session_at)

    async def _stop_critics(
        self, gid: int, cid: str, job: Job, loop: cr.Loop, inputs: Inputs, why: str
    ) -> Job | None:
        """The loop ends without agreement (or could not go on): the best draft read goes on, and
        what its soft critics still asked is kept for the DM's recap page. A hard change in it is
        never shipped: those sentences are cut in code, and if what is left no longer passes the
        checks, or the change was not about one sentence, the job fails as any unfixable check
        does. A draft the critics never got to read (the service failed twice before the motive
        check ran, or a ceiling or the day's tally was reached) is never shipped unchecked and
        never cut by a word list: the recap waits for its check (`CHECK_WAIT`) and it is tried
        again, or it fails when the job's own ceiling is the reason."""
        self._written.pop((gid, cid, job.session_at), None)  # (a writer's text is not kept on)
        best = loop.best
        sheet = bt.from_json(job.beats)
        said = self._said(inputs, set(job.speakers))
        length = rd.length_for(rd.session_hours(self._parts(job, said)[0], inputs.hours))
        transcript = "\n".join(s.text for s in said)
        if best is None:
            # No draft could be read. A word list cannot tell a player character's aim from story
            # (an NPC's request is often the cliffhanger), so nothing is cut and nothing ships
            # unchecked: the recap waits and the check is tried again, or fails when the job's own
            # ceiling is the reason (waiting would never change it).
            if why == STOP_CEILING:
                return await self._fail(gid, cid, job, CAP)
            return await self._hold(gid, cid, job, loop, why)
        prose = rd.Prose(best.summary, best.music)
        cut = 0
        # What the DM was told about the draft that stands holds only while the critics' best draft
        # is that same text (a different one was fact-checked and ended on its own).
        told = self._told(loop) if best.summary == job.summary else ()
        # The critics stopped because the service did not answer (or the day's tally): that is ours,
        # so what would fail the recap waits for its check instead, and is tried again (#2365). Only
        # the job's own ceiling still ends in a failure.
        ours = why in (STOP_ERRORS, STOP_TALLY, STOP_REVISION)
        if best.hard:
            if best.unfixable:
                if ours:
                    return await self._hold(gid, cid, job, loop, why)
                return await self._fail(
                    gid, cid, job, FAILED_CHECKS, check="unfixable", ours=why != STOP_CEILING
                )
            prose = rd.cut_sentences(prose, best.cuts)
            problems = rd.check_prose(
                prose, beats=sheet, transcript=transcript,
                allowed_names=inputs.allowed_names, secret_names=inputs.secret_names,
**self._given_args(gid, cid, job),
                previous=inputs.previous, setting=inputs.setting, length=length, last=True,
                refused=[], style=inputs.style, corrections=inputs.corrections,
                pcs=inputs.pcs,
                identity_ties=inputs.identity_ties,
            )  # fmt: skip
            if problems and not hard_after_cut(problems) and ENDING_CUT_NOTE not in told:
                told += (ENDING_CUT_NOTE,)  # the cut took the ending off: say so
            problems = hard_after_cut(problems)  # (the cut sentence may have been the ending)
            if problems:
                # The rule that failed, by name, so a run that ends here says why (#2365).
                log.info(
                    "Recap: after the critics stopped (%s) the draft fails: %s",
                    why,
                    cl.kinds(problems),
                )
                if ours:
                    return await self._hold(gid, cid, job, loop, why)
                # A failed check is our side's (the writing), as in the prose stage (#2341): only
                # the job's own ceiling is not, and the rule names go in the reason.
                return await self._fail(
                    gid, cid, job, FAILED_CHECKS,
                    check=", ".join(cl.kinds(problems)), ours=why != STOP_CEILING,
                )  # fmt: skip
            cut = len(best.cuts)
        # (the away notes are read again from the text that is kept, cuts and all)
        away_left = self._away_notes(prose.summary, sheet, inputs)
        self._count("away_left", len(away_left))
        kept_notes = (
            told + tuple(n for n in best.notes if not away.is_note(n) and n not in told) + away_left
        )
        log.info(
            "Recap: the critics stopped (%s) after %d reading(s): %d note(s) left, %d cut",
            why, loop.round, len(kept_notes), cut,
        )  # fmt: skip
        await self._store.update(
            gid, cid, job.session_at,
            summary=prose.summary, original=prose.summary, music=list(prose.music),
            grade=best.grade or job.grade, errors=0,
            to_confirm=self._to_confirm(prose.summary, sheet, transcript, inputs),
            critics=replace(
                loop,
                done=True,
                pending=None,
                motives=None,
                waits=0,
                since=0,
                told=False,
                notes=kept_notes
                + (() if best.grade or job.grade else (cr.STYLE_UNCHECKED,)),
                cut=cut,
            ).to_json(),
        )  # fmt: skip
        return await self._store.get(gid, cid, job.session_at)

    # --- stage 3: the title and the housekeeping ---------------------------------------

    async def _finish(
        self, gid: int, cid: str, job: Job, inputs: Inputs, consenting: Consenting
    ) -> Job | None:
        sheet = bt.from_json(job.beats)
        key = (gid, cid, job.session_at)
        # The table's own catching up before play goes to the housekeeping alone (#2253).
        catchup = self._parts(job, self._said(inputs, set(job.speakers)))[2]
        message = rd.housekeeping_message(sheet, job.summary, catchup)
        if job.problems:
            message += rd.finish_retry(job.problems, self._rejected_finish.get(key))
        raw = await self._call(
            gid,
            cid,
            job,
            self._client(job),
            rd.housekeeping_system(),
            message,
            more_room(FINISH_TOKENS, job.attempt),
            schema=rd.finish_schema(),
        )
        if raw is None:
            return await self._store.get(gid, cid, job.session_at)
        if not set(job.speakers) <= set(await consenting()):
            return await self._restart(gid, cid, job)
        if why := unusable(raw):
            self._annotate(gid, cid, job, "title", checks=cl.kinds([why]))
            return await self._retry_or_fail(gid, cid, job, [why])
        parsed = rd.parse_finish(raw.text)
        if parsed is None:
            self._annotate(gid, cid, job, "title", checks=["shape"])
            return await self._retry_or_fail(
                gid, cid, job, ["answer with the title and housekeeping JSON asked for"]
            )
        transcript = "\n".join(x.text for x in self._said(inputs, set(job.speakers)))
        # Aliases put back to their names here too, and what is too like a secret refused (#2591).
        fixed = [
            rd.respelled(
                rd.Prose(text, ()),
                beats=sheet,
                allowed_names=inputs.allowed_names,
                secret_names=inputs.secret_names,
                previous=inputs.previous,
                transcript=transcript,
                aliases=inputs.aliases,
            )
            for text in (parsed[0], *parsed[1])
        ]
        parsed = (fixed[0][0].summary, [f.summary for f, _ in fixed[1:]])
        title_names: dict[str, int] = {}
        finish, problems = rd.check_finish(
            parsed,
            beats=sheet,
            summary=job.summary,
            allowed_names=inputs.allowed_names,
            secret_names=inputs.secret_names,
            previous=inputs.previous,
            transcript=transcript,
            name_counts=title_names,
            pcs=inputs.pcs,
            aliases=inputs.aliases,
        )
        if title_names:
            log.info("Recap: names outside the beat sheet, by kind: %s", _kinds_line(title_names))
            self._annotate(gid, cid, job, "title", names=dict(title_names))
        if finish is not None and any(r for _, r in fixed):
            finish, problems = None, ["a name is too like one the DM keeps secret: leave it out"]
        self._annotate(
            gid, cid, job, "title", checks=lambda: cl.kinds(problems) if finish is None else []
        )
        if finish is None:
            self._keep_rejected(
                key, parsed, inputs, [parsed[0], *parsed[1]], near_secret=any(r for _, r in fixed)
            )
            return await self._retry_or_fail(gid, cid, job, problems)
        self._rejected_finish.pop(key, None)
        await self._store.update(
            gid,
            cid,
            job.session_at,
            state="ready",
            # The fixed opening and closing lines go on last (#2253), after every check, so they
            # are never counted or checked as story; the stored original has them too, so the
            # DM's edit is compared with the text they were shown.
            summary=(
                wrapped := fixed_lines.wrap(
                    job.summary, inputs.campaign_name, inputs.secret_names, inputs.ending
                )
            ),
            original=wrapped,
            title=finish.title,
            housekeeping=list(finish.housekeeping),
            to_confirm=un.to_confirm(
                job.to_confirm,
                rd.kept_names(
                    " ".join([finish.title, *finish.housekeeping]),
                    beats=sheet,
                    transcript=transcript,
                    allowed_names=inputs.allowed_names,
                    secret_names=inputs.secret_names,
                    previous=inputs.previous,
                ),
            ),
            attempt=0,
            errors=0,
            problems=[],
            last_error="",
            **self._tie_notes(job, finish, inputs),
        )
        self._forget_reads(key)
        log.info("Recap: ready after %d call(s)", job.calls)
        # No audio is queued here (#2083): the DM reads the text, fixes its cues, approves it and
        # picks what to make; the approval queues the audio.
        return await self._store.get(gid, cid, job.session_at)

    # --- ways out -----------------------------------------------------------------------

    async def _retry_or_fail(
        self,
        gid: int,
        cid: str,
        job: Job,
        problems: Sequence[str],
        *,
        last_attempt: int = 1,
        cut_off: bool = False,
    ) -> Job | None:
        """A check failed: once more on the careful tier (the prose stage twice more), then the
        job fails plainly."""
        if self._on_problems is not None:  # the live job keeps only the first try's problems
            self._on_problems(job.state, job.attempt, problems)
        if job.attempt >= last_attempt:
            # The check's fixed name goes in the reason and the log (names only, never the text),
            # so the recap page and the server's owner can say why (#2341).
            named = ", ".join(cl.kinds(problems))
            log.info("Recap: failed checks at %s: %s", job.state, named)
            return await self._fail(
                gid,
                cid,
                job,
                CUT_OFF if cut_off else FAILED_CHECKS,
                error=named,
                check=named,
                ours=True,
            )
        await self._store.update(
            gid, cid, job.session_at, attempt=job.attempt + 1, problems=list(problems)
        )
        return await self._store.get(gid, cid, job.session_at)

    async def _wait(
        self,
        gid: int,
        cid: str,
        job: Job,
        why: str,
        *,
        calls: int | None = None,
        spent: int | None = None,
        error: str = "",
    ) -> Job | None:
        """Something outside failed: wait, longer each time, and fail on the third."""
        errors = job.errors + 1
        if errors >= len(BACKOFF_S):
            return await self._fail(gid, cid, job, why, error=error, ours=why == SERVICE)
        extra = {} if calls is None else {"calls": calls, "spent": spent}
        await self._store.update(
            gid,
            cid,
            job.session_at,
            errors=errors,
            next_at=int(self._now()) + BACKOFF_S[errors - 1],
            last_error=why,
            **extra,
        )
        return await self._store.get(gid, cid, job.session_at)

    async def _unreadable(self, gid: int, cid: str, job: Job, stage: str) -> Job | None:
        """A critic's answer could not be read. That is never agreement: it counts as a failed
        call (asked again after a wait, and the loop stops after `CRITIC_ERRORS_MAX`)."""
        fresh = await self._store.get(gid, cid, job.session_at) or job
        await self._soft_wait(gid, cid, fresh, fresh.calls, fresh.spent, f"unreadable {stage}")
        return await self._store.get(gid, cid, job.session_at)

    async def _soft_wait(
        self, gid: int, cid: str, job: Job, calls: int, spent: int, error: str
    ) -> None:
        """A critics-loop call failed: count it and wait, but never fail the job (the loop stops
        by itself after `CRITIC_ERRORS_MAX`: a draft not yet read then waits, `_hold`)."""
        errors = job.errors + 1
        await self._store.update_seen(
            gid,
            cid,
            job.session_at,
            0,  # a call of the critics' loop is moot once the DM approved the draft
            errors=errors,
            next_at=int(self._now()) + BACKOFF_S[min(errors, len(BACKOFF_S)) - 1],
            # (a recap that is waiting for its check keeps saying so between its tries)
            last_error=CHECK_WAIT if cr.loop_from_json(job.critics).waits > 0 else SERVICE,
            calls=calls,
            spent=spent,
        )
        log.info("Recap: a critics call failed (%s), %d so far", error, errors)

    async def _fail(
        self,
        gid: int,
        cid: str,
        job: Job,
        why: str,
        *,
        error: str = "",
        check: str = "",
        ours: bool = False,
    ) -> Job | None:
        log.info("Recap: failed at %s (%s)", job.state, error or "plain reason")
        self._forget(gid, cid, job)
        # A failure on our side (the writing, a check, the service) does not use up one of the
        # DM's Try agains (#2341). Only the callers that say `ours` give it back: a limit, the
        # allowance or a stopped player are the DM's, so the cap on tries still holds.
        if ours and job.tries > 0:
            await self._store.give_back_try(gid, cid, job.session_at)
        reason = f"{why} (check: {check})" if check else why
        await self._store.update_seen(
            gid,
            cid,
            job.session_at,
            # a step that began before the DM approved the draft must not fail it (#2305)
            cr.loop_from_json(job.critics).approved_at,
            state="failed",
            last_error=reason[:200],
        )
        return await self._store.get(gid, cid, job.session_at)

    def _keep_rejected(
        self,
        key: tuple[int, str, int],
        draft: rd.Prose | tuple[str, list[str]],
        inputs: Inputs,
        texts: Sequence[str],
        near_secret: bool = False,
    ) -> None:
        """Keep a rejected draft to send back on the retry (#2026), unless it names a secret, or a
        word too like one (`near_secret`, #2591): the checks keep secret names out of every retry
        message, and so must the draft. The retry then goes without it."""
        if near_secret or names_a_secret(
            " ".join(texts), inputs.secret_names, inputs.allowed_names
        ):
            self._rejected_prose.pop(key, None)
            self._rejected_finish.pop(key, None)
        elif isinstance(draft, rd.Prose):
            self._rejected_prose[key] = draft
        else:
            self._rejected_finish[key] = draft

    def _forget_reads(self, key: tuple[int, str, int]) -> None:
        """What a transcript writer kept for one job: the lines it was shown (for the fact check)
        and the fall-back to the beats. Dropped when the job ends, so nothing outlives it."""
        self._heard.pop(key, None)
        self._given.pop(key, None)
        self._beats_instead.discard(key)

    def _forget(self, gid: int, cid: str, job: Job) -> None:
        self._forget_reads((gid, cid, job.session_at))
        self._rejected_prose.pop((gid, cid, job.session_at), None)
        self._rejected_finish.pop((gid, cid, job.session_at), None)
        self._dm_notes.pop((gid, cid, job.session_at), None)
        self._fallback.pop((gid, cid, job.session_at), None)
        self._flags.pop((gid, cid, job.session_at), None)
        self._revised.discard((gid, cid, job.session_at))
        self._written.pop((gid, cid, job.session_at), None)
        for stage in (
            "split",
            "beats",
            "significance",
            "motives",
            "plotbot",
            "revise",
            "prose",
            "write",
            "format",
            "facts",
            "grade",
            "title",
        ):
            self._last.pop((gid, cid, job.session_at, stage), None)

    async def _restart(self, gid: int, cid: str, job: Job) -> Job | None:
        """A player whose lines were used stopped being recorded: begin again without them, a
        limited number of times."""
        if job.restarts >= MAX_RESTARTS:
            return await self._fail(gid, cid, job, STOPPED)
        log.info("Recap: starting again, a speaker stopped being recorded")
        self._forget(gid, cid, job)
        await self._store.update(
            gid,
            cid,
            job.session_at,
            state="queued",
            beats=[],
            split=None,  # the split is made again from whoever is still recorded
            critics=None,  # and so are the critics' readings of the old draft
            window_done=0,
            window_count=0,
            speakers=[],
            summary="",
            original="",
            music=[],
            housekeeping=[],
            title="",
            attempt=0,
            errors=0,
            problems=[],
            restarts=job.restarts + 1,
        )
        return await self._store.get(gid, cid, job.session_at)
