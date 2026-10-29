"""Where house rules came from (#2285): the grouping, the 📂 Sources list and its downloads, the
source each save path records, and the backup round trip."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.models import CampaignError
from dmbot.rules import house_file, house_sources
from dmbot.rules.followed import FollowedRule
from dmbot.rules.house import HouseRule, HouseRulesSection, HouseRuleStore
from dmbot.rules.house_file_link import FileLink
from dmbot.ui import house_sources as ui
from tests.pg import DatabaseTest
from tests.test_house_rules_ui import DM, GUILD, PLAYER, UITest, campaign

NOW = 1_700_000_000


def rule(n: int, source: str = "discord", name: str | None = None, **kw: Any) -> HouseRule:
    return HouseRule(
        n, "c1", f"Rule {n}", None, None, None, DM, NOW + n, NOW + n, 1, kw.get("from_set"),
        source, name,
    )  # fmt: skip


def followed(n: int, set_name: str) -> FollowedRule:
    return FollowedRule(
        n, "c1", f"Set rule {n}", None, None, None, 0, NOW, NOW, set_name=set_name, set_version=2
    )


class Grouping(unittest.TestCase):
    def test_each_place_is_its_own_group_in_reading_order(self) -> None:
        rules = [
            rule(1),  # from before: added in Discord
            rule(2, "typed"),
            rule(3, "voice"),
            rule(4, "alert"),
            rule(5, "linked"),
            rule(6, "upload", "our-rules.txt"),
            rule(7, "upload", "our-rules.txt"),
            rule(8, "upload", "older.txt"),
            followed(1, "Red Table"),
        ]
        out = house_sources.group(rules, {"Red Table": "Sam"})
        self.assertEqual(
            [(s.kind, s.title, len(s.rules)) for s in out],
            [
                ("linked", house_sources.LINKED_TITLE, 1),
                ("upload", "older.txt", 1),  # (rule 8 is the newest)
                ("upload", "our-rules.txt", 2),
                ("discord", house_sources.DISCORD_TITLE, 4),
                ("followed", "Red Table", 1),
            ],
        )
        self.assertEqual(out[-1].publisher, "Sam")
        self.assertEqual(out[-1].line, "1 rule · Set: Red Table (by Sam)")

    def test_an_upload_line_has_its_name_and_date(self) -> None:
        (only,) = house_sources.group([rule(6, "upload", "our-rules.txt")])
        self.assertEqual(only.line, "1 rule · File: our-rules.txt (14 Nov)")

    def test_rules_copied_from_a_set_are_not_a_follow(self) -> None:
        copied = rule(3, from_set="Red Table house rules, v4")
        (only,) = house_sources.group([copied])
        self.assertEqual(only.kind, "copied")
        self.assertEqual(only.line, "1 rule · Copied from Red Table house rules, v4")

    def test_old_rules_read_as_added_in_discord(self) -> None:
        (only,) = house_sources.group([rule(1)])
        self.assertEqual(only.title, "Added in Discord")

    def test_a_source_with_no_rules_is_not_listed(self) -> None:
        self.assertEqual(house_sources.group([]), [])

    def test_a_download_is_exactly_that_sources_rules(self) -> None:
        out = house_sources.group([rule(1), rule(5, "linked"), rule(9, "linked")])
        linked = next(s for s in out if s.kind == "linked")
        name, text = house_sources.download("Frostmaiden", linked)
        self.assertTrue(name.startswith("house-rules-frostmaiden"))
        parsed = house_file.parse(text)
        self.assertEqual([r.number for r in parsed.rules], [5, 9])
        self.assertNotIn("Rule 1", text)


class TheList(UITest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store.seed("c1", 1)
        self.store.rules["c1"][0] = rule(1, "linked")
        self.bot.rule_sets = None
        self.bot.house_file_links = None

        async def everything(guild_id: int, cid: str) -> Any:
            return await self.store.list(guild_id, cid)

        self.bot.all_house_rules = everything

    async def test_anyone_who_can_read_the_list_can_open_sources(self) -> None:
        for user in (DM, PLAYER):
            it = self.it(user)
            await ui.open_sources(it, campaign())
            text = it.followup.send.await_args.args[0]
            self.assertIn(house_sources.LINKED_TITLE, text)  # (no link record: no site to name)
            view = it.followup.send.await_args.kwargs["view"]
            self.assertEqual(len(view.pick.options), 1)

    async def test_the_linked_file_is_named_by_its_site(self) -> None:
        rules = [rule(1, "linked")]
        named = house_sources.group(rules, linked_site="docs.google.com")
        self.assertEqual(named[0].title, "from docs.google.com")
        self.assertIn("1 rule · from docs.google.com", named[0].line)
        self.assertEqual(house_sources.group(rules)[0].title, house_sources.LINKED_TITLE)

    async def test_a_player_never_gets_the_linked_files_address(self) -> None:
        get = AsyncMock(return_value=FileLink("https://docs.example.com/d/abc", None, 7, 1))
        self.bot.house_file_links = SimpleNamespace(get=get)
        it = self.it(PLAYER)
        await ui.open_sources(it, campaign())
        view = it.followup.send.await_args.kwargs["view"]
        self.assertFalse([c for c in view.children if isinstance(c, discord.ui.Button)])
        # The site is said (the list is for the whole server); the address never is.
        self.assertIn("from docs.example.com", str(it.followup.send.await_args))
        self.assertNotIn("/d/abc", str(it.followup.send.await_args))

    async def test_two_long_names_that_share_their_start_get_their_own_downloads(self) -> None:
        shared = "x" * 110
        self.store.rules["c1"] = [
            rule(1, "upload", shared + "-one.txt"),
            rule(2, "upload", shared + "-two.txt"),
        ]
        it = self.it()
        await ui.open_sources(it, campaign())
        view = it.followup.send.await_args.kwargs["view"]
        values = [o.value for o in view.pick.options]
        self.assertEqual(len(set(values)), 2)
        view.pick._values = [values[1]]
        press = self.it()
        await view.pick.callback(press)
        text = press.followup.send.await_args.kwargs["file"].fp.read().decode()
        self.assertEqual(len(house_file.parse(text).rules), 1)

    async def test_the_message_always_fits_discord(self) -> None:
        self.store.rules["c1"] = [rule(n, "upload", "f" * 150 + str(n)) for n in range(1, 40)]
        it = self.it()
        await ui.open_sources(it, campaign())
        self.assertLessEqual(len(it.followup.send.await_args.args[0]), 2000)
        view = it.followup.send.await_args.kwargs["view"]
        self.assertLessEqual(len(view.pick.options), 25)

    async def test_the_list_has_a_sources_button_for_everyone_once_there_are_rules(self) -> None:
        it = await self.command(PLAYER)
        _, view = self.opened(it)
        self.assertIn(ui.SOURCES_LABEL, [str(c.label) for c in view.children])

    async def test_a_download_gives_only_that_sources_rules(self) -> None:
        self.store.seed("c1", 1)  # rule 2: from before, "added in Discord"
        it = self.it()
        await ui.open_sources(it, campaign())
        view = it.followup.send.await_args.kwargs["view"]
        view.pick._values = ["0"]
        press = self.it()
        await view.pick.callback(press)
        sent = press.followup.send.await_args
        text = sent.kwargs["file"].fp.read().decode()
        self.assertEqual([r.number for r in house_file.parse(text).rules], [1])

    async def test_the_linked_file_gets_an_open_button(self) -> None:
        link = FileLink("https://docs.example.com/d/abc", None, 7, 1)
        self.bot.house_file_links = SimpleNamespace(get=AsyncMock(return_value=link))
        it = self.it()
        await ui.open_sources(it, campaign())
        view = it.followup.send.await_args.kwargs["view"]
        buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
        self.assertEqual([b.url for b in buttons], ["https://docs.example.com/d/abc"])

    async def test_an_address_that_cannot_be_a_button_is_left_out(self) -> None:
        link = FileLink("https://docs.example.com/" + "a" * 600, None, 7, 1)
        self.bot.house_file_links = SimpleNamespace(get=AsyncMock(return_value=link))
        it = self.it()
        await ui.open_sources(it, campaign())
        view = it.followup.send.await_args.kwargs["view"]
        self.assertFalse([c for c in view.children if isinstance(c, discord.ui.Button)])

    async def test_a_followed_set_is_labelled_with_its_name_and_publisher(self) -> None:
        async def everything(guild_id: int, cid: str) -> Any:
            return [*await self.store.list(guild_id, cid), followed(1, "Red Table")]

        self.bot.all_house_rules = everything
        follow = SimpleNamespace(name="Red Table", publisher="Sam", kind="house_rules")
        self.bot.rule_sets = SimpleNamespace(following=AsyncMock(return_value=[follow]))
        it = self.it()
        await ui.open_sources(it, campaign())
        self.assertIn("1 rule · Set: Red Table (by Sam)", it.followup.send.await_args.args[0])

    async def test_a_campaign_that_only_follows_a_set_still_has_sources(self) -> None:
        self.store.rules["c1"] = []
        follow = SimpleNamespace(
            name="Red Table", publisher="Sam", kind="house_rules", updating=True
        )
        self.bot.rule_sets = SimpleNamespace(following=AsyncMock(return_value=[follow]))
        it = await self.command(DM)
        _, view = self.opened(it)
        self.assertIn(ui.SOURCES_LABEL, [str(c.label) for c in view.children])

    async def test_no_rules_say_so(self) -> None:
        self.store.rules["c1"] = []
        it = self.it()
        await ui.open_sources(it, campaign())
        self.assertEqual(it.followup.send.await_args.args[0], ui.NONE_YET)


class Recorded(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.rules = HouseRuleStore(self.db)
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(HouseRulesSection())
        self.one = await self.campaigns.create(GUILD, "One", DM)

    async def test_each_save_records_its_source(self) -> None:
        c = self.one.id
        typed = await self.rules.add(GUILD, c, DM, "Typed rule")
        voice = await self.rules.add(GUILD, c, DM, "Voice rule", source="voice")
        up = await self.rules.add(
            GUILD, c, DM, "Upload rule", source="upload", source_name="  our   rules.txt "
        )
        self.assertEqual((typed.source, typed.source_name), ("typed", None))
        self.assertEqual(voice.source, "voice")
        self.assertEqual((up.source, up.source_name), ("upload", "our rules.txt"))
        with self.assertRaises(ValueError):
            await self.rules.add(GUILD, c, DM, "Nope", source="somewhere")

    async def test_editing_a_rule_keeps_its_source(self) -> None:
        saved = await self.rules.add(GUILD, self.one.id, DM, "A rule", source="voice")
        edited = await self.rules.edit(GUILD, self.one.id, DM, saved.number, "A better rule")
        self.assertEqual(edited.source, "voice")

    async def test_the_backup_round_trip_keeps_the_source(self) -> None:
        c = self.one.id
        await self.rules.add(GUILD, c, DM, "Typed")
        await self.rules.add(GUILD, c, DM, "Up", source="upload", source_name="a.txt")
        await self.rules.add(GUILD, c, DM, "Linked", source="linked")
        data = await self.campaigns.export(GUILD, c)
        again = await self.campaigns.import_backup(GUILD + 1, json.loads(json.dumps(data)), DM)
        copy = await self.rules.list(GUILD + 1, again.id)
        self.assertEqual(
            sorted((r.rule, r.source, r.source_name) for r in copy),
            [("Linked", "linked", None), ("Typed", "typed", None), ("Up", "upload", "a.txt")],
        )

    async def test_an_old_backup_without_it_reads_as_added_in_discord(self) -> None:
        section = HouseRulesSection()
        old = {
            "number": 1,
            "rule": "Old",
            "instead": None,
            "scenario": None,
            "by": "5",
            "created_at": 1,
            "updated_at": 1,
        }
        (checked,) = section.check([old]).rules
        self.assertEqual((checked.source, checked.source_name), ("discord", None))

    async def test_a_damaged_source_is_refused(self) -> None:
        section = HouseRulesSection()
        base = {
            "number": 1,
            "rule": "Old",
            "instead": None,
            "scenario": None,
            "by": "5",
            "created_at": 1,
            "updated_at": 1,
        }
        for extra in (
            {"source": "somewhere"},
            {"source": 3},
            {"source_name": ""},
            {"source_name": 4},
        ):
            with self.assertRaises(CampaignError, msg=str(extra)):
                section.check([{**base, **extra}])


class EachPath(UITest):
    async def test_a_typed_rule_is_typed_and_a_card_override_is_an_alert(self) -> None:
        from dmbot.ui import house_rules as hr

        add = hr.AddForm(campaign())
        add.rule._value = "Typed"
        await self.submit(add)
        override = hr.OverrideForm(campaign(), "Fireball")
        override.rule._value = "From a card"
        await self.submit(override)
        self.assertEqual(self.store.sources, ["typed", "alert"])
