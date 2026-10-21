"""Say it this way? (#2171): counting the cleaner's fixes, the end-of-session offers (consent, plan,
threshold), the store's rules, and the DM's review. Invented names throughout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.memory.models import MemoryRuleError
from dmbot.memory.scene import find_mentions
from dmbot.transcript.cleaner import DM_FIX, SOUND, SPELLING, Fix
from dmbot.ui import said_as_review as review
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest, make_table

SPEAKER = 7


def fix(
    entity_id: str,
    heard: str = "Kerrick",
    how: str = SOUND,
    *,
    sure: bool = True,
    written: str = "Cerric",
) -> Fix:
    return Fix(0, len(heard), heard, written, entity_id, how, sure)


class SaidAsTest(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.ui import names as ui

        self.bot.settings = SimpleNamespace(recap_audio_on=True, enforce_plans=False)  # type: ignore[assignment]
        self.cerric = await ui.save_name(self.memory, self.campaign, "Cerric", "npc", [], [])
        await self.consent.grant(GUILD, SPEAKER)

    def table(self, counts: dict[tuple[str, str, str, int], int]) -> Any:
        table = make_table(self.campaign.id)
        table.heard_forms = dict(counts)
        return table

    def heard(
        self, spelling: str = "kerrick", times: int = 5
    ) -> dict[tuple[str, str, str, int], int]:
        """`times` fixes from `spelling` to Cerric, by one person."""
        return {(self.cerric.id, spelling, "cerric", SPEAKER): times}

    async def waiting(self) -> list[Any]:
        return await self.memory.said_as_suggestions(GUILD, self.campaign.id)

    async def offered(self, counts: dict[tuple[str, str, str, int], int]) -> list[Any]:
        await self.bot.suggest_said_as(self.table(counts))
        return await self.waiting()


class TheCounting(SaidAsTest):
    async def test_cleaner_fixes_are_counted_per_person_and_a_dms_own_fix_is_not(self) -> None:
        table = make_table(self.campaign.id)
        self.bot._count_heard_forms(
            table, SPEAKER, [fix(self.cerric.id), fix(self.cerric.id, "kerrick")]
        )
        self.bot._count_heard_forms(table, SPEAKER, [fix(self.cerric.id, how=DM_FIX)])
        self.assertEqual(table.heard_forms, {(self.cerric.id, "kerrick", "cerric", SPEAKER): 2})

    async def test_only_sure_sound_fixes_are_counted(self) -> None:
        table = make_table(self.campaign.id)
        self.bot._count_heard_forms(
            table,
            SPEAKER,
            [
                fix(self.cerric.id, how=SPELLING),  # dropped later anyway
                fix(self.cerric.id, sure=False),  # may be undone
            ],
        )
        self.assertEqual(table.heard_forms, {})

    async def test_what_the_fix_was_written_as_is_kept_and_possessives_are_folded(self) -> None:
        table = make_table(self.campaign.id)
        self.bot._count_heard_forms(
            table, SPEAKER, [fix(self.cerric.id, written="Cerric's"), fix(self.cerric.id)]
        )
        self.assertEqual(list(table.heard_forms), [(self.cerric.id, "kerrick", "cerric", SPEAKER)])

    async def test_stopping_recording_forgets_that_persons_counts(self) -> None:
        table = make_table(self.campaign.id)
        self.bot.tables[GUILD] = table
        self.bot._count_heard_forms(table, SPEAKER, [fix(self.cerric.id)])
        self.bot._count_heard_forms(table, 8, [fix(self.cerric.id)])
        self.bot.stop_recording(GUILD, SPEAKER)
        self.assertEqual(list(table.heard_forms), [(self.cerric.id, "kerrick", "cerric", 8)])


class TheOffers(SaidAsTest):
    async def test_three_fixes_make_an_offer_and_two_do_not(self) -> None:
        key = (self.cerric.id, "kerrick", "cerric", SPEAKER)
        self.assertEqual(await self.offered({key: 2}), [])
        (one,) = await self.offered({key: 3})
        self.assertEqual((one.name, one.heard, one.times), ("Cerric", "kerrick", 3))

    async def test_only_people_who_still_agree_count(self) -> None:
        self.consent.stop_now(GUILD, SPEAKER)
        self.assertEqual(await self.offered(self.heard(times=5)), [])

    async def test_nothing_is_offered_without_spoken_recaps(self) -> None:
        self.bot.settings = SimpleNamespace(recap_audio_on=False, enforce_plans=False)  # type: ignore[assignment]
        self.assertEqual(await self.offered(self.heard(times=5)), [])
        self.bot.settings = SimpleNamespace(recap_audio_on=True, enforce_plans=False)  # type: ignore[assignment]
        self.bot.plan_allows = AsyncMock(return_value=False)  # type: ignore[method-assign]
        self.assertEqual(await self.offered(self.heard(times=5)), [])

    async def test_the_most_often_heard_spelling_wins(self) -> None:
        counts = {
            (self.cerric.id, "kerrick", "cerric", SPEAKER): 3,
            (self.cerric.id, "kerric", "cerric", SPEAKER): 5,
        }
        (one,) = await self.offered(counts)
        self.assertEqual(one.heard, "kerric")

    async def test_a_name_with_a_spelling_already_or_a_no_is_not_offered(self) -> None:
        key = (self.cerric.id, "kerrick", "cerric", SPEAKER)
        await self.memory.set_said_as(
            GUILD, self.campaign.id, self.cerric.id, "Kair-ick", source="dm"
        )
        self.assertEqual(await self.offered({key: 3}), [])
        await self.memory.set_said_as(GUILD, self.campaign.id, self.cerric.id, None, source="dm")
        self.assertEqual(len(await self.offered({key: 3})), 1)
        await self.memory.answer_said_as(GUILD, self.campaign.id, self.cerric.id, None)  # no
        self.assertEqual(await self.offered({key: 9}), [])  # never again

    async def test_the_same_spelling_as_the_name_is_not_an_offer(self) -> None:
        self.assertEqual(await self.offered({(self.cerric.id, "cerric", "cerric", SPEAKER): 5}), [])

    async def test_a_fix_into_an_alias_is_not_an_offer_for_the_main_name(self) -> None:
        from dmbot.ui import names as ui

        wolves = await ui.save_name(
            self.memory, self.campaign, "Frostwolf tribe", "faction", ["Frostwolves"], []
        )
        key = (wolves.id, "frost wolves", "frostwolves", SPEAKER)
        self.assertEqual(await self.offered({key: 3}), [])
        main = (wolves.id, "frost wolves", "frostwolf tribe", SPEAKER)
        (one,) = await self.offered({main: 3})
        self.assertEqual(one.name, "Frostwolf tribe")

    async def test_a_person_at_the_table_is_never_offered(self) -> None:
        async with self.db.guild(GUILD) as conn:  # (DMbot makes person entries itself)
            await conn.execute(
                "UPDATE memory_entities SET type = 'person' WHERE guild_id = %s AND id = %s",
                (GUILD, self.cerric.id),
            )
        self.assertEqual(await self.offered(self.heard()), [])
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET type = 'npc' WHERE guild_id = %s AND id = %s",
                (GUILD, self.cerric.id),
            )
        self.assertEqual(len(await self.offered(self.heard())), 1)  # only the kind kept it out

    async def test_an_offer_goes_with_its_name(self) -> None:
        await self.offered(self.heard(times=3))
        await self.memory.set_entity_status(
            GUILD, self.campaign.id, self.cerric.id, "rejected", source="dm"
        )
        self.assertEqual(await self.waiting(), [])  # a forgotten name is not offered


class TheReview(SaidAsTest):
    async def start(self, user: int = DM) -> tuple[Any, Any]:
        await self.offered(self.heard(times=3))
        it = self.it(user)
        await review.start_said_as_review(it, self.campaign.id)
        if not it.response.sent or "view" not in it.response.sent[0][1]:
            return it, None
        return it, it.response.sent[0][1]["view"]

    async def test_the_offer_reads_plainly_and_has_four_answers(self) -> None:
        it, view = await self.start()
        self.assertIn("At your table, **Cerric** sounds like **kerrick**", it.response.sent[0][0])
        labels = [b.label for b in view.children]
        self.assertEqual(labels, ["✅ Yes", "❌ No", "✏️ Type my own", "⏳ Later"])

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
        self.assertEqual(await self.offered(self.heard(times=9)), [])

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

        await self.offered(self.heard(times=3))
        campaign = await self.campaigns.get(GUILD, self.campaign.id)
        assert campaign is not None
        self.assertEqual((await ui.waiting_reviews(self.it(), campaign)).said, 1)
        self.assertEqual((await ui.waiting_reviews(self.it(MANAGER), campaign)).said, 0)
        self.assertIn("to say out loud", ui.review_line(ui.Reviews(said=1)) or "")


class TheSafetyRules(TheReview):
    async def make_secret(self) -> None:
        async with self.db.guild(GUILD) as conn:  # (the store refuses to make a main name secret)
            await conn.execute(
                "UPDATE memory_aliases SET secret = true WHERE guild_id = %s AND campaign_id = %s"
                " AND entity_id = %s",
                (GUILD, self.campaign.id, self.cerric.id),
            )

    async def test_a_secret_name_is_never_offered(self) -> None:
        await self.make_secret()
        self.assertEqual(await self.offered(self.heard(times=5)), [])
        async with self.db.guild(GUILD) as conn:  # none stored, not only none shown
            cur = await conn.execute("SELECT count(*) AS n FROM said_as_suggestions")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_a_name_made_secret_after_the_offer_is_not_shown_or_set(self) -> None:
        _, view = await self.start()
        await self.make_secret()
        self.assertEqual(await self.waiting(), [])
        press = self.it()
        await view._yes(press)  # the screen was already open: the store refuses
        self.assertIsNone(await self.memory.said_as(GUILD, self.campaign.id, self.cerric.id))

    async def test_a_non_dm_cannot_use_the_no_the_form_or_the_type_button(self) -> None:
        _, view = await self.start()
        for press in (view._no, view._type):
            it = self.it(MANAGER)
            await press(it)
            self.assertIn("Only a DM", it.response.sent[0][0])
        form = review.TypeIt(view, view.shown)
        form.said_as = SimpleNamespace(value="Kair-ick")  # type: ignore[assignment]
        it = self.it(MANAGER)
        await form.on_submit(it)
        self.assertIn("Only a DM", it.response.sent[0][0])
        self.assertIsNone(await self.memory.said_as(GUILD, self.campaign.id, self.cerric.id))
        self.assertEqual(len(await self.waiting()), 1)  # still waiting

    async def test_a_name_made_secret_meanwhile_leaves_the_offer_waiting(self) -> None:
        await self.offered(self.heard(times=3))
        await self.make_secret()
        with self.assertRaises(MemoryRuleError):
            await self.memory.answer_said_as(GUILD, self.campaign.id, self.cerric.id, "Kair-ick")
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT state FROM said_as_suggestions WHERE entity_id = %s", (self.cerric.id,)
            )
            rows = await cur.fetchall()
        self.assertEqual([r["state"] for r in rows], ["waiting"])  # not lost
        self.assertIsNone(await self.memory.said_as(GUILD, self.campaign.id, self.cerric.id))

    async def test_spoken_recaps_are_checked_at_the_start_and_on_every_answer(self) -> None:
        _, view = await self.start()
        self.bot.plan_allows = AsyncMock(return_value=False)  # type: ignore[method-assign]
        press = self.it()
        await view._yes(press)
        self.assertIn("aren't switched on", press.response.sent[0][0])
        self.assertIsNone(await self.memory.said_as(GUILD, self.campaign.id, self.cerric.id))
        again = self.it()
        await review.start_said_as_review(again, self.campaign.id)
        self.assertIn("aren't switched on", again.response.sent[0][0])
        later = self.it(MANAGER)
        await view._later(later)  # not a DM: nothing happens but the refusal
        self.assertIn("Only a DM", later.response.sent[0][0])

    async def test_an_offer_answered_elsewhere_says_so(self) -> None:
        _, view = await self.start()
        await self.memory.answer_said_as(GUILD, self.campaign.id, self.cerric.id, None)
        press = self.it()
        await view._yes(press)
        self.assertIn("already answered", press.response.sent[0][0])
        self.assertIsNone(await self.memory.said_as(GUILD, self.campaign.id, self.cerric.id))

    async def test_a_press_answers_the_offer_it_was_for(self) -> None:
        _, view = await self.start()
        stale = view.shown
        view.shown = None  # the screen moved on
        form = review.TypeIt(view, stale)
        form.said_as = SimpleNamespace(value="Kair-ick")  # type: ignore[assignment]
        await form.on_submit(self.it())
        self.assertEqual(
            await self.memory.said_as(GUILD, self.campaign.id, self.cerric.id), "Kair-ick"
        )

    async def test_offers_stay_in_their_campaign_and_go_with_it(self) -> None:
        await self.offered(self.heard(times=3))
        other = await self.campaigns.create(GUILD, "Other", DM)
        self.assertEqual(await self.memory.said_as_suggestions(GUILD, other.id), [])
        async with self.db.guild(GUILD + 1) as conn:  # another server sees none
            cur = await conn.execute("SELECT count(*) AS n FROM said_as_suggestions")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "DELETE FROM campaigns WHERE guild_id = %s AND id = %s", (GUILD, self.campaign.id)
            )
            cur = await conn.execute("SELECT count(*) AS n FROM said_as_suggestions")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # deleted with the campaign


class TheSpokenWay(SaidAsTest):
    """A name said with its pronunciation at the table: "Cerric is pronounced SEH-rick"."""

    async def lookup(self, table: Any) -> Any:
        from dmbot.memory.lookup import CampaignLookup

        table.name_lookup = CampaignLookup.build(
            await self.memory.lookup_data(GUILD, self.campaign.id)
        )
        self.bot.tables[GUILD] = table
        return table

    async def said(self, *lines: tuple[int, str]) -> Any:
        table = await self.lookup(make_table(self.campaign.id))
        for speaker, line in lines:
            found = find_mentions(table.name_lookup, line)
            self.bot._note_pronounced(table, speaker, line, found, line)
        return table

    async def test_the_name_and_its_sound_are_kept_as_words_only(self) -> None:
        table = await self.said((SPEAKER, "Cerric is pronounced SEH-rick by the way"))
        self.assertEqual(table.pronounced, {(self.cerric.id, "Seh-rick", SPEAKER): 1})

    async def test_the_name_said_a_moment_before_by_someone_else(self) -> None:
        table = await self.said((8, "we ask Cerric"), (SPEAKER, "it's pronounced SEH-rick"))
        self.assertEqual(table.pronounced, {(self.cerric.id, "Seh-rick", SPEAKER): 1})

    async def test_a_line_with_no_name_nearby_is_nothing(self) -> None:
        table = await self.said((SPEAKER, "it's pronounced SEH-rick"))
        self.assertEqual(table.pronounced, {})

    async def test_the_one_said_most_often_is_offered(self) -> None:
        table = await self.said(
            (SPEAKER, "Cerric is pronounced Sarick"),
            (SPEAKER, "Cerric is pronounced Seh-rick"),
            (8, "Cerric is pronounced Seh-rick"),
        )
        await self.consent.grant(GUILD, 8)
        (one,) = await self.offered(table)
        self.assertEqual((one.heard, one.times), ("Seh-rick", 2))

    async def test_an_other_name_says_nothing_about_the_main_name(self) -> None:
        from dmbot.ui import names as ui

        await ui.save_name(self.memory, self.campaign, "Ser Cerric", "npc", ["Old Ced"], [])
        table = await self.said((SPEAKER, "Old Ced is pronounced Sed"))
        self.assertEqual(table.pronounced, {})

    async def test_stopping_recording_forgets_what_that_person_said_and_the_name_they_said(
        self,
    ) -> None:
        table = await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        self.bot.stop_recording(GUILD, SPEAKER)
        self.assertEqual(table.pronounced, {})
        table = await self.said((SPEAKER, "we ask Cerric"))
        self.bot.stop_recording(GUILD, SPEAKER)  # the name they had just said goes too
        self.bot._note_pronounced(
            table, 8, "it's pronounced Seh-rick", [], "it's pronounced Seh-rick"
        )
        self.assertEqual(table.pronounced, {})

    async def test_the_sound_is_read_from_the_line_as_heard(self) -> None:
        table = await self.lookup(make_table(self.campaign.id))
        line = "Cerric is pronounced Cerric"  # the cleaner turned "Kerrick" into the name
        self.bot._note_pronounced(
            table,
            SPEAKER,
            line,
            find_mentions(table.name_lookup, line),
            "Cerric is pronounced Kerrick",
        )
        self.assertEqual(table.pronounced, {(self.cerric.id, "Kerrick", SPEAKER): 1})

    async def test_the_log_says_only_how_long_it_was(self) -> None:
        with self.assertLogs("dmbot.bot", level="INFO") as logs:
            await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        said = [m for m in logs.output if "Said aloud" in m]
        self.assertEqual(len(said), 1)
        self.assertNotIn("Seh-rick", said[0])
        self.assertNotIn("Cerric", said[0])

    async def test_a_plan_that_says_no_still_forgets_the_session(self) -> None:
        table = await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        self.bot.plan_allows = AsyncMock(return_value=False)  # type: ignore[method-assign]
        await self.bot.suggest_pronounced(table)
        self.assertEqual((table.pronounced, await self.waiting()), ({}, []))

    async def offered(self, table: Any) -> list[Any]:  # type: ignore[override]
        await self.bot.suggest_pronounced(table)
        return await self.waiting()

    async def test_after_the_session_it_is_offered_not_changed(self) -> None:
        table = await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        (one,) = await self.offered(table)
        self.assertEqual((one.name, one.heard), ("Cerric", "Seh-rick"))
        self.assertIsNone(await self.memory.said_as(GUILD, self.campaign.id, self.cerric.id))
        self.assertEqual(table.pronounced, {})  # nothing is kept past the session

    async def test_only_a_person_who_still_agrees_counts(self) -> None:
        table = await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        self.consent.stop_now(GUILD, SPEAKER)
        self.assertEqual(await self.offered(table), [])

    async def test_nothing_is_offered_without_spoken_recaps(self) -> None:
        table = await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        self.bot.plan_allows = AsyncMock(return_value=False)  # type: ignore[method-assign]
        self.assertEqual(await self.offered(table), [])

    async def test_a_secret_name_is_never_noted_or_offered(self) -> None:
        async with self.db.guild(GUILD) as conn:  # (the store refuses to make a main name secret)
            await conn.execute(
                "UPDATE memory_aliases SET secret = true WHERE guild_id = %s"
                " AND campaign_id = %s AND entity_id = %s",
                (GUILD, self.campaign.id, self.cerric.id),
            )
        table = await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        self.assertEqual(table.pronounced, {})
        table.pronounced = {(self.cerric.id, "Seh-rick", SPEAKER): 1}  # even if one got in
        self.assertEqual(await self.offered(table), [])
