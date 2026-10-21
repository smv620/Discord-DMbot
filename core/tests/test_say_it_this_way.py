"""Say it this way? (#2171): counting the cleaner's fixes, the end-of-session offers (consent, plan,
threshold), the store's rules, and the DM's review. Invented names throughout."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.memory.models import MemoryRuleError
from dmbot.memory.scene import find_mentions
from dmbot.transcript import pronounced
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

    def hear(self, table: Any, speaker: int, line: str, raw: str | None = None, **kw: Any) -> None:
        found = find_mentions(table.name_lookup, line)
        self.bot._note_pronounced(table, speaker, line, found, raw or line, **kw)

    async def said(self, *lines: tuple[int, str]) -> Any:
        table = await self.lookup(make_table(self.campaign.id))
        for speaker, line in lines:
            self.hear(table, speaker, line)
        return table

    async def offered_spoken(self, table: Any) -> list[Any]:
        await self.bot.suggest_pronounced(table)
        return await self.waiting()

    def sound(self, key: str, writing: str, speaker: int = SPEAKER, times: int = 1) -> Any:
        return {(self.cerric.id, key, speaker): {writing: times}}

    async def test_the_name_and_its_sound_are_kept_as_words_only(self) -> None:
        table = await self.said((SPEAKER, "Cerric is pronounced SEH-rick by the way"))
        self.assertEqual(table.pronounced, self.sound("sehrick", "Seh-rick"))

    async def test_the_name_said_a_moment_before_by_someone_else(self) -> None:
        table = await self.said((8, "we ask Cerric"), (SPEAKER, "it's pronounced SEH-rick"))
        self.assertEqual(table.pronounced, self.sound("sehrick", "Seh-rick"))

    async def test_a_line_with_no_name_nearby_is_nothing(self) -> None:
        table = await self.said((SPEAKER, "it's pronounced SEH-rick"))
        self.assertEqual(table.pronounced, {})

    async def test_the_sound_said_most_often_is_offered_not_the_first_or_the_last(self) -> None:
        await self.consent.grant(GUILD, 8)
        table = await self.said(
            (SPEAKER, "Cerric is pronounced Sarick"),
            (SPEAKER, "Cerric is pronounced Seh-rick"),
            (8, "Cerric is pronounced Seh-rick"),
            (8, "Cerric is pronounced Serik"),
        )
        (one,) = await self.offered_spoken(table)
        self.assertEqual((one.heard, one.times), ("Seh-rick", 2))

    async def test_two_ways_of_writing_one_sound_vote_together(self) -> None:
        table = await self.said(
            (SPEAKER, "Cerric is pronounced Seh rick"),
            (SPEAKER, "Cerric is pronounced Seh-rick"),
            (SPEAKER, "Cerric is pronounced Seh-rick"),
            (SPEAKER, "Cerric is pronounced Sarick"),
            (SPEAKER, "Cerric is pronounced Sarick"),
        )
        (one,) = await self.offered_spoken(table)
        self.assertEqual((one.heard, one.times), ("Seh-rick", 3))  # (as most often written)

    async def test_an_other_name_says_nothing_about_the_main_name(self) -> None:
        from dmbot.ui import names as ui

        await ui.save_name(self.memory, self.campaign, "Ser Cerric", "npc", ["Old Ced"], [])
        table = await self.said((SPEAKER, "Old Ced is pronounced Sed"))
        self.assertEqual(table.pronounced, {})

    async def test_a_known_name_of_another_entry_is_not_a_sound(self) -> None:
        from dmbot.ui import names as ui

        await ui.save_name(self.memory, self.campaign, "Kael", "npc", [], [])
        table = await self.said((SPEAKER, "No the other one, Cerric is pronounced Kael"))
        self.assertEqual(table.pronounced, {})
        table = await self.said((SPEAKER, "Cerric is pronounced Cerric"))  # its own, fine to keep
        self.assertEqual(len(table.pronounced), 1)

    async def test_a_secret_name_is_never_the_sound_nor_a_word_of_it(self) -> None:
        from dmbot.ui import names as ui

        queen = await ui.save_name(self.memory, self.campaign, "The Raven Queen", "npc", [], [])
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_aliases SET secret = true WHERE guild_id = %s"
                " AND campaign_id = %s AND entity_id = %s",
                (GUILD, self.campaign.id, queen.id),
            )
        for sound in ("Raven", "Raven Queen", "Queen"):
            table = await self.said((SPEAKER, f"Cerric is pronounced {sound}"))
            self.assertEqual(table.pronounced, {}, sound)

    async def test_stopping_recording_forgets_what_that_person_said_and_the_name_they_said(
        self,
    ) -> None:
        table = await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        self.bot.stop_recording(GUILD, SPEAKER)
        self.assertEqual(table.pronounced, {})
        table = await self.said((SPEAKER, "we ask Cerric"))
        self.bot.stop_recording(GUILD, SPEAKER)  # the name they had just said can't be attached to
        self.hear(table, 8, "it's pronounced Seh-rick")
        self.assertEqual(table.pronounced, {})

    async def test_the_sound_is_read_from_the_line_as_heard(self) -> None:
        table = await self.lookup(make_table(self.campaign.id))
        self.hear(
            table, SPEAKER, "Cerric is pronounced Cerric", "Cerric is pronounced Kerrick"
        )  # the cleaner turned "Kerrick" into the name
        self.assertEqual(table.pronounced, self.sound("kerrick", "Kerrick"))

    async def test_the_log_says_only_how_long_it_was(self) -> None:
        with self.assertLogs("dmbot.bot", level="INFO") as logs:
            await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        said = [m for m in logs.output if "Said aloud" in m]
        self.assertEqual(len(said), 1)
        self.assertNotIn("Seh-rick", said[0])
        self.assertNotIn("Cerric", said[0])

    async def test_at_most_forty_sounds_are_kept(self) -> None:
        table = await self.lookup(make_table(self.campaign.id))
        table.pronounced = {(self.cerric.id, f"k{n}", SPEAKER): {f"K{n}": 1} for n in range(40)}
        self.hear(table, SPEAKER, "Cerric is pronounced Seh-rick")
        self.assertEqual(len(table.pronounced), 40)
        self.assertNotIn((self.cerric.id, "sehrick", SPEAKER), table.pronounced)

    async def test_after_the_session_it_is_offered_not_changed_and_nothing_is_kept(self) -> None:
        table = await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        (one,) = await self.offered_spoken(table)
        self.assertEqual((one.name, one.heard), ("Cerric", "Seh-rick"))
        self.assertIsNone(await self.memory.said_as(GUILD, self.campaign.id, self.cerric.id))
        self.assertEqual(table.pronounced, {})
        self.assertEqual(table.pronouncing._recent, deque())

    async def test_only_a_person_who_still_agrees_counts(self) -> None:
        table = await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        self.consent.stop_now(GUILD, SPEAKER)
        self.assertEqual(await self.offered_spoken(table), [])

    async def test_nothing_is_offered_when_the_plan_says_no_but_the_session_is_forgotten(
        self,
    ) -> None:
        table = await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        self.bot.plan_allows = AsyncMock(return_value=False)  # type: ignore[method-assign]
        self.assertEqual(await self.offered_spoken(table), [])
        self.assertEqual(table.pronounced, {})

    async def test_nothing_is_offered_with_spoken_recaps_switched_off(self) -> None:
        table = await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        self.bot.settings = SimpleNamespace(recap_audio_on=False, enforce_plans=False)  # type: ignore[assignment]
        self.assertEqual(await self.offered_spoken(table), [])
        self.assertEqual(table.pronounced, {})  # forgotten all the same

    async def test_a_name_said_aloud_beats_the_cleaners_count(self) -> None:
        table = self.table(self.heard(times=5))  # the cleaner heard it as "kerrick" five times
        await self.lookup(table)
        self.hear(table, SPEAKER, "Cerric is pronounced Seh-rick")
        # the end-of-session order is part of the rule: said aloud first, counted second
        order = [name for name, _ in self.bot_end_steps(table)]
        self.assertLess(order.index("said aloud"), order.index("say it this way"))

    def bot_end_steps(self, table: Any) -> list[tuple[str, Any]]:
        import inspect
        import re as regex

        source = inspect.getsource(type(self.bot))
        names = regex.findall(r'\("(said aloud|say it this way)", lambda', source)
        return [(n, None) for n in names]

    async def test_a_held_line_waits_for_its_label(self) -> None:
        from unittest.mock import patch

        from dmbot import bot as bot_module
        from dmbot.transcript.topics import Waiting

        self.bot.topic_ai = object()  # type: ignore[assignment]
        self.bot._save_topic = AsyncMock()  # type: ignore[method-assign]
        self.bot._hide_lines = AsyncMock()  # type: ignore[method-assign]
        for topic, kept in (("game", 1), ("off_topic", 0)):
            table = await self.lookup(make_table(self.campaign.id))
            line = "Cerric is pronounced Seh-rick"
            table.held = {(SPEAKER, 100): line}
            self.hear(table, SPEAKER, line, held_key=(SPEAKER, 100))
            self.assertEqual(table.pronounced, {})  # held with the line
            reply = SimpleNamespace(input_tokens=0, output_tokens=0)
            with patch.object(bot_module, "classify", AsyncMock(return_value=([topic], reply))):
                await self.bot._label_topics(table, [Waiting(SPEAKER, 100, line)])
            self.assertEqual(len(table.pronounced), kept, topic)
            self.assertEqual(table.held_pronounced, {})

    async def test_a_held_line_of_someone_who_stopped_gives_nothing(self) -> None:
        from unittest.mock import patch

        from dmbot import bot as bot_module
        from dmbot.transcript.topics import Waiting

        self.bot.topic_ai = object()  # type: ignore[assignment]
        self.bot._save_topic = AsyncMock()  # type: ignore[method-assign]
        self.bot._hide_lines = AsyncMock()  # type: ignore[method-assign]
        table = await self.lookup(make_table(self.campaign.id))
        line = "Cerric is pronounced Seh-rick"
        table.held = {(SPEAKER, 100): line}
        self.hear(table, SPEAKER, line, held_key=(SPEAKER, 100))
        self.consent.stop_now(GUILD, SPEAKER)
        reply = SimpleNamespace(input_tokens=0, output_tokens=0)
        with patch.object(bot_module, "classify", AsyncMock(return_value=(["game"], reply))):
            await self.bot._label_topics(table, [Waiting(SPEAKER, 100, line)])
        self.assertEqual((table.pronounced, table.held_pronounced), ({}, {}))

    async def test_a_plain_word_counts_only_when_it_sounds_like_the_name(self) -> None:
        for line in (
            "Cerric was pronounced Prince",
            "Cerric was pronounced Hero",
            "Cerric was pronounced Emperor",
            "Cerric was pronounced Tuesday",
            "Cerric was pronounced Fireball",
            "Cerric was pronounced Lord Protector",
            "Cerric, say it like Sir Kael",
        ):
            table = await self.said((SPEAKER, line))
            self.assertEqual(table.pronounced, {}, line)
        table = await self.said((SPEAKER, "Cerric is pronounced Kerrick"))  # close to the name
        self.assertEqual(table.pronounced, self.sound("kerrick", "Kerrick"))
        table = await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))  # shaped like a sound
        self.assertEqual(table.pronounced, self.sound("sehrick", "Seh-rick"))

    async def test_a_title_is_not_a_pronunciation_whatever_it_looks_like(self) -> None:
        """The stop list: these pass the length and score rule, so only the list rejects them."""
        from dmbot.ui import names as ui

        for name, word in (
            ("Aron", "Baron"),
            ("Bran", "Baron"),
            ("Kount", "Count"),
            ("Duka", "Duke"),
            ("Viktor", "Victor"),
            ("Tor", "Traitor"),
            ("Champa", "Champion"),
        ):
            with self.subTest(name=name):
                await ui.save_name(self.memory, self.campaign, name, "npc", [], [])
                table = await self.said((SPEAKER, f"{name} was pronounced {word}"))
                self.assertEqual(table.pronounced, {})

    async def test_a_word_much_longer_than_the_name_is_not_a_pronunciation(self) -> None:
        """The length rule: "Final" is not on any list, it is two letters longer than "Fin"."""
        from dmbot.ui import names as ui

        await ui.save_name(self.memory, self.campaign, "Fin", "npc", [], [])
        table = await self.said((SPEAKER, "Fin was pronounced Final"))
        self.assertEqual(table.pronounced, {})
        await ui.save_name(self.memory, self.campaign, "Torin", "npc", [], [])
        table = await self.said((SPEAKER, "Torin was pronounced Thorin"))  # one letter off: kept
        self.assertEqual([w for c in table.pronounced.values() for w in c], ["Thorin"])

    async def test_a_long_real_name_is_still_found_through_the_sound_codes(self) -> None:
        """The length rule is only on the plain-letters path: these differ by 2 or more letters
        and are found by their sound codes, so the gap must never move onto that path."""
        from dmbot.ui import names as ui

        for name, said in (("Brannoch", "Branok"), ("Llewellyn", "Lewelin"), ("Vaughn", "Von")):
            with self.subTest(name=name):
                await ui.save_name(self.memory, self.campaign, name, "npc", [], [])
                table = await self.said((SPEAKER, f"{name} was pronounced {said}"))
                self.assertEqual([w for c in table.pronounced.values() for w in c], [said])

    async def test_the_names_that_are_said_the_way_they_sound_are_still_found(self) -> None:
        from dmbot.ui import names as ui

        await ui.save_name(self.memory, self.campaign, "Kael", "npc", [], [])
        await ui.save_name(self.memory, self.campaign, "Kelli", "npc", [], [])
        for line, writing in (
            ("Kael is pronounced KAY-el", "Kay-el"),
            ("Kael is pronounced Kay el", "Kay el"),
            ("Cerric is pronounced SEH-rick", "Seh-rick"),
            ("Cerric is pronounced Kerrick", "Kerrick"),
            ("Kelli is pronounced Kelly", "Kelly"),
        ):
            table = await self.said((SPEAKER, line))
            self.assertEqual(
                [w for counts in table.pronounced.values() for w in counts], [writing], line
            )

    async def label(self, table: Any, topic: str, *, during: Any = None) -> None:
        from unittest.mock import patch

        from dmbot import bot as bot_module
        from dmbot.transcript.topics import Waiting

        self.bot.topic_ai = object()  # type: ignore[assignment]
        self.bot._save_topic = AsyncMock()  # type: ignore[method-assign]
        self.bot._hide_lines = AsyncMock()  # type: ignore[method-assign]
        reply = SimpleNamespace(input_tokens=0, output_tokens=0)

        async def classify(*a: Any, **k: Any) -> Any:
            if during is not None:
                during()  # something happens while the call is out
            return [topic], reply

        with patch.object(bot_module, "classify", classify):
            await self.bot._label_topics(table, [Waiting(SPEAKER, 100, "held")])

    async def test_a_held_line_keeps_the_name_it_had_when_it_was_heard(self) -> None:
        from dmbot.ui import names as ui

        await ui.save_name(self.memory, self.campaign, "Kael", "npc", [], [])
        table = await self.said((8, "we ask Cerric"))
        table.held = {(SPEAKER, 100): "held"}
        self.hear(table, SPEAKER, "it's pronounced Seh-rick", held_key=(SPEAKER, 100))
        self.hear(table, 8, "and Kael is there too")  # by the time it is labelled, two names
        await self.label(table, "game")
        self.assertEqual(list(table.pronounced), [(self.cerric.id, "sehrick", SPEAKER)])

    async def test_stopping_recording_while_a_line_is_held_gives_nothing(self) -> None:
        table = await self.said((8, "we ask Cerric"))
        table.held = {(SPEAKER, 100): "held"}
        self.hear(table, SPEAKER, "it's pronounced Seh-rick", held_key=(SPEAKER, 100))
        self.assertEqual(len(table.held_pronounced), 1)
        self.bot.stop_recording(GUILD, SPEAKER)
        self.assertEqual(table.held_pronounced, {})
        await self.label(table, "game")
        self.assertEqual(table.pronounced, {})

    async def test_someone_who_stops_while_the_filter_is_out_gives_nothing(self) -> None:
        table = await self.said((8, "we ask Cerric"))
        table.held = {(SPEAKER, 100): "held"}
        self.hear(table, SPEAKER, "it's pronounced Seh-rick", held_key=(SPEAKER, 100))
        await self.label(table, "game", during=lambda: self.consent.stop_now(GUILD, SPEAKER))
        self.assertEqual(table.pronounced, {})

    async def test_nothing_here_may_break_a_lines_delivery(self) -> None:
        table = await self.said((SPEAKER, "Cerric is pronounced Seh-rick"))
        table.name_lookup = SimpleNamespace(entities=None)  # whatever goes wrong inside
        with self.assertLogs("dmbot.bot", level="ERROR"):
            self.bot._count_pronounced(table, SPEAKER, [(pronounced.Sound("X", 0, True), "e")])

    SOUNDS = ("Brenach", "Bren-ach", "Ach-uh")  # close and plain; shaped; shaped, a secret word

    async def brannoch(self, secret: list[str]) -> Any:
        from dmbot.ui import names as ui

        return await ui.save_name(self.memory, self.campaign, "Brannoch", "npc", [], secret)

    async def test_a_secret_name_of_the_same_entry_is_never_its_sound_even_when_it_would_pass(
        self,
    ) -> None:
        """The name's own secret other name is not "another entry's name", so only the secret check
        keeps it out. Each sound passes the shape and closeness rules on its own (see the
        control), and each is the secret, or has a word of it."""
        await self.brannoch(["Brenach", "Bren Ach"])
        for sound in self.SOUNDS:
            table = await self.said((SPEAKER, f"Brannoch is pronounced {sound}"))
            self.assertEqual(table.pronounced, {}, sound)

    async def test_the_same_lines_pass_without_the_secret(self) -> None:
        """The control: the same sounds are kept when nothing is secret."""
        entry = await self.brannoch([])
        for sound in self.SOUNDS:
            table = await self.said((SPEAKER, f"Brannoch is pronounced {sound}"))
            (key,) = table.pronounced
            self.assertEqual(key[0], entry.id, sound)
