"""The campaign's own homebrew on the rules cards (#1524): the names are noticed like the book's
(whole name, longest first, everyday words only with a lead-in), the card goes to the DM screen
only, hidden or not, with its tag and source, after any house rule and before the book's card; a
homebrew name shadows the book's; one card per entry per session; and nothing from another
campaign."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock

from dmbot.rules.homebrew import Homebrew
from dmbot.rules.homebrew_spotter import HomebrewSpotter
from dmbot.rules.house import HouseRule
from tests.test_rules_cards import C1, DM, NOW, SCREEN, SPEAKER, TableTest


def brew(name: str, text: str = "Mine.", *, kind: str = "monster", known: bool = False,
         cid: str = C1, summary: str | None = None) -> Homebrew:  # fmt: skip
    return Homebrew(1, cid, kind, name, text, summary, known, DM, NOW, NOW)


class FakeBrew:
    """Like `HomebrewStore`: this campaign's entries and no other's."""

    def __init__(self) -> None:
        self.entries: dict[str, list[Homebrew]] = {}
        self.asked: list[str] = []

    async def list(self, guild_id: int, cid: str, *, usable_only: bool = False) -> list[Homebrew]:
        self.asked.append(cid)
        return [e for e in self.entries.get(cid, []) if e.enabled or not usable_only]

    async def find(
        self, guild_id: int, cid: str, name: str, *, usable_only: bool = False
    ) -> Homebrew | None:
        self.asked.append(cid)
        key = " ".join(name.casefold().split())
        return next(
            (
                e
                for e in self.entries.get(cid, [])
                if (e.enabled or not usable_only) and " ".join(e.name.casefold().split()) == key
            ),
            None,
        )


def names(spotter: HomebrewSpotter, line: str) -> list[str]:
    return [m.name for m in spotter.find(line)]


class TheSpotter(unittest.TestCase):
    def test_whole_names_are_found_longest_first_in_order(self) -> None:
        spotter = HomebrewSpotter.from_entries(
            [brew("Hollow"), brew("Hollow King"), brew("Pale Queen")]
        )
        self.assertEqual(
            names(spotter, "the hollow king bows to the Pale Queen"), ["Hollow King", "Pale Queen"]
        )
        self.assertEqual(names(spotter, "a hollowed log"), [])  # never inside another word

    def test_a_plural_is_the_same_name_and_an_entry_counts_once_a_line(self) -> None:
        spotter = HomebrewSpotter.from_entries([brew("Ash Wraith")])
        self.assertEqual(names(spotter, "two ash wraiths and an Ash Wraith"), ["Ash Wraith"])

    def test_the_words_around_the_name_are_kept_for_the_card(self) -> None:
        spotter = HomebrewSpotter.from_entries([brew("Hollow King")])
        (found,) = spotter.find("I think the Hollow King is behind this")
        self.assertEqual(found.said, "Hollow King")
        self.assertIn("think the Hollow King is behind", found.heard)

    def test_an_everyday_word_needs_its_lead_in_and_a_creature_never_counts(self) -> None:
        spotter = HomebrewSpotter.from_entries(
            [brew("Light", kind="spell"), brew("Prone", kind="condition"), brew("Wolf")]
        )
        self.assertEqual(names(spotter, "the light is dim"), [])
        self.assertEqual(names(spotter, "I cast light on it"), ["Light"])
        self.assertEqual(names(spotter, "he is prone"), ["Prone"])
        self.assertEqual(names(spotter, "a wolf howls"), [])

    def test_very_short_names_are_not_looked_for(self) -> None:
        self.assertEqual(HomebrewSpotter.from_entries([brew("Ox")]).by_first, {})

    def test_an_empty_campaign_finds_nothing(self) -> None:
        self.assertEqual(HomebrewSpotter.from_entries([]).find("Fireball and Hollow King"), [])


class Cards(TableTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.brews = FakeBrew()
        self.bot.homebrew = self.brews  # type: ignore[assignment]
        self.table.campaign_name = "Frostmaiden"

    def own(self, *entries: Homebrew) -> None:
        self.brews.entries[C1] = list(entries)
        self.table.homebrew_spotter = HomebrewSpotter.from_entries(entries)

    async def test_a_homebrew_name_puts_a_tagged_card_on_the_dm_screen_only(self) -> None:
        self.own(brew("Hollow King", "A crowned skeleton.", summary="Crowned."))
        await self.say("the Hollow King rises")
        (post,) = self.posts
        self.assertEqual(post[0], SCREEN)
        text = post[1]
        self.assertIn("📚 **Hollow King** (Monster) [Homebrew]", text)
        self.assertNotIn("hidden", text)  # (nothing is hidden from players any more)
        self.assertIn("Crowned.", text)
        self.assertIn("Source: Frostmaiden's homebrew: Hollow King", text)
        self.assertIn("Heard: “the Hollow King rises”", text)
        self.assertLessEqual(len(text.splitlines()), 6)
        labels = [str(b.item.label) for b in post[2].children]
        self.assertEqual(labels, ["Got it", "Ignore", "Override", "Read it all"])

    async def test_a_house_rule_comes_first_then_the_homebrew(self) -> None:
        self.own(brew("Hollow King"))
        self.store.rules[C1] = [
            HouseRule(3, C1, "The Hollow King is immune to fire", None, None, None, DM, NOW, NOW)
        ]
        await self.say("the Hollow King rises")
        text = self.posts[0][1]
        self.assertTrue(text.startswith("🏠 **House rule 3:**"))
        self.assertLess(text.index("House rule 3"), text.index("[Homebrew]"))

    async def test_a_homebrew_name_shadows_the_books_card(self) -> None:
        self.own(brew("Fireball", "Mine: it freezes.", kind="spell"))
        await self.say("I cast Fireball")
        (post,) = self.posts  # one card, not two
        self.assertIn("[Homebrew]", post[1])
        self.assertNotIn("SRD", post[1])

    async def test_once_a_session_and_the_book_still_gets_its_own_names(self) -> None:
        self.own(brew("Hollow King"))
        await self.say("the Hollow King rises")
        self.rewind()
        await self.say("the Hollow King again")
        self.assertEqual(len(self.posts), 1)
        self.rewind()
        await self.say("I cast Hold Person")
        self.assertEqual(len(self.posts), 2)
        self.assertIn("Hold Person", self.posts[1][1])

    async def test_nothing_when_the_cards_are_off_or_the_session_is_over(self) -> None:
        self.own(brew("Hollow King"))
        self.table.rules_on = False
        await self.say("the Hollow King rises")
        self.table.rules_on = True
        self.bot.tables.clear()
        await self.say("the Hollow King rises")
        self.assertEqual(self.posts, [])

    async def test_an_entry_removed_meanwhile_gives_back_its_name_and_minute(self) -> None:
        self.own(brew("Hollow King"))
        self.brews.entries[C1] = []  # removed after the names were read
        await self.say("the Hollow King rises")
        self.assertEqual(self.posts, [])
        self.assertIsNone(self.table.rules.last_at)

    async def test_a_speaker_who_stopped_being_recorded_meanwhile_shows_nothing(self) -> None:
        self.own(brew("Hollow King"))
        real = self.brews.find

        async def stop_then_find(
            guild_id: int, cid: str, name: str, *, usable_only: bool = False
        ) -> Homebrew | None:
            self.consented.discard(SPEAKER)
            return await real(guild_id, cid, name, usable_only=usable_only)

        self.brews.find = stop_then_find  # type: ignore[method-assign]
        await self.say("the Hollow King rises")
        self.assertEqual(self.posts, [])

    async def test_a_store_that_cannot_be_read_costs_the_card_not_the_line(self) -> None:
        self.own(brew("Hollow King"))
        self.brews.find = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        await self.say("the Hollow King rises")
        self.assertEqual(self.posts, [])


class Books(TableTest):
    """A shared book's entries rank in their edition's place on the rules cards (#1569)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.brews = FakeBrew()
        self.bot.homebrew = self.brews  # type: ignore[assignment]
        self.table.campaign_name = "Frostmaiden"

    def own(self, *entries: Homebrew) -> None:
        self.brews.entries[C1] = list(entries)
        self.table.homebrew_spotter = HomebrewSpotter.from_entries(entries)

    def book(self, name: str, edition: str | None, **kw: Any) -> Homebrew:
        import dataclasses

        return dataclasses.replace(
            brew(name, "From the book.", **kw), from_book=True, book_edition=edition
        )

    async def test_a_2024_book_entry_gives_the_card_and_the_free_rules_step_aside(self) -> None:
        self.own(self.book("Fireball", "2024", kind="spell"))
        await self.say("I cast Fireball")
        (post,) = self.posts
        self.assertIn("[Shared book]", post[1])
        self.assertNotIn("Legacy", post[1])
        self.assertNotIn("SRD", post[1])

    async def gives_way(self, edition: str | None) -> None:
        self.own(self.book("Fireball", edition, kind="spell"))
        await self.say("I cast Fireball")
        (post,) = self.posts
        self.assertNotIn("[Shared book]", post[1])
        self.assertIn("SRD", post[1])

    async def test_a_2014_book_entry_gives_way_to_the_newer_free_rules(self) -> None:
        await self.gives_way("2014")

    async def test_an_add_on_book_entry_gives_way_to_the_newer_free_rules(self) -> None:
        await self.gives_way("other")

    async def test_an_entry_loaded_before_the_question_gives_way_too(self) -> None:
        await self.gives_way(None)

    async def test_a_hidden_book_entry_that_gives_way_leaves_only_the_free_rules_card(self) -> None:
        self.own(self.book("Fireball", "2014", kind="spell", known=False))
        await self.say("I cast Fireball")
        (post,) = self.posts
        self.assertNotIn("hidden from players", post[1])
        self.assertNotIn("From the book", post[1])

    async def test_a_2014_book_entry_is_tagged_legacy_when_it_answers(self) -> None:
        self.own(self.book("Orc", "2014"))
        await self.say("an Orc attacks")
        (post,) = self.posts
        self.assertIn("[Shared book] [Legacy 2014]", post[1])


class Reading(TableTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.brews = FakeBrew()
        self.bot.homebrew = self.brews  # type: ignore[assignment]

    async def settle(self) -> None:
        for task in list(asyncio.all_tasks() - {asyncio.current_task()}):
            if task.get_name() == "homebrew-spotter":
                await task

    async def test_only_this_campaigns_names_are_read_and_a_change_reads_them_again(self) -> None:
        self.brews.entries[C1] = [brew("Hollow King")]
        self.brews.entries["other"] = [brew("Strahd's Secret", cid="other")]
        await self.bot._load_homebrew_spotter(self.table)
        assert self.table.homebrew_spotter is not None
        self.assertEqual(self.brews.asked, [C1])
        self.assertEqual(
            names(self.table.homebrew_spotter, "Strahd's Secret, Hollow King"), ["Hollow King"]
        )
        self.brews.entries[C1] = [brew("Hollow King"), brew("Pale Queen")]
        self.bot.homebrew_changed(GUILD_ID, C1)
        await self.settle()
        assert self.table.homebrew_spotter is not None
        self.assertEqual(names(self.table.homebrew_spotter, "Pale Queen"), ["Pale Queen"])

    async def test_a_change_in_another_campaign_or_with_the_cards_off_reads_nothing(self) -> None:
        self.bot.homebrew_changed(GUILD_ID, "other")
        self.table.rules_on = False
        self.bot.homebrew_changed(GUILD_ID, C1)
        await self.settle()
        self.assertEqual(self.brews.asked, [])

    async def test_a_store_that_fails_leaves_the_book_cards_working(self) -> None:
        self.brews.list = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        await self.bot._load_homebrew_spotter(self.table)
        self.assertIsNone(self.table.homebrew_spotter)
        await self.say("I cast Fireball")
        self.assertIn("📖 **Fireball**", self.posts[0][1])

    async def test_no_campaign_homebrew_means_no_spotter(self) -> None:
        await self.bot._load_homebrew_spotter(self.table)
        self.assertIsNone(self.table.homebrew_spotter)


GUILD_ID = 1


class NewestReadWins(TableTest):
    async def test_an_older_read_that_finishes_last_does_not_overwrite_a_newer_one(self) -> None:
        brews = FakeBrew()
        self.bot.homebrew = brews  # type: ignore[assignment]
        gate = asyncio.Event()
        calls = 0
        real = brews.list

        async def slow_then_fast(
            guild_id: int, cid: str, *, usable_only: bool = False
        ) -> list[Homebrew]:
            nonlocal calls
            calls += 1
            if calls == 1:
                await gate.wait()  # the first read is slow...
                return [brew("Old Name")]
            return await real(guild_id, cid, usable_only=usable_only)

        brews.list = slow_then_fast  # type: ignore[method-assign]
        brews.entries[C1] = [brew("New Name")]
        first = asyncio.create_task(self.bot._load_homebrew_spotter(self.table))
        await asyncio.sleep(0)
        await self.bot._load_homebrew_spotter(self.table)  # ...a newer one finishes first
        gate.set()
        await first
        assert self.table.homebrew_spotter is not None
        self.assertEqual(names(self.table.homebrew_spotter, "New Name and Old Name"), ["New Name"])
