"""core never joins voice, so discord.py's voice warnings must stay quiet (#38)."""

import unittest
from contextlib import ExitStack
from unittest.mock import patch

import discord

from dmbot.bot import silence_voice_warnings

FLAGS = [f for f in ("warn_nacl", "warn_dave") if hasattr(discord.VoiceClient, f)]


class SilenceVoiceWarningsTest(unittest.TestCase):
    def _flags_on(self, stack: ExitStack) -> None:
        # As on a machine without PyNaCl or davey; restored after each test.
        for flag in FLAGS:
            stack.enter_context(patch.object(discord.VoiceClient, flag, True))

    def test_turns_the_flags_off(self) -> None:
        with ExitStack() as stack:
            self._flags_on(stack)
            silence_voice_warnings()
            for flag in FLAGS:
                self.assertFalse(getattr(discord.VoiceClient, flag), flag)

    def test_client_start_logs_no_voice_warning(self) -> None:
        with ExitStack() as stack:
            self._flags_on(stack)
            stack.enter_context(self.assertNoLogs("discord.client", level="WARNING"))
            silence_voice_warnings()
            discord.Client(intents=discord.Intents.none())

    def test_without_it_discord_py_would_warn(self) -> None:
        # Guards the test above: if discord.py stops warning this way, we'll know.
        with ExitStack() as stack:
            self._flags_on(stack)
            logs = stack.enter_context(self.assertLogs("discord.client", level="WARNING"))
            discord.Client(intents=discord.Intents.none())
        self.assertTrue(any("voice will NOT be supported" in m for m in logs.output))
