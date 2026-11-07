"""The AI feed (#3710): what DMbot asks the AI, and what comes back, for the DM's live windows on
the
lore upload and import pages. Only `complete()` calls made inside a lore or import job are
captured: embeddings (Voyage) and speech-to-text are not prompts and stay out, and so does every
call
outside a job (a table answer, a sheet import).

What is stored, in the `ai_feed` table, for one call: the time, the service and model in plain
words,
which step it was, the **first 500 characters** of the user text (the instructions are the same
constant every time, so they are not stored) then `[...]` when there was more, and the first 500
characters of the reply the same way; an image is the word `[image]`. The cut is made here, in
Python, before anything is written, so nothing longer ever reaches the database. Never a key, a
header or an account detail. Nothing is logged: not a word of a prompt or a reply, only counts.

It is kept at most 24 hours (`sweep`), "Stop recording me" deletes every row of the server at once
(`ConsentStore.revoke`), a deleted campaign takes its rows, and it is never in a backup.

It must never slow or fail the real work: the job's context is a `contextvars` value set where a job
starts (`with job(...)`), `begin` and `finish` are plain functions that only schedule a short write
on a connection of their own with a 2-second timeout, and every failure is swallowed and counted in
`lost` ("Some calls aren't shown"). With `DMBOT_AI_FEED` off, nothing is written.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dmbot.db import Database

log = logging.getLogger(__name__)

CUT = 500  # characters kept of a prompt and of a reply
MORE = "[...]"
WRITE_TIMEOUT_S = 2.0  # a write that takes longer is skipped, never waited for
MAX_PENDING = 100  # writes waiting at once; more are skipped (and counted) rather than queued
KEEP_S = 24 * 3600
KINDS = ("lore", "import")
SERVICE = "Anthropic"
TIER_LABELS = {"fast": "smaller model", "careful": "larger model", "creative": "writing model"}


@dataclass(frozen=True, slots=True)
class JobContext:
    guild_id: int
    campaign_id: str
    kind: str
    job_id: str
    step: str = ""


_current: contextvars.ContextVar[JobContext | None] = contextvars.ContextVar(
    "dmbot_ai_feed_job", default=None
)
_db: Database | None = None
_on = False
_pending = 0
_tasks: set[asyncio.Task[None]] = set()  # (kept so a running write is not garbage collected)


def configure(db: Database | None, *, on: bool) -> None:
    """Called once at startup. With `on` false (the default), nothing is ever written."""
    global _db, _on
    _db, _on = db, bool(on and db is not None)


@contextlib.contextmanager
def job(
    guild_id: int, campaign_id: str, kind: str, job_id: str, *, step: str = ""
) -> Iterator[None]:
    """Everything `complete()` sends inside this block belongs to this lore upload or import. A
    ContextVar is per task, so one job never leaks into another and the value is gone after."""
    token = _current.set(JobContext(guild_id, campaign_id, kind, job_id, step[:80]))
    try:
        yield
    finally:
        _current.reset(token)


def step(text: str) -> None:
    """Name the step the next calls belong to ("reading part 3 of 10"): words and counts only."""
    ctx = _current.get()
    if ctx is not None:
        _current.set(JobContext(ctx.guild_id, ctx.campaign_id, ctx.kind, ctx.job_id, text[:80]))


def cut(text: str) -> str:
    """The first 500 characters, then `[...]` when there was more. Done before any write."""
    return text if len(text) <= CUT else text[:CUT] + MORE


@dataclass(slots=True)
class Entry:
    ctx: JobContext
    n: asyncio.Future[int | None]
    at: int


def begin(
    text: str, tier: str, images: Sequence[Any] = (), *, wall: float | None = None
) -> Entry | None:
    """A call is about to go out. None unless the feed is on and a lore or import job is set.
    Returns at once: the write is scheduled, never waited for. Never raises."""
    try:
        ctx = _current.get()
        if not _on or ctx is None or _db is None or ctx.kind not in KINDS:
            return None
        shown = cut("[image]\n" + text if images else text)
        at = int(wall if wall is not None else time.time())
        label = TIER_LABELS.get(tier, "")
        future: asyncio.Future[int | None] = asyncio.get_running_loop().create_future()
        entry = Entry(ctx, future, at)
        _schedule(_insert(entry, shown, label))
        return entry
    except Exception as exc:
        log.info("AI feed: a call could not be noted (%s)", type(exc).__name__)
        return None


def done(entry: Entry | None, reply_text: str) -> None:
    """The call answered."""
    if entry is not None:
        _finish(entry, "done", cut(reply_text), None)


def failed(entry: Entry | None, reason: str) -> None:
    """The call failed; `reason` is the plain wording the wait panel already gives."""
    if entry is not None:
        _finish(entry, "failed", None, reason[:200])


def _finish(entry: Entry, state: str, reply: str | None, reason: str | None) -> None:
    try:
        _schedule(_update(entry, state, reply, reason))
    except Exception as exc:
        log.info("AI feed: a reply could not be noted (%s)", type(exc).__name__)


def _schedule(work: Any) -> None:
    global _pending
    if _pending >= MAX_PENDING:
        work.close()  # (never started: nothing to await)
        raise RuntimeError("too many feed writes waiting")
    _pending += 1
    task = asyncio.get_running_loop().create_task(_run(work))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _run(work: Any) -> None:
    global _pending
    try:
        await asyncio.wait_for(work, WRITE_TIMEOUT_S)
    except BaseException as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        log.info("AI feed: a write was skipped (%s)", type(exc).__name__)
    finally:
        _pending -= 1


async def _insert(entry: Entry, prompt: str, label: str) -> None:
    ctx = entry.ctx
    assert _db is not None
    try:
        async with _db.guild(ctx.guild_id) as conn:
            # The number is given out by one statement on the job's bookkeeping row, so calls
            # started at the same moment never share one.
            cur = await conn.execute(
                "INSERT INTO ai_feed (guild_id, campaign_id, kind, job_id, n, at, state, next_n)"
                " VALUES (%s, %s, %s, %s, 0, %s, 'done', 1)"
                " ON CONFLICT (guild_id, campaign_id, kind, job_id, n)"
                " DO UPDATE SET next_n = ai_feed.next_n + 1 RETURNING next_n",
                (ctx.guild_id, ctx.campaign_id, ctx.kind, ctx.job_id, entry.at),
            )
            n = int((await cur.fetchone() or {"next_n": 0})["next_n"])
            await conn.execute(
                "INSERT INTO ai_feed (guild_id, campaign_id, kind, job_id, n, at, service,"
                " model_label, step, prompt, state) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, 'waiting')",
                (
                    ctx.guild_id,
                    ctx.campaign_id,
                    ctx.kind,
                    ctx.job_id,
                    n,
                    entry.at,
                    SERVICE,
                    label,
                    ctx.step,
                    prompt,
                ),
            )
        if not entry.n.done():
            entry.n.set_result(n)
    except BaseException:
        if not entry.n.done():
            entry.n.set_result(None)
        await _lost(ctx)
        raise


async def _update(entry: Entry, state: str, reply: str | None, reason: str | None) -> None:
    ctx = entry.ctx
    assert _db is not None
    n = await entry.n  # (the insert has finished, or failed: then there is nothing to update)
    if n is None:
        return
    async with _db.guild(ctx.guild_id) as conn:
        await conn.execute(
            "UPDATE ai_feed SET state = %s, reply = %s, reason = %s WHERE guild_id = %s"
            " AND campaign_id = %s AND kind = %s AND job_id = %s AND n = %s",
            (state, reply, reason, ctx.guild_id, ctx.campaign_id, ctx.kind, ctx.job_id, n),
        )


async def _lost(ctx: JobContext) -> None:
    """Count a call that is not shown (best effort, on its own short write)."""
    if _db is None:
        return
    with contextlib.suppress(Exception):
        async with asyncio.timeout(WRITE_TIMEOUT_S), _db.guild(ctx.guild_id) as conn:
            await conn.execute(
                "INSERT INTO ai_feed (guild_id, campaign_id, kind, job_id, n, at, state, lost)"
                " VALUES (%s, %s, %s, %s, 0, %s, 'done', 1)"
                " ON CONFLICT (guild_id, campaign_id, kind, job_id, n)"
                " DO UPDATE SET lost = ai_feed.lost + 1",
                (ctx.guild_id, ctx.campaign_id, ctx.kind, ctx.job_id, int(time.time())),
            )


async def sweep(db: Database, guild_id: int, *, now: int | None = None) -> int:
    """Delete this server's entries written more than 24 hours ago; the count (never a word)."""
    cutoff = int(now if now is not None else time.time()) - KEEP_S
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "DELETE FROM ai_feed WHERE guild_id = %s AND at < %s", (guild_id, cutoff)
        )
        return int(cur.rowcount)
