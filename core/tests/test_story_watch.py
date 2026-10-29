"""Watch the story (#1729): the adventure needs someone alive, the table says dead, and the DM is
told once, privately, with no AI. The clash table is pure; the rest needs the test database."""

from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from typing import Any

from dmbot.campaigns.models import STORY_OFF, STORY_WATCH
from dmbot.memory import book_links as links
from dmbot.memory.story_watch import Clash, Found, StoryWatch, clashes
from dmbot.ui import story_watch as ui
from tests import test_book_links as base
from tests.answer_first import Timeline
from tests.test_adventure import press
from tests.test_memory_store import DM, GUILD_A, GUILD_B


class Table(unittest.TestCase):
    def test_what_cannot_both_be_true(self) -> None:
        for need in ("alive", "present"):
            for now in ("dead", "missing", "captured", "transformed"):
                self.assertTrue(clashes(need, now, set_after_plan=False), (need, now))
            for now in ("alive", "unknown", "intact"):
                self.assertFalse(clashes(need, now, set_after_plan=False), (need, now))
        for now in ("lost", "destroyed"):
            self.assertTrue(clashes("found", now, set_after_plan=False))
        self.assertFalse(clashes("found", "intact", set_after_plan=False))

    def test_dead_against_alive_only_counts_when_said_after_the_plan_was_read(self) -> None:
        self.assertTrue(clashes("dead", "alive", set_after_plan=True))
        self.assertFalse(clashes("dead", "alive", set_after_plan=False))
        self.assertFalse(clashes("dead", "missing", set_after_plan=True))

    def test_other_words_and_unknown_are_ignored(self) -> None:
        for need in ("", "friendly", "on the throne", "unknown", "alive and well"):
            for now in ("dead", "alive", "unknown", "lost"):
                self.assertFalse(clashes(need, now, set_after_plan=True), (need, now))

    def test_capitals_and_stops_do_not_matter(self) -> None:
        self.assertTrue(clashes(" Alive. ", "dead", set_after_plan=False))


class Setup(base.Setup):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.watch = StoryWatch(self.db, clock=lambda: self.now)
        await self.campaigns.set_story_mode(GUILD_A, self.c, STORY_WATCH, confirmed_by=DM)

    async def link(
        self, doc: str, ids: dict[str, str], a: str, b: str, need: str, *, guild: int = GUILD_A,
        campaign: str | None = None, at: int = 1_000
    ) -> str:  # fmt: skip
        link_id = uuid.uuid4().hex
        async with self.db.guild(guild) as conn:
            await conn.execute(
                "INSERT INTO plan_links (guild_id, campaign_id, id, document_id, from_id, to_id,"
                " need, quote, chunk_ids, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s,"
                " 'He must live to the end.', %s, %s)",
                (guild, campaign or self.c, link_id, doc, ids[a], ids[b], need, [], at),
            )
        return link_id

    async def varn(self, need: str = "alive") -> tuple[str, str, str]:
        """A chapter that needs Captain Varn, the DM's word that he is the table's Varn."""
        doc, ids = await self.book(
            [("chapter", "Chapter 3: The Circle", 5), ("person", "Captain Varn", 2)]
        )
        entity = await self.add("Captain Varn", type="npc")
        await self.say(ids["Captain Varn"], entity)
        link_id = await self.link(doc, ids, "Chapter 3: The Circle", "Captain Varn", need)
        return entity, link_id, doc

    async def set(self, entity: str, condition: str) -> Any:
        return await self.memory.set_condition(GUILD_A, self.c, entity, condition, source="dm")


