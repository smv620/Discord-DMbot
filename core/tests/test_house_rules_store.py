"""House rules in the database (#865): who may change them, that no other campaign or
server ever sees them, and that they go into backups and out with the campaign."""

from __future__ import annotations

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
        self.clock.now += 60
        c = campaign or self.campaign
        saved = await self.rules.add(c.guild_id, c.id, who, text)
        return saved.id

    async def texts(self, campaign: Campaign | None = None) -> list[str]:
        c = campaign or self.campaign
        return [r.rule for r in await self.rules.list(c.guild_id, c.id)]


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
        (back,) = await self.rules.list(GUILD_A, self.campaign.id)
        self.assertEqual(back, saved)

    async def test_the_optional_texts_may_be_left_empty(self) -> None:
        self.clock.now += 60
        saved = await self.rules.add(GUILD_A, self.campaign.id, DM, "Crits double", "   ")
        self.assertIsNone(saved.supersedes)
        self.assertIsNone(saved.scenario)

    async def test_bad_text_is_refused_in_plain_words(self) -> None:
        for text, words in (("   ", "box was empty"), ("x" * 501, "under 500")):
            with self.assertRaisesRegex(HouseRuleError, words):
                await self.rules.add(GUILD_A, self.campaign.id, DM, text)
        with self.assertRaisesRegex(HouseRuleError, "under 500"):
            await self.rules.add(GUILD_A, self.campaign.id, DM, "ok", "y" * 501)
        self.assertEqual(await self.texts(), [])

    async def test_editing_changes_the_words_and_when(self) -> None:
        rule_id = await self.add("Crits double the dice")
        self.clock.now += 3600
        changed = await self.rules.edit(
            GUILD_A, self.campaign.id, DM, rule_id, "Crits max the dice", "Crits double the dice"
        )
        self.assertEqual(changed.rule, "Crits max the dice")
        self.assertEqual(changed.supersedes, "Crits double the dice")
        self.assertEqual(changed.updated_at, int(self.clock.now))
        self.assertLess(changed.created_at, changed.updated_at)
        self.assertEqual(await self.texts(), ["Crits max the dice"])
        # Emptying "instead of" clears it.
        again = await self.rules.edit(GUILD_A, self.campaign.id, DM, rule_id, "Crits max", "")
        self.assertIsNone(again.supersedes)

    async def test_removing_returns_the_rule_and_it_is_gone(self) -> None:
        rule_id = await self.add("Crits double the dice")
        gone = await self.rules.remove(GUILD_A, self.campaign.id, DM, rule_id)
        self.assertEqual(gone.rule, "Crits double the dice")
        self.assertEqual(await self.texts(), [])
        with self.assertRaisesRegex(HouseRuleError, "isn't here any more"):
            await self.rules.remove(GUILD_A, self.campaign.id, DM, rule_id)
        with self.assertRaisesRegex(HouseRuleError, "isn't here any more"):
            await self.rules.edit(GUILD_A, self.campaign.id, DM, rule_id, "Back again")

    async def test_remove_only_takes_the_rule_the_dm_was_shown(self) -> None:
        rule_id = await self.add("Crits double the dice")
        (shown,) = await self.rules.list(GUILD_A, self.campaign.id)
        self.clock.now += 60
        await self.rules.edit(GUILD_A, self.campaign.id, DM, rule_id, "Crits max")
        with self.assertRaisesRegex(HouseRuleError, "was changed since you looked"):
            await self.rules.remove(
                GUILD_A, self.campaign.id, DM, rule_id, unchanged_since=shown.updated_at
            )
        self.assertEqual(await self.texts(), ["Crits max"])  # nothing was removed
        (now,) = await self.rules.list(GUILD_A, self.campaign.id)
        gone = await self.rules.remove(
            GUILD_A, self.campaign.id, DM, rule_id, unchanged_since=now.updated_at
        )
        self.assertEqual(gone.rule, "Crits max")
        with self.assertRaisesRegex(HouseRuleError, "isn't here any more"):  # already gone
            await self.rules.remove(
                GUILD_A, self.campaign.id, DM, rule_id, unchanged_since=now.updated_at
            )

    async def test_a_campaign_holds_a_few_hundred_and_no_more(self) -> None:
        for number in range(HOUSE_RULES_MAX):
            await self.add(f"Rule {number}")
        with self.assertRaisesRegex(HouseRuleError, "Remove one first"):
            await self.add("One too many")
        newest = (await self.rules.list(GUILD_A, self.campaign.id))[0]
        await self.rules.remove(GUILD_A, self.campaign.id, DM, newest.id)
        await self.add("Room again")  # a removal makes room

    async def test_a_co_dm_may_change_them_too(self) -> None:
        await self.campaigns.add_dm(GUILD_A, self.campaign.id, CO_DM)
        rule_id = await self.add("Crits double the dice", who=CO_DM)
        await self.rules.edit(GUILD_A, self.campaign.id, CO_DM, rule_id, "Crits max the dice")
        self.assertEqual(await self.texts(), ["Crits max the dice"])


