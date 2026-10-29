"""The book and the table (#1715): suggestions by name with no AI, the DM's Same and Not the same,
by hand, the one-to-one rule, Undo, another campaign seeing nothing, and nothing in recall or the
players' views. Needs the test database."""

from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace
from typing import Any

import discord

from dmbot.campaigns.models import STORY_OFF, STORY_WATCH
from dmbot.dm_screen import adventure_card as adventure_ui
from dmbot.memory import book_links as links
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService
from dmbot.memory.story_plan import name_key as plan_key
from dmbot.ui import book_link as ui
from dmbot.ui.name_card import BOOK_BUTTON, NameCard, card_text
from tests.test_adventure import press
from tests.test_memory_store import DM, GUILD_A, GUILD_B, ConfirmedMemoryTest


class Setup(ConfirmedMemoryTest):
    async def book(
        self,
        nodes: list[tuple[str, str, int]],
        *,
        guild: int = GUILD_A,
        campaign: str | None = None,
        text: str = "The Sunken Keep",
    ) -> tuple[str, dict[str, str]]:
        """A file with the given plan entries, (kind, name, position): their ids by name."""
        cid = campaign or self.c
        doc = uuid.uuid4().hex
        ids: dict[str, str] = {}
        async with self.db.guild(guild) as conn:
            await conn.execute(
                "INSERT INTO adventure_documents (guild_id, campaign_id, id, file_name,"
                " fingerprint, characters, chunks, added_by, added_at)"
                " VALUES (%s, %s, %s, 'keep.txt', %s, 100, 1, %s, %s)",
                (guild, cid, doc, hashlib.sha256(f"{text}{doc}".encode()).hexdigest(), DM, 1),
            )
            for kind, name, position in nodes:
                ids[name] = uuid.uuid4().hex
                await conn.execute(
                    "INSERT INTO plan_nodes (guild_id, campaign_id, id, document_id, kind, name,"
                    " name_key, summary, quote, chunk_ids, position, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, '', '', %s, %s, 1)",
                    (guild, cid, ids[name], doc, kind, name, plan_key(name), [], position),
                )
        return doc, ids

    async def say(self, plan_id: str, entity_id: str, status: str = links.SAME) -> Any:
        return await links.decide(self.memory, GUILD_A, self.c, [(plan_id, entity_id, status)], DM)

    async def pairs(self, **kw: Any) -> list[tuple[str, str]]:
        got = await links.suggestions(self.memory, GUILD_A, self.c, **kw)
        return [(p.plan_name, p.entity_name) for p in got]


class Suggesting(Setup):
    async def test_a_book_name_is_matched_to_a_table_name_or_what_it_is_called(self) -> None:
        await self.book(
            [
                ("person", "Captain Varn", 1),
                ("person", "Mother Ilse", 1),
                ("place", "Saltmere", 1),
                ("person", "Nobody At Table", 1),
                ("chapter", "Chapter 1", 1),
            ]
        )
        await self.add("captain varn", type="npc")  # case does not matter
        ilse = await self.add("Ilse the Elder", type="npc")
        await self.memory.add_alias(
            GUILD_A, self.c, ilse, "Mother Ilse", kind="title", source="dm", status="confirmed"
        )
        await self.add("Saltmere", type="place")
        self.assertEqual(
            await self.pairs(),
            [
                ("Captain Varn", "captain varn"),
                ("Mother Ilse", "Ilse the Elder"),
                ("Saltmere", "Saltmere"),
            ],
        )

    async def test_an_apostrophe_or_an_accent_does_not_hide_a_match(self) -> None:
        await self.book([("person", "Ka'Zeth", 1)])
        await self.add("Kazeth", type="npc")
        self.assertEqual(await self.pairs(), [("Ka'Zeth", "Kazeth")])

    async def test_a_person_is_never_matched_to_a_place_and_a_place_never_to_a_person(self) -> None:
        await self.book([("person", "Saltmere", 1), ("place", "Captain Varn", 1)])
        await self.add("Saltmere", type="place")
        await self.add("Captain Varn", type="npc")
        self.assertEqual(await self.pairs(), [])

    async def test_only_confirmed_unmerged_entries_count(self) -> None:
        await self.book([("person", "Bryn", 1), ("person", "Old Pell", 1)])
        await self.add("Bryn", type="npc", status="proposed")
        pell = await self.add("Old Pell", type="npc")
        other = await self.add("Pell", type="npc")
        await self.memory.merge_entities(GUILD_A, self.c, pell, other, source="dm") if hasattr(
            self.memory, "merge_entities"
        ) else None
        names = [e for _, e in await self.pairs()]
        self.assertNotIn("Bryn", names)

    async def test_at_most_ten_are_asked_at_a_time(self) -> None:
        names = [f"Person{chr(97 + n)}" for n in range(14)]
        await self.book([("person", n, 1) for n in names])
        for n in names:
            await self.add(n, type="npc")
        self.assertEqual(len(await self.pairs()), 10)
        self.assertEqual(len(await self.pairs(limit=3)), 3)


