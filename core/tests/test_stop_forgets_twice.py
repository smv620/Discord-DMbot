"""#4153: "Stop recording me" forgets what cites the person once at once and once more after the
consent row is deleted (catching an after-session writer that re-read who agreed just before that),
and a failure of the second pass never fails the stop."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.bot import DMBot


class TheSecondForget(unittest.IsolatedAsyncioTestCase):
    def fake(self, order: list[str]) -> SimpleNamespace:
        async def revoke(*_a: object) -> bool:
            order.append("revoke")
            return True

        return SimpleNamespace(
            consent=SimpleNamespace(agreed_now=lambda *_: True, revoke=revoke),
            stop_recording=MagicMock(),
            _tell_dm_about_consent=MagicMock(),
            push_allowlist=AsyncMock(),
            heard_questions=None,
            memory=SimpleNamespace(forget_learned=AsyncMock()),
            members=None,
        )

    def claims(self, order: list[str], fail_second: bool = False) -> Any:
        calls = []

        async def forget_speaker(*_a: object) -> int:
            calls.append(1)
            order.append("forget")
            if fail_second and len(calls) == 2:
                raise RuntimeError("row Mira")
            return 0

        return MagicMock(return_value=SimpleNamespace(forget_speaker=forget_speaker))

    async def test_it_forgets_before_and_again_after_the_revoke(self) -> None:
        order: list[str] = []
        with patch("dmbot.bot.ClaimStore", self.claims(order)):
            self.assertTrue(await DMBot.withdraw_consent(self.fake(order), 5, 7))  # type: ignore[arg-type]
        self.assertEqual(order, ["forget", "revoke", "forget"])

    async def test_a_failing_second_forget_never_fails_the_stop_and_logs_only_the_type(
        self,
    ) -> None:
        order: list[str] = []
        with (
            patch("dmbot.bot.ClaimStore", self.claims(order, fail_second=True)),
            self.assertLogs("dmbot.bot", level="WARNING") as logs,
        ):
            self.assertTrue(await DMBot.withdraw_consent(self.fake(order), 5, 7))  # type: ignore[arg-type]
        self.assertIn("RuntimeError", " ".join(logs.output))
        self.assertNotIn("Mira", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
