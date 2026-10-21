"""Say it this way? (#2171): counting the cleaner's fixes, the end-of-session offers (consent, plan,
threshold), the store's rules, and the DM's review. Invented names throughout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.transcript.cleaner import DM_FIX, SOUND, SPELLING, Fix
from dmbot.ui import said_as_review as review
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest, make_table

SPEAKER = 7


def fix(entity_id: str, heard: str = "Kerrick", how: str = SPELLING) -> Fix:
    return Fix(0, len(heard), heard, "Cerric", entity_id, how)


class SaidAsTest(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.ui import names as ui

        self.bot.settings = SimpleNamespace(recap_audio_on=True, enforce_plans=False)  # type: ignore[assignment]
        self.cerric = await ui.save_name(self.memory, self.campaign, "Cerric", "npc", [], [])
        await self.consent.grant(GUILD, SPEAKER)

    def table(self, counts: dict[tuple[str, str, int], int]) -> Any:
        table = make_table(self.campaign.id)
        table.heard_forms = dict(counts)
        return table

    async def waiting(self) -> list[Any]:
        return await self.memory.said_as_suggestions(GUILD, self.campaign.id)

    async def offered(self, counts: dict[tuple[str, str, int], int]) -> list[Any]:
        await self.bot.suggest_said_as(self.table(counts))
        return await self.waiting()


class TheCounting(SaidAsTest):
    async def test_cleaner_fixes_are_counted_per_person_and_a_dms_own_fix_is_not(self) -> None:
        table = make_table(self.campaign.id)
        self.bot._count_heard_forms(
            table, SPEAKER, [fix(self.cerric.id), fix(self.cerric.id, "kerrick", SOUND)]
        )
        self.bot._count_heard_forms(table, SPEAKER, [fix(self.cerric.id, how=DM_FIX)])
        self.assertEqual(table.heard_forms, {(self.cerric.id, "kerrick", SPEAKER): 2})

    async def test_stopping_recording_forgets_that_persons_counts(self) -> None:
        table = make_table(self.campaign.id)
        self.bot.tables[GUILD] = table
        self.bot._count_heard_forms(table, SPEAKER, [fix(self.cerric.id)])
        self.bot._count_heard_forms(table, 8, [fix(self.cerric.id)])
        self.bot.stop_recording(GUILD, SPEAKER)
        self.assertEqual(list(table.heard_forms), [(self.cerric.id, "kerrick", 8)])


class TheOffers(SaidAsTest):
    async def test_three_fixes_make_an_offer_and_two_do_not(self) -> None:
        key = (self.cerric.id, "kerrick", SPEAKER)
        self.assertEqual(await self.offered({key: 2}), [])
        (one,) = await self.offered({key: 3})
        self.assertEqual((one.name, one.heard, one.times), ("Cerric", "kerrick", 3))

    async def test_only_people_who_still_agree_count(self) -> None:
        self.consent.stop_now(GUILD, SPEAKER)
        self.assertEqual(await self.offered({(self.cerric.id, "kerrick", SPEAKER): 5}), [])

    async def test_nothing_is_offered_without_spoken_recaps(self) -> None:
        self.bot.settings = SimpleNamespace(recap_audio_on=False, enforce_plans=False)  # type: ignore[assignment]
        self.assertEqual(await self.offered({(self.cerric.id, "kerrick", SPEAKER): 5}), [])
        self.bot.settings = SimpleNamespace(recap_audio_on=True, enforce_plans=False)  # type: ignore[assignment]
        self.bot.plan_allows = AsyncMock(return_value=False)  # type: ignore[method-assign]
        self.assertEqual(await self.offered({(self.cerric.id, "kerrick", SPEAKER): 5}), [])

    async def test_the_most_often_heard_spelling_wins(self) -> None:
        counts = {
            (self.cerric.id, "kerrick", SPEAKER): 3,
            (self.cerric.id, "kerric", SPEAKER): 5,
        }
        (one,) = await self.offered(counts)
        self.assertEqual(one.heard, "kerric")

    async def test_a_name_with_a_spelling_already_or_a_no_is_not_offered(self) -> None:
        key = (self.cerric.id, "kerrick", SPEAKER)
        await self.memory.set_said_as(
            GUILD, self.campaign.id, self.cerric.id, "Kair-ick", source="dm"
        )
        self.assertEqual(await self.offered({key: 3}), [])
        await self.memory.set_said_as(GUILD, self.campaign.id, self.cerric.id, None, source="dm")
        self.assertEqual(len(await self.offered({key: 3})), 1)
        await self.memory.answer_said_as(GUILD, self.campaign.id, self.cerric.id, None)  # no
        self.assertEqual(await self.offered({key: 9}), [])  # never again

    async def test_the_same_spelling_as_the_name_is_not_an_offer(self) -> None:
        self.assertEqual(await self.offered({(self.cerric.id, "cerric", SPEAKER): 5}), [])

    async def test_an_offer_goes_with_its_name(self) -> None:
        await self.offered({(self.cerric.id, "kerrick", SPEAKER): 3})
        await self.memory.set_entity_status(
            GUILD, self.campaign.id, self.cerric.id, "rejected", source="dm"
        )
        self.assertEqual(await self.waiting(), [])  # a forgotten name is not offered


class TheReview(SaidAsTest):
    async def start(self, user: int = DM) -> tuple[Any, Any]:
        await self.offered({(self.cerric.id, "kerrick", SPEAKER): 3})
        it = self.it(user)
        await review.start_said_as_review(it, self.campaign.id)
        if not it.response.sent or "view" not in it.response.sent[0][1]:
            return it, None
        return it, it.response.sent[0][1]["view"]

    async def test_the_offer_reads_plainly_and_has_four_answers(self) -> None:
        it, view = await self.start()
        self.assertIn("People say **Cerric** like **kerrick**", it.response.sent[0][0])
        labels = [b.label for b in view.children]
        self.assertEqual(labels, ["✅ Yes", "❌ No", "✏️ Type it", "⏳ Later"])

    async def test_yes_sets_the_spelling_and_ends_the_offer(self) -> None:
        _, view = await self.start()
        await view._yes(self.it())
        self.assertEqual(
            await self.memory.said_as(GUILD, self.campaign.id, self.cerric.id), "kerrick"
        )
        self.assertEqual(await self.waiting(), [])

    async def test_no_is_remembered_and_changes_nothing(self) -> None:
        _, view = await self.start()
        await view._no(self.it())
        self.assertIsNone(await self.memory.said_as(GUILD, self.campaign.id, self.cerric.id))
        self.assertEqual(await self.waiting(), [])
        self.assertEqual(await self.offered({(self.cerric.id, "kerrick", SPEAKER): 9}), [])

    async def test_typing_your_own_sets_that(self) -> None:
        _, view = await self.start()
        form = review.TypeIt(view, view.shown)
        form.said_as = SimpleNamespace(value="  Kair-ick ")  # type: ignore[assignment]
        await form.on_submit(self.it())
        self.assertEqual(
            await self.memory.said_as(GUILD, self.campaign.id, self.cerric.id), "Kair-ick"
        )

    async def test_later_keeps_it_waiting(self) -> None:
        _, view = await self.start()
        await view._later(self.it())
        self.assertEqual(len(await self.waiting()), 1)

    async def test_only_a_dm_gets_or_answers_the_offer(self) -> None:
        it, view = await self.start(MANAGER)
        self.assertIsNone(view)
        self.assertIn("Only a DM", it.response.sent[0][0])
        _, view = await self.start()
        press = self.it(MANAGER)
        await view._yes(press)  # the same view pressed by someone who is not a DM
        self.assertIn("Only a DM", press.response.sent[0][0])
        self.assertIsNone(await self.memory.said_as(GUILD, self.campaign.id, self.cerric.id))

    async def test_the_home_screen_counts_them_for_dms_only(self) -> None:
        from dmbot.ui import names as ui

        await self.offered({(self.cerric.id, "kerrick", SPEAKER): 3})
        campaign = await self.campaigns.get(GUILD, self.campaign.id)
        assert campaign is not None
        self.assertEqual((await ui.waiting_reviews(self.it(), campaign)).said, 1)
        self.assertEqual((await ui.waiting_reviews(self.it(MANAGER), campaign)).said, 0)
        self.assertIn("to say", ui.review_line(ui.Reviews(said=1)) or "")
