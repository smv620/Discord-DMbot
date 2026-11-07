"""The AI feed (#3710): what DMbot asks the AI and what comes back, for the DM's live windows on the
lore upload and import pages.

Scope: only `complete()` calls made inside a lore or import job are captured (`with job(...)`).
Embeddings (Voyage) and speech-to-text are not prompts and stay out, and so does every call outside
a job (a table answer, a sheet import). Today the lore runner and the import steps set the job (a
separate change by the readers' owner); until they do, nothing is captured.

What is stored, in the `ai_feed` table, for one call: the time, the service and model in plain
words, the step, the first 500 characters of the user text (the instructions are the same constant
every call, so they are not stored) then `[...]` when there was more, and the first 500
characters of the reply the same way. An image is the word `[image]`. The cut is made here, in
Python, before anything is written, so nothing longer reaches the database. Never a key, a header
or an account detail. Nothing is logged: not a word of a prompt or a reply, only counts and
exception class names.

It is kept at most 24 hours (`sweep`). "Stop recording me" deletes every row of the server at once
(`ConsentStore.revoke` calls `forget_server`, which also drops a write that was already on its way).
A deleted campaign takes its rows. It is never in a backup.

It must never slow or fail the real work. `begin`, `done` and `failed` are plain functions that only
schedule a short write on a connection of their own, with a 2-second timeout; they never raise and
never wait. A skipped write is counted in `lost` ("Some calls aren't shown"). With `DMBOT_AI_FEED`
off, nothing is written.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import time
from collections import defaultdict
from collections.abc import Coroutine, Iterator, Sequence
from dataclasses import dataclass, field
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
_epoch: defaultdict[int, int] = defaultdict(int)  # bumped when a server's feed is wiped


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


def forget_server(guild_id: int) -> None:
    """A person stopped being recorded in this server (called with the delete of its rows): a write
    that was already on its way is dropped, or undone if it had just landed."""
    _epoch[guild_id] += 1


@dataclass(slots=True)
class Entry:
    ctx: JobContext
    at: int
    epoch: int
    prompt: str
    label: str
    n: int | None = None  # the number given when the row was written
    skipped: bool = False  # the row was never written: there is nothing to update
    finish: tuple[str, str | None, str | None] | None = field(default=None)


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
        entry = Entry(ctx, at, _epoch[ctx.guild_id], shown, TIER_LABELS.get(tier, ""))
        _schedule(_insert(entry), entry)
        return entry
    except Exception as exc:
        log.info("AI feed: a call could not be noted (%s)", type(exc).__name__)
        return None


def done(entry: Entry | None, reply_text: str) -> None:
    """The call answered."""
    _finish(entry, "done", reply_text, None)


def failed(entry: Entry | None, reason: str) -> None:
    """The call failed; `reason` is the plain wording the wait panel already gives."""
    _finish(entry, "failed", None, reason)


def _finish(entry: Entry | None, state: str, reply: str | None, reason: str | None) -> None:
    """Record how the call ended. If the row is not written yet, the write applies it as soon as
    it has its number (no waiting on anything); otherwise it is a write of its own."""
    if entry is None:
        return
    try:
        entry.finish = (
            state,
            None if reply is None else cut(str(reply)),
            None if reason is None else str(reason)[:200],
        )
        if entry.n is not None:
            _schedule(_update(entry), entry)
    except Exception as exc:
        log.info("AI feed: a reply could not be noted (%s)", type(exc).__name__)


def _schedule(work: Coroutine[Any, Any, None], entry: Entry) -> None:
    global _pending
    if _pending >= MAX_PENDING:
        work.close()  # (never started: nothing to await)
        _count_lost(entry)
        raise RuntimeError("too many feed writes waiting")
    try:
        task = asyncio.get_running_loop().create_task(_run(work, entry))
    except BaseException:
        work.close()
        raise
    _pending += 1
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _count_lost(entry: Entry) -> None:
    """Count a call that is not shown, on a write of its own (best effort)."""
    with contextlib.suppress(Exception):
        asyncio.get_running_loop().create_task(_lost(entry.ctx))


async def _run(work: Coroutine[Any, Any, None], entry: Entry) -> None:
    global _pending
    try:
        await asyncio.wait_for(work, WRITE_TIMEOUT_S)
    except asyncio.CancelledError:
        entry.skipped = entry.n is None
        raise
    except BaseException as exc:
        entry.skipped = entry.n is None
        log.info("AI feed: a write was skipped (%s)", type(exc).__name__)
        await _lost(entry.ctx)
    finally:
        _pending -= 1


async def _insert(entry: Entry) -> None:
    ctx = entry.ctx
    assert _db is not None
    if _epoch[ctx.guild_id] != entry.epoch:
        return  # the server's feed was wiped while this was waiting: nothing is written
    async with _db.guild(ctx.guild_id) as conn:
        # The number is given out by one statement on the job's bookkeeping row (`n = 0`), so calls
        # started at the same moment never share one. `at` moves with it, so the sweep never takes
        # the bookkeeping of a job that is still going.
        cur = await conn.execute(
            "INSERT INTO ai_feed (guild_id, campaign_id, kind, job_id, n, at, state, next_n)"
            " VALUES (%s, %s, %s, %s, 0, %s, 'done', 1)"
            " ON CONFLICT (guild_id, campaign_id, kind, job_id, n)"
            " DO UPDATE SET next_n = ai_feed.next_n + 1, at = EXCLUDED.at RETURNING next_n",
            (ctx.guild_id, ctx.campaign_id, ctx.kind, ctx.job_id, entry.at),
        )
        n = int((await cur.fetchone() or {"next_n": 0})["next_n"])
        state, reply, reason = entry.finish or ("waiting", None, None)
        await conn.execute(
            "INSERT INTO ai_feed (guild_id, campaign_id, kind, job_id, n, at, service,"
            " model_label, step, prompt, state, reply, reason) VALUES (%s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, %s, %s, %s)",
            (
                ctx.guild_id,
                ctx.campaign_id,
                ctx.kind,
                ctx.job_id,
                n,
                entry.at,
                SERVICE,
                entry.label,
                ctx.step,
                entry.prompt,
                state,
                reply,
                reason,
            ),
        )
    entry.n = n  # (no await between this and the check below: the call cannot finish in between)
    await _after(entry)
    if entry.finish is not None and state == "waiting":
        # The call ended after the row was read above but before it was committed.
        _schedule(_update(entry), entry)


async def _update(entry: Entry) -> None:
    ctx = entry.ctx
    assert _db is not None
    if entry.n is None or entry.finish is None or _epoch[ctx.guild_id] != entry.epoch:
        return
    state, reply, reason = entry.finish
    async with _db.guild(ctx.guild_id) as conn:
        await conn.execute(
            "UPDATE ai_feed SET state = %s, reply = %s, reason = %s WHERE guild_id = %s"
            " AND campaign_id = %s AND kind = %s AND job_id = %s AND n = %s",
            (state, reply, reason, ctx.guild_id, ctx.campaign_id, ctx.kind, ctx.job_id, entry.n),
        )
    await _after(entry)


async def _after(entry: Entry) -> None:
    """If the server's feed was wiped (Stop recording me) while this write was in flight, wipe it
    again: this row may have landed after the delete."""
    ctx = entry.ctx
    if _epoch[ctx.guild_id] != entry.epoch and _db is not None:
        async with _db.guild(ctx.guild_id) as conn:
            await conn.execute("DELETE FROM ai_feed WHERE guild_id = %s", (ctx.guild_id,))


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
                " DO UPDATE SET lost = ai_feed.lost + 1, at = EXCLUDED.at",
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