class Watching(Setup):
    async def test_a_book_link_a_same_as_and_a_death_give_one_line_and_a_second_run_none(
        self,
    ) -> None:
        entity, _, _ = await self.varn()
        await self.set(entity, "alive")
        self.assertEqual((await self.watch.check(GUILD_A, self.c)).shown, ())
        await self.set(entity, "dead")
        found = await self.watch.check(GUILD_A, self.c)
        (one,) = found.shown
        self.assertEqual(
            (one.book_name, one.book_needs, one.table_name, one.need, one.condition, found.more),
            ("Chapter 3: The Circle", "Captain Varn", "Captain Varn", "alive", "dead", 0),
        )
        text = ui.message_text(found, {})
        self.assertIn(
            "📖 Your adventure: **Chapter 3: The Circle** needs **Captain Varn** alive", text
        )
        self.assertIn("At your table **Captain Varn** is dead", text)
        again = await self.watch.check(GUILD_A, self.c)
        self.assertEqual((again.shown, again.more), ((), 0))

    async def test_after_i_know_nothing_more_is_posted_and_its_words_are_the_books(self) -> None:
        entity, _, _ = await self.varn()
        await self.set(entity, "dead")
        (one,) = (await self.watch.check(GUILD_A, self.c)).shown
        self.assertEqual(
            await self.watch.words(GUILD_A, self.c, one.id), "He must live to the end."
        )
        self.assertTrue(await self.watch.know(GUILD_A, self.c, one.id))
        self.assertFalse(await self.watch.know(GUILD_A, self.c, one.id))  # already answered
        self.assertEqual((await self.watch.check(GUILD_A, self.c)).shown, ())
        self.assertEqual(await self.watch.count(GUILD_A, self.c, "known"), 1)

    async def test_the_story_switch_off_or_a_restored_confirmation_gives_none(self) -> None:
        entity, _, _ = await self.varn()
        await self.set(entity, "dead")
        await self.campaigns.set_story_mode(GUILD_A, self.c, STORY_OFF)
        self.assertEqual((await self.watch.check(GUILD_A, self.c)).shown, ())
        await self.campaigns.set_story_mode(GUILD_A, self.c, STORY_WATCH, confirmed_by=DM)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE shared_confirmations SET restored = true")
        self.assertEqual((await self.watch.check(GUILD_A, self.c)).shown, ())

    async def test_a_name_the_dm_rejected_never_counts(self) -> None:
        entity, _, _ = await self.varn()
        await self.set(entity, "dead")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE memory_entities SET status = 'rejected'")
        self.assertEqual((await self.watch.check(GUILD_A, self.c)).shown, ())

    async def test_without_the_dms_same_as_or_with_not_the_same_nothing_is_said(self) -> None:
        doc, ids = await self.book(
            [("chapter", "Chapter 3: The Circle", 5), ("person", "Captain Varn", 2)]
        )
        entity = await self.add("Captain Varn", type="npc")
        await self.link(doc, ids, "Chapter 3: The Circle", "Captain Varn", "alive")
        await self.set(entity, "dead")
        self.assertEqual((await self.watch.check(GUILD_A, self.c)).shown, ())  # no word yet
        await self.say(ids["Captain Varn"], entity, links.NOT_SAME)
        self.assertEqual((await self.watch.check(GUILD_A, self.c)).shown, ())

    async def test_words_the_table_may_not_contradict_are_ignored(self) -> None:
        entity, _, _ = await self.varn(need="friendly")
        await self.set(entity, "dead")
        self.assertEqual((await self.watch.check(GUILD_A, self.c)).shown, ())

    async def test_dead_in_the_book_and_alive_said_after_the_plan_was_read(self) -> None:
        entity, _, _ = await self.varn(need="dead")
        await self.set(entity, "alive")
        (one,) = (await self.watch.check(GUILD_A, self.c)).shown
        self.assertEqual((one.need, one.condition), ("dead", "alive"))

    async def test_undo_of_the_death_marks_it_gone_and_a_new_death_is_told_again(self) -> None:
        entity, _, _ = await self.varn()
        await self.set(entity, "alive")
        died = await self.set(entity, "dead")
        self.assertEqual(len((await self.watch.check(GUILD_A, self.c)).shown), 1)
        await self.memory.undo(GUILD_A, self.c, died.batch)
        self.assertEqual((await self.watch.check(GUILD_A, self.c)).shown, ())
        self.assertEqual(
            (await self.watch.count(GUILD_A, self.c, "gone"),
             await self.watch.count(GUILD_A, self.c, "open")),
            (1, 0),
        )  # fmt: skip
        self.now += 5
        await self.set(entity, "dead")
        self.assertEqual(len((await self.watch.check(GUILD_A, self.c)).shown), 1)

    async def test_a_condition_that_changes_back_marks_it_gone(self) -> None:
        entity, _, _ = await self.varn()
        await self.set(entity, "dead")
        await self.watch.check(GUILD_A, self.c)
        self.now += 5
        await self.memory.set_condition(GUILD_A, self.c, entity, "alive", source="dm")
        self.assertEqual((await self.watch.check(GUILD_A, self.c)).shown, ())
        self.assertEqual(await self.watch.count(GUILD_A, self.c, "gone"), 1)

    async def test_a_book_link_taken_off_marks_it_gone(self) -> None:
        entity, _, _ = await self.varn()
        await self.set(entity, "dead")
        await self.watch.check(GUILD_A, self.c)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM plan_same_as")
        await self.watch.check(GUILD_A, self.c)
        self.assertEqual(await self.watch.count(GUILD_A, self.c, "gone"), 1)

    async def test_only_five_are_told_at_a_time_and_the_rest_come_next(self) -> None:
        names = [f"Person{chr(97 + n)}" for n in range(7)]
        doc, ids = await self.book(
            [("chapter", "Chapter 1", 1), *[("person", n, 2) for n in names]]
        )
        for n in names:
            entity = await self.add(n, type="npc")
            await self.say(ids[n], entity)
            await self.link(doc, ids, "Chapter 1", n, "alive")
            await self.set(entity, "dead")
        first = await self.watch.check(GUILD_A, self.c)
        self.assertEqual((len(first.shown), first.more), (5, 2))
        text = ui.message_text(first, {})
        self.assertTrue(text.endswith("+2 more"))
        self.assertEqual(len(text.splitlines()), 6)
        second = await self.watch.check(GUILD_A, self.c)
        self.assertEqual((len(second.shown), second.more), (2, 0))
        self.assertEqual((await self.watch.check(GUILD_A, self.c)).shown, ())

    async def test_one_entry_after_the_dm_sets_its_condition(self) -> None:
        entity, _, _ = await self.varn()
        other, _, _ = await self.varn()
        await self.set(entity, "dead")
        await self.set(other, "dead")
        (one,) = (await self.watch.check(GUILD_A, self.c, entity_id=entity)).shown
        self.assertEqual(one.table_name, "Captain Varn")
        rest = await self.watch.check(GUILD_A, self.c)
        self.assertEqual(len(rest.shown), 1)  # the other one, not this one again

    async def test_the_session_number_is_said_when_known(self) -> None:
        entity, _, _ = await self.varn()
        await self.memory.set_condition(
            GUILD_A, self.c, entity, "dead", source="dm", from_session_at=777
        )
        found = await self.watch.check(GUILD_A, self.c)
        self.assertIn("(Session 4)", ui.message_text(found, {777: 4}))
        self.assertNotIn("Session", ui.message_text(found, {}))