class OnlyTheDM(HouseRulesTest):
    async def test_a_player_can_read_but_not_change_anything(self) -> None:
        rule_id = await self.add("Crits double the dice")
        self.assertEqual(len(await self.rules.list(GUILD_A, self.campaign.id)), 1)  # anyone reads
        calls = (
            self.rules.add(GUILD_A, self.campaign.id, PLAYER, "Everyone gets a pony"),
            self.rules.edit(GUILD_A, self.campaign.id, PLAYER, rule_id, "Crits kill"),
            self.rules.remove(GUILD_A, self.campaign.id, PLAYER, rule_id),
        )
        for call in calls:
            with self.assertRaisesRegex(HouseRuleError, "Only this campaign's DM"):
                await call
        self.assertEqual(await self.texts(), ["Crits double the dice"])

    async def test_the_dm_of_another_campaign_cannot(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Strahd", PLAYER)
        rule_id = await self.add("Crits double the dice")
        with self.assertRaisesRegex(HouseRuleError, "Only this campaign's DM"):
            await self.rules.add(GUILD_A, self.campaign.id, PLAYER, "Mine now")
        with self.assertRaisesRegex(HouseRuleError, "Only this campaign's DM"):
            await self.rules.remove(GUILD_A, self.campaign.id, PLAYER, rule_id)
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


class Backups(HouseRulesTest):
    async def test_house_rules_go_into_a_backup_and_come_back(self) -> None:
        await self.rules.add(
            GUILD_A, self.campaign.id, DM, "Falling deals 2d6", "1d6 per 10 feet", session_id="s1"
        )
        await self.add("Crits double the dice")
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        rows = backup["sections"]["house_rules"]
        self.assertEqual([r["rule"] for r in rows], ["Falling deals 2d6", "Crits double the dice"])
        self.assertEqual(rows[0]["instead"], "1d6 per 10 feet")
        self.assertEqual(rows[0]["by"], str(DM))
        self.assertNotIn("session", " ".join(rows[0]))  # no transcript id leaves the server

        restored = await self.campaigns.import_backup(GUILD_B, backup, PLAYER)
        back = await self.rules.list(GUILD_B, restored.id)
        # Same words, same order (newest first), in the other server.
        self.assertEqual([r.rule for r in back], ["Crits double the dice", "Falling deals 2d6"])
        self.assertEqual(back[1].supersedes, "1d6 per 10 feet")
        self.assertIsNone(back[1].session_id)
        self.assertEqual(back[1].created_at, rows[0]["created_at"])
        # The original is untouched, and the copy's rules are its own.
        self.assertEqual(len(await self.rules.list(GUILD_A, self.campaign.id)), 2)
        again = await self.campaigns.export(GUILD_B, restored.id)
        self.assertEqual(again["sections"]["house_rules"], rows)

    async def test_restoring_over_a_campaign_replaces_its_rules(self) -> None:
        await self.add("Crits double the dice")
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        await self.add("A rule made after the backup")
        await self.campaigns.import_backup(
            GUILD_A, backup, DM, replace_campaign_id=self.campaign.id
        )
        (replaced,) = await self.campaigns.list_campaigns(GUILD_A)
        self.assertEqual(await self.texts(replaced), ["Crits double the dice"])

    async def test_a_backup_from_before_house_rules_restores_with_none(self) -> None:
        await self.add("Crits double the dice")
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        del backup["sections"]["house_rules"]
        restored = await self.campaigns.import_backup(GUILD_B, backup, PLAYER)
        self.assertEqual(await self.texts(restored), [])

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
        row = good["sections"]["house_rules"][0]
        damaged: list[Any] = [
            "not a row",
            {k: v for k, v in row.items() if k != "by"},
            {**row, "extra": 1},
            {**row, "rule": ""},
            {**row, "rule": "x" * 501},
            {**row, "rule": " spaced  out "},  # DMbot always writes it cleaned
            {**row, "rule": 5},
            {**row, "instead": ""},
            {**row, "instead": 5},
            {**row, "scenario": "y" * 501},
            {**row, "by": "0"},
            {**row, "by": "-5"},
            {**row, "by": "abc"},
            {**row, "by": 7},
            {**row, "by": str(2**63)},
            {**row, "created_at": -1},
            {**row, "created_at": True},
            {**row, "created_at": "1"},
            {**row, "updated_at": row["created_at"] - 1},
            {**row, "updated_at": 2**63},
        ]
        for bad in damaged:
            with self.subTest(bad=bad):
                backup = copy.deepcopy(good)
                backup["sections"]["house_rules"] = [row, bad]
                with self.assertRaises(CampaignError) as caught:
                    await self.campaigns.import_backup(GUILD_B, backup, PLAYER)
                self.assertIn("damaged", str(caught.exception))
        too_many = copy.deepcopy(good)
        too_many["sections"]["house_rules"] = [row] * (HOUSE_RULES_MAX + 1)
        with self.assertRaisesRegex(CampaignError, "damaged"):
            await self.campaigns.import_backup(GUILD_B, too_many, PLAYER)
        self.assertEqual(await self.campaigns.list_campaigns(GUILD_B), [])  # nothing half made

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
        rule_id = await self.add("Crits double the dice")
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
        self.assertEqual(existing.id, rule_id)