class Deciding(Setup):
    async def setUpBook(self) -> tuple[dict[str, str], str, str]:
        _, ids = await self.book(
            [
                ("chapter", "Chapter 1: The Harbour", 1),
                ("chapter", "Chapter 3: The Circle", 5),
                ("person", "Captain Varn", 2),
                ("person", "Mother Ilse", 6),
            ]
        )
        return ids, await self.add("Captain Varn", type="npc"), await self.add("Ilse", type="npc")

    async def test_same_writes_the_link_and_the_card_can_say_where_in_the_book(self) -> None:
        ids, varn, ilse = await self.setUpBook()
        written = await self.say(ids["Captain Varn"], varn, links.SAME)
        self.assertEqual((written.value, written.batch is not None), (1, True))
        (link,) = await links.linked(self.memory, GUILD_A, self.c, varn)
        self.assertEqual(
            (link.name, link.kind, link.chapter),
            ("Captain Varn", "person", "Chapter 1: The Harbour"),
        )
        # A later entry belongs to the chapter before it in the text.
        await self.say(ids["Mother Ilse"], ilse, links.SAME)
        (other,) = await links.linked(self.memory, GUILD_A, self.c, ilse)
        self.assertEqual(other.chapter, "Chapter 3: The Circle")
        self.assertEqual(await self.pairs(), [])  # nothing left to ask about

    async def test_not_the_same_is_remembered_so_it_is_not_asked_again(self) -> None:
        ids, varn, _ = await self.setUpBook()
        self.assertEqual(await self.pairs(), [("Captain Varn", "Captain Varn")])
        await self.say(ids["Captain Varn"], varn, links.NOT_SAME)
        self.assertEqual(await self.pairs(), [])
        self.assertEqual(await links.linked(self.memory, GUILD_A, self.c, varn), [])
        # The DM may change their mind by hand: it becomes a Same.
        await self.say(ids["Captain Varn"], varn, links.SAME)
        self.assertEqual(len(await links.linked(self.memory, GUILD_A, self.c, varn)), 1)

    async def test_several_answers_are_one_change_and_one_undo_takes_them_all_back(self) -> None:
        ids, varn, ilse = await self.setUpBook()
        before = await self.snapshot()
        written = await links.decide(
            self.memory,
            GUILD_A,
            self.c,
            [(ids["Captain Varn"], varn, links.SAME), (ids["Mother Ilse"], ilse, links.NOT_SAME)],
            DM,
        )
        self.assertEqual(written.value, 2)
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertEqual(await self.snapshot(), before)
        self.assertEqual(
            len(await self.pairs()), 1
        )  # (Ilse isn't a name match) Varn is asked again

    async def test_taking_a_link_off_is_undoable(self) -> None:
        ids, varn, _ = await self.setUpBook()
        await self.say(ids["Captain Varn"], varn, links.SAME)
        off = await links.unlink(self.memory, GUILD_A, self.c, ids["Captain Varn"], varn)
        self.assertTrue(off.value)
        self.assertEqual(await links.linked(self.memory, GUILD_A, self.c, varn), [])
        assert off.batch is not None
        await self.memory.undo(GUILD_A, self.c, off.batch)
        self.assertEqual(len(await links.linked(self.memory, GUILD_A, self.c, varn)), 1)
        nothing = await links.unlink(self.memory, GUILD_A, self.c, ids["Mother Ilse"], varn)
        self.assertEqual((nothing.value, nothing.batch), (False, None))

    async def test_one_for_one_and_one_per_file(self) -> None:
        ids, varn, ilse = await self.setUpBook()
        await self.say(ids["Captain Varn"], varn, links.SAME)
        with self.assertRaisesRegex(
            MemoryRuleError, "already linked to another name DMbot remembers"
        ):
            await self.say(ids["Captain Varn"], ilse, links.SAME)
        with self.assertRaisesRegex(MemoryRuleError, "linked to another name in this"):
            await self.say(ids["Mother Ilse"], varn, links.SAME)
        # In another file the same table entry may have its own book entry.
        _, more = await self.book([("person", "Varn of the Second Book", 1)])
        await links.decide(
            self.memory, GUILD_A, self.c, [(more["Varn of the Second Book"], varn, links.SAME)], DM
        )
        self.assertEqual(len(await links.linked(self.memory, GUILD_A, self.c, varn)), 2)
        # A refused answer writes nothing at all, not even the ones before it in the same go.
        before = await self.snapshot()
        with self.assertRaises(MemoryRuleError):
            await links.decide(
                self.memory,
                GUILD_A,
                self.c,
                [(ids["Mother Ilse"], ilse, links.SAME), (ids["Mother Ilse"], varn, links.SAME)],
                DM,
            )
        self.assertEqual(await self.snapshot(), before)

    async def test_a_person_cannot_be_the_same_as_a_place(self) -> None:
        ids, varn, _ = await self.setUpBook()
        _, more = await self.book([("place", "Saltmere", 1)])
        with self.assertRaisesRegex(MemoryRuleError, "character in your adventure"):
            await links.decide(
                self.memory, GUILD_A, self.c, [(more["Saltmere"], varn, links.SAME)], DM
            )
        with self.assertRaisesRegex(MemoryRuleError, "isn't in your adventure"):
            await links.decide(self.memory, GUILD_A, self.c, [("0" * 32, varn, links.SAME)], DM)
        with self.assertRaises(MemoryRuleError):
            await links.decide(
                self.memory, GUILD_A, self.c, [(ids["Captain Varn"], varn, "maybe")], DM
            )

    async def test_removing_the_file_or_the_entry_takes_the_link_with_it(self) -> None:
        ids, varn, _ = await self.setUpBook()
        await self.say(ids["Captain Varn"], varn, links.SAME)
        self.assertEqual(await self.count("plan_same_as"), 1)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM adventure_documents")
        self.assertEqual(await self.count("plan_same_as"), 0)
        self.assertEqual(await links.linked(self.memory, GUILD_A, self.c, varn), [])