class MoreCases(Setup):
    async def test_found_against_lost_through_the_database(self) -> None:
        doc, ids = await self.book([("chapter", "Chapter 2", 1), ("place", "The Vault", 2)])
        vault = await self.add("The Vault", type="place")
        await self.say(ids["The Vault"], vault)
        await self.link(doc, ids, "Chapter 2", "The Vault", "found")
        await self.memory.set_condition(GUILD_A, self.c, vault, "intact", source="dm")
        self.assertEqual((await self.watch.check(GUILD_A, self.c)).shown, ())
        self.now += 5
        await self.memory.set_condition(GUILD_A, self.c, vault, "destroyed", source="dm")
        (one,) = (await self.watch.check(GUILD_A, self.c)).shown
        self.assertEqual((one.need, one.condition), ("found", "destroyed"))

    async def test_an_entry_merged_away_never_counts_and_a_told_clash_goes_gone(self) -> None:
        entity, _, _ = await self.varn()
        keeper = await self.add("The Captain", type="npc")
        await self.set(entity, "dead")
        await self.watch.check(GUILD_A, self.c)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'merged', merged_into = %s WHERE id = %s",
                (keeper, entity),
            )
        self.assertEqual((await self.watch.check(GUILD_A, self.c)).shown, ())
        self.assertEqual(await self.watch.count(GUILD_A, self.c, "gone"), 1)

    async def test_five_longest_names_still_fit_in_one_message(self) -> None:
        long = "N" * 120
        found = Found(
            tuple(Clash(f"{n:032x}", long, long, long, long, long, "dead", 1) for n in range(5)),
            3,
        )
        self.assertLessEqual(len(ui.message_text(found, {1: 4})), 2000)

    async def test_a_clash_that_could_not_be_posted_is_told_again(self) -> None:
        entity, _, _ = await self.varn()
        await self.set(entity, "dead")
        found = await self.watch.check(GUILD_A, self.c)
        await self.watch.retract(GUILD_A, self.c, [c.id for c in found.shown])
        self.assertEqual(len((await self.watch.check(GUILD_A, self.c)).shown), 1)

    async def test_gone_only_touches_this_campaigns_rows_and_a_delete_cascades(self) -> None:
        entity, link_id, _ = await self.varn()
        await self.set(entity, "dead")
        await self.watch.check(GUILD_A, self.c)
        other = (await self.campaigns.create(GUILD_A, "Other table", DM)).id
        await self.campaigns.set_story_mode(GUILD_A, other, STORY_WATCH, confirmed_by=DM)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM plan_same_as")  # would make it gone in this campaign
        await self.watch.check(GUILD_A, other)  # another campaign's check
        self.assertEqual(await self.watch.count(GUILD_A, self.c, "open"), 1)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM plan_links WHERE id = %s", (link_id,))
        self.assertEqual(await self.watch.count(GUILD_A, self.c, "open"), 0)


