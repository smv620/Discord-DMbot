"""The bot-level people flows (#1208 item 7): learning after a session, Stop recording me when
removing the learned names fails, and the DM's Check who is who buttons. Names are made up."""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.bot import DMBot
from dmbot.memory.models import PeopleSuggestion
from dmbot.ui import people_review
from tests.test_memory_store import DM, GUILD_A, MemoryTest
from tests.test_people import PLAYER

CAMPAIGN = "c" * 32


class Stopping(unittest.IsolatedAsyncioTestCase):
    async def test_a_failing_cleanup_never_blocks_stopping(self) -> None:
        fake = SimpleNamespace(
            consent=SimpleNamespace(
                has_consent=lambda *_: True, revoke=AsyncMock(return_value=True)
            ),
            stop_recording=MagicMock(),
            _tell_dm_about_consent=MagicMock(),
            push_allowlist=AsyncMock(),
            heard_questions=None,
            members=None,
            memory=SimpleNamespace(forget_learned=AsyncMock(side_effect=RuntimeError("db down"))),
        )
        with self.assertLogs("dmbot.bot", level=logging.ERROR) as logged:
            stopped = await DMBot.withdraw_consent(fake, 1, 2)  # type: ignore[arg-type]
        self.assertTrue(stopped)
        fake.stop_recording.assert_called_once()  # stopped at once
        fake.consent.revoke.assert_awaited_once()  # and saved
        self.assertIn("Could not remove the names learned", logged.output[0])


class Learning(unittest.IsolatedAsyncioTestCase):
    def table(self) -> Any:
        answers = [
            SimpleNamespace(name="Mike", speaker=1, times=3),
            SimpleNamespace(name="Vera", speaker=2, times=2),
        ]
        return SimpleNamespace(
            guild_id=1,
            campaign_id=CAMPAIGN,
            screen_channel_id=9,
            name_answers=SimpleNamespace(answered=lambda: answers),
        )

    def suggestion(self, user: int) -> PeopleSuggestion:
        return PeopleSuggestion("name", user, "Name", "row", None, 2, "Shown")

    async def test_someone_who_stopped_meanwhile_is_forgotten_and_not_counted(self) -> None:
        written = SimpleNamespace(value=[self.suggestion(1), self.suggestion(2)])
        fake = SimpleNamespace(
            memory=SimpleNamespace(
                learn_people=AsyncMock(return_value=written), forget_learned=AsyncMock()
            ),
            consent=SimpleNamespace(consenting=AsyncMock(side_effect=[{1, 2}, {1}])),
            post=AsyncMock(),
        )
        await DMBot.learn_people(fake, self.table())  # type: ignore[arg-type]
        fake.memory.forget_learned.assert_awaited_once_with(1, 2)
        text = fake.post.await_args.args[1]
        self.assertIn("1 quick question", text)  # only the one who still agrees

    async def test_nobody_who_agrees_means_nothing_is_written(self) -> None:
        fake = SimpleNamespace(
            memory=SimpleNamespace(learn_people=AsyncMock(), forget_learned=AsyncMock()),
            consent=SimpleNamespace(consenting=AsyncMock(return_value=set())),
            post=AsyncMock(),
        )
        await DMBot.learn_people(fake, self.table())  # type: ignore[arg-type]
        fake.memory.learn_people.assert_not_called()
        fake.post.assert_not_called()


class Buttons(MemoryTest):
    def interaction(self, user: int = DM) -> Any:
        sent: list[str] = []

        async def edit(**kw: Any) -> None:
            sent.append(str(kw.get("content")))

        async def send(text: str = "", **kw: Any) -> None:
            sent.append(text)

        bot = SimpleNamespace(
            memory=self.memory,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=None)),
            name_of=lambda guild, user_id: "Mia",
            consent=SimpleNamespace(consenting=AsyncMock(return_value={PLAYER})),
        )
        it = MagicMock()
        it.user = SimpleNamespace(id=user)
        it.guild = SimpleNamespace(id=GUILD_A)
        it.client = bot
        it.response.is_done = lambda: False
        it.response.send_message = send
        it.response.edit_message = edit
        it.followup.send = send
        it.edit_original_response = edit
        it.sent = sent
        return it

    async def test_yes_makes_the_name_theirs_and_no_is_not_asked_again(self) -> None:
        await self.memory.learn_people(
            GUILD_A, self.c, [("Mike", PLAYER, 2), ("Vera", PLAYER, 2)], limit=10
        )
        it = self.interaction()
        # The campaign check is the screen's own; here the DM is let through.
        real = self.campaigns.get
        it.client.campaigns.get = real
        view = people_review.PeopleReview(self.c)
        text = await view.current_text(it)
        assert text is not None
        self.assertIn("another name for **Mia**", text)
        await view._yes(it)
        await view._no(it)
        left = await self.memory.people_suggestions(GUILD_A, self.c)
        self.assertEqual(left, [])
        confirmed = {
            a.key for a in await self.memory.aliases(GUILD_A, self.c) if a.status == "confirmed"
        }
        self.assertEqual(len({"mike", "vera"} & confirmed), 1)  # one yes, the other refused

    async def test_later_keeps_the_question_waiting(self) -> None:
        await self.memory.learn_people(GUILD_A, self.c, [("Mike", PLAYER, 2)], limit=10)
        it = self.interaction()
        it.client.campaigns.get = self.campaigns.get
        view = people_review.PeopleReview(self.c)
        await view.current_text(it)
        await view._later(it)
        self.assertEqual(len(await self.memory.people_suggestions(GUILD_A, self.c)), 1)
        self.assertIn(people_review.NOTHING_WAITING, it.sent[-1])


class TranscriptLabels(unittest.IsolatedAsyncioTestCase):
    """The download's {character} label (#1208): the entry's player, else the `plays` link;
    a DM keeps the nickname."""

    async def test_labels_come_from_the_entry_then_the_plays_link_and_never_for_a_dm(self) -> None:
        from dmbot.memory.lookup import CampaignLookup, LookupData
        from dmbot.ui import transcripts

        bot = SimpleNamespace(
            lookup=SimpleNamespace(
                get=AsyncMock(return_value=CampaignLookup.build(LookupData(1, (), (), (), ())))
            ),
            memory=SimpleNamespace(
                table_people=AsyncMock(
                    return_value=[(1, ["Cerric", "Vera"], []), (2, [], []), (3, ["Hiro"], [])]
                )
            ),
        )
        got = await transcripts.player_characters(bot, 1, CAMPAIGN, {3})  # type: ignore[arg-type]
        self.assertEqual(got, {1: "Vera"})  # the newest link; 2 plays nobody; 3 is a DM

    async def test_a_failing_read_still_gives_a_file(self) -> None:
        from dmbot.ui import transcripts

        bot = SimpleNamespace(
            lookup=None,
            memory=SimpleNamespace(table_people=AsyncMock(side_effect=RuntimeError("down"))),
        )
        with self.assertLogs("dmbot.ui.transcripts", level=logging.ERROR):
            got = await transcripts.player_characters(bot, 1, CAMPAIGN)  # type: ignore[arg-type]
        self.assertEqual(got, {})


if __name__ == "__main__":
    unittest.main()