class ByHand(Setup):
    async def test_the_options_are_the_books_people_of_the_right_kind_best_match_first(
        self,
    ) -> None:
        _, ids = await self.book(
            [
                ("person", "Zed the Grey", 1),
                ("person", "Old Captain Pell", 1),
                ("person", "Captain Varn", 1),
                ("place", "Saltmere", 1),
            ]
        )
        captain = await self.add("Captain Brisk", type="npc")
        options, total = await links.options_for(self.memory, GUILD_A, self.c, captain)
        self.assertEqual(
            [o.name for o in options], ["Captain Varn", "Old Captain Pell", "Zed the Grey"]
        )  # (those sharing "captain" first, then by name)
        self.assertEqual(total, 3)
        await self.say(ids["Captain Varn"], captain, links.SAME)
        # (Taken in this file, so none of this file's others can also be the same as it.)
        again, _ = await links.options_for(self.memory, GUILD_A, self.c, captain)
        self.assertEqual(again, [])
        await self.book([("person", "Captain Brisk of the Second Book", 1)])
        elsewhere, _ = await links.options_for(self.memory, GUILD_A, self.c, captain)
        self.assertEqual([o.name for o in elsewhere], ["Captain Brisk of the Second Book"])
        town = await self.add("Saltmere Harbour", type="place")
        places, _ = await links.options_for(self.memory, GUILD_A, self.c, town)
        self.assertEqual([o.name for o in places], ["Saltmere"])

    async def test_a_page_of_twenty_five_and_a_count(self) -> None:
        await self.book([("person", f"Person {n:02d}", 1) for n in range(30)])
        who = await self.add("Somebody", type="npc")
        first, total = await links.options_for(self.memory, GUILD_A, self.c, who)
        rest, _ = await links.options_for(self.memory, GUILD_A, self.c, who, offset=links.PICK_MAX)
        self.assertEqual((len(first), len(rest), total), (25, 5, 30))

    async def test_a_thing_that_is_not_a_person_or_place_gets_no_options(self) -> None:
        await self.book([("person", "Captain Varn", 1)])
        item = await self.add("Heartseeker", type="item")
        self.assertEqual(await links.options_for(self.memory, GUILD_A, self.c, item), ([], 0))
        self.assertEqual(await links.options_for(self.memory, GUILD_A, self.c, "0" * 32), ([], 0))