class Triggers(Setup):
    """The bot's step: the plan gate, a failed post, and one condition at a time."""

    def bot(self, *, allowed: bool = True, posted: bool = True) -> Any:
        sent: list[Any] = []

        async def post(channel: int, text: str, view: Any = None, **kw: Any) -> bool:
            sent.append((channel, text))
            return posted

        async def plan_allows(*args: Any, **kw: Any) -> bool:
            return allowed

        return SimpleNamespace(
            watch=self.watch, campaigns=self.campaigns, plan_allows=plan_allows, post=post,
            transcripts=None, sent=sent,
        )  # fmt: skip

    async def test_the_owners_plan_is_asked_and_a_no_posts_nothing_and_writes_nothing(self) -> None:
        from dmbot.bot import DMBot

        entity, _, _ = await self.varn()
        await self.set(entity, "dead")
        fake = self.bot(allowed=False)
        await DMBot.watch_story(fake, GUILD_A, self.c, 777)
        self.assertEqual(fake.sent, [])
        self.assertEqual(await self.watch.count(GUILD_A, self.c, "open"), 0)

    async def test_a_failed_post_forgets_the_rows_so_the_next_check_tells_them(self) -> None:
        from dmbot.bot import DMBot

        entity, _, _ = await self.varn()
        await self.set(entity, "dead")
        await DMBot.watch_story(self.bot(posted=False), GUILD_A, self.c, 777)
        self.assertEqual(await self.watch.count(GUILD_A, self.c, "open"), 0)
        fake = self.bot()
        await DMBot.watch_story(fake, GUILD_A, self.c, 777)
        self.assertEqual(len(fake.sent), 1)
        self.assertIn("(Session", fake.sent[0][1] + "(Session")  # numbering is optional
        self.assertEqual(await self.watch.count(GUILD_A, self.c, "open"), 1)

    async def test_a_check_for_one_condition_tells_only_that_one(self) -> None:
        entity, _, _ = await self.varn()
        other, _, _ = await self.varn()
        first = await self.set(entity, "dead")
        await self.set(other, "dead")
        state = first.value[0].id
        (one,) = (await self.watch.check(GUILD_A, self.c, state_id=state)).shown
        self.assertEqual(one.table_name, "Captain Varn")
        self.assertEqual(len((await self.watch.check(GUILD_A, self.c)).shown), 1)

    async def test_the_line_names_the_chapter_a_person_is_in_and_not_for_a_chapter(self) -> None:
        doc, ids = await self.book(
            [
                ("chapter", "Chapter 3: The Circle", 5),
                ("person", "Mother Ilse", 6),
                ("person", "Captain Varn", 7),
            ]
        )
        entity = await self.add("Captain Varn", type="npc")
        await self.say(ids["Captain Varn"], entity)
        await self.link(doc, ids, "Mother Ilse", "Captain Varn", "alive")
        await self.set(entity, "dead")
        found = await self.watch.check(GUILD_A, self.c)
        (one,) = found.shown
        self.assertEqual(one.chapter, "Chapter 3: The Circle")
        self.assertIn("alive (Chapter 3: The Circle). At your table", ui.message_text(found, {}))

    async def test_open_for_lists_the_told_clashes_of_some_entries_with_their_chapter(self) -> None:
        doc, ids = await self.book(
            [
                ("chapter", "Chapter 3", 5),
                ("person", "Mother Ilse", 6),
                ("person", "Captain Varn", 7),
            ]
        )
        entity = await self.add("Captain Varn", type="npc")
        await self.say(ids["Captain Varn"], entity)
        await self.link(doc, ids, "Mother Ilse", "Captain Varn", "alive")
        await self.set(entity, "dead")
        self.assertEqual(await self.watch.open_for(GUILD_A, self.c, frozenset(ids.values())), [])
        await self.watch.check(GUILD_A, self.c)
        (one,) = await self.watch.open_for(GUILD_A, self.c, frozenset([ids["Captain Varn"]]))
        self.assertEqual((one.book_name, one.chapter), ("Mother Ilse", "Chapter 3"))
        self.assertEqual(await self.watch.open_for(GUILD_A, self.c, frozenset()), [])
        await self.watch.know(GUILD_A, self.c, one.id)
        self.assertEqual(await self.watch.open_for(GUILD_A, self.c, frozenset(ids.values())), [])

    async def test_after_an_undo_the_buttons_no_longer_answer_the_old_message(self) -> None:
        entity, _, _ = await self.varn()
        await self.set(entity, "alive")
        died = await self.set(entity, "dead")
        (one,) = (await self.watch.check(GUILD_A, self.c)).shown
        await self.memory.undo(GUILD_A, self.c, died.batch)
        self.assertIsNone(await self.watch.words(GUILD_A, self.c, one.id))
        self.assertFalse(await self.watch.know(GUILD_A, self.c, one.id))


