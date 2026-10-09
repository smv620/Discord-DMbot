"""House rules in the database (#865): who may change them, that a rule's number is its own
for good, that another DM's change isn't undone by a stale one, that no other campaign or
server ever sees them, and that they go into backups and out with the campaign."""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.bot import DMBot
from dmbot.campaigns import Campaign, CampaignError, CampaignStore
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.rules import house
from dmbot.rules.house import (
    HOUSE_RULES_MAX,
    HouseRuleError,
    HouseRulesSection,
    HouseRuleStore,
)
from dmbot.sessions import SessionStore
from dmbot.ui import house_rules as ui
from tests.pg import DatabaseTest
from tests.test_memory_names import FakeResponse

GUILD_A, GUILD_B = 111, 222
DM, CO_DM, PLAYER = 7, 8, 9


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


class HouseRulesTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.clock = Clock()
        self.campaigns = CampaignStore(self.db, clock=self.clock)
        self.campaigns.register_section(HouseRulesSection())
        self.rules = HouseRuleStore(self.db, clock=self.clock)
        self.campaign = await self.campaigns.create(GUILD_A, "Frostmaiden", DM)

    async def add(self, text: str, *, who: int = DM, campaign: Campaign | None = None) -> int:
        """Add a rule; its number."""
        self.clock.now += 60
        c = campaign or self.campaign
        return (await self.rules.add(c.guild_id, c.id, who, text)).number

    async def texts(self, campaign: Campaign | None = None) -> list[str]:
        c = campaign or self.campaign
        return [r.rule for r in await self.rules.list(c.guild_id, c.id)]

    async def numbers(self, campaign: Campaign | None = None) -> list[int]:
        c = campaign or self.campaign
        return [r.number for r in await self.rules.list(c.guild_id, c.id)]


class Writing(HouseRulesTest):
    async def test_rules_are_listed_newest_first(self) -> None:
        await self.add("Crits double the dice")
        await self.add("Healing potions are a bonus action")
        self.assertEqual(
            await self.texts(), ["Healing potions are a bonus action", "Crits double the dice"]
        )

    async def test_a_rule_keeps_what_it_replaces_and_what_happened(self) -> None:
        self.clock.now += 60
        saved = await self.rules.add(
            GUILD_A,
            self.campaign.id,
            DM,
            "  Falling  deals 2d6  ",
            "Falling deals 1d6 per 10 feet",
            scenario="Brom fell off the tower",
            session_id="s1",
        )
        self.assertEqual(saved.rule, "Falling deals 2d6")  # spacing cleaned
        self.assertEqual(saved.supersedes, "Falling deals 1d6 per 10 feet")
        self.assertEqual((saved.scenario, saved.session_id), ("Brom fell off the tower", "s1"))
        self.assertEqual((saved.created_by, saved.created_at), (DM, int(self.clock.now)))
        self.assertEqual((saved.number, saved.version), (1, 1))
        (back,) = await self.rules.list(GUILD_A, self.campaign.id)
        self.assertEqual(back, saved)

    async def test_the_optional_texts_may_be_left_empty(self) -> None:
        self.clock.now += 60
        saved = await self.rules.add(GUILD_A, self.campaign.id, DM, "Crits double", "   ")
        self.assertIsNone(saved.supersedes)
        self.assertIsNone(saved.scenario)

    async def test_bad_text_is_refused_in_plain_words_naming_the_box(self) -> None:
        for text, words in (("   ", "box was empty"), ("x" * 501, "Keep the rule under 500")):
            with self.assertRaisesRegex(HouseRuleError, words):
                await self.rules.add(GUILD_A, self.campaign.id, DM, text)
        with self.assertRaisesRegex(HouseRuleError, "Keep “Instead of” under 500"):
            await self.rules.add(GUILD_A, self.campaign.id, DM, "ok", "y" * 501)
        with self.assertRaisesRegex(HouseRuleError, "Keep what happened under 500"):
            await self.rules.add(GUILD_A, self.campaign.id, DM, "ok", scenario="y" * 501)
        self.assertEqual(await self.texts(), [])

    async def test_editing_changes_the_words_and_when_and_counts_the_change(self) -> None:
        number = await self.add("Crits double the dice")
        self.clock.now += 3600
        changed = await self.rules.edit(
            GUILD_A, self.campaign.id, DM, number, "Crits max the dice", "Crits double the dice"
        )
        self.assertEqual(changed.rule, "Crits max the dice")
        self.assertEqual(changed.supersedes, "Crits double the dice")
        self.assertEqual(changed.updated_at, int(self.clock.now))
        self.assertLess(changed.created_at, changed.updated_at)
        self.assertEqual((changed.number, changed.version), (number, 2))
        self.assertEqual(await self.texts(), ["Crits max the dice"])
        # Emptying "instead of" clears it.
        again = await self.rules.edit(GUILD_A, self.campaign.id, DM, number, "Crits max", "")
        self.assertIsNone(again.supersedes)
        self.assertEqual(again.version, 3)

    async def test_removing_returns_the_rule_and_it_is_gone(self) -> None:
        number = await self.add("Crits double the dice")
        gone = await self.rules.remove(GUILD_A, self.campaign.id, DM, number)
        self.assertEqual(gone.rule, "Crits double the dice")
        self.assertEqual(await self.texts(), [])
        with self.assertRaisesRegex(HouseRuleError, "isn't here any more"):
            await self.rules.remove(GUILD_A, self.campaign.id, DM, number)
        with self.assertRaisesRegex(HouseRuleError, "isn't here any more"):
            await self.rules.edit(GUILD_A, self.campaign.id, DM, number, "Back again")

    async def test_a_co_dm_may_change_them_too(self) -> None:
        await self.campaigns.add_dm(GUILD_A, self.campaign.id, CO_DM)
        number = await self.add("Crits double the dice", who=CO_DM)
        await self.rules.edit(GUILD_A, self.campaign.id, CO_DM, number, "Crits max the dice")
        self.assertEqual(await self.texts(), ["Crits max the dice"])


