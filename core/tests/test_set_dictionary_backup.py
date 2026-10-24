"""The sets a campaign publishes, in backups (#2552); the pronunciation dictionary is not (#2604).
A restored publisher keeps its sets and their versions under new ids, never a share code.
Needs Postgres."""

from __future__ import annotations

import json
from typing import Any

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.recaps.store import RecapStore
from dmbot.rules.house import HouseRulesSection, HouseRuleStore
from dmbot.rules.rule_set_backup import RuleSetsSection
from dmbot.rules.rule_sets import RuleSetStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 1001, 2002
DM_A, DM_B = 11, 22


class SetsInBackups(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: 1_700_000_000)
        self.campaigns.register_section(HouseRulesSection())
        self.campaigns.register_section(RuleSetsSection())
        self.houses = HouseRuleStore(self.db)
        self.sets = RuleSetStore(self.db, clock=lambda: 1_700_000_000)
        self.recaps = RecapStore(self.db)
        self.pub = (await self.campaigns.create(GUILD_A, "Frostmaiden", DM_A)).id
        await self.houses.add(GUILD_A, self.pub, DM_A, "Crits double the dice")
        rules = await self.houses.list(GUILD_A, self.pub)
        await self.sets.publish(GUILD_A, self.pub, DM_A, "Frostmaiden", rules, None)
        await self.sets.save_version(
            GUILD_A, self.pub, DM_A,
            {"rules": [{"number": 1, "rule": "Crits triple", "instead": None}], "link": None},
            plan_ok=True,
        )  # fmt: skip
        self.code = await self.sets.make_code(GUILD_A, self.pub, DM_A)

    async def rows(self, guild: int, campaign: str, table: str) -> list[dict[str, Any]]:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                f"SELECT * FROM {table} WHERE guild_id = %s AND campaign_id = %s",
                (guild, campaign),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def test_the_sets_and_every_version_come_back_with_new_ids_and_no_code(self) -> None:
        backup = await self.campaigns.export(GUILD_A, self.pub)
        self.assertNotIn(self.code, json.dumps(backup))
        (row,) = backup["sections"]["rule_sets"]
        self.assertEqual([v["version"] for v in row["versions"]], [1, 2])
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM_B)
        (got,) = await self.rows(GUILD_B, restored.id, "rule_sets")
        (orig,) = await self.rows(GUILD_A, self.pub, "rule_sets")
        self.assertNotEqual(got["id"], orig["id"])  # unique across the database
        self.assertEqual(
            (got["name"], got["kind"], got["status"]), ("Frostmaiden", "house_rules", "shared")
        )
        versions = await self.rows(GUILD_B, restored.id, "rule_set_versions")
        self.assertEqual(sorted(v["version"] for v in versions), [1, 2])
        self.assertEqual(await self.rows(GUILD_B, restored.id, "rule_set_codes"), [])
        self.assertEqual(len(await self.rows(GUILD_A, self.pub, "rule_set_codes")), 1)  # original

    async def test_a_restore_over_the_campaign_replaces_its_sets(self) -> None:
        backup = await self.campaigns.export(GUILD_A, self.pub)
        await self.campaigns.import_backup(GUILD_A, backup, DM_A, replace_campaign_id=self.pub)
        self.assertEqual(len(await self.rows(GUILD_A, self.pub, "rule_sets")), 1)

    async def test_an_old_backup_without_the_section_loads_with_none(self) -> None:
        backup = await self.campaigns.export(GUILD_A, self.pub)
        del backup["sections"]["rule_sets"]
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM_B)
        self.assertEqual(await self.rows(GUILD_B, restored.id, "rule_sets"), [])

    async def test_a_damaged_set_is_refused_whole(self) -> None:
        backup = await self.campaigns.export(GUILD_A, self.pub)
        damages = (
            lambda r: r.update(kind="elsewhere"),
            lambda r: r.update(name=""),
            lambda r: r.update(status="open"),
            lambda r: r.update(versions=[]),
            lambda r: r["versions"][0].update(version=0),
            lambda r: r["versions"][0].update(content={"rules": "no"}),
            lambda r: r["versions"][0].update(source="hacker"),
            lambda r: r["versions"].append(dict(r["versions"][0])),  # a version number twice
            lambda r: r.update(code="ABCDEFGHIJ"),
        )
        for damage in damages:
            broken = json.loads(json.dumps(backup))
            damage(broken["sections"]["rule_sets"][0])
            with self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD_B, broken, DM_B)
        self.assertEqual(len(await self.campaigns.list_campaigns(GUILD_B)), 0)

    async def test_the_pronunciation_dictionary_stays_out_of_a_backup(self) -> None:
        # (#2604) it is an object on one shared account at the voice company: a file could point a
        # campaign at another's, and a name change deletes it
        await self.recaps.save_dictionary(GUILD_A, self.pub, "dict-1", "v-1", "a" * 64)
        backup = await self.campaigns.export(GUILD_A, self.pub)
        self.assertNotIn("recap_dictionaries", backup["sections"])
        self.assertNotIn("dict-1", json.dumps(backup))
        hand_made = json.loads(json.dumps(backup))
        hand_made["sections"]["recap_dictionaries"] = [
            {"dictionary_id": "someone-elses", "version_id": "v", "rules_hash": "a" * 64, "at": 1}
        ]
        with self.assertRaises(CampaignError):  # a file that carries one is not read
            await self.campaigns.import_backup(GUILD_B, hand_made, DM_B)
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM_B)
        self.assertIsNone(await self.recaps.dictionary(GUILD_B, restored.id))