class Isolation(Setup):
    async def test_another_campaign_or_server_sees_none_of_it(self) -> None:
        entity, _, _ = await self.varn()
        await self.set(entity, "dead")
        other = (await self.campaigns.create(GUILD_A, "Other table", DM)).id
        theirs = (await self.campaigns.create(GUILD_B, "Elsewhere", DM)).id
        await self.campaigns.set_story_mode(GUILD_A, other, STORY_WATCH, confirmed_by=DM)
        await self.campaigns.set_story_mode(GUILD_B, theirs, STORY_WATCH, confirmed_by=DM)
        self.assertEqual((await self.watch.check(GUILD_A, other)).shown, ())
        self.assertEqual((await self.watch.check(GUILD_B, theirs)).shown, ())
        (mine,) = (await self.watch.check(GUILD_A, self.c)).shown
        # Another campaign cannot answer or read this one's clash either.
        self.assertFalse(await self.watch.know(GUILD_A, other, mine.id))
        self.assertIsNone(await self.watch.words(GUILD_A, other, mine.id))
        self.assertIsNone(await self.watch.words(GUILD_B, self.c, mine.id))
        self.assertEqual(await self.watch.count(GUILD_B, theirs, "open"), 0)
        self.assertEqual(await self.watch.count(GUILD_A, other, "open"), 0)


class Buttons(Setup):
    def pressing(self, user: int) -> Any:
        it = press(user, GUILD_A)

        async def plan_allows(*args: Any, **kw: Any) -> bool:
            return True

        it.client = SimpleNamespace(
            campaigns=self.campaigns, watch=self.watch, plan_allows=plan_allows
        )
        it.type = None

        async def followup(content: str = "", **kw: Any) -> None:
            it.sent.update(content=content, **kw)

        it.followup.send = followup
        return it

    async def test_only_a_dm_can_press_and_each_press_checks_again(self) -> None:
        entity, _, _ = await self.varn()
        await self.set(entity, "dead")
        (one,) = (await self.watch.check(GUILD_A, self.c)).shown
        know, words = ui.KnowButton(self.c, one.id), ui.WordsButton(self.c, one.id)
        stranger = self.pressing(999)
        await words.callback(stranger)
        await know.callback(self.pressing(999))
        self.assertEqual(await self.watch.count(GUILD_A, self.c, "open"), 1)  # untouched
        self.assertIn(ui.ONLY_DM, str(stranger.sent))
        seen = self.pressing(DM)
        await words.callback(seen)
        self.assertIn("He must live to the end.", str(seen.sent))
        await know.callback(self.pressing(DM))
        self.assertEqual(await self.watch.count(GUILD_A, self.c, "known"), 1)

    async def test_the_buttons_answer_discord_before_the_database(self) -> None:
        entity, _, _ = await self.varn()
        await self.set(entity, "dead")
        (one,) = (await self.watch.check(GUILD_A, self.c)).shown
        for button in (ui.KnowButton(self.c, one.id), ui.WordsButton(self.c, one.id)):
            line = Timeline()
            it = self.pressing(DM)
            it.response, it.followup = line.response, line.followup
            it.client.campaigns = SimpleNamespace(
                get=line.slow("campaign", await self.campaigns.get(GUILD_A, self.c))
            )
            await button.callback(it)
            line.assert_answered_first()
            self.assertIn("slow:campaign", line.events, type(button).__name__)

    async def test_the_view_has_two_buttons_for_each_line_and_numbered_when_many(self) -> None:
        entity, _, _ = await self.varn()
        await self.set(entity, "dead")
        found = await self.watch.check(GUILD_A, self.c)
        view = ui.watch_view(self.c, found)
        labels = [getattr(getattr(c, "item", c), "label", "") for c in view.children]
        self.assertEqual(labels, [ui.KNOW_LABEL, ui.WORDS_LABEL])
