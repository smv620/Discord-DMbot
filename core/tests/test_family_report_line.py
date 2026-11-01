"""The after-session line about worked-out family links (#3066): how many wait for the DM's yes,
a number only, on the DM screen with the button only a DM can use, and nothing when none wait.
Invented names."""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from typing import Any, cast

from dmbot.bot import NO_PRIVATE_MESSAGE, DMBot
from dmbot.dm_screen import links_ready
from dmbot.memory.models import CONFIRMED, REJECTED
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

SCREEN = 77


class TheText(unittest.TestCase):
    def test_a_number_and_what_the_button_does_never_a_name(self) -> None:
        one, many = links_ready.family_notice(1), links_ready.family_notice(3)
        self.assertIn("1 family link** DMbot guessed", one)
        self.assertIn("needs your yes or no", one)
        self.assertIn("3 family links** DMbot guessed", many)
        self.assertIn("need your yes or no", many)
        self.assertIn("players are not told", many)

    def test_none_is_no_line(self) -> None:
        self.assertEqual((links_ready.family_notice(0), links_ready.family_notice(-1)), ("", ""))


class TheLine(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.aldric = await self.add("Aldric", type="character", status=CONFIRMED)
        self.bryn = await self.add("Bryn", type="character", status=CONFIRMED)
        self.mira = await self.add("Mira", type="character", status=CONFIRMED)
        self.posted: list[tuple[int, str, Any]] = []

        async def post(channel: int, text: str, view: Any = None) -> bool:
            self.posted.append((channel, text, view))
            return True

        self.asked: list[tuple[int, str, str, Any]] = []
        self.reachable = False  # no DM can be reached: the DM screen keeps the line

        async def ask_privately(guild_id: int, campaign_id: str, text: str, view: Any) -> bool:
            self.asked.append((guild_id, campaign_id, text, view))
            return self.reachable

        self.bot = SimpleNamespace(memory=self.memory, post=post, ask_privately=ask_privately)
        self.table = SimpleNamespace(guild_id=GUILD_A, campaign_id=self.c, screen_channel_id=SCREEN)

    async def state(self, a: str, role: str, b: str) -> None:
        await self.memory.add_relation(
            GUILD_A, self.c, a, "kin_of", b, source="dm", confidence=1.0, status=CONFIRMED,
            detail=role, directional=True,
        )  # fmt: skip

    async def run_step(self) -> None:
        await DMBot.warn_about_family(cast(Any, self.bot), cast(Any, self.table))

    async def test_one_waiting_suggestion_shows_its_count_with_the_button(self) -> None:
        await self.state(self.aldric, "mother", self.bryn)
        await self.state(self.bryn, "sister", self.mira)  # so Aldric is Mira's parent, suggested
        await self.run_step()
        [(channel, text, view)] = self.posted
        self.assertEqual(channel, SCREEN)
        # (the DM screen's line says why it is not in the private chat)
        self.assertEqual(text, links_ready.family_notice(1) + NO_PRIVATE_MESSAGE)
        self.assertNotIn("Aldric", text)
        self.assertNotIn("Mira", text)
        [item] = view.children
        self.assertEqual(item.item.label, links_ready.LABEL)
        self.assertEqual(item.item.custom_id, f"dmbot:linksready:{self.c}")

    async def test_the_line_goes_to_the_dms_private_chat_when_one_can_be_reached(self) -> None:
        # #3210: asked privately; the DM screen gets nothing when the private message was sent.
        self.reachable = True
        await self.state(self.aldric, "mother", self.bryn)
        await self.state(self.bryn, "sister", self.mira)
        await self.run_step()
        self.assertEqual(self.posted, [])
        [(guild_id, campaign_id, text, view)] = self.asked
        self.assertEqual((guild_id, campaign_id), (GUILD_A, self.c))
        self.assertEqual(text, links_ready.family_notice(1))
        [item] = view.children
        self.assertEqual(item.item.custom_id, f"dmbot:linksready:{self.c}")

    async def test_none_waiting_shows_no_line(self) -> None:
        await self.state(self.aldric, "mother", self.bryn)  # nothing to work out from one link
        await self.run_step()
        self.assertEqual(self.posted, [])

    async def test_a_suggestion_the_dm_answered_is_no_longer_counted(self) -> None:
        await self.state(self.aldric, "mother", self.bryn)
        await self.state(self.bryn, "sister", self.mira)
        await self.memory.rework_family(GUILD_A, self.c)
        [waiting] = await self.memory.worked_out_waiting(GUILD_A, self.c)
        await self.memory.update_relation(
            GUILD_A, self.c, waiting.relation_id, source="dm", status=REJECTED
        )
        await self.run_step()
        self.assertEqual(self.posted, [])

    async def test_no_memory_or_no_campaign_posts_nothing(self) -> None:
        self.bot.memory = None
        await self.run_step()
        self.table.campaign_id = None
        self.bot.memory = self.memory
        await self.run_step()
        self.assertEqual(self.posted, [])

    async def test_the_log_has_the_count_only(self) -> None:
        await self.state(self.aldric, "mother", self.bryn)
        await self.state(self.bryn, "sister", self.mira)
        with self.assertLogs("dmbot", level=logging.DEBUG) as logs:
            await self.run_step()
        shown = "\n".join(logs.output)
        self.assertIn("Worked-out family links: 1 waiting", shown)
        for name in ("Aldric", "Bryn", "Mira"):
            self.assertNotIn(name, shown)
