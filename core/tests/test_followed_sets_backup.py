"""Followed sets in backups and "no longer updated" (#1260, from #1190): a campaign never loses
the set version it uses. The backup holds the name, version and rules, never the share code; a
restored campaign keeps its copy as a follow that no longer updates. Needs Postgres."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.rules import followed
from dmbot.rules.house import HouseRulesSection, HouseRuleStore
from dmbot.rules.rule_sets import FollowedSetsSection, RuleSetStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 1001, 2002
DM_A, DM_B = 11, 22


class _Base(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: 1_700_000_000)
        self.campaigns.register_section(HouseRulesSection())
        self.campaigns.register_section(FollowedSetsSection())
        self.houses = HouseRuleStore(self.db)
        self.sets = RuleSetStore(self.db, clock=lambda: 1_700_000_000)
        self.pub = (await self.campaigns.create(GUILD_A, "Frostmaiden", DM_A)).id
        self.far = (await self.campaigns.create(GUILD_B, "Far Away", DM_B)).id
        await self.houses.add(GUILD_A, self.pub, DM_A, "Crits double the dice")
        rules = await self.houses.list(GUILD_A, self.pub)
        await self.sets.publish(GUILD_A, self.pub, DM_A, "Frostmaiden", rules, None)
        self.code = await self.sets.make_code(GUILD_A, self.pub, DM_A)
        offer = await self.sets.by_code(GUILD_B, DM_B, self.code)
        self.follow = await self.sets.follow(GUILD_B, self.far, DM_B, offer)


class FollowedBackup(_Base):
    async def test_the_backup_holds_the_set_version_and_never_the_code(self) -> None:
        backup = await self.campaigns.export(GUILD_B, self.far)
        rows = backup["sections"]["rule_set_follows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Frostmaiden")
        self.assertEqual(rows[0]["version"], 1)
        self.assertEqual(rows[0]["content"]["rules"][0]["rule"], "Crits double the dice")
        self.assertNotIn(self.code, json.dumps(backup))
        self.assertNotIn("code", rows[0])

    async def test_a_restored_campaign_keeps_the_copy_but_no_longer_updates(self) -> None:
        backup = await self.campaigns.export(GUILD_B, self.far)
        restored = await self.campaigns.import_backup(GUILD_A, backup, DM_A)
        follows = await self.sets.following(GUILD_A, restored.id)
        self.assertEqual(len(follows), 1)
        copy = follows[0]
        self.assertFalse(copy.updating)
        self.assertIsNone(copy.code)
        self.assertEqual((copy.name, copy.version), ("Frostmaiden", 1))
        self.assertEqual(copy.content, self.follow.content)
        # The publisher moves on; the restored copy does not hear about it, and its rules
        # still answer from the version it kept.
        await self.sets.save_version(
            GUILD_A,
            self.pub,
            DM_A,
            {"rules": [{"number": 1, "rule": "Crits triple", "instead": None}], "link": None},
            plan_ok=True,
        )
        self.assertIsNone(await self.sets.pending_update(GUILD_A, restored.id))
        mine = followed.from_follow(copy, restored.id)
        self.assertEqual([r.rule for r in mine], ["Crits double the dice"])

    async def test_a_restore_over_a_campaign_replaces_its_follows(self) -> None:
        backup = await self.campaigns.export(GUILD_B, self.far)
        await self.campaigns.import_backup(GUILD_B, backup, DM_B, replace_campaign_id=self.far)
        follows = await self.sets.following(GUILD_B, self.far)
        self.assertEqual([f.updating for f in follows], [False])

    async def test_a_damaged_follow_row_is_refused_whole(self) -> None:
        backup = await self.campaigns.export(GUILD_B, self.far)
        damages: tuple[Callable[[dict[str, Any]], object], ...] = (
            lambda r: r.update(version=0),
            lambda r: r.update(set_id="nope"),
            lambda r: r.update(kind="elsewhere"),
            lambda r: r.update(content={"rules": "no"}),
            lambda r: r.update(code="ABCDEFGHIJ"),
            lambda r: r.pop("by"),
        )
        for damage in damages:
            broken = json.loads(json.dumps(backup))
            damage(broken["sections"]["rule_set_follows"][0])
            with self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD_A, broken, DM_A)
        self.assertEqual(len(await self.campaigns.list_campaigns(GUILD_A)), 1)  # nothing was made


class NoLongerUpdated(_Base):
    async def test_a_deleted_set_is_known_to_be_gone(self) -> None:
        listed = (await self.campaigns.create(GUILD_A, "Listed", DM_A)).id
        offer = (await self.sets.shared_here(GUILD_A, listed))[0]
        await self.sets.follow(GUILD_A, listed, DM_A, offer)
        self.assertFalse(await self.sets.is_gone(GUILD_A, listed))
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM rule_sets WHERE guild_id = %s", (GUILD_A,))
        self.assertTrue(await self.sets.is_gone(GUILD_A, listed))
        await self.sets.stop_updating(GUILD_A, listed)
        self.assertFalse(await self.sets.is_gone(GUILD_A, listed))  # said once
        kept = (await self.sets.following(GUILD_A, listed))[0]
        self.assertEqual(kept.content["rules"][0]["rule"], "Crits double the dice")

    async def test_an_unshared_set_by_code_is_known_to_be_gone_and_rules_stay(self) -> None:
        self.assertFalse(await self.sets.is_gone(GUILD_B, self.far))
        await self.sets.stop_sharing(GUILD_A, self.pub, DM_A)
        self.assertTrue(await self.sets.is_gone(GUILD_B, self.far))
        await self.sets.stop_updating(GUILD_B, self.far)
        self.assertFalse(await self.sets.is_gone(GUILD_B, self.far))
        follows = await self.sets.following(GUILD_B, self.far)
        self.assertEqual(
            [r.rule for r in followed.from_follow(follows[0], self.far)], ["Crits double the dice"]
        )
