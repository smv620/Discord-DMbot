"""The DM's after-session questions about what was said (#1297): the words, the buttons, that
only the campaign's DM can answer, and the DM-screen post that says how many and never what.
Names are made up; no AI call anywhere."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.bot import DMBot
from dmbot.memory.claims import PAGE, ClaimStore, Extracted
from dmbot.memory.models import MemoryRuleError
from dmbot.ui import claims_review
from tests.asking import with_asking
from tests.test_memory_store import DM, GUILD_A, ConfirmedMemoryTest

STRANGER = 99


def said(subject: str, relationship: str, obj: str, how: str = "dm_said") -> Extracted:
    return Extracted(subject, relationship, obj, "", ("1",), how)


class Review(ConfirmedMemoryTest):
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
        it.response.defer = AsyncMock()
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
        view = claims_review.ClaimsReview(self.c, GUILD_A)
        text = await view.current_text(it)
        assert text is not None
        self.assertIn("**Bram** carries **Heartseeker**? _(you said)_", text)
        self.assertIn("**Hesper** is **captured**? _(a player said)_", text)
        self.assertLess(text.index("__Bram__"), text.index("__Hesper__"))
        self.assertEqual(text.count("__Hesper__"), 1)  # one heading for a name's questions
        # a yes and a no for each of three, a secret yes for the two that are not a state, and the
        # two buttons that answer the whole page (#3624)
        self.assertEqual(len(view.children), 10)
        self.assertNotIn("extractor", text.lower())

    async def test_yes_remembers_it_and_no_turns_it_down_and_the_next_page_follows(self) -> None:
        await self.ask(said("Bram", "carries", "Heartseeker"), said("Hesper", "knows", "Bram"))
        it = self.interaction()
        view = claims_review.ClaimsReview(self.c, GUILD_A)
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
        view = claims_review.ClaimsReview(self.c, GUILD_A)
        await view.current_text(it)
        order = [c.id for c in view.page]
        await view._decide(it, order[0], True)
        text = it.sent[-1]
        self.assertIn("1. ✅ Remembered:", text)
        self.assertIn("2. **", text)  # the others kept their numbers
        rows = [b for b in view.children if "All" not in str(getattr(b, "label", ""))]
        self.assertEqual(
            [getattr(b, "disabled", None) for b in rows][:3], [True] * 3
        )  # 1 is done: its yes, its secret yes and its no
        self.assertEqual([getattr(b, "disabled", None) for b in rows][3:9], [False] * 6)
        self.assertIn("✅ All true", text)

    async def test_truth_but_secret_remembers_it_and_keeps_it_from_the_players(self) -> None:
        await self.ask(said("Bram", "carries", "Heartseeker"), said("Hesper", "knows", "Bram"))
        it = self.interaction()
        view = claims_review.ClaimsReview(self.c, GUILD_A)
        await view.current_text(it)
        first, second = view.page
        await view._decide(it, first.id, True, secret=True)
        self.assertIn("🤫 Remembered, and kept secret from your players: **Bram**", it.sent[-1])
        await view._decide(it, second.id, True)
        known = {
            r.secret for r in await self.memory.relations(GUILD_A, self.c, include_secret=True)
        }
        self.assertEqual(known, {True, False})  # the first is secret, the second is not
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 1)  # players: one

    async def test_a_remembered_fact_can_be_taken_back_while_the_page_is_open(self) -> None:
        await self.ask(said("Bram", "carries", "Heartseeker"), said("Hesper", "knows", "Bram"))
        it = self.interaction()
        view = claims_review.ClaimsReview(self.c, GUILD_A)
        await view.current_text(it)
        first = view.page[0]
        await view._decide(it, first.id, True)
        self.assertIn(first.id, view.undoable)
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 1)
        await view._take_back(it, first.id)
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])  # gone again
        self.assertIn("↩️ Taken back", it.sent[-1])
        self.assertNotIn(first.id, view.undoable)
        again = await self.claims.get(GUILD_A, self.c, first.id)
        assert again is not None
        self.assertEqual((again.status, again.fact_id), ("proposed", None))  # asked again

    async def test_a_state_has_no_secret_button(self) -> None:
        await self.ask(said("Hesper", "is", "captured", "player_said"))
        view = claims_review.ClaimsReview(self.c, GUILD_A)
        await view.current_text(self.interaction())
        self.assertEqual(len(view.children), 3)  # a yes and a no, and All true; no 🤫 anywhere

    async def test_a_speaker_who_stopped_since_deletes_the_question_and_says_so(self) -> None:
        await self.ask(said("Bram", "carries", "Heartseeker"))
        it = self.interaction()
        view = claims_review.ClaimsReview(self.c, GUILD_A)
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
        view = claims_review.ClaimsReview(self.c, GUILD_A)
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
        view = claims_review.ClaimsReview(self.c, GUILD_A)
        self.assertIsNone(await view.current_text(it))
        self.assertNotIn("Heartseeker", "\n".join(it.sent))

    async def test_a_new_item_the_judge_kept_is_stored_unique(self) -> None:
        # (#3618) the session path stores the judge's yes like the lore path does
        await self.ask(said("Bram", "carries", "Moonblade"))
        made = [e for e in await self.memory.entities(GUILD_A, self.c, statuses=("proposed",))]
        (moonblade,) = [e for e in made if e.name == "Moonblade"]
        self.assertTrue(moonblade.unique_item)
        await self.ask(said("Bram", "knows", "Marek"))  # (a person is never stored unique)
        marek = next(
            e
            for e in await self.memory.entities(GUILD_A, self.c, statuses=("proposed",))
            if e.name == "Marek"
        )
        self.assertIsNone(marek.unique_item)

    async def test_a_second_holder_says_so_in_plain_words(self) -> None:
        await self.memory.set_unique(GUILD_A, self.c, self.bow, True, source="dm")
        await self.ask(
            said("Bram", "carries", "Heartseeker"), said("Hesper", "carries", "Heartseeker")
        )
        it = self.interaction()
        view = claims_review.ClaimsReview(self.c, GUILD_A)
        await view.current_text(it)
        for claim in list(view.page):
            await view._decide(it, claim.id, True)
        self.assertIn("Someone else already has that, and there is only one", "\n".join(it.sent))

    async def test_nobody_but_the_campaigns_dm_can_answer(self) -> None:
        await self.ask(said("Bram", "carries", "Heartseeker"))
        (waiting,) = await self.claims.waiting(GUILD_A, self.c, opted_in={7})
        it = self.interaction(STRANGER)
        await claims_review.start_claims_review(it, self.c)
        await claims_review.ClaimsReview(self.c, GUILD_A)._decide(it, waiting.id, True)
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])
        self.assertEqual(len(await self.claims.waiting(GUILD_A, self.c, opted_in={7})), 1)
        self.assertNotIn("Heartseeker", "\n".join(it.sent))  # nothing of the questions leaked

    async def page(self, *found: Extracted, user: int = DM) -> tuple[Any, Any]:
        await self.ask(*found)
        it = self.interaction(user)
        view = claims_review.ClaimsReview(self.c, GUILD_A)
        await view.current_text(it)
        return it, view

    def labelled(self, view: Any, label: str) -> Any:
        return next(b for b in view.children if str(getattr(b, "label", "")) == label)

    async def waiting(self) -> int:
        return len(await self.claims.waiting(GUILD_A, self.c, opted_in={7}))

    async def test_all_true_answers_exactly_the_questions_waiting_on_the_page(self) -> None:
        it, view = await self.page(
            said("Bram", "carries", "Heartseeker"), said("Hesper", "knows", "Bram"),
            said("Bram", "knows", "Hesper"),
        )  # fmt: skip
        first = view.page[0]
        await view._decide(it, first.id, False)  # one is already answered: it is left alone
        await view._answer_all(secret=False)(it)
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 2)
        self.assertIn("Saved 2 as true", it.sent[-1])
        self.assertIn(claims_review.NOTHING_WAITING, it.sent[-1])
        self.assertEqual(await self.waiting(), 0)

    async def test_it_never_answers_questions_on_a_page_not_shown(self) -> None:
        names = [f"Hero{n}" for n in range(PAGE + 2)]
        for name in names:
            await self.add(name, type="character")
        it, view = await self.page(*[said(n, "carries", "Heartseeker") for n in names])
        self.assertEqual(len(view.page), PAGE)
        await view._answer_all(secret=False)(it)
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), PAGE)
        self.assertEqual(await self.waiting(), 2)  # the next page is still waiting
        self.assertIn("Hero", it.sent[-1])  # ... and is shown now, as today

    async def test_all_secret_keeps_them_from_the_players_and_skips_the_states(self) -> None:
        it, view = await self.page(
            said("Bram", "carries", "Heartseeker"), said("Hesper", "is", "captured", "player_said")
        )
        await view._answer_all(secret=True)(it)
        known = await self.memory.relations(GUILD_A, self.c, include_secret=True)
        self.assertEqual([r.secret for r in known], [True])
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])  # players: none
        self.assertIn("Saved 1 as true, and secret from your players", it.sent[-1])
        self.assertIn("1 left for you: a state like “is dead” can't be secret", it.sent[-1])
        self.assertEqual(await self.waiting(), 1)

    async def test_one_undo_takes_the_whole_press_back_and_asks_again(self) -> None:
        it, view = await self.page(
            said("Bram", "carries", "Heartseeker"), said("Hesper", "knows", "Bram")
        )
        await view._answer_all(secret=False)(it)
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 2)
        self.assertEqual(len(view.last_bulk), 2)
        self.assertEqual(self.labelled(view, "↩️ Undo all 2").row, 0)  # still on offer
        await view._take_back_all(it)
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])
        self.assertEqual(await self.waiting(), 2)
        self.assertIn("Undid 2 answers", it.sent[-1])
        self.assertEqual(view.last_bulk, [])

    async def test_a_refused_one_is_counted_and_the_rest_still_go_in(self) -> None:
        it, view = await self.page(
            said("Bram", "carries", "Heartseeker"), said("Bram", "knows", "Hesper")
        )
        # the item's name is turned down on another screen after the page was shown
        await self.memory.set_entity_status(GUILD_A, self.c, self.bow, "rejected", source="dm")
        await view._answer_all(secret=False)(it)
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 1)  # the other went in
        self.assertIn("Saved 1 as true", it.sent[-1])
        self.assertIn("1 not saved: Can't write that now", it.sent[-1])
        self.assertEqual(len(view.last_bulk), 1)  # and one Undo for what did go in

    async def test_a_second_holder_is_saved_and_flagged_for_a_later_question(self) -> None:
        await self.memory.set_unique(GUILD_A, self.c, self.bow, True, source="dm")
        it, view = await self.page(
            said("Bram", "carries", "Heartseeker"), said("Hesper", "carries", "Heartseeker")
        )
        await view._answer_all(secret=False)(it)
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 2)
        self.assertIn("⚠️ 1 saved, and DMbot noticed something odd. It will ask you.", it.sent[-1])

    async def test_a_speaker_who_stopped_since_is_counted_and_the_page_still_moves_on(self) -> None:
        it, view = await self.page(said("Bram", "carries", "Heartseeker"))
        it.client.consent.consenting = AsyncMock(return_value=set())
        await view._answer_all(secret=False)(it)
        self.assertIn("Saved 0 as true", it.sent[-1])
        self.assertIn("stopped being recorded", it.sent[-1])
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])

    async def test_a_single_answer_after_the_press_ends_the_whole_undo(self) -> None:
        it, view = await self.page(
            said("Bram", "carries", "Heartseeker"), said("Hesper", "knows", "Bram"),
            said("Bram", "knows", "Hesper"),
        )  # fmt: skip
        first = view.page[0]
        await view._decide(it, first.id, True)
        await view._answer_all(secret=False)(it)
        self.assertEqual(len(view.last_bulk), 2)
        await view._take_back_all(it)
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 1)  # the first stays

    async def test_nobody_but_the_dm_can_use_the_two_buttons(self) -> None:
        it, view = await self.page(said("Bram", "carries", "Heartseeker"), user=STRANGER)
        await view._answer_all(secret=False)(it)
        await view._answer_all(secret=True)(it)
        self.assertEqual(await self.memory.relations(GUILD_A, self.c, include_secret=True), [])
        self.assertEqual(await self.waiting(), 1)
        self.assertNotIn("Heartseeker", "\n".join(it.sent))
        await view._take_back_all(it)  # and nothing to take back

    async def test_the_colours_the_secret_row_is_blurple_and_all_true_is_green(self) -> None:
        import discord

        _, view = await self.page(said("Bram", "carries", "Heartseeker"))
        self.assertEqual(self.labelled(view, "🤫 1").style, discord.ButtonStyle.primary)
        self.assertEqual(self.labelled(view, "🤫 All secret").style, discord.ButtonStyle.primary)
        self.assertEqual(self.labelled(view, "✅ All true").style, discord.ButtonStyle.success)
        self.assertEqual(self.labelled(view, "❌ 1").style, discord.ButtonStyle.secondary)
        rows = {b.label: b.row for b in view.children}
        self.assertEqual(
            [rows[k] for k in ("✅ All true", "✅ 1", "🤫 1", "❌ 1")], [0, 1, 2, 3]
        )  # the whole-page buttons share the top row, above the rows

    async def test_the_press_answers_discord_first_before_any_write(self) -> None:
        it, view = await self.page(said("Bram", "carries", "Heartseeker"))
        order: list[str] = []
        it.response.defer = AsyncMock(side_effect=lambda *a, **k: order.append("defer"))
        real = ClaimStore.confirm

        async def spy(self_: Any, *a: Any, **k: Any) -> Any:
            order.append("confirm")
            return await real(self_, *a, **k)

        with patch.object(ClaimStore, "confirm", spy):
            await view._answer_all(secret=False)(it)
        self.assertEqual(order[:2], ["defer", "confirm"])

    async def test_an_error_part_way_keeps_what_was_saved_with_its_undo_and_says_so(self) -> None:
        it, view = await self.page(
            said("Bram", "carries", "Heartseeker"), said("Bram", "knows", "Hesper"),
            said("Hesper", "knows", "Bram"),
        )  # fmt: skip
        real = ClaimStore.confirm
        calls = {"n": 0}

        async def flaky(self_: Any, *a: Any, **k: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("down")
            return await real(self_, *a, **k)

        with patch.object(ClaimStore, "confirm", flaky):
            await view._answer_all(secret=False)(it)
        self.assertEqual(len(await self.memory.relations(GUILD_A, self.c)), 2)
        self.assertEqual(len(view.last_bulk), 2)  # the two that went in can be taken back
        self.assertIn("Saved 2 as true", it.sent[-1])
        self.assertIn("1 could not be saved. Try those again.", it.sent[-1])
        self.assertNotIn("down", it.sent[-1])  # the error's text is never shown
        self.assertEqual(await self.waiting(), 1)

    async def test_a_second_press_while_the_first_is_writing_waits(self) -> None:
        it, view = await self.page(said("Bram", "carries", "Heartseeker"))
        view.busy = True
        await view._answer_all(secret=False)(it)
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])
        self.assertIn("Still saving", it.sent[-1])

    async def test_a_press_that_keeps_nothing_leaves_the_earlier_undo_alone(self) -> None:
        it, view = await self.page(
            said("Bram", "carries", "Heartseeker"), said("Bram", "knows", "Hesper")
        )
        view.last_bulk = [("x", 1)]  # an earlier press's Undo
        it.client.consent.consenting = AsyncMock(return_value=set())  # nothing can be kept now
        await view._answer_all(secret=False)(it)
        self.assertEqual(view.last_bulk, [("x", 1)])

    async def test_a_long_reason_is_cut_in_the_summary(self) -> None:
        it, view = await self.page(said("Bram", "carries", "Heartseeker"))

        async def refuse(self_: Any, *a: Any, **k: Any) -> Any:
            raise MemoryRuleError("x" * 900)

        with patch.object(ClaimStore, "confirm", refuse):
            await view._answer_all(secret=False)(it)
        self.assertLess(len(it.sent[-1]), 1500)

    async def test_an_all_answered_page_offers_neither_button(self) -> None:
        it, view = await self.page(said("Bram", "carries", "Heartseeker"))
        await view._answer_all(secret=False)(it)
        self.assertFalse(
            [b for b in view.children if str(getattr(b, "label", "")).endswith("All true")]
        )

    async def test_with_nothing_waiting_it_says_so(self) -> None:
        it = self.interaction()
        await claims_review.start_claims_review(it, self.c)
        self.assertIn(claims_review.NOTHING_WAITING, it.sent[-1])


class DmScreen(ConfirmedMemoryTest):
    def table(self) -> Any:
        return SimpleNamespace(guild_id=GUILD_A, campaign_id=self.c, screen_channel_id=9, asks=None)

    async def test_the_dm_screen_says_how_many_and_never_what(self) -> None:
        await self.add("Bram", type="character")
        await self.add("Heartseeker", type="item")
        await ClaimStore(self.memory).propose(
            GUILD_A, self.c, [said("Bram", "carries", "Heartseeker")], {"1": 7}, opted_in={7},
            prompt_version="p", model="m", session_started_at=1,
        )  # fmt: skip
        fake = with_asking(
            SimpleNamespace(
                memory=self.memory,
                post=AsyncMock(),
                ask_privately=AsyncMock(return_value=False),  # no DM can be reached: the screen
                consent=SimpleNamespace(consenting=AsyncMock(return_value={7})),
            )
        )
        table = self.table()
        await DMBot.note_claims(fake, table)  # type: ignore[arg-type]
        fake.post.assert_not_called()  # counted, not yet told: the summary may carry it
        self.assertEqual(table.claims_waiting, 1)
        # the words are kept for the DM-only summary; the post below never has them
        self.assertEqual(len(table.claim_preview), 1)
        self.assertIn("Bram", table.claim_preview[0])
        await DMBot.post_claims(fake, table)  # type: ignore[arg-type]
        self.assertEqual(table.claims_waiting, 0)  # told once
        text = fake.post.await_args.args[1]
        self.assertIn("1 thing said at the table", text)
        self.assertNotIn("Bram", text)
        self.assertNotIn("Heartseeker", text)

    async def test_the_question_goes_to_the_dms_private_chat_not_the_screen(self) -> None:
        # #3210: asked in the DM's private chat; the DM screen only when no DM can be reached.
        fake = with_asking(
            SimpleNamespace(post=AsyncMock(), ask_privately=AsyncMock(return_value=True))
        )
        table = self.table()
        table.claims_waiting = 2
        table.claim_preview = []
        await DMBot.post_claims(fake, table)  # type: ignore[arg-type]
        fake.post.assert_not_called()
        args = fake.ask_privately.await_args.args
        self.assertEqual(args[:2], (GUILD_A, self.c))
        self.assertIn("2 things said at the table", args[2])
        self.assertNotIn("only you will see", args[2])  # nobody else reads a private chat

    async def test_with_nothing_waiting_or_the_extractor_off_nothing_is_posted(self) -> None:
        fake = SimpleNamespace(
            memory=self.memory,
            post=AsyncMock(),
            consent=SimpleNamespace(consenting=AsyncMock(return_value={7})),
        )
        table = self.table()
        await DMBot.note_claims(fake, table)  # type: ignore[arg-type]
        await DMBot.post_claims(fake, table)  # type: ignore[arg-type]
        fake.post.assert_not_called()


class StewardLog(unittest.IsolatedAsyncioTestCase):
    async def test_the_session_numbers_are_logged_once_and_start_over(self) -> None:
        from dmbot.memory.ontology import Tally

        table = SimpleNamespace(steward_tally=Tally())
        table.steward_tally.reused = 2
        table.steward_tally.refused["rules word"] = 1
        with self.assertLogs("dmbot.memory.ontology", level="INFO") as logged:
            await DMBot.log_steward_tally(SimpleNamespace(), table)  # type: ignore[arg-type]
        self.assertIn("reused 2", logged.output[0])
        self.assertIn("1 rules word", logged.output[0])
        self.assertEqual(table.steward_tally.reused, 0)  # a new session starts at zero

    async def test_nothing_is_logged_when_nothing_was_decided(self) -> None:
        from dmbot.memory.ontology import Tally

        table = SimpleNamespace(steward_tally=Tally())
        with self.assertNoLogs("dmbot.memory.ontology", level="INFO"):
            await DMBot.log_steward_tally(SimpleNamespace(), table)  # type: ignore[arg-type]
