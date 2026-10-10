"""Watching DMbot's AI account (#972): who is told when it is out of funds or at its spend
limit, and a daily line of how much AI was used.

Every AI call goes through `AnthropicClient`, which reports to one `AIWatch`. The watch
messages the two admins named in the settings (at most once a day each, remembered in a
small file in the data folder so a restart doesn't repeat it), logs an error at most once an
hour, and logs one INFO line a day with counts only (calls and tokens, never text, never the
key), so unusual spending is easy to spot.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path

log = logging.getLogger(__name__)

NOTICE_EVERY_S = 24 * 3600  # each admin hears about it at most this often
ERROR_LOG_EVERY_S = 3600
USAGE_WINDOW_S = 24 * 3600
STATE_FILE = "ai_notice.json"

# Said to each admin. The backup admin may not know the setup, so it names the key and what
# stops; the last sentence says which of the two they are (owner decision, #972).
ADMIN_NOTICE = (
    "⚠️ DMbot's AI account needs attention. The Anthropic API key that DMbot's core uses to "
    "run its AI jobs (finding names, the off-topic filter, quick answers for DMs) is out of "
    "funds or has reached its monthly spend limit, so those jobs are paused. Games can keep "
    "running. To fix it, add funds or raise the limit at "
    "https://console.anthropic.com/settings/billing. (You're getting this as DMbot's {role} "
    "admin.)"
)

# Tells one admin (a Discord user ID); raises if it can't be sent.
Send = Callable[[int, str], Awaitable[None]]


class AIWatch:
    def __init__(
        self,
        *,
        admins: dict[str, int],
        state_dir: Path,
        send: Send | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`admins`: role name ("primary", "secondary") to Discord user ID; only the ones set."""
        self._admins = admins
        self._path = state_dir / STATE_FILE
        self._send = send
        self._clock = clock
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._last_error_log = float("-inf")
        self._last_usage_log = clock()
        self._calls: deque[tuple[float, int, int]] = deque()  # when, input, output tokens

    # ---- usage ---------------------------------------------------------------

    def record(self, input_tokens: int, output_tokens: int) -> None:
        """One finished AI call. Once a day, logs the last 24 hours' counts."""
        now = self._clock()
        self._calls.append((now, input_tokens, output_tokens))
        self._trim(now)
        if now - self._last_usage_log >= USAGE_WINDOW_S:
            self._last_usage_log = now
            log.info(
                "AI usage, last 24 hours: calls=%d input_tokens=%d output_tokens=%d",
                len(self._calls),
                sum(c[1] for c in self._calls),
                sum(c[2] for c in self._calls),
            )

    def _trim(self, now: float) -> None:
        while self._calls and now - self._calls[0][0] > USAGE_WINDOW_S:
            self._calls.popleft()

    # ---- out of funds --------------------------------------------------------

    def out_of_funds(self) -> None:
        """The AI service refused a call for want of funds or at a spend limit. Never blocks
        the caller: the messages go out in the background."""
        now = self._clock()
        if now - self._last_error_log >= ERROR_LOG_EVERY_S:
            self._last_error_log = now
            log.error("DMbot's AI account is out of funds or at its spend limit")
        if not self._admins or self._send is None:
            return  # nobody to tell: the log line is all there is
        task = asyncio.create_task(self._tell_admins())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _tell_admins(self) -> None:
        async with self._lock:  # many calls fail at once; one pass decides
            sent = self._read()
            now = self._clock()
            changed = False
            for role, user_id in self._admins.items():
                if now - sent.get(role, float("-inf")) < NOTICE_EVERY_S:
                    continue
                try:
                    assert self._send is not None
                    await self._send(user_id, ADMIN_NOTICE.format(role=role))
                except Exception as exc:  # one failing doesn't stop the other
                    log.warning("Couldn't tell the %s admin: %s", role, type(exc).__name__)
                    continue
                sent[role] = now
                changed = True
            if changed:
                self._write(sent)

    def _read(self) -> dict[str, float]:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: float(v) for k, v in data.items() if type(v) in (int, float)}

    def _write(self, sent: dict[str, float]) -> None:
        # Best effort: if it can't be saved, the worst case is one repeat after a restart.
        with contextlib.suppress(OSError):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(sent))
            tmp.replace(self._path)

    async def wait(self) -> None:
        """Let background messages finish (tests, shutdown)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
