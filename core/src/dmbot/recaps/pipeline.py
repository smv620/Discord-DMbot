"""The recap job's worker (#1382): takes one queued job and runs its stages, each saved before the
next, so a restart picks up where it stopped.

    queued → beats      the AI reads the cleaned transcript and returns a cited beat sheet
    beats → checked     code cuts every beat that cites no real line or names what its lines do not
    checked → prose     the AI writes the recap from the beat sheet only; code checks it; a cheap
                        grader scores it against a fixed rubric
    prose → housekeeping  the title and the housekeeping bullets, each citing a beat
    housekeeping → ready  (slice 2 adds the audio stage here)

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
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass

from dmbot.ai import AIOutOfFunds
from dmbot.memory.extractor import Client, Said, readable
from dmbot.recaps import beats as bt
from dmbot.recaps import draft as rd
from dmbot.recaps.store import Job, RecapStore
from dmbot.transcript.models import Line

log = logging.getLogger(__name__)

MAX_CALLS = 9  # AI calls for one recap: three stages, a retry each, and the grader
DAILY_CALLS = 40  # AI calls for one campaign's recaps in a day (a Try again is a new recap)
BACKOFF_S = (60, 240, 960)
MAX_RESTARTS = 2  # a stopped player restarts the work; not for ever
DAY_S = 86400

NOTHING = "No one who agreed to be recorded spoke this session, so there was nothing to recap."
TOO_LITTLE = "There wasn't enough said to tell as a story."
FAILED_CHECKS = (
    "DMbot couldn't write this one so that every detail came from what was said, so it wrote "
    "nothing. You can try again."
)
SERVICE = "The writing service wasn't available. You can try again."
FUNDS = "DMbot's writing service is unavailable right now. We've been told. Try again later."
CAP = "This recap has hit its limit for now. You can try again tomorrow."
DAILY = "This campaign has used up today's recap writing. Try again tomorrow."
NO_NAMES = (
    "DMbot couldn't check your campaign's secret names, so it wrote nothing to be safe. "
    "Try again in a minute."
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


Load = Callable[[int, str, Job], Awaitable["Inputs | None"]]
Consenting = Callable[[], Awaitable[Collection[int]]]
Notify = Callable[[int, str, Job], Awaitable[None]]  # (server, campaign, job): ready or failed


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
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._fast = fast
        self._careful = careful
        self._load = load
        self._consenting = consenting
        self._allowed = allowed
        self._notify = notify
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
        if job.state == "queued" and job.emailed_at is not None:
            # The DM pressed Try again (the page set the job back to queued): a fresh recap, with
            # a fresh share of calls and a fresh email. The day's tally (`spent`) is kept, and
            # this comes before the ceiling, which is what a failed-at-the-ceiling job needs.
            await self._store.update(
                gid, cid, job.session_at, calls=0, emailed_at=None, summary="", original="",
                title="", housekeeping=[], music=[], beats=[], speakers=[], grade={},
                attempt=0, errors=0, restarts=0, next_at=0, problems=[], last_error="",
                window_done=0, window_count=0,
            )  # fmt: skip
            return await self._store.get(gid, cid, job.session_at)
        if not await self._allowed(gid, cid, job):
            return await self._fail(gid, cid, job, "recaps aren't on for this campaign")
        if job.calls >= self._ceiling(job):
            return await self._fail(gid, cid, job, CAP)
        if await self._store.calls_since(gid, cid, int(self._now()) - DAY_S) >= DAILY_CALLS:
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
            return await self._finish(gid, cid, job, inputs, consenting)
        return await self._fail(gid, cid, job, "unknown stage")

    async def _call(
        self,
        gid: int,
        cid: str,
        job: Job,
        client: Client,
        system: str,
        text: str,
        max_tokens: int,
    ) -> str | None:
        """One AI call, counted before it is made. None if it failed (the job is then waiting or
        failed already: the caller returns)."""
        await self._store.update(gid, cid, job.session_at, calls=job.calls + 1, spent=job.spent + 1)
        try:
            reply = await client.complete(system, text, max_tokens=max_tokens)
        except AIOutOfFunds:
            await self._fail(gid, cid, job, FUNDS)
            return None
        except Exception as exc:  # the AI service, the network: a wait, never fatal to the bot
            log.warning("Recap: a call failed (%s)", type(exc).__name__)  # the type, not the text
            await self._wait(
                gid,
                cid,
                job,
                SERVICE,
                calls=job.calls + 1,
                spent=job.spent + 1,
                error=type(exc).__name__,
            )
            return None
        return reply.text

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
        rows = [
            (str(s.number), "DM" if s.user_id in inputs.dm_ids else "Player", s.text) for s in said
        ]
        parts = bt.windows(rows, inputs.window_chars)
        k = job.window_done
        if k >= len(parts):  # the transcript is shorter than when this began: begin again
            return await self._restart(gid, cid, job)
        text = bt.message(parts[k], inputs.allowed_names)
        if job.problems:
            text += (
                "\nYour last sheet was rejected: " + "; ".join(job.problems) + ". Write it again."
            )
        raw = await self._call(
            gid, cid, job, self._client(job), bt.system(k + 1, len(parts)), text, bt.MAX_TOKENS
        )
        if raw is None:
            return await self._store.get(gid, cid, job.session_at)
        sent = {s.user_id for s in said}
        if not sent <= set(await consenting()):  # again, after the answer
            return await self._restart(gid, cid, job)
        parsed = bt.parse(raw)
        if parsed is None:
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

    async def _check_beats(
        self, gid: int, cid: str, job: Job, inputs: Inputs, consenting: Consenting
    ) -> Job | None:
        said = self._said(inputs, set(job.speakers))
        kept, problems = bt.check(
            [b for b in (job.beats or []) if isinstance(b, dict)],
            lines={s.number: s.text for s in said},
            speakers={s.number: s.user_id for s in said},
            allowed_names=inputs.allowed_names,
            secret_names=inputs.secret_names,
        )
        usable = len(kept) >= bt.MIN_BEATS and not any("in no beat" in p for p in problems)
        if not usable:
            if len(kept) < bt.MIN_BEATS and job.attempt >= 1:
                return await self._fail(gid, cid, job, TOO_LITTLE)
            return await self._back_to_start(gid, cid, job, problems)
        log.info("Recap: %d beat(s) kept, %d problem(s)", len(kept), len(problems))  # counts only
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

    @staticmethod
    def _said(inputs: Inputs, speakers: set[int]) -> list[Said]:
        return [s for s in readable(inputs.lines) if s.user_id in speakers]

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

    async def _prose(
        self, gid: int, cid: str, job: Job, inputs: Inputs, consenting: Consenting
    ) -> Job | None:
        sheet = bt.from_json(job.beats)
        if len(sheet) < bt.MIN_BEATS:
            return await self._fail(gid, cid, job, TOO_LITTLE)
        message = rd.prose_message(sheet, inputs.previous, inputs.allowed_names, inputs.day)
        if job.problems:
            message += (
                "\nYour last draft was rejected: "
                + "; ".join(job.problems)
                + ". Write the whole recap again, fixing exactly that."
            )
        raw = await self._call(
            gid,
            cid,
            job,
            self._client(job),
            rd.prose_system(inputs.setting),
            message,
            rd.MAX_TOKENS,
        )
        if raw is None:
            return await self._store.get(gid, cid, job.session_at)
        if not set(job.speakers) <= set(await consenting()):
            return await self._restart(gid, cid, job)
        prose = rd.parse_prose(raw)
        if prose is None:
            return await self._retry_or_fail(gid, cid, job, ["follow the form: SUMMARY then MUSIC"])
        transcript = "\n".join(s.text for s in self._said(inputs, set(job.speakers)))
        problems = rd.check_prose(
            prose,
            beats=sheet,
            transcript=transcript,
            allowed_names=inputs.allowed_names,
            secret_names=inputs.secret_names,
            previous=inputs.previous,
            setting=inputs.setting,
        )
        if problems:
            log.info("Recap: a draft failed %d check(s)", len(problems))  # counts only
            return await self._retry_or_fail(gid, cid, job, problems)
        try:
            grade = await self._grade(gid, cid, job, sheet, prose.summary)
        except AIOutOfFunds:
            return await self._fail(gid, cid, job, FUNDS)
        weak = rd.weak(grade) if grade else []
        if weak and job.attempt == 0:  # below the bar: one more try, told what fell short
            await self._store.update(
                gid, cid, job.session_at, attempt=1, problems=weak, grade=grade
            )
            return await self._store.get(gid, cid, job.session_at)
        log.info("Recap: graded %s", ",".join(f"{k}={v}" for k, v in sorted(grade.items())))
        await self._store.update(
            gid,
            cid,
            job.session_at,
            state="prose",
            summary=prose.summary,
            original=prose.summary,
            music=list(prose.music),
            game_day=inputs.day,
            grade=grade,
            attempt=0,
            errors=0,
            problems=[],
            last_error="",
        )
        return await self._store.get(gid, cid, job.session_at)

    async def _grade(
        self, gid: int, cid: str, job: Job, sheet: Sequence[bt.Beat], summary: str
    ) -> dict[str, int]:
        """The grader's numbers, or none: a grader that fails never stops a recap that passed
        every check."""
        fresh = await self._store.get(gid, cid, job.session_at) or job
        if fresh.calls >= self._ceiling(fresh):
            return {}  # the job's ceiling holds for the grader too
        await self._store.update(
            gid, cid, job.session_at, calls=fresh.calls + 1, spent=fresh.spent + 1
        )
        try:
            reply = await self._fast.complete(
                rd.GRADE_SYSTEM, rd.grade_message(sheet, summary), max_tokens=200
            )
        except AIOutOfFunds:
            raise  # the next stage would only hit it too
        except Exception as exc:
            log.warning("Recap: the grader failed (%s)", type(exc).__name__)
            return {}
        return rd.parse_grade(reply.text) or {}

    # --- stage 3: the title and the housekeeping ---------------------------------------

    async def _finish(
        self, gid: int, cid: str, job: Job, inputs: Inputs, consenting: Consenting
    ) -> Job | None:
        sheet = bt.from_json(job.beats)
        message = rd.housekeeping_message(sheet, job.summary)
        if job.problems:
            message += (
                "\nYour last answer was rejected: " + "; ".join(job.problems) + ". Write it again."
            )
        raw = await self._call(
            gid, cid, job, self._client(job), rd.housekeeping_system(), message, 600
        )
        if raw is None:
            return await self._store.get(gid, cid, job.session_at)
        if not set(job.speakers) <= set(await consenting()):
            return await self._restart(gid, cid, job)
        parsed = rd.parse_finish(raw)
        if parsed is None:
            return await self._retry_or_fail(
                gid, cid, job, ["follow the form: TITLE then HOUSEKEEPING"]
            )
        finish, problems = rd.check_finish(
            parsed,
            beats=sheet,
            summary=job.summary,
            allowed_names=inputs.allowed_names,
            secret_names=inputs.secret_names,
            previous=inputs.previous,
        )
        if finish is None:
            return await self._retry_or_fail(gid, cid, job, problems)
        await self._store.update(
            gid,
            cid,
            job.session_at,
            state="ready",
            title=finish.title,
            housekeeping=list(finish.housekeeping),
            attempt=0,
            errors=0,
            problems=[],
            last_error="",
        )
        log.info("Recap: ready after %d call(s)", job.calls)
        return await self._store.get(gid, cid, job.session_at)

    # --- ways out -----------------------------------------------------------------------

    async def _retry_or_fail(
        self, gid: int, cid: str, job: Job, problems: Sequence[str]
    ) -> Job | None:
        """A check failed: once more on the careful tier, then the job fails plainly."""
        if job.attempt >= 1:
            return await self._fail(gid, cid, job, FAILED_CHECKS)
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
            return await self._fail(gid, cid, job, why, error=error)
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

    async def _fail(self, gid: int, cid: str, job: Job, why: str, *, error: str = "") -> Job | None:
        log.info("Recap: failed at %s (%s)", job.state, error or "plain reason")
        await self._store.update(gid, cid, job.session_at, state="failed", last_error=why[:200])
        return await self._store.get(gid, cid, job.session_at)

    async def _restart(self, gid: int, cid: str, job: Job) -> Job | None:
        """A player whose lines were used stopped being recorded: begin again without them, a
        limited number of times."""
        if job.restarts >= MAX_RESTARTS:
            return await self._fail(gid, cid, job, STOPPED)
        log.info("Recap: starting again, a speaker stopped being recorded")
        await self._store.update(
            gid,
            cid,
            job.session_at,
            state="queued",
            beats=[],
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
