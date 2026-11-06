"""Guide me (#1731): where the table seems to be in the DM's adventure and what is next, with no
AI. The chapter rule and the guess are pure; the rest needs the test database."""

from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord

from dmbot.campaigns.models import STORY_OFF, STORY_WATCH
from dmbot.memory import book_links as links
from dmbot.memory import guide as model
from dmbot.memory.adventure import AdventureStore
from dmbot.memory.guide import Node, build, guess
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.story_plan import PlanStore
from dmbot.memory.story_watch import StoryWatch
from dmbot.ui import guide as ui
from tests import speed
from tests import test_story_watch as base
from tests.test_adventure import press
from tests.test_memory_store import DM, GUILD_A, GUILD_B


def node(kind: str, name: str, position: int, **kw: Any) -> Node:
    return Node(f"{name}".lower().replace(" ", "-"), kind, name, kw.pop("summary", ""), position,
                kw.pop("created_at", 1), **kw)  # fmt: skip


def book_of(nodes: list[Node]) -> list[Node]:
    """A test fixture: each entry's chapter id as the SQL rule gives it (the nearest chapter at or
    before it, the later when two share a place). The database test below proves they agree."""
    chapters = sorted(
        (n for n in nodes if n.kind == "chapter"), key=lambda c: (c.position, c.created_at)
    )
    out = []
    for n in nodes:
        home = [c for c in chapters if c.position <= n.position]
        out.append(replace(n, chapter_id=home[-1].id if home else None))
    return out


BOOK = book_of(
    [
        node("chapter", "Chapter 1", 1, summary="The harbour."),
        node("place", "Saltmere", 2),
        node("person", "Captain Varn", 3, table_name="Varn", heard=True),
        node("person", "Mother Ilse", 3, table_name="Ilse", heard=False),
        node("chapter", "Chapter 2", 5, summary="x" * 400),
        node("place", "The Vault", 6),
        node("person", "Bryn", 7, table_name="Bryn", heard=True),
        node("person", "Pell", 8, table_name="Pell", heard=True),
        node("person", "Orla", 8),
    ]
)


class Rules(unittest.TestCase):
    def test_the_guess_is_the_chapter_with_most_linked_names_heard_lately(self) -> None:
        self.assertEqual(guess(BOOK).name, "Chapter 2")  # type: ignore[union-attr]
        one = [n for n in BOOK if n.name not in ("Bryn", "Pell")]
        self.assertEqual(guess(one).name, "Chapter 1")  # type: ignore[union-attr]

    def test_a_tie_goes_to_the_later_chapter(self) -> None:
        tied = book_of(
            [
                node("chapter", "A", 1),
                node("person", "x", 2, table_name="X", heard=True),
                node("chapter", "B", 5),
                node("person", "y", 6, table_name="Y", heard=True),
            ]
        )
        self.assertEqual(guess(tied).name, "B")  # type: ignore[union-attr]

    def test_no_linked_heard_name_is_no_guess(self) -> None:
        quiet = [n for n in BOOK if not n.heard]
        self.assertIsNone(guess(quiet))
        self.assertIsNone(build(quiet, None).chapter)

    def test_the_dms_pick_beats_the_guess(self) -> None:
        guide = build(BOOK, "chapter-1")
        self.assertEqual((guide.chapter.name, guide.picked), ("Chapter 1", True))  # type: ignore[union-attr]
        self.assertFalse(build(BOOK, None).picked)
        self.assertFalse(build(BOOK, "not-a-chapter").picked)  # a stale pick falls back

    def test_still_ahead_leaves_out_what_is_linked_and_counts_the_rest(self) -> None:
        guide = build(BOOK, "chapter-1")
        self.assertEqual(guide.ahead, ("Saltmere",))
        self.assertEqual(guide.evidence, ("Varn", "Ilse"))  # the pick: all linked ones in it
        guess_ = build(BOOK, None)  # Chapter 2
        self.assertEqual((guess_.ahead, guess_.evidence), (("The Vault", "Orla"), ("Bryn", "Pell")))
        many = book_of([node("chapter", "C", 1)] + [node("place", f"P{n}", 2) for n in range(9)])
        guide = build(many, "c")
        self.assertEqual((len(guide.ahead), guide.ahead_more), (6, 3))

    def test_the_next_chapter_and_the_end_of_the_book(self) -> None:
        self.assertEqual(build(BOOK, "chapter-1").following.name, "Chapter 2")  # type: ignore[union-attr]
        self.assertIsNone(build(BOOK, None).following)
        text = ui.guide_text(build(BOOK, None), [])
        self.assertIn(ui.LAST, text)

    def test_the_text_is_at_most_ten_lines_and_names_are_escaped(self) -> None:
        bad = book_of(
            [
                node("chapter", "**Chapter** @everyone " + "z" * 100, 1, summary="`code` <@123>"),
                node("chapter", "Next @here", 5, summary="*bold* " + "y" * 300),
                node("person", "_Varn_ @everyone", 2),
            ]
        )
        guide = build(bad, bad[0].id)
        text = ui.guide_text(guide, [])
        self.assertLessEqual(len(text.splitlines()), 10)
        self.assertLessEqual(len(text), 1990)
        self.assertNotIn("**Chapter**", text)
        self.assertIn("\\_Varn\\_", text)
        self.assertNotIn("y" * 200, text)  # the summary is cut at 150

    def test_a_summary_is_cut_at_150_characters(self) -> None:
        self.assertEqual(len(model.summary_of(node("chapter", "c", 1, summary="x" * 400))), 150)


