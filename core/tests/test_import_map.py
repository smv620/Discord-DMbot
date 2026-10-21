"""The remembered import labels and their backup (#2105)."""

from __future__ import annotations

import json
import unittest

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.models import CampaignError
from dmbot.transcript.import_map import ImportSpeakersSection
from tests.pg import DatabaseTest

GUILD_A, GUILD_B, DM = 61, 62, 5


class TheMapping(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(ImportSpeakersSection())
        self.one = await self.campaigns.create(GUILD_A, "One", DM)

    async def remember(self, label: str, user: int) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO import_speakers (guild_id, campaign_id, label, user_id, updated_at)"
                " VALUES (%s, %s, %s, %s, 1)",
                (GUILD_A, self.one.id, label, user),
            )

    async def test_the_labels_travel_in_a_backup_and_go_with_the_campaign(self) -> None:
        await self.remember("Cerric (stormy#1234)", 7001)
        await self.remember("Mira", 7002)
        backup = await self.campaigns.export(GUILD_A, self.one.id)
        await self.campaigns.delete(GUILD_A, self.one.id)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM import_speakers")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
        restored = await self.campaigns.import_backup(GUILD_B, backup, DM)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute(
                "SELECT label, user_id FROM import_speakers WHERE campaign_id = %s ORDER BY label",
                (restored.id,),
            )
            rows = [(r["label"], r["user_id"]) for r in await cur.fetchall()]
        self.assertEqual(rows, [("Cerric (stormy#1234)", 7001), ("Mira", 7002)])
        self.assertNotIn("words", json.dumps(backup, default=str))

    async def test_another_server_never_sees_them(self) -> None:
        await self.remember("Mira", 7002)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM import_speakers")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)

    def test_a_damaged_row_is_refused(self) -> None:
        section = ImportSpeakersSection()
        good = {"label": "Mira", "user": "7001", "at": 1}
        for bad in (
            [{**good, "extra": 1}],
            [{**good, "label": ""}],
            [{**good, "label": "x" * 81}],
            [{**good, "user": "0"}],
            [{**good, "user": 7001}],
            [{**good, "at": True}],
            [good, good],
            ["Mira"],
            "not a list",
        ):
            with self.assertRaises(CampaignError):
                section.check(bad)  # type: ignore[arg-type]
        self.assertEqual(section.check([good]), [("Mira", 7001, 1)])


if __name__ == "__main__":
    unittest.main()