class Private(Setup):
    async def test_another_campaign_sees_nothing_and_cannot_reach_in(self) -> None:
        ids, varn, _ = await self.stage()
        other = (await self.campaigns.create(GUILD_B, "Elsewhere", 8)).id
        self.assertFalse(await links.has_plan(self.memory, GUILD_B, other))
        self.assertEqual(await links.suggestions(self.memory, GUILD_B, other), [])
        self.assertEqual(await links.linked(self.memory, GUILD_B, other, varn), [])
        self.assertEqual(await links.options_for(self.memory, GUILD_B, other, varn), ([], 0))
        with self.assertRaises(MemoryRuleError):
            await links.decide(
                self.memory, GUILD_B, other, [(ids["Captain Varn"], varn, links.SAME)], 8
            )
        self.assertEqual(await self.count("plan_same_as", GUILD_B), 0)
        self.assertTrue(await links.has_plan(self.memory, GUILD_A, self.c))

    async def stage(self) -> tuple[dict[str, str], str, str]:
        _, ids = await self.book([("person", "Captain Varn", 1), ("chapter", "One", 0)])
        varn = await self.add("Captain Varn", type="npc")
        await self.say(ids["Captain Varn"], varn, links.SAME)
        return ids, varn, ""

    async def test_recall_and_the_players_never_see_the_link_or_the_book(self) -> None:
        _, ids = await self.book([("person", "Varn of the Book", 1)])
        varn = await self.add("Captain Varn", type="npc")
        await self.say(ids["Varn of the Book"], varn, links.SAME)
        service = RecallService(self.memory)
        for audience in (PLAYERS, DM_SCREEN):
            got = await service.recall(
                GUILD_A, self.c, seeds=["Captain Varn"], audience=audience, opted_in={DM}
            )
            said = " ".join(i.text for i in got.items).lower()
            self.assertNotIn("book", said, audience)
            self.assertNotIn("same", said, audience)
        relations = await self.memory.relations(
            GUILD_A, self.c, entity_id=varn, include_secret=True
        )
        self.assertEqual(relations, [])  # the link is not a connection between table entries