class Menus(unittest.TestCase):
    def test_more_than_25_chapters_can_all_be_reached(self) -> None:
        nodes = [node("chapter", f"Chapter {n}", n * 10) for n in range(40)]
        guide = build(nodes, "chapter-5")
        seen: set[str] = set()
        menu = ui.GuideMenu("c" * 32, "d" * 32, guide, [], None)
        for _ in range(4):
            seen |= {o.value for o in menu.select.options}
            if not any("Later" in str(getattr(c, "label", "")) for c in menu.children):
                break
            menu = ui.GuideMenu("c" * 32, "d" * 32, guide, [], None, start=menu.start + 25)
        self.assertEqual(len(seen), 40)
        self.assertLessEqual(len(menu.select.options), 25)

    def test_the_worst_case_message_drops_whole_lines_and_keeps_markers_closed(self) -> None:
        from dmbot.memory.story_watch import Clash

        long = "N" * 120
        nodes = book_of(
            [
                node("chapter", long, 1, summary="s" * 400),
                node("chapter", long + "2", 5, summary="t" * 400),
                *[node("person", long + str(n), 2) for n in range(9)],
            ]
        )
        guide = build(nodes, nodes[0].id)
        clashes = [Clash(f"{n:032x}", long, long, long, long, long, "dead", 1) for n in range(5)]
        text = ui.guide_text(guide, clashes, file_name=long, note="x" * 200)
        self.assertLessEqual(len(text), 1990)
        self.assertEqual(text.count("**") % 2, 0)


class Setup(base.Setup):
    async def chapters(self) -> tuple[str, dict[str, str]]:
        doc, ids = await self.book(
            [
                ("chapter", "Chapter 1", 1),
                ("place", "Saltmere", 2),
                ("person", "Captain Varn", 3),
                ("chapter", "Chapter 2", 5),
                ("place", "The Vault", 6),
                ("person", "Bryn", 7),
            ]
        )
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE plan_nodes SET summary = 'The harbour.' WHERE id = %s",
                (ids["Chapter 1"],),
            )
        return doc, ids

    async def hear(self, entity: str, session: int) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_heard (guild_id, campaign_id, entity_id,"
                " session_started_at, speaker_id, times) VALUES (%s, %s, %s, %s, 7, 1)",
                (GUILD_A, self.c, entity, session),
            )

    async def linked(self, ids: dict[str, str], name: str, session: int | None) -> str:
        entity = await self.add(name, type="place" if name == "Saltmere" else "npc")
        await self.say(ids[name], entity)
        if session is not None:
            await self.hear(entity, session)
        return entity


