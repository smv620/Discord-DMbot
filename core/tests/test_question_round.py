"""❓ Questions for you (#3435): the round that counts, steps each DM's timer and sends. Uses a fake
Discord; the database is real. Invented names throughout."""

from __future__ import annotations

from dmbot.memory.models import DM, PROPOSED
from dmbot.memory.store import MemoryStore
from dmbot.questions import kinds
from dmbot.questions.counts import Sources
from dmbot.questions.loop import Round
from dmbot.questions.notices import NoticeStore
from dmbot.questions.store import QuestionCounts
from tests.test_recap_web import BOB, Base
from tests.test_web_api import ALICE, THURSDAY

START = 1_800_000_000


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, int]] = []
        self.deleted: list[tuple[int, int]] = []
        self.busy: set[str] = set()
        self._next = 100

    async def recording(self, guild_id: int, campaign_id: str) -> bool:
        return campaign_id in self.busy

    async def send(self, user_id: int, text: str) -> int | None:
        self._next += 1
        self.sent.append((user_id, text, self._next))
        return self._next

    async def delete(self, user_id: int, message_id: int) -> None:
        self.deleted.append((user_id, message_id))


class TheRound(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = float(START)
        self.g = THURSDAY.id
        self.memory = MemoryStore(self.db)
        self.sender = FakeSender()
        async with self.db.guild(self.g) as conn:  # (the fixture's recaps are already posted)
            await conn.execute("UPDATE recap_jobs SET status = 'posted'")
        self.round = self.make()

    def make(self) -> Round:
        async def campaigns(guild_id: int) -> list:  # type: ignore[type-arg]
            return await self.campaign_store.list_campaigns(guild_id)

        return Round(
            sources=Sources(db=self.db, memory=self.memory),
            counts=QuestionCounts(self.db, clock=lambda: self.now),
            notices=NoticeStore(self.db, clock=lambda: self.now),
            sender=self.sender,
            campaigns=campaigns,
            clock=lambda: self.now,
        )

    @property
    def campaign_store(self):  # type: ignore[no-untyped-def]
        return self.campaigns

    async def ask(self, campaign: str, name: str = "Hrothgar") -> str:
        made = await self.memory.add_entity(
            self.g, campaign, type="character", name=name, source=DM, status=PROPOSED
        )
        return made.value.id

    async def tick(self, seconds: int = 0) -> int:
        self.now += seconds
        return await self.round.run([self.g])

    async def test_a_new_question_tells_its_dm_once_and_never_the_other_campaigns_dm(self) -> None:
        await self.ask(self.one.id)
        self.assertEqual(await self.tick(), 1)
        ((user, text, _),) = self.sender.sent
        self.assertEqual(user, ALICE.id)
        self.assertEqual(text, "❓ DMbot has a question for you about **One**.")
        self.assertEqual(await self.tick(10), 0)  # not again at once
        counts = await QuestionCounts(self.db).read(self.g, self.one.id)
        self.assertEqual(counts[kinds.NAMES].n, 1)
        self.assertNotIn(BOB.id, [u for u, _t, _i in self.sender.sent])

    async def test_reminders_come_one_two_four_minutes_apart_each_replacing_the_last(self) -> None:
        await self.ask(self.one.id)
        await self.tick()
        times = []
        for wait in (60, 120, 240):
            await self.tick(wait - 1)
            self.assertEqual(len(self.sender.sent), len(times) + 1)  # nothing early
            await self.tick(1)
            times.append(self.now)
        self.assertEqual(len(self.sender.sent), 4)
        self.assertEqual(self.sender.sent[1][1], "❓ Still waiting: 1 question about **One**.")
        ids = [i for _u, _t, i in self.sender.sent]
        self.assertEqual([i for _u, i in self.sender.deleted], ids[:-1])  # one at a time

    async def test_answering_a_question_sets_the_gap_back_and_the_last_answer_clears_it(
        self,
    ) -> None:
        first = await self.ask(self.one.id)
        await self.ask(self.one.id, "Maren")
        await self.tick()
        await self.tick(60)  # reminder 1
        await self.tick(120)  # reminder 2: the next gap is 4 minutes
        sent = len(self.sender.sent)
        await self.memory.confirm_entity(self.g, self.one.id, first, "character", source=DM)
        await self.tick(5)  # answered: the gap starts again from one minute
        await self.tick(59)
        self.assertEqual(len(self.sender.sent), sent)
        await self.tick(1)
        self.assertEqual(len(self.sender.sent), sent + 1)  # a reminder one minute after, not four
        async with self.db.guild(self.g) as conn:  # everything answered
            await conn.execute("UPDATE memory_entities SET status = 'confirmed'")
        deleted = len(self.sender.deleted)
        await self.tick(5)
        self.assertEqual(len(self.sender.deleted), deleted + 1)  # the reminder goes
        self.assertEqual(len(self.sender.sent), sent + 1)
        await self.tick(3600)
        self.assertEqual(len(self.sender.sent), sent + 1)  # and nothing more

    async def test_nothing_is_sent_while_recording_and_the_notice_comes_after(self) -> None:
        self.sender.busy.add(self.one.id)
        await self.ask(self.one.id)
        self.assertEqual(await self.tick(), 0)
        self.assertEqual(await self.tick(3600), 0)
        self.sender.busy.clear()
        self.assertEqual(await self.tick(60), 1)
        self.assertIn("has a question", self.sender.sent[0][1])

    async def test_not_now_stops_reminders_until_a_new_question(self) -> None:
        await self.ask(self.one.id)
        await self.tick()
        notices = NoticeStore(self.db)
        self.assertEqual(await notices.mute_all(ALICE.id, [self.g]), 1)
        sent = len(self.sender.sent)
        for wait in (60, 600, 6 * 3600):
            await self.tick(wait)
        self.assertEqual(len(self.sender.sent), sent)
        await self.ask(self.one.id, "Maren")
        await self.tick(10)
        await self.tick(60)
        self.assertEqual(len(self.sender.sent), sent + 1)

    async def test_two_campaigns_of_one_dm_share_one_message(self) -> None:
        await self.campaigns.add_dm(self.g, self.two.id, ALICE.id)
        await self.ask(self.one.id)
        await self.ask(self.two.id)
        await self.tick()
        alice = [t for u, t, _i in self.sender.sent if u == ALICE.id]
        self.assertEqual(len(alice), 1)
        self.assertIn("**One**: 1 question", alice[0])
        self.assertIn("**Two**: 1 question", alice[0])

    async def test_a_restart_does_not_tell_them_again(self) -> None:
        await self.ask(self.one.id)
        await self.tick()
        self.round = self.make()  # a fresh bot, the same database
        self.assertEqual(await self.tick(10), 0)
        self.assertEqual(len(self.sender.sent), 1)

    async def test_a_failed_send_is_not_retried_in_a_loop_and_nothing_raises(self) -> None:
        async def refuse(user_id: int, text: str) -> int | None:
            return None

        self.sender.send = refuse  # type: ignore[method-assign]
        await self.ask(self.one.id)
        self.assertEqual(await self.tick(), 0)

    async def test_a_failed_send_deletes_the_old_message_and_leaves_no_reminder_id(self) -> None:
        await self.ask(self.one.id)
        await self.tick()
        first = self.sender.sent[0][2]

        async def refuse(user_id: int, text: str) -> int | None:
            return None

        self.sender.send = refuse  # type: ignore[method-assign]
        await self.tick(60)
        self.assertEqual([i for _u, i in self.sender.deleted], [first])
        stored = await NoticeStore(self.db).get(self.g, self.one.id, ALICE.id)
        self.assertIsNone(stored.reminder_id)

    async def test_not_now_touches_only_the_presser_and_never_another_dm(self) -> None:
        await self.campaigns.add_dm(self.g, self.one.id, BOB.id)
        await self.ask(self.one.id)
        await self.tick()
        notices = NoticeStore(self.db)
        self.assertEqual(await notices.mute_all(ALICE.id, [self.g]), 1)
        self.assertTrue((await notices.get(self.g, self.one.id, ALICE.id)).notice.muted)
        self.assertFalse((await notices.get(self.g, self.one.id, BOB.id)).notice.muted)
        self.assertEqual(await notices.mute_all(ALICE.id, [self.g]), 0)  # (already muted)

    async def test_a_paused_campaign_is_left_alone(self) -> None:
        await self.ask(self.one.id)
        async with self.db.guild(self.g) as conn:
            await conn.execute("UPDATE campaigns SET paused = TRUE WHERE id = %s", (self.one.id,))
        self.assertEqual(await self.tick(), 0)

    async def test_a_finished_campaign_keeps_a_message_that_another_still_waiting_one_shares(
        self,
    ) -> None:
        await self.campaigns.add_dm(self.g, self.two.id, ALICE.id)
        first = await self.ask(self.one.id)
        await self.ask(self.two.id)
        await self.tick()  # one message names both
        await self.tick(10)
        await self.memory.confirm_entity(self.g, self.one.id, first, "character", source=DM)
        await self.tick(10)
        self.assertEqual(self.sender.deleted, [])  # campaign Two still waits and is in it
        stored = await NoticeStore(self.db).get(self.g, self.two.id, ALICE.id)
        self.assertEqual(stored.reminder_id, self.sender.sent[0][2])

    async def test_the_sent_view_holds_the_not_now_button_itself(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from dmbot.questions.discord import DiscordSender, NotNowButton

        bot = MagicMock()
        bot._dm_message = AsyncMock(return_value=MagicMock(id=7))
        await DiscordSender(bot).send(ALICE.id, "x")
        view = bot._dm_message.await_args.args[2]
        self.assertTrue(any(isinstance(item, NotNowButton) for item in view.children))