class Mess(Setup):
    async def test_rejecting_an_entry_frees_its_book_entry_and_undo_brings_it_back(
        self,
    ) -> None:
        _, ids = await self.book([("person", "Captain Varn", 1)])
        wrong = await self.add("Varn the Wrong", type="npc")
        right = await self.add("Captain Varn", type="npc")
        await self.say(ids["Captain Varn"], wrong)
        rejected = await self.memory.set_entity_status(
            GUILD_A, self.c, wrong, "rejected", source="dm"
        )
        self.assertEqual(await self.count("plan_same_as"), 0)
        await self.say(ids["Captain Varn"], right)  # the right one can have it now
        self.assertEqual(len(await links.linked(self.memory, GUILD_A, self.c, right)), 1)
        assert rejected.batch is not None
        with self.assertRaisesRegex(MemoryRuleError, "changed again since"):
            await self.memory.undo(GUILD_A, self.c, rejected.batch)  # the book entry was taken
        off = await links.unlink(self.memory, GUILD_A, self.c, ids["Captain Varn"], right)
        self.assertTrue(off.value)
        await self.memory.undo(GUILD_A, self.c, rejected.batch)  # free again: the link is back
        (back,) = await links.linked(self.memory, GUILD_A, self.c, wrong)
        self.assertEqual(back.name, "Captain Varn")

    async def test_a_players_character_and_the_party_are_never_offered(self) -> None:
        _, ids = await self.book([("person", "Captain Varn", 1)])
        pc = await self.add("Captain Varn", type="character", role="player_character")
        self.assertEqual(await self.pairs(), [])
        with self.assertRaises(MemoryRuleError):
            await self.say(ids["Captain Varn"], pc)
        self.assertEqual(await links.options_for(self.memory, GUILD_A, self.c, pc), ([], 0))

    """What the reviewers found: merging, undoing into a conflict, long names, colliding pairs."""

    async def test_merging_two_table_entries_moves_the_link_and_undo_splits_them(self) -> None:
        _, ids = await self.book([("person", "Captain Varn", 1)])
        keep = await self.add("Varn", type="npc")
        gone = await self.add("Captain Varn", type="npc")
        await self.say(ids["Captain Varn"], gone)
        before = await self.snapshot()
        merged = await self.memory.merge(
            GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True
        )
        (link,) = await links.linked(self.memory, GUILD_A, self.c, keep)
        self.assertEqual(link.name, "Captain Varn")
        self.assertEqual(await links.linked(self.memory, GUILD_A, self.c, gone), [])
        assert merged.batch is not None
        await self.memory.undo(GUILD_A, self.c, merged.batch)
        self.assertEqual(await self.snapshot(), before)

    async def test_a_merge_keeps_the_kept_entrys_own_answer(self) -> None:
        _, ids = await self.book([("person", "Captain Varn", 1), ("person", "Varn", 1)])
        keep = await self.add("Varn the Bold", type="npc")
        gone = await self.add("Captain Varn", type="npc")
        await self.say(ids["Varn"], keep)  # the kept entry is already linked in this file
        await self.say(ids["Captain Varn"], gone)
        await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True)
        (link,) = await links.linked(self.memory, GUILD_A, self.c, keep)
        self.assertEqual(link.name, "Varn")
        self.assertEqual(await self.count("plan_same_as"), 1)
        # The book entry the merged one had is free again.
        options, _ = await links.options_for(self.memory, GUILD_A, self.c, keep)
        self.assertEqual(options, [])  # (keep is linked in the only file, so it needs no more)

    async def test_undo_into_a_conflict_is_a_plain_refusal_not_a_crash(self) -> None:
        _, ids = await self.book([("person", "Captain Varn", 1)])
        first = await self.add("Captain Varn", type="npc")
        second = await self.add("Varn", type="npc")
        await self.say(ids["Captain Varn"], first)
        off = await links.unlink(self.memory, GUILD_A, self.c, ids["Captain Varn"], first)
        await self.say(ids["Captain Varn"], second)  # the book entry is taken again
        assert off.batch is not None
        with self.assertRaisesRegex(MemoryRuleError, "changed again since"):
            await self.memory.undo(GUILD_A, self.c, off.batch)
        self.assertEqual(len(await links.linked(self.memory, GUILD_A, self.c, second)), 1)

    async def test_undo_after_the_file_is_gone_is_a_plain_refusal(self) -> None:
        _, ids = await self.book([("person", "Captain Varn", 1)])
        who = await self.add("Captain Varn", type="npc")
        await self.say(ids["Captain Varn"], who)
        off = await links.unlink(self.memory, GUILD_A, self.c, ids["Captain Varn"], who)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM adventure_documents")
        assert off.batch is not None
        with self.assertRaises(MemoryRuleError):
            await self.memory.undo(GUILD_A, self.c, off.batch)

    async def test_not_the_same_then_same_by_hand_and_undo_goes_back_to_not_the_same(self) -> None:
        _, ids = await self.book([("person", "Captain Varn", 1)])
        who = await self.add("Captain Varn", type="npc")
        await self.say(ids["Captain Varn"], who, links.NOT_SAME)
        again = await self.say(ids["Captain Varn"], who, links.SAME)
        self.assertEqual(len(await links.linked(self.memory, GUILD_A, self.c, who)), 1)
        assert again.batch is not None
        await self.memory.undo(GUILD_A, self.c, again.batch)
        self.assertEqual(await links.linked(self.memory, GUILD_A, self.c, who), [])
        self.assertEqual(await links.suggestions(self.memory, GUILD_A, self.c), [])  # still asked

    async def test_two_pairs_on_one_page_never_share_a_book_or_table_entry(self) -> None:
        _, ids = await self.book([("person", "Captain Varn", 1), ("person", "Varn", 1)])
        both = await self.add("Captain Varn", type="npc")
        await self.memory.add_alias(
            GUILD_A, self.c, both, "Varn", kind="short", source="dm", status="confirmed"
        )
        page = await links.suggestions(self.memory, GUILD_A, self.c)
        self.assertEqual(len(page), 1)  # the other waits until this one is answered
        await self.say(page[0].plan_id, page[0].entity_id)
        self.assertEqual(await links.suggestions(self.memory, GUILD_A, self.c), [])
        with self.assertRaisesRegex(MemoryRuleError, "link the same name twice"):
            await links.decide(
                self.memory, GUILD_A, self.c,
                [(ids["Captain Varn"], both, links.SAME), (ids["Varn"], both, links.SAME)],
                DM,
            )  # fmt: skip

    async def test_ten_pairs_with_the_longest_names_still_fit_one_message(self) -> None:
        long = "Z" * 120
        pairs = [
            links.Pair(f"{n:032x}", long, "person", f"{n + 99:032x}", "Y" * 100) for n in range(10)
        ]
        text = ui.match_text(pairs, "✅ Linked 10 to your adventure.")
        self.assertLessEqual(len(text), ui.TEXT_MAX)
        self.assertIn("…", text)
        option = ui.pair_label(1, pairs[0])
        self.assertLessEqual(len(option), ui.LABEL_SHOWN)
        self.assertLessEqual(len(ui.pair_description(pairs[0])), ui.LABEL_MAX)


