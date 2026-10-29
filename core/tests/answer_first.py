"""A stand-in interaction that records the order of slow calls and Discord answers (#1798).

Discord drops a button press that gets no answer in 3 seconds, so a callback must answer (defer,
send_message, edit_message or send_modal) before anything slow: a Discord REST call, an AI call or
more than one database round trip. A test builds a `Timeline`, makes each slow call with
`timeline.slow(...)`, uses `timeline.response` and `timeline.followup` on the interaction, runs the
callback and calls `assert_answered_first()`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock


class Timeline:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.done = False

        def answer(name: str) -> AsyncMock:
            async def record(*args: Any, **kwargs: Any) -> None:
                # (Discord allows one answer per press; a second one raises InteractionResponded.)
                assert not self.done, f"answered twice: {self.events} then {name}"
                self.events.append(f"answer:{name}")
                self.done = True

            return AsyncMock(side_effect=record)

        self.response = SimpleNamespace(
            defer=answer("defer"),
            send_message=answer("send_message"),
            edit_message=answer("edit_message"),
            send_modal=answer("send_modal"),
            is_done=lambda: self.done,
        )

        async def followed(*args: Any, **kwargs: Any) -> None:
            self.events.append("followup")

        self.followup = SimpleNamespace(send=AsyncMock(side_effect=followed))
        self.edit_original_response = AsyncMock(side_effect=followed)

    def slow(self, name: str, result: Any = None, error: Exception | None = None) -> AsyncMock:
        """A stand-in for one slow call (a store method, a REST call): it notes when it ran."""

        async def run(*args: Any, **kwargs: Any) -> Any:
            self.events.append(f"slow:{name}")
            if error is not None:
                raise error
            return result

        return AsyncMock(side_effect=run)

    def assert_answered_first(self) -> None:
        answers = [i for i, e in enumerate(self.events) if e.startswith("answer:")]
        slow = [i for i, e in enumerate(self.events) if e.startswith("slow:")]
        assert answers, f"never answered Discord: {self.events}"
        assert not slow or answers[0] < slow[0], f"slow work before the answer: {self.events}"