class Reading(Setup):
    async def test_the_guess_comes_from_names_heard_in_the_last_two_sessions(self) -> None:
        doc, ids = await self.chapters()
        await self.linked(ids, "Captain Varn", 100)  # old: three sessions ago
        await self.hear((await self.memory.entities(GUILD_A, self.c))[0].id, 200)
        bryn = await self.linked(ids, "Bryn", 300)
        await self.hear(bryn, 400)
        nodes, pick = await model.read(self.memory, GUILD_A, self.c, doc)
        self.assertIsNone(pick)
        by = {n.name: n for n in nodes}
        self.assertEqual(
            (by["Captain Varn"].heard, by["Bryn"].heard, by["Saltmere"].heard),
            (False, True, False),  # Varn was last heard in session 200, older than the last two
        )
        self.assertEqual(build(nodes, pick).chapter.name, "Chapter 2")  # type: ignore[union-attr]

    async def test_the_chapter_rule_agrees_with_the_one_the_names_card_uses(self) -> None:
        doc, ids = await self.chapters()
        entity = await self.linked(ids, "Bryn", 100)
        (link,) = await links.linked(self.memory, GUILD_A, self.c, entity)
        nodes, _ = await model.read(self.memory, GUILD_A, self.c, doc)
        bryn = next(n for n in nodes if n.name == "Bryn")
        names = {n.id: n.name for n in nodes if n.kind == "chapter"}
        self.assertEqual(names[bryn.chapter_id or ""], link.chapter)
        # ...and the fixture the pure tests use gives the same answer as the database does.
        fixture = {
            n.id: n.chapter_id for n in book_of([replace(n, chapter_id=None) for n in nodes])
        }
        self.assertEqual(
            fixture,
            {n.id: n.chapter_id for n in nodes if n.kind != "chapter"}
            | {n.id: n.chapter_id for n in nodes if n.kind == "chapter"},
        )

    async def test_the_dms_pick_beats_the_guess_and_undo_gives_the_guess_back(self) -> None:
        doc, ids = await self.chapters()
        bryn = await self.linked(ids, "Bryn", 100)
        self.assertTrue(bryn)
        batch = await model.pick(self.memory, GUILD_A, self.c, doc, ids["Chapter 1"], DM)
        nodes, pick = await model.read(self.memory, GUILD_A, self.c, doc)
        self.assertEqual(build(nodes, pick).chapter.name, "Chapter 1")  # type: ignore[union-attr]
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch)
        nodes, pick = await model.read(self.memory, GUILD_A, self.c, doc)
        self.assertEqual(build(nodes, pick).chapter.name, "Chapter 2")  # type: ignore[union-attr]
        # Picking again, then going back to the guess, and Undo of that.
        await model.pick(self.memory, GUILD_A, self.c, doc, ids["Chapter 1"], DM)
        cleared = await model.clear(self.memory, GUILD_A, self.c, doc)
        _, pick = await model.read(self.memory, GUILD_A, self.c, doc)
        self.assertIsNone(pick)
        assert cleared is not None
        await self.memory.undo(GUILD_A, self.c, cleared)
        _, pick = await model.read(self.memory, GUILD_A, self.c, doc)
        self.assertEqual(pick, ids["Chapter 1"])

    async def test_a_second_pick_replaces_the_first_and_only_a_chapter_of_the_file(self) -> None:
        doc, ids = await self.chapters()
        await model.pick(self.memory, GUILD_A, self.c, doc, ids["Chapter 1"], DM)
        await model.pick(self.memory, GUILD_A, self.c, doc, ids["Chapter 2"], DM)
        _, pick = await model.read(self.memory, GUILD_A, self.c, doc)
        self.assertEqual(pick, ids["Chapter 2"])
        with self.assertRaises(MemoryRuleError):
            await model.pick(self.memory, GUILD_A, self.c, doc, ids["Saltmere"], DM)  # a place

    async def test_isolation_between_campaigns_and_servers(self) -> None:
        doc, ids = await self.chapters()
        await model.pick(self.memory, GUILD_A, self.c, doc, ids["Chapter 1"], DM)
        other = (await self.campaigns.create(GUILD_A, "Other table", DM)).id
        theirs = (await self.campaigns.create(GUILD_B, "Elsewhere", DM)).id
        for guild, campaign in ((GUILD_A, other), (GUILD_B, theirs)):
            self.assertEqual(await model.read(self.memory, guild, campaign, doc), ([], None))
        with self.assertRaises(MemoryRuleError):  # another campaign cannot pick this one's chapter
            await model.pick(self.memory, GUILD_A, other, doc, ids["Chapter 2"], DM)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM plan_position")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(int(row["n"]), 0)

    async def test_the_pick_goes_with_its_file(self) -> None:
        doc, ids = await self.chapters()
        await model.pick(self.memory, GUILD_A, self.c, doc, ids["Chapter 1"], DM)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM adventure_documents WHERE id = %s", (doc,))
            cur = await conn.execute("SELECT count(*) AS n FROM plan_position")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(int(row["n"]), 0)


