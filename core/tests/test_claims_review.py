"""The DM's after-session questions about what was said (#1297): the words, the buttons, that
only the campaign's DM can answer, and the DM-screen post that says how many and never what.
Names are made up; no AI call anywhere."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.bot import DMBot
from dmbot.memory.claims import ClaimStore, Extracted
from dmbot.ui import claims_review
from tests.test_memory_store import DM, GUILD_A, MemoryTest

STRANGER = 99


def said(subject: str, relationship: str, obj: str, how: str = "dm_said") -> Extracted:
    return Extracted(subject, relationship, obj, "", ("1",), how)


class Review(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.bram = await self.add("Bram", type="character")
        self.hesper = await self.add("Hesper", type="character")
        self.bow = await self.add("Heartseeker", type="item")

    async def ask(self, *found: Extracted) -> None:
        await self.claims.propose(
            GUILD_A, self.c, list(found), {"1": 7}, opted_in={7}, prompt_version="p", model="m",
            session_started_at=1,
        )  # fmt: skip

    def interaction(self, user: int = DM) -> Any:
        sent: list[str] = []

        async def reply(text: str = "", **kw: Any) -> None:
            sent.append(str(kw.get("content", text)))

        it = MagicMock()
        it.user = SimpleNamespace(id=user)
        it.guild = SimpleNamespace(id=GUILD_A)
        it.client = SimpleNamespace(
            memory=self.memory,
            campaigns=self.campaigns,
            consent=SimpleNamespace(consenting=AsyncMock(return_value={7})),
        )
        it.response.is_done = lambda: False
        it.response.send_message = reply
        it.response.edit_message = reply
        it.followup.send = reply
        it.edit_original_response = reply
        it.sent = sent
        return it

    async def test_the_questions_are_in_plain_words_grouped_by_name_with_a_button_pair_each(
        self,
    ) -> None:
        await self.ask(
            said("Hesper", "is", "captured", "player_said"),
            said("Bram", "carries", "Heartseeker"),
            said("Hesper", "knows", "Bram"),
        )
        it = self.interaction()
        view = claims_review.ClaimsReview(self.c)
        text = await view.current_text(it)
        assert text is not None
        self.assertIn("**Bram** carries **Heartseeker**? _(you said)_", text)
        self.assertIn("**Hesper** is **captured**? _(a player said)_", text)
        self.assertLess(text.index("__Bram__"), text.index("__Hesper__"))
        self.assertEqual(text.count("__Hesper__"), 1)  # one heading for a name's questions
        self.assertEqual(len(view.children), 6)  # a yes and a no for each of three
        self.assertNotIn("extractor", text.lower())

    async def test_yes_remembers_it_and_no_turns_it_down_and_the_next_page_follows(self) -> None:
        await self.ask(said("Bram", "carries", "Heartseeker"), said("Hesper", "knows", "Bram"))
        it = self.interaction()
        view = claims_review.ClaimsReview(self.c)
        await view.current_text(it)
        first, second = view.page
        await view._decide(it, first.id, True)
        self.assertIn("✅ Remembered: **Bram** carries **Heartseeker**", it.sent[-1])
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 1)
        await view._decide(it, second.id, False)
        self.assertIn("❌ Not remembered: **Hesper** knows **Bram**", it.sent[-1])
        self.assertIn(claims_review.NOTHING_WAITING, it.sent[-1])
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 1)

    async def test_numbers_stay_put_while_a_page_is_answered_and_answered_ones_go_grey(
        self,
    ) -> None:
        await self.ask(
            said("Bram", "carries", "Heartseeker"), said("Hesper", "knows", "Bram"),
            said("Bram", "knows", "Hesper"),
        )  # fmt: skip
        it = self.interaction()
        view = claims_review.ClaimsReview(self.c)
        await view.current_text(it)
        order = [c.id for c in view.page]
        await view._decide(it, order[0], True)
        text = it.sent[-1]
        self.assertIn("1. ✅ Remembered:", text)
        self.assertIn("2. **", text)  # the others kept their numbers
        self.assertEqual(
            [getattr(b, "disabled", None) for b in view.children][:2], [True, True]
        )  # 1 is done
        self.assertEqual([getattr(b, "disabled", None) for b in view.children][2:], [False] * 4)
        self.assertIn("Top row ✅", text)

    async def test_a_speaker_who_stopped_since_deletes_the_question_and_says_so(self) -> None:
        await self.ask(said("Bram", "carries", "Heartseeker"))
        it = self.interaction()
        view = claims_review.ClaimsReview(self.c)
        await view.current_text(it)  # shown while they still agreed
        it.client.consent.consenting = AsyncMock(return_value=set())  # then they stop
        await view._decide(it, view.page[0].id, True)
        self.assertIn("stopped being recorded", it.sent[-1])
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])
        self.assertEqual(await self.claims.waiting(GUILD_A, self.c, opted_in={7}), [])

    async def test_a_question_answered_behind_the_pages_back_ends_instead_of_staying_live(
        self,
    ) -> None:
        await self.ask(said("Bram", "carries", "Heartseeker"), said("Hesper", "knows", "Bram"))
        it = self.interaction()
        view = claims_review.ClaimsReview(self.c)
        await view.current_text(it)
        first = view.page[0]
        await self.claims.reject(GUILD_A, self.c, first.id)  # answered on another screen
        await view._decide(it, first.id, True)
        self.assertIn("doesn't have that question any more", it.sent[-1])
        self.assertIn(first.id, view.answered)  # it ended: its buttons are off
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])
        (second,) = [c for c in view.page if c.id != first.id]
        await view._decide(it, second.id, False)  # and the page moves on
        self.assertIn(claims_review.NOTHING_WAITING, it.sent[-1])

    async def test_a_stopped_speakers_words_are_never_in_the_page(self) -> None:
        await self.claims.propose(
            GUILD_A, self.c, [said("Bram", "carries", "Heartseeker")], {"1": 8}, opted_in={8},
            prompt_version="p", model="m", session_started_at=1,
        )  # fmt: skip
        it = self.interaction()  # the bot says only speaker 7 agrees now
        view = claims_review.ClaimsReview(self.c)
        self.assertIsNone(await view.current_text(it))
        self.assertNotIn("Heartseeker", "\n".join(it.sent))

    async def test_a_second_holder_says_so_in_plain_words(self) -> None:
        await self.memory.set_unique(GUILD_A, self.c, self.bow, True, source="dm")
        await self.ask(
            said("Bram", "carries", "Heartseeker"), said("Hesper", "carries", "Heartseeker")
        )
        it = self.interaction()
        view = claims_review.ClaimsReview(self.c)
        await view.current_text(it)
        for claim in list(view.page):
            await view._decide(it, claim.id, True)
        self.assertIn("Someone else already has that, and there is only one", "\n".join(it.sent))

    async def test_nobody_but_the_campaigns_dm_can_answer(self) -> None:
        await self.ask(said("Bram", "carries", "Heartseeker"))
        (waiting,) = await self.claims.waiting(GUILD_A, self.c, opted_in={7})
        it = self.interaction(STRANGER)
        await claims_review.start_claims_review(it, self.c)
        await claims_review.ClaimsReview(self.c)._decide(it, waiting.id, True)
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])
        self.assertEqual(len(await self.claims.waiting(GUILD_A, self.c, opted_in={7})), 1)
        self.assertNotIn("Heartseeker", "\n".join(it.sent))  # nothing of the questions leaked

    async def test_with_nothing_waiting_it_says_so(self) -> None:
        it = self.interaction()
        await claims_review.start_claims_review(it, self.c)
        self.assertIn(claims_review.NOTHING_WAITING, it.sent[-1])


class DmScreen(MemoryTest):
    def table(self) -> Any:
        return SimpleNamespace(guild_id=GUILD_A, campaign_id=self.c, screen_channel_id=9)

    async def test_the_dm_screen_says_how_many_and_never_what(self) -> None:
        await self.add("Bram", type="character")
        await self.add("Heartseeker", type="item")
        await ClaimStore(self.memory).propose(
            GUILD_A, self.c, [said("Bram", "carries", "Heartseeker")], {"1": 7}, opted_in={7},
            prompt_version="p", model="m", session_started_at=1,
        )  # fmt: skip
        fake = SimpleNamespace(
            memory=self.memory,
            post=AsyncMock(),
            consent=SimpleNamespace(consenting=AsyncMock(return_value={7})),
        )
        await DMBot.ask_about_claims(fake, self.table())  # type: ignore[arg-type]
        text = fake.post.await_args.args[1]
        self.assertIn("1 thing said at the table", text)
        self.assertNotIn("Bram", text)
        self.assertNotIn("Heartseeker", text)

    async def test_with_nothing_waiting_or_the_extractor_off_nothing_is_posted(self) -> None:
        fake = SimpleNamespace(
            memory=self.memory,
            post=AsyncMock(),
            consent=SimpleNamespace(consenting=AsyncMock(return_value={7})),
        )
        await DMBot.ask_about_claims(fake, self.table())  # type: ignore[arg-type]
        fake.post.assert_not_called()