class Numbers(HouseRulesTest):
    """A rule's number is its own, for good: alerts cite "house rule 12"."""

    async def test_adding_and_removing_never_renumbers(self) -> None:
        one = await self.add("First")
        two = await self.add("Second")
        three = await self.add("Third")
        self.assertEqual((one, two, three), (1, 2, 3))
        await self.rules.remove(GUILD_A, self.campaign.id, DM, two)
        self.assertEqual(await self.numbers(), [3, 1])  # a hole, not a renumbering
        four = await self.add("Fourth")
        self.assertEqual(four, 4)
        self.assertEqual(await self.texts(), ["Fourth", "Third", "First"])
        self.assertEqual(await self.numbers(), [4, 3, 1])

    async def test_a_removed_number_is_not_used_again_not_even_the_highest(self) -> None:
        await self.add("First")
        two = await self.add("Second")
        await self.rules.remove(GUILD_A, self.campaign.id, DM, two)  # the newest, removed
        self.assertEqual(await self.add("Third"), 3)  # not 2: "house rule 2" stays unused

    async def test_editing_keeps_the_number(self) -> None:
        number = await self.add("Crits double the dice")
        await self.add("Another")
        await self.rules.edit(GUILD_A, self.campaign.id, DM, number, "Changed")
        self.assertEqual(await self.numbers(), [2, 1])
        self.assertEqual((await self.rules.list(GUILD_A, self.campaign.id))[1].rule, "Changed")

    async def test_each_campaign_counts_its_own(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Strahd", DM)
        await self.add("First here")
        await self.add("Second here")
        self.assertEqual(await self.add("First there", campaign=other), 1)
        self.assertEqual(await self.numbers(other), [1])

    async def test_adds_at_the_same_moment_each_get_their_own_number(self) -> None:
        saved = await asyncio.gather(
            *(self.rules.add(GUILD_A, self.campaign.id, DM, f"Rule {i}") for i in range(8))
        )
        self.assertEqual(sorted(r.number for r in saved), list(range(1, 9)))
        self.assertEqual(sorted(await self.numbers()), list(range(1, 9)))


class Changes(HouseRulesTest):
    """Two DMs looking at the same rule: the second to act is told, not obeyed."""

    async def test_edit_from_an_old_view_is_refused(self) -> None:
        await self.campaigns.add_dm(GUILD_A, self.campaign.id, CO_DM)
        number = await self.add("Crits double the dice")
        (seen,) = await self.rules.list(GUILD_A, self.campaign.id)  # both DMs look: version 1
        await self.rules.edit(
            GUILD_A, self.campaign.id, CO_DM, number, "The co-DM's words", unchanged_since=1
        )
        with self.assertRaisesRegex(HouseRuleError, "Another DM changed that house rule"):
            await self.rules.edit(
                GUILD_A,
                self.campaign.id,
                DM,
                number,
                "My words, from the old view",
                unchanged_since=seen.version,
            )
        self.assertEqual(await self.texts(), ["The co-DM's words"])  # theirs stands

    async def test_remove_from_an_old_view_is_refused(self) -> None:
        number = await self.add("Crits double the dice")
        (seen,) = await self.rules.list(GUILD_A, self.campaign.id)
        await self.rules.edit(GUILD_A, self.campaign.id, DM, number, "Crits max")
        with self.assertRaisesRegex(HouseRuleError, "Another DM changed that house rule"):
            await self.rules.remove(
                GUILD_A, self.campaign.id, DM, number, unchanged_since=seen.version
            )
        self.assertEqual(await self.texts(), ["Crits max"])  # nothing was removed
        (now,) = await self.rules.list(GUILD_A, self.campaign.id)
        gone = await self.rules.remove(
            GUILD_A, self.campaign.id, DM, number, unchanged_since=now.version
        )
        self.assertEqual(gone.rule, "Crits max")

    async def test_a_removed_rule_is_gone_not_changed(self) -> None:
        number = await self.add("Crits double the dice")
        (seen,) = await self.rules.list(GUILD_A, self.campaign.id)
        await self.rules.remove(GUILD_A, self.campaign.id, DM, number)
        for call in (
            self.rules.edit(
                GUILD_A, self.campaign.id, DM, number, "Late", unchanged_since=seen.version
            ),
            self.rules.remove(GUILD_A, self.campaign.id, DM, number, unchanged_since=seen.version),
        ):
            with self.assertRaisesRegex(HouseRuleError, "isn't here any more"):
                await call

    async def test_two_changes_in_one_second_are_told_apart(self) -> None:
        # The clock doesn't move: a time stamp couldn't tell these changes apart.
        number = await self.add("One")
        (seen,) = await self.rules.list(GUILD_A, self.campaign.id)
        await self.rules.edit(GUILD_A, self.campaign.id, DM, number, "Two")
        with self.assertRaisesRegex(HouseRuleError, "Another DM changed"):
            await self.rules.edit(
                GUILD_A, self.campaign.id, DM, number, "Three", unchanged_since=seen.version
            )

    async def test_without_unchanged_since_the_last_one_wins(self) -> None:
        number = await self.add("One")
        await self.rules.edit(GUILD_A, self.campaign.id, DM, number, "Two")
        await self.rules.edit(GUILD_A, self.campaign.id, DM, number, "Three")
        self.assertEqual(await self.texts(), ["Three"])


class Limits(HouseRulesTest):
    async def test_a_campaign_holds_a_few_hundred_and_no_more(self) -> None:
        for n in range(HOUSE_RULES_MAX):
            await self.add(f"Rule {n}")
        with self.assertRaisesRegex(HouseRuleError, "Remove one first"):
            await self.add("One too many")
        newest = (await self.rules.list(GUILD_A, self.campaign.id))[0]
        await self.rules.remove(GUILD_A, self.campaign.id, DM, newest.number)
        await self.add("Room again")  # a removal makes room

    async def test_the_limit_is_for_each_campaign(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Strahd", DM)
        for n in range(HOUSE_RULES_MAX):
            await self.add(f"Rule {n}")
        with self.assertRaisesRegex(HouseRuleError, "Remove one first"):
            await self.add("One too many")
        await self.add("Still room here", campaign=other)  # the other campaign is not full
        self.assertEqual(await self.texts(other), ["Still room here"])


class OnlyTheDM(HouseRulesTest):
    async def test_a_player_can_read_but_not_change_anything(self) -> None:
        number = await self.add("Crits double the dice")
        self.assertEqual(len(await self.rules.list(GUILD_A, self.campaign.id)), 1)  # anyone reads
        calls = (
            self.rules.add(GUILD_A, self.campaign.id, PLAYER, "Everyone gets a pony"),
            self.rules.edit(GUILD_A, self.campaign.id, PLAYER, number, "Crits kill"),
            self.rules.remove(GUILD_A, self.campaign.id, PLAYER, number),
        )
        for call in calls:
            with self.assertRaisesRegex(HouseRuleError, "Only this campaign's DM"):
                await call
        self.assertEqual(await self.texts(), ["Crits double the dice"])

    async def test_the_dm_of_another_campaign_cannot(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Strahd", PLAYER)
        number = await self.add("Crits double the dice")
        with self.assertRaisesRegex(HouseRuleError, "Only this campaign's DM"):
            await self.rules.add(GUILD_A, self.campaign.id, PLAYER, "Mine now")
        with self.assertRaisesRegex(HouseRuleError, "Only this campaign's DM"):
            await self.rules.remove(GUILD_A, self.campaign.id, PLAYER, number)
        self.assertEqual(await self.texts(), ["Crits double the dice"])
        await self.add("Their own rule", who=PLAYER, campaign=other)  # in their own campaign
        self.assertEqual(await self.texts(other), ["Their own rule"])

    async def test_a_server_manager_without_being_a_dm_cannot(self) -> None:
        # Managers may run a campaign, but a house rule is the DM's to declare.
        with self.assertRaisesRegex(HouseRuleError, "Only this campaign's DM"):
            await self.rules.add(GUILD_A, self.campaign.id, 10, "Managers decide")


class Isolation(HouseRulesTest):
    async def test_another_campaign_sees_nothing_and_can_change_nothing(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Strahd", DM)
        mine = await self.add("Crits double the dice")
        self.assertEqual(await self.texts(other), [])
        # A rule's number from one campaign never reaches into another.
        with self.assertRaisesRegex(HouseRuleError, "isn't here any more"):
            await self.rules.remove(GUILD_A, other.id, DM, mine)
        with self.assertRaisesRegex(HouseRuleError, "isn't here any more"):
            await self.rules.edit(GUILD_A, other.id, DM, mine, "Sneaky")
        self.assertEqual(await self.texts(), ["Crits double the dice"])

    async def test_another_server_sees_nothing_and_can_change_nothing(self) -> None:
        mine = await self.add("Crits double the dice")
        self.assertEqual(await self.rules.list(GUILD_B, self.campaign.id), [])
        for call in (
            self.rules.add(GUILD_B, self.campaign.id, DM, "Sneaky"),
            self.rules.edit(GUILD_B, self.campaign.id, DM, mine, "Sneaky"),
            self.rules.remove(GUILD_B, self.campaign.id, DM, mine),
        ):
            with self.assertRaisesRegex(HouseRuleError, "campaign isn't here"):
                await call
        self.assertEqual(await self.texts(), ["Crits double the dice"])

    async def test_without_a_server_set_the_database_shows_none(self) -> None:
        await self.add("Crits double the dice")
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM house_rules")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # row-level security, not just the queries' filters

    async def test_deleting_the_campaign_deletes_its_rules(self) -> None:
        await self.add("Crits double the dice")
        await self.campaigns.delete(GUILD_A, self.campaign.id)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM house_rules")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_deleting_one_campaign_leaves_the_servers_other_campaigns_rules(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Strahd", DM)
        await self.add("Frostmaiden's rule")
        await self.add("Strahd's rule", campaign=other)
        await self.campaigns.delete(GUILD_A, self.campaign.id)
        self.assertEqual(await self.texts(other), ["Strahd's rule"])
        self.assertEqual(await self.numbers(other), [1])


def rules_in(backup: dict[str, Any]) -> list[dict[str, Any]]:
    """The rule rows of a backup (its first row may say how many numbers were used)."""
    return [r for r in backup["sections"]["house_rules"] if "rule" in r]


class Backups(HouseRulesTest):
    async def two_campaigns(self) -> Campaign:
        """A second campaign in the same server, each with rules of its own."""
        other = await self.campaigns.create(GUILD_A, "Strahd", DM)
        await self.add("Frostmaiden: crits double the dice")
        await self.add("Frostmaiden: potions are a bonus action")
        await self.add("Strahd: garlic works", campaign=other)
        await self.add("Strahd: no long rests in Barovia", campaign=other)
        return other

    async def test_house_rules_go_into_a_backup_and_come_back(self) -> None:
        await self.rules.add(
            GUILD_A, self.campaign.id, DM, "Falling deals 2d6", "1d6 per 10 feet", session_id="s1"
        )
        await self.add("Crits double the dice")
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        self.assertEqual(backup["sections"]["house_rules"][0], {"made": 2})  # numbers used
        rows = rules_in(backup)
        self.assertEqual([r["rule"] for r in rows], ["Falling deals 2d6", "Crits double the dice"])
        self.assertEqual([r["number"] for r in rows], [1, 2])
        self.assertEqual(rows[0]["instead"], "1d6 per 10 feet")
        self.assertEqual(rows[0]["by"], str(DM))
        self.assertNotIn("session", " ".join(rows[0]))  # no transcript id leaves the server
        self.assertNotIn("version", rows[0])

        restored = await self.campaigns.import_backup(GUILD_B, backup, PLAYER)
        back = await self.rules.list(GUILD_B, restored.id)
        # Same words, same numbers, same order (newest first), in the other server.
        self.assertEqual([r.rule for r in back], ["Crits double the dice", "Falling deals 2d6"])
        self.assertEqual([r.number for r in back], [2, 1])
        self.assertEqual(back[1].supersedes, "1d6 per 10 feet")
        self.assertIsNone(back[1].session_id)
        self.assertEqual(back[1].created_at, rows[0]["created_at"])
        # The original is untouched, and the copy's rules are its own.
        self.assertEqual(len(await self.rules.list(GUILD_A, self.campaign.id)), 2)
        again = await self.campaigns.export(GUILD_B, restored.id)
        self.assertEqual(again["sections"]["house_rules"], backup["sections"]["house_rules"])

    async def test_a_backup_holds_only_its_own_campaigns_rules(self) -> None:
        other = await self.two_campaigns()
        mine = await self.campaigns.export(GUILD_A, self.campaign.id)
        theirs = await self.campaigns.export(GUILD_A, other.id)
        self.assertEqual(
            [r["rule"] for r in rules_in(mine)],
            ["Frostmaiden: crits double the dice", "Frostmaiden: potions are a bonus action"],
        )
        self.assertEqual(
            [r["rule"] for r in rules_in(theirs)],
            ["Strahd: garlic works", "Strahd: no long rests in Barovia"],
        )

    async def test_restoring_over_a_campaign_leaves_the_others_rules(self) -> None:
        other = await self.two_campaigns()
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        await self.add("Frostmaiden: made after the backup")
        await self.campaigns.import_backup(
            GUILD_A, backup, DM, replace_campaign_id=self.campaign.id
        )
        self.assertEqual(
            await self.texts(self.campaign),
            ["Frostmaiden: potions are a bonus action", "Frostmaiden: crits double the dice"],
        )
        self.assertEqual(
            await self.texts(other), ["Strahd: no long rests in Barovia", "Strahd: garlic works"]
        )  # not cleared, not changed

    async def test_restoring_a_copy_changes_nothing_in_the_original_campaigns(self) -> None:
        other = await self.two_campaigns()
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        await self.campaigns.import_backup(GUILD_A, backup, DM)  # a copy, in the same server
        self.assertEqual(len(await self.rules.list(GUILD_A, self.campaign.id)), 2)
        self.assertEqual(len(await self.rules.list(GUILD_A, other.id)), 2)

    async def test_restoring_over_a_campaign_replaces_its_rules(self) -> None:
        await self.add("Crits double the dice")
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        await self.add("A rule made after the backup")
        await self.campaigns.import_backup(
            GUILD_A, backup, DM, replace_campaign_id=self.campaign.id
        )
        (replaced,) = await self.campaigns.list_campaigns(GUILD_A)
        self.assertEqual(await self.texts(replaced), ["Crits double the dice"])

    async def test_a_number_used_before_is_not_used_again_after_a_replace(self) -> None:
        await self.add("One")
        await self.add("Two")
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        await self.add("Three")
        await self.add("Four")  # the campaign has used 1 to 4
        await self.campaigns.import_backup(
            GUILD_A, backup, DM, replace_campaign_id=self.campaign.id
        )
        (replaced,) = await self.campaigns.list_campaigns(GUILD_A)
        self.assertEqual(await self.numbers(replaced), [2, 1])
        self.assertEqual(await self.add("Five", campaign=replaced), 5)  # not 3

    async def test_a_restored_copy_goes_on_after_its_highest_number(self) -> None:
        await self.add("One")
        await self.add("Two")
        three = await self.add("Three")
        await self.rules.remove(GUILD_A, self.campaign.id, DM, three)
        await self.add("Four")  # 4: the highest in the backup
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        restored = await self.campaigns.import_backup(GUILD_B, backup, PLAYER)
        self.assertEqual(await self.numbers(restored), [4, 2, 1])
        self.assertEqual(await self.add("Five", campaign=restored, who=PLAYER), 5)

    async def test_numbers_used_by_removed_rules_are_not_used_again_by_a_copy(self) -> None:
        # Rules 1 to 5 were made; 4 and 5 were removed. The backup holds 1 to 3, yet "house
        # rule 4" and "house rule 5" once meant something: a copy goes on at 6.
        for n in range(5):
            await self.add(f"Rule {n + 1}")
        for number in (5, 4):
            await self.rules.remove(GUILD_A, self.campaign.id, DM, number)
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        self.assertEqual(backup["sections"]["house_rules"][0], {"made": 5})
        restored = await self.campaigns.import_backup(GUILD_B, backup, PLAYER)
        self.assertEqual(await self.numbers(restored), [3, 2, 1])
        self.assertEqual(await self.add("Next", campaign=restored, who=PLAYER), 6)

    async def test_a_campaign_with_all_its_rules_removed_remembers_its_numbers(
        self,
    ) -> None:
        one, two = await self.add("One"), await self.add("Two")
        await self.rules.remove(GUILD_A, self.campaign.id, DM, one)
        await self.rules.remove(GUILD_A, self.campaign.id, DM, two)
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        self.assertEqual(backup["sections"]["house_rules"], [{"made": 2}])
        restored = await self.campaigns.import_backup(GUILD_B, backup, PLAYER)
        self.assertEqual(await self.numbers(restored), [])
        self.assertEqual(await self.add("Three", campaign=restored, who=PLAYER), 3)

    async def test_a_campaign_that_never_had_a_rule_has_nothing_to_remember(self) -> None:
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        self.assertEqual(backup["sections"]["house_rules"], [])

    async def test_a_backup_made_before_the_count_was_kept_goes_on_after_its_highest(self) -> None:
        await self.add("One")
        await self.add("Two")
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        backup["sections"]["house_rules"] = rules_in(backup)  # no count row, as an older file
        restored = await self.campaigns.import_backup(GUILD_B, backup, PLAYER)
        self.assertEqual(await self.add("Three", campaign=restored, who=PLAYER), 3)

    async def test_a_bad_count_of_numbers_used_refuses_the_backup(self) -> None:
        await self.add("One")
        await self.add("Two")
        good = await self.campaigns.export(GUILD_A, self.campaign.id)
        rows = rules_in(good)
        for made in (0, -1, True, "2", 1, 2**31 - 1, 2**31, None, 1.5):
            with self.subTest(made=made):
                backup = copy.deepcopy(good)
                backup["sections"]["house_rules"] = [{"made": made}, *rows]
                with self.assertRaisesRegex(CampaignError, "damaged"):
                    await self.campaigns.import_backup(GUILD_B, backup, PLAYER)
        self.assertEqual(await self.campaigns.list_campaigns(GUILD_B), [])

    async def test_a_backup_from_before_house_rules_restores_with_none(self) -> None:
        await self.add("Crits double the dice")
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        del backup["sections"]["house_rules"]
        restored = await self.campaigns.import_backup(GUILD_B, backup, PLAYER)
        self.assertEqual(await self.texts(restored), [])
        self.assertEqual(await self.add("First", campaign=restored, who=PLAYER), 1)

    async def test_replacing_with_a_backup_from_before_house_rules_clears_them(self) -> None:
        # "Replace" means replace: the copy's rules are the campaign's rules afterwards,
        # and a backup that has none leaves it with none (the same as for its names).
        await self.add("Crits double the dice")
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        del backup["sections"]["house_rules"]
        await self.add("A rule made after the backup")
        await self.campaigns.import_backup(
            GUILD_A, backup, DM, replace_campaign_id=self.campaign.id
        )
        (replaced,) = await self.campaigns.list_campaigns(GUILD_A)
        self.assertEqual(await self.texts(replaced), [])

    async def test_a_damaged_row_refuses_the_whole_backup(self) -> None:
        await self.add("Crits double the dice")
        good = await self.campaigns.export(GUILD_A, self.campaign.id)
        row = rules_in(good)[0]  # number 1; the file also starts with how many were used
        used = {"made": 9}
        # Each variant is a good row (number 2) with one thing wrong, after a good row 1: so
        # only that one thing can be why the file is refused.
        base = {**row, "number": 2}
        damaged: list[Any] = [
            "not a row",
            {k: v for k, v in base.items() if k != "by"},
            {k: v for k, v in base.items() if k != "number"},
            {**base, "extra": 1},
            {**base, "rule": ""},
            {**base, "rule": "x" * 501},
            {**base, "rule": " spaced  out "},  # DMbot always writes it cleaned
            {**base, "rule": 5},
            {**base, "instead": ""},
            {**base, "instead": 5},
            {**base, "scenario": "y" * 501},
            {**base, "by": "0"},
            {**base, "by": "-5"},
            {**base, "by": "abc"},
            {**base, "by": 7},
            {**base, "by": str(2**63)},
            {**base, "created_at": -1},
            {**base, "created_at": True},
            {**base, "created_at": "1"},
            {**base, "updated_at": row["created_at"] - 1},
            {**base, "updated_at": 2**63},
            {**base, "number": 1},  # the same number as the first row
            {**base, "number": 0},
            {**base, "number": -3},
            {**base, "number": True},
            {**base, "number": "2"},
            {**base, "number": 2**31},
        ]
        okay = copy.deepcopy(good)
        okay["sections"]["house_rules"] = [used, row, base]  # the same, with nothing wrong
        restored = await self.campaigns.import_backup(GUILD_B, okay, PLAYER)
        self.assertEqual(await self.numbers(restored), [2, 1])
        for n, bad in enumerate(damaged):
            with self.subTest(n=n, bad=bad):
                backup = copy.deepcopy(good)
                backup["sections"]["house_rules"] = [used, row, bad]
                with self.assertRaises(CampaignError) as caught:
                    await self.campaigns.import_backup(GUILD_B, backup, PLAYER)
                self.assertIn("damaged", str(caught.exception))
        too_many = copy.deepcopy(good)
        too_many["sections"]["house_rules"] = [
            {**row, "number": n + 1} for n in range(HOUSE_RULES_MAX + 1)
        ]
        with self.assertRaisesRegex(CampaignError, "damaged"):
            await self.campaigns.import_backup(GUILD_B, too_many, PLAYER)
        self.assertEqual(len(await self.campaigns.list_campaigns(GUILD_B)), 1)  # only the good one

    async def test_a_backup_asks_nothing_of_a_player_restoring_it(self) -> None:
        # Whoever restores becomes the copy's DM and may change its rules.
        await self.add("Crits double the dice")
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        restored = await self.campaigns.import_backup(GUILD_B, backup, PLAYER)
        await self.rules.add(GUILD_B, restored.id, PLAYER, "Mine now")
        self.assertEqual(len(await self.rules.list(GUILD_B, restored.id)), 2)


class Texts(HouseRulesTest):
    async def test_cleaning(self) -> None:
        self.assertEqual(house.clean_rule("  a \n b\t c "), "a b c")
        self.assertIsNone(house.clean_optional(None, "that"))
        self.assertIsNone(house.clean_optional("  \n ", "that"))
        self.assertEqual(house.clean_optional(" x  y ", "that"), "x y")


class Command(HouseRulesTest):
    """`/dmbot houserules` against the real stores: what a DM and a player each get."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            ConsentStore(self.db),
            self.campaigns,
            SessionStore(self.db),
            house_rules=self.rules,
        )

    def it(self, user_id: int, kind: discord.InteractionType) -> Any:
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        edited: list[tuple[str, Any]] = []

        async def edit_original_response(*, content: str = "", view: Any = None, **_: Any) -> None:
            edited.append((content, view))

        return SimpleNamespace(
            client=self.bot,
            guild=SimpleNamespace(id=GUILD_A),
            guild_id=GUILD_A,
            user=user,
            type=kind,
            response=FakeResponse(),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(side_effect=edit_original_response),
            edited=edited,
        )

    async def test_a_dm_adds_a_rule_and_everyone_reads_it(self) -> None:
        opened = self.it(DM, discord.InteractionType.application_command)
        await ui.dmbot_house_rules.callback(opened)  # type: ignore[call-arg]
        text = opened.followup.send.await_args.args[0]  # it answers Discord first
        self.assertIn("No house rules yet.", text)
        form = ui.AddForm(self.campaign)
        form.rule._value = "A natural 20 doubles the damage dice"
        form.instead._value = "Only attack rolls get extra"
        submitted = self.it(DM, discord.InteractionType.modal_submit)
        await form.on_submit(submitted)
        shown = submitted.edited[0][0]
        self.assertIn("1. A natural 20 doubles the damage dice (instead of: Only attack", shown)
        # A player opens it: the same list, and nothing to press.
        reading = self.it(PLAYER, discord.InteractionType.application_command)
        await ui.dmbot_house_rules.callback(reading)  # type: ignore[call-arg]
        call = reading.followup.send.await_args
        text, kw = call.args[0], call.kwargs
        self.assertIn("1. A natural 20 doubles the damage dice", text)
        self.assertIsNone(kw.get("view"))

    async def test_a_player_with_a_form_in_hand_still_changes_nothing(self) -> None:
        number = await self.add("Crits double the dice")
        form = ui.AddForm(self.campaign)
        form.rule._value = "Everyone gets a pony"
        it = self.it(PLAYER, discord.InteractionType.modal_submit)
        await form.on_submit(it)
        self.assertIn("Only this campaign's DM", it.followup.send.await_args.args[0])
        self.assertIn("Everyone gets a pony", it.followup.send.await_args.args[0])  # words back
        (existing,) = await self.rules.list(GUILD_A, self.campaign.id)
        confirm = ui.ConfirmRemove(self.campaign, existing, 0)
        press = self.it(PLAYER, discord.InteractionType.component)
        yes = next(c for c in confirm.children if getattr(c, "label", "") == ui.YES_REMOVE_LABEL)
        await yes.callback(press)
        self.assertEqual(await self.texts(), ["Crits double the dice"])
        self.assertEqual(existing.number, number)

    async def test_a_stale_edit_changes_nothing_and_gives_the_words_back(self) -> None:
        number = await self.add("Crits double the dice")
        (seen,) = await self.rules.list(GUILD_A, self.campaign.id)
        form = ui.EditForm(self.campaign, seen, 0)
        await self.rules.edit(GUILD_A, self.campaign.id, DM, number, "The other DM's words")
        form.rule._value = "My words from the old view"
        it = self.it(DM, discord.InteractionType.modal_submit)
        await form.on_submit(it)
        told = it.followup.send.await_args.args[0]
        self.assertIn("Another DM changed that house rule", told)
        self.assertIn("My words from the old view", told)
        self.assertEqual(await self.texts(), ["The other DM's words"])