class Pressing(Setup):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.plan = PlanStore(self.db, clock=lambda: self.now)
        self.store = AdventureStore(self.db, clock=lambda: self.now)
        self.allowed = True
        await self.campaigns.set_story_mode(GUILD_A, self.c, "guide", confirmed_by=DM)

    def pressing(self, user: int = DM) -> Any:
        it = press(user, GUILD_A)

        async def plan_allows(*args: Any, **kw: Any) -> bool:
            return self.allowed

        it.client = SimpleNamespace(
            memory=self.memory, campaigns=self.campaigns, adventure=self.store,
            plan=self.plan, plan_allows=plan_allows, watch=StoryWatch(self.db), lookup=None,
        )  # fmt: skip
        it.type = None

        async def followup(content: str = "", **kw: Any) -> None:
            it.sent.update(content=content, **kw)

        it.followup.send = followup
        return it

    async def test_the_button_answers_the_dm_with_where_and_what_is_next(self) -> None:
        doc, ids = await self.chapters()
        await self.linked(ids, "Captain Varn", 100)
        entity = (await self.memory.entities(GUILD_A, self.c))[0].id
        await self.set(entity, "dead")
        it = self.pressing()
        await ui.GuideButton(self.c).callback(it)
        text = it.sent["content"]
        self.assertIn("Probably **Chapter 1** (heard: Captain Varn).", text)
        self.assertIn("In the book, this chapter also has: Saltmere.", text)
        self.assertIn("The book's next chapter: **Chapter 2**", text)
        labels = [
            str(getattr(getattr(c, "item", c), "label", "")) for c in it.sent["view"].children
        ]
        self.assertNotIn("Use DMbot's guess", labels)  # nothing picked yet: nothing to go back to
        self.assertTrue(any(isinstance(c, discord.ui.Select) for c in it.sent["view"].children))
        self.assertTrue(doc)

    async def test_the_menu_saves_the_pick_and_undo_restores_the_guess(self) -> None:
        doc, ids = await self.chapters()
        await self.linked(ids, "Captain Varn", 100)
        it = self.pressing()
        await ui.show(it, self.c, doc)
        menu = it.sent["view"]
        it2 = self.pressing()
        menu.select._values = [ids["Chapter 2"]]
        await menu._picked(it2)
        self.assertIn("**Chapter 2** (you picked it)", it2.sent["content"])
        undo_menu = it2.sent["view"]
        it3 = self.pressing()
        await undo_menu._undo(it3)
        self.assertIn("Probably **Chapter 1**", it3.sent["content"])

    async def test_no_press_works_in_watch_mode_off_a_restored_confirmation_or_without_the_plan(
        self,
    ) -> None:
        _, ids = await self.chapters()
        await self.linked(ids, "Captain Varn", 100)
        await self.campaigns.set_story_mode(GUILD_A, self.c, STORY_WATCH, confirmed_by=DM)
        it = self.pressing()
        await ui.GuideButton(self.c).callback(it)
        self.assertIn(ui.NOT_GUIDE, it.sent["content"])
        await self.campaigns.set_story_mode(GUILD_A, self.c, STORY_OFF)
        it = self.pressing()
        await ui.GuideButton(self.c).callback(it)
        self.assertIn(ui.NOT_GUIDE, it.sent["content"])
        await self.campaigns.set_story_mode(GUILD_A, self.c, "guide", confirmed_by=DM)
        self.allowed = False
        it = self.pressing()
        await ui.GuideButton(self.c).callback(it)
        self.assertIn(ui.NO_PLAN, it.sent["content"])
        self.allowed = True
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE shared_confirmations SET restored = true")
        it = self.pressing()
        await ui.GuideButton(self.c).callback(it)
        self.assertIn(ui.NOT_GUIDE, it.sent["content"])

    async def test_the_menus_ask_again_so_nothing_is_saved_after_the_switch_goes_off(self) -> None:
        doc, ids = await self.chapters()
        await self.linked(ids, "Captain Varn", 100)
        it = self.pressing()
        await ui.show(it, self.c, doc)
        menu = it.sent["view"]
        await self.campaigns.set_story_mode(GUILD_A, self.c, STORY_OFF)
        menu.select._values = [ids["Chapter 2"]]
        refused = self.pressing()
        await menu._picked(refused)
        self.assertIn(ui.NOT_GUIDE, refused.sent["content"])
        _, pick = await model.read(self.memory, GUILD_A, self.c, doc)
        self.assertIsNone(pick)  # nothing was written

    async def test_a_bad_pick_says_a_fixed_line_not_the_error(self) -> None:
        doc, ids = await self.chapters()
        await self.linked(ids, "Captain Varn", 100)
        it = self.pressing()
        await ui.show(it, self.c, doc)
        menu = it.sent["view"]
        menu.select._values = [ids["Saltmere"]]  # a place, not a chapter
        again = self.pressing()
        await menu._picked(again)
        self.assertIn(ui.CANT_SAVE, again.sent["content"])

    async def test_with_several_files_it_asks_which_first_and_then_answers(self) -> None:
        doc, ids = await self.chapters()
        await self.linked(ids, "Captain Varn", 100)
        two, ids2 = await self.book([("chapter", "Other 1", 1), ("person", "Nell", 2)])
        it = self.pressing()
        await ui.GuideButton(self.c).callback(it)
        menu = it.sent["view"]
        self.assertIsInstance(menu, ui.FileMenu)
        self.assertEqual({o.value for o in menu.select.options}, {doc, two})
        menu.select._values = [two]
        again = self.pressing()
        await menu._picked(again)
        self.assertIn("keep.txt", again.sent["content"])  # which file, when there are several
        shown = again.sent["view"].select.options
        self.assertEqual([o.label for o in shown], ["Other 1"])  # that file's chapters only
        self.assertTrue(ids2)

    async def test_the_answer_never_pings(self) -> None:
        doc, ids = await self.chapters()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE plan_nodes SET name = '@everyone <@123>' WHERE id = %s", (ids["Saltmere"],)
            )
        it = self.pressing()
        await ui.show(it, self.c, doc)
        self.assertEqual(it.sent["allowed_mentions"].to_dict(), {"parse": []})

    async def test_the_note_and_the_card_both_ask_the_same_three_things(self) -> None:
        from dmbot.bot import DMBot

        await self.chapters()
        fake: Any = SimpleNamespace(campaigns=self.campaigns, plan_allows=self.plan_allows)
        for mode, want in (("guide", True), ("watch", False), ("off", False)):
            if mode == "off":
                await self.campaigns.set_story_mode(GUILD_A, self.c, STORY_OFF)
            else:
                await self.campaigns.set_story_mode(GUILD_A, self.c, mode, confirmed_by=DM)
            campaign = await self.campaigns.get(GUILD_A, self.c)
            assert campaign is not None
            self.assertEqual(await DMBot.guide_button_allowed(fake, GUILD_A, campaign), want, mode)
        await self.campaigns.set_story_mode(GUILD_A, self.c, "guide", confirmed_by=DM)
        campaign = await self.campaigns.get(GUILD_A, self.c)
        assert campaign is not None
        self.allowed = False  # the plan says no
        self.assertFalse(await DMBot.guide_button_allowed(fake, GUILD_A, campaign))
        self.allowed = True
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE shared_confirmations SET restored = true")
        self.assertFalse(await DMBot.guide_button_allowed(fake, GUILD_A, campaign))

    async def test_a_read_that_fails_costs_only_the_button(self) -> None:
        from dmbot.bot import DMBot

        await self.chapters()
        await self.campaigns.set_story_mode(GUILD_A, self.c, "guide", confirmed_by=DM)
        campaign = await self.campaigns.get(GUILD_A, self.c)
        assert campaign is not None

        async def broken(*a: Any, **kw: Any) -> Any:
            raise RuntimeError("the database is down")

        fake: Any = SimpleNamespace(
            campaigns=SimpleNamespace(story_confirmed=broken), plan_allows=self.plan_allows
        )
        self.assertFalse(await DMBot.guide_button_allowed(fake, GUILD_A, campaign))
        fake.campaigns = self.campaigns
        fake.plan_allows = broken  # the plan check failing is closed too
        self.assertFalse(await DMBot.guide_button_allowed(fake, GUILD_A, campaign))

    async def test_the_sidebar_leaves_a_fight_to_the_usual_answer(self) -> None:
        from dmbot.bot import DMBot

        _, ids = await self.chapters()
        await self.linked(ids, "Captain Varn", 100)
        host = self.sidebar_host()
        table: Any = SimpleNamespace(guild_id=GUILD_A, campaign_id=self.c)
        self.assertIsNotNone(await DMBot.sidebar_guide(host, table, DM))
        host.clocks = SimpleNamespace(in_combat=AsyncMock(return_value=True))
        self.assertIsNone(await DMBot.sidebar_guide(host, table, DM))  # whose turn it is, then
        host.clocks = SimpleNamespace(in_combat=AsyncMock(return_value=False))
        self.assertIsNotNone(await DMBot.sidebar_guide(host, table, DM))

    async def test_several_files_give_one_block_each_and_only_the_first_few(self) -> None:
        for n in range(4):
            await self.book(
                [("chapter", f"Book {n} chapter 1", 1), ("chapter", f"Book {n} chapter 2", 5)],
                text=f"book {n}",
            )
        host = self.sidebar_host()
        campaign = await self.campaigns.get(GUILD_A, self.c)
        assert campaign is not None
        text = await ui.answer_text(host, campaign)
        assert text is not None
        self.assertEqual(text.count("🧭"), ui.SPOKEN_FILES)  # three of the four, whole blocks
        self.assertLessEqual(len(text), ui.TEXT_MAX)
        self.assertEqual(text.count("\n\n"), ui.SPOKEN_FILES - 1)

    async def plan_allows(self, *args: Any, **kw: Any) -> bool:
        return self.allowed

    def sidebar_host(self) -> Any:
        """A stand-in for the bot with the pieces `DMBot.sidebar_guide` uses."""
        from dmbot.bot import DMBot

        host: Any = SimpleNamespace(
            campaigns=self.campaigns, plan_allows=self.plan_allows, memory=self.memory,
            adventure=self.store, plan=self.plan, watch=StoryWatch(self.db), clocks=None,
        )  # fmt: skip
        host.guide_button_allowed = lambda g, c: DMBot.guide_button_allowed(host, g, c)
        return host

    async def test_the_sidebars_answer_is_the_buttons_text(self) -> None:
        from dmbot.bot import DMBot

        _, ids = await self.chapters()
        await self.linked(ids, "Captain Varn", 100)
        it = self.pressing()
        await ui.GuideButton(self.c).callback(it)
        host = self.sidebar_host()
        table: Any = SimpleNamespace(guild_id=GUILD_A, campaign_id=self.c)
        text = await DMBot.sidebar_guide(host, table, DM)
        self.assertEqual(text, it.sent["content"])

    async def test_the_sidebar_gives_it_only_to_a_dm_with_all_three_conditions(self) -> None:
        from dmbot.bot import DMBot

        _, ids = await self.chapters()
        await self.linked(ids, "Captain Varn", 100)
        host = self.sidebar_host()
        table: Any = SimpleNamespace(guild_id=GUILD_A, campaign_id=self.c)
        self.assertIsNone(await DMBot.sidebar_guide(host, table, 999))  # not a DM
        self.allowed = False
        self.assertIsNone(await DMBot.sidebar_guide(host, table, DM))  # the plan says no
        self.allowed = True
        for mode in ("watch", "off"):
            if mode == "off":
                await self.campaigns.set_story_mode(GUILD_A, self.c, STORY_OFF)
            else:
                await self.campaigns.set_story_mode(GUILD_A, self.c, mode, confirmed_by=DM)
            self.assertIsNone(await DMBot.sidebar_guide(host, table, DM), mode)
        await self.campaigns.set_story_mode(GUILD_A, self.c, "guide", confirmed_by=DM)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE shared_confirmations SET restored = true")
        self.assertIsNone(await DMBot.sidebar_guide(host, table, DM))  # restored confirmation

    async def test_only_a_dm_can_press(self) -> None:
        await self.chapters()
        it = self.pressing(999)
        await ui.GuideButton(self.c).callback(it)
        self.assertIn(ui.ONLY_DM, it.sent["content"])

    async def test_a_file_without_chapters_and_nothing_read(self) -> None:
        it = self.pressing()
        await ui.GuideButton(self.c).callback(it)
        self.assertIn(ui.NOT_READ, it.sent["content"])
        await self.book([("person", "Bryn", 1)])
        it = self.pressing()
        await ui.GuideButton(self.c).callback(it)
        self.assertIn(ui.NO_CHAPTERS, it.sent["content"])  # read, but no chapters found

    async def test_the_card_has_the_button_only_in_guide_mode_and_with_chapters(self) -> None:
        from dmbot.dm_screen import adventure_card as card

        await self.chapters()
        for mode, want in (("guide", True), ("watch", False)):
            await self.campaigns.set_story_mode(GUILD_A, self.c, mode, confirmed_by=DM)
            campaign = await self.campaigns.get(GUILD_A, self.c)
            assert campaign is not None
            _, view = await card._card(self.pressing(), campaign)
            labels = [str(getattr(getattr(c, "item", c), "label", "")) for c in view.children]
            self.assertEqual(ui.LABEL in labels, want, mode)

    async def test_a_clash_told_by_watch_the_story_shows_for_its_chapter(self) -> None:
        doc, ids = await self.chapters()
        link = await self.link(doc, ids, "Chapter 1", "Captain Varn", "alive")
        self.assertTrue(link)
        entity = await self.linked(ids, "Captain Varn", 100)
        await self.set(entity, "dead")
        await StoryWatch(self.db, clock=lambda: self.now).check(GUILD_A, self.c)
        it = self.pressing()
        await ui.GuideButton(self.c).callback(it)
        text = it.sent["content"]
        self.assertIn("📖 Your adventure: **Chapter 1** needs **Captain Varn** alive", text)
        words = [
            c
            for c in it.sent["view"].children
            if "words" in str(getattr(getattr(c, "item", c), "label", ""))
        ]
        self.assertEqual(len(words), 1)
        self.assertLessEqual(len(text.splitlines()), 10)


class Speed(Setup):
    async def test_a_300_page_plan_is_read_in_a_few_milliseconds(self) -> None:
        import time

        nodes: list[tuple[str, str, int]] = []
        for c in range(40):
            nodes.append(("chapter", f"Chapter {c}", c * 10))
            nodes += [("person", f"Person {c}-{n}", c * 10 + 1 + n % 8) for n in range(6)]
            nodes += [("place", f"Place {c}-{n}", c * 10 + 1 + n % 8) for n in range(4)]
        doc, ids = await self.book(nodes)
        for c in range(0, 40, 2):
            await self.linked(ids, f"Person {c}-0", 100 + c)
        await model.read(self.memory, GUILD_A, self.c, doc)  # (the first call opens a connection)
        began = time.perf_counter()
        read, pick = await model.read(self.memory, GUILD_A, self.c, doc)
        guide = build(read, pick)
        took = time.perf_counter() - began
        print(f"\nGuide me on {len(read)} entries and 20 links: {took * 1000:.1f} ms")
        self.assertEqual(len(read), 440)
        self.assertIsNotNone(guide.chapter)
        self.assertLess(took, speed.allowed(0.5))