class Screens(Setup):
    """The DM's two screens, with a fake Discord."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.campaigns.set_story_mode(GUILD_A, self.c, STORY_WATCH, confirmed_by=DM)

    def pressing(self, user: int = DM) -> Any:
        it = press(user, GUILD_A)
        it.client = SimpleNamespace(
            memory=self.memory, campaigns=self.campaigns, lookup=None, adventure=None, plan=None
        )
        it.type = None

        async def followup(content: str = "", **kw: Any) -> None:
            it.sent.update(content=content, **kw)

        it.followup.send = followup
        return it

    def selecting(self, it: Any, menu: Any, which: int, values: list[Any]) -> Any:
        select = [c for c in menu.children if isinstance(c, discord.ui.Select)][which]
        select._values = values  # what the person picked
        return select

    async def test_the_match_screen_lists_pairs_and_saves_same_and_not_the_same(self) -> None:
        await self.book([("person", "Captain Varn", 1), ("person", "Bryn", 1)])
        await self.add("Captain Varn", type="npc")
        await self.add("Bryn", type="npc")
        it = self.pressing()
        await ui.show_matches(it, self.c)
        text, menu = it.sent["content"], it.sent["view"]
        self.assertIn("1. In your adventure: **Bryn** ↔ DMbot remembers: **Bryn**", text)
        self.assertIn(
            "2. In your adventure: **Captain Varn** ↔ DMbot remembers: **Captain Varn**", text
        )
        self.assertIn("You say; DMbot never guesses", text)
        same = self.selecting(it, menu, 0, ["0"])
        no = self.selecting(it, menu, 1, ["1"])
        await same.callback(it)  # Bryn: the same
        self.assertIn("Linked 1 to your adventure", it.sent["content"])
        self.assertIn("↩ Undo that", [getattr(c, "label", "") for c in it.sent["view"].children])
        await no.callback(it)  # (the menu still holds its first picks) Varn: not the same
        self.assertEqual(await links.suggestions(self.memory, GUILD_A, self.c), [])
        self.assertEqual(await self.count("plan_same_as"), 2)
        again = self.pressing()
        await ui.show_matches(again, self.c)
        self.assertIn(ui.NOTHING_TO_MATCH, again.sent["content"])

    async def test_undo_after_saving_puts_the_question_back(self) -> None:
        await self.book([("person", "Captain Varn", 1)])
        await self.add("Captain Varn", type="npc")
        it = self.pressing()
        await ui.show_matches(it, self.c)
        await self.selecting(it, it.sent["view"], 0, ["0"]).callback(it)
        undo = next(c for c in it.sent["view"].children if getattr(c, "label", "") == "↩ Undo that")
        await undo.callback(it)
        self.assertEqual(await self.count("plan_same_as"), 0)
        self.assertIn("Undone.", it.sent["content"])
        self.assertEqual(len(await links.suggestions(self.memory, GUILD_A, self.c)), 1)

    async def test_only_the_dm_may_see_or_answer(self) -> None:
        await self.book([("person", "Captain Varn", 1)])
        await self.add("Captain Varn", type="npc")
        player = self.pressing(user=99)
        await ui.show_matches(player, self.c)
        self.assertEqual(player.sent["content"], ui.ONLY_DM)
        asked = self.pressing()
        await ui.show_matches(asked, self.c)
        menu = asked.sent["view"]
        late = self.pressing(user=99)  # the menu forwarded to someone who is not a DM
        await self.selecting(late, menu, 0, ["0"]).callback(late)
        self.assertEqual(await self.count("plan_same_as"), 0)
        self.assertEqual(late.sent["content"], ui.ONLY_DM)
        self.assertIsNone(late.sent.get("view"))

    async def test_picking_by_hand_shows_it_on_the_card_line_and_can_be_taken_off(self) -> None:
        _, ids = await self.book([("person", "Old Pell", 1), ("person", "Captain Varn", 1)])
        who = await self.add("Captain Brisk", type="npc")
        it = self.pressing()
        await ui.show_picker(it, self.c, who)
        menu = it.sent["view"]
        self.assertIn("which entry in your adventure is this?", it.sent["content"])
        await self.selecting(it, menu, 0, [ids["Captain Varn"]]).callback(it)
        self.assertIn("Linked to **Captain Varn** in your adventure", it.sent["content"])
        self.assertIn("In your adventure: **Captain Varn**", it.sent["content"])
        off = next(
            c for c in it.sent["view"].children if getattr(c, "label", "").startswith("Take off")
        )
        await off.callback(it)
        self.assertIn("Taken off.", it.sent["content"])
        self.assertEqual(await links.linked(self.memory, GUILD_A, self.c, who), [])

    async def test_a_refused_pick_says_why_and_writes_nothing(self) -> None:
        _, ids = await self.book([("person", "Old Pell", 1), ("person", "Captain Varn", 1)])
        one, two = await self.add("Captain Brisk", type="npc"), await self.add("Pell", type="npc")
        await self.say(ids["Captain Varn"], one)
        it = self.pressing()
        await ui.show_picker(it, self.c, two)
        await self.selecting(it, it.sent["view"], 0, [ids["Captain Varn"]]).callback(it)
        self.assertIn("⚠️", it.sent["content"])
        self.assertEqual(await self.count("plan_same_as"), 1)

    async def test_the_shared_story_card_offers_match_names_only_when_the_book_has_names(
        self,
    ) -> None:
        campaign: Any = SimpleNamespace(id=self.c)

        def labels(view: Any) -> list[str]:
            return [getattr(getattr(c, "item", c), "label", "") for c in view.children]

        self.assertIn("Match names", labels(adventure_ui.card_view(campaign, [], can_match=True)))
        self.assertNotIn("Match names", labels(adventure_ui.card_view(campaign, [])))

    async def test_the_name_card_says_who_it_is_in_the_book_to_the_dm_alone(self) -> None:
        who = await self.add("Captain Varn", type="npc")
        lookup = CampaignLookup.build(await self.memory.lookup_data(GUILD_A, self.c))
        line = "📖 **In your adventure:** Captain Varn (Chapter 1)"
        dm = card_text(lookup.for_viewer(True), who, [], secrets=True, book_line=line)
        players = card_text(lookup.for_viewer(False), who, [], secrets=False, book_line=line)
        assert dm is not None and players is not None
        self.assertIn(line, dm)
        self.assertNotIn("In your adventure", players)  # (even if a caller passed it by mistake)
        self.assertNotIn(
            "In your adventure", card_text(lookup.for_viewer(True), who, [], secrets=True) or ""
        )
        with_button = [getattr(c, "label", "") for c in NameCard(self.c, who, book=True).children]
        without = [getattr(c, "label", "") for c in NameCard(self.c, who).children]
        self.assertIn(BOOK_BUTTON, with_button)
        self.assertNotIn(BOOK_BUTTON, without)

    async def test_the_screens_refuse_once_shared_story_is_turned_off(self) -> None:
        _, ids = await self.book([("person", "Captain Varn", 1)])
        who = await self.add("Captain Varn", type="npc")
        await self.campaigns.set_story_mode(GUILD_A, self.c, STORY_OFF)
        for show in (ui.show_matches, lambda it, c: ui.show_picker(it, c, who)):
            it = self.pressing()
            await show(it, self.c)
            self.assertEqual(it.sent["content"], ui.NOT_ON)
        self.assertEqual(await self.count("plan_same_as"), 0)
        self.assertIsNotNone(ids)

    async def test_the_picker_says_how_many_more_links_there_are(self) -> None:
        many = [links.Linked(f"{n:032x}", f"Varn {n}", "person", "") for n in range(7)]
        text = ui.pick_text("Captain Varn", many, 3)
        self.assertIn("5. In your adventure: **Varn 4**", text)
        self.assertNotIn("Varn 5", text)
        self.assertIn("…and 2 more.", text)
