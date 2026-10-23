"""A recap job kept in memory, for the test run (#1424): the real `RecapWorker` needs a store,
and this one holds a single job in the process and never touches the database, so a test run
writes nothing unless `--keep` says so."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from dmbot.recaps.store import _LISTS, _WRITABLE, Job


def blank(session_at: int, dm_user_id: int, now: int) -> Job:
    return Job(
        session_at=session_at, dm_user_id=dm_user_id, state="queued", attempt=0, errors=0,
        restarts=0, next_at=0, calls=0, window_count=0, window_done=0, spent=0, last_error="",
        problems=(), beats=[], title="", summary="", original="", housekeeping=(), music=(),
        game_day=None, status="draft", tries=0, speakers=(), grade={}, emailed_at=None,
        created_at=now, updated_at=now,
    )  # fmt: skip


class EphemeralStore:
    """The store methods the worker calls, for one job held in memory."""

    def __init__(self, session_at: int, dm_user_id: int) -> None:
        self.job = blank(session_at, dm_user_id, int(time.time()))
        # Every beat sheet the checks saw, in order, so a failed run can say why (#1997).
        self.sheets: list[list[dict[str, Any]]] = []

    async def get(self, guild_id: int, campaign_id: str, session_at: int) -> Job:
        return self.job

    async def lease(self, guild_id: int, campaign_id: str, session_at: int) -> bool:
        return True  # nobody else works on it

    async def release(self, guild_id: int, campaign_id: str, session_at: int) -> None:
        return None

    async def calls_since(self, guild_id: int, campaign_id: str, since: int) -> int:
        return 0  # the day's tally is the live job's; a test run has its own per-recap ceiling

    async def update(self, guild_id: int, campaign_id: str, session_at: int, **fields: Any) -> None:
        if not fields or not set(fields) <= _WRITABLE:  # the same rule as the real store
            raise ValueError(sorted(set(fields) - _WRITABLE))
        values: dict[str, Any] = {
            name: (tuple(value) if name in _LISTS else value) for name, value in fields.items()
        }
        if fields.get("state") == "beats" and fields.get("beats"):
            self.sheets.append([dict(b) for b in fields["beats"] if isinstance(b, dict)])
        self.job = replace(self.job, **{**values, "updated_at": int(time.time())})

    async def update_seen(
        self, guild_id: int, campaign_id: str, session_at: int, approval: int, **fields: Any
    ) -> None:
        """`update` only if the DM's approval is still what the step saw (#2305)."""
        critics = self.job.critics
        stored = int(critics.get("approved_at") or 0) if isinstance(critics, dict) else 0
        if stored == approval:
            await self.update(guild_id, campaign_id, session_at, **fields)

    async def reset_audio(self, guild_id: int, campaign_id: str, session_at: int) -> None:
        return None

    async def set_audio(self, guild_id: int, campaign_id: str, session_at: int, **_: Any) -> bool:
        return True
