"""A backup holds the story memory, and a restore brings every count of it back (#4844; CLAUDE.md:
"Backups are complete"). A campaign with player characters, names, other spellings, connections
and the knowers the party's knowledge makes is backed up with the production sections, written to
the file DMbot sends, read back and restored as a new campaign: each memory table has the rows it
had, and the main file is not the size of a campaign with no memory. Invented names only."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock

from dmbot.bot import register_backup_sections
from dmbot.campaigns import CampaignStore
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory.models import CONFIRMED, DM
from dmbot.memory.store import MemoryStore
from tests.pg import DatabaseTest

GUILD, DM_ID, ANNA, BEN = 4401, 9, 31, 32
COUNTED = (
    "memory_entities",
    "memory_aliases",
    "memory_relations",
    "memory_knowers",
    "memory_party_spans",
)


class ABackupHoldsTheMemory(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        register_backup_sections(
            self.store,
            forget_audio=lambda g, c: None,
            consenting=cast(
                Callable[[int], Awaitable[frozenset[int]]],
                AsyncMock(return_value=frozenset({ANNA, BEN})),
            ),
        )
        self.memory = MemoryStore(self.db)
        self.campaign = await self.store.create(GUILD, "Frostmaiden (sample)", DM_ID)
        add = self.memory.add_entity
        c = self.campaign.id
        for name, player in (("Vell", ANNA), ("Tuck", BEN)):
            await add(
                GUILD, c, name=name, type="character", role="player_character",
                played_by=player, status=CONFIRMED, source=DM,
            )  # fmt: skip
        self.ids: list[str] = []
        for n in range(40):
            made = await add(GUILD, c, name=f"Npc {n}", type="npc", status=CONFIRMED, source=DM)
            self.ids.append(str(made.value.id))
            await self.memory.add_alias(
                GUILD, c, self.ids[-1], f"Old Npc {n}", kind="nickname", source=DM,
                status=CONFIRMED,
            )  # fmt: skip
        for a, b in zip(self.ids, self.ids[1:], strict=False):
            await self.memory.add_relation(
                GUILD, c, a, "knows", b, source=DM, confidence=1.0, status=CONFIRMED
            )

    async def count(self, guild: int, campaign_id: str, table: str) -> int:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                f"SELECT count(*) AS n FROM {table} WHERE guild_id = %s AND campaign_id = %s",
                (guild, campaign_id),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def test_every_count_comes_back_through_the_file_that_is_sent(self) -> None:
        before = {t: await self.count(GUILD, self.campaign.id, t) for t in COUNTED}
        self.assertGreater(before["memory_entities"], 40, before)
        self.assertGreater(before["memory_knowers"], 40, before)  # (the party's knowledge rows)
        main, parts = await self.store.export_set(GUILD, self.campaign.id)
        raw = encode_backup(main)  # (the bytes DMbot sends, compressed)
        self.assertGreater(len(raw), 3000, "a campaign with its names is not a tiny file")
        restored = await self.store.import_backup(GUILD, decode_backup(raw), DM_ID, parts=parts)
        after = {t: await self.count(GUILD, restored.id, t) for t in COUNTED}
        self.assertEqual(after, before)


class AndThisWeeksPartyRows(ABackupHoldsTheMemory):
    """Every row of the party record is in it: a stretch that ended (and why), a character who
    joined later, and who-knows rows that carry a backstory's mark (#4858, deploy 38)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory import party_record

        c = self.campaign.id
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                " AND role = 'player_character' ORDER BY name",
                (GUILD, c),
            )
            self.pcs = [str(r["id"]) for r in await cur.fetchall()]
        async with self.memory._write(GUILD, c, DM) as w:  # (the second player character leaves)
            await party_record.close_span(w, self.pcs[1], "retired", at=self.now_ms())
        async with self.db.guild(GUILD) as conn:  # (a backstory's mark on a who-knows row)
            await conn.execute(
                "UPDATE memory_knowers SET backstory_of = %s WHERE id = ("
                " SELECT id FROM memory_knowers WHERE guild_id = %s AND campaign_id = %s"
                " AND about_entity_id IS NOT NULL ORDER BY id LIMIT 1)",
                (self.pcs[0], GUILD, c),
            )

    @staticmethod
    def now_ms() -> int:
        import time

        return int(time.time()) + 10


class AFileRefusedSaysWhichTableBrokeIt(ABackupHoldsTheMemory):
    """ "bad campaign memory entry" alone does not say which of ~30 checks refused a file (#4858):
    the log names the table tags it breaks on, never a row."""

    async def test_a_refused_file_names_the_tag_and_nothing_else(self) -> None:
        from dmbot.campaigns.models import CampaignError
        from dmbot.memory import backup

        main, _ = await self.store.export_set(GUILD, self.campaign.id)
        rows = main["memory"] if "memory" in main else main["sections"]["memory"]
        spans = [r for r in rows if r.get("table") == "party_span"]
        self.assertTrue(spans)
        spans[0]["ended"] = "vanished"  # (a value the check does not know)
        spans[0]["to_at"] = spans[0]["from_at"]
        with (
            self.assertLogs("dmbot.memory.backup", level="WARNING") as logged,
            self.assertRaises(CampaignError),
        ):
            backup.MemorySection().check(rows)
        self.assertIn("party_span", logged.output[0])
        self.assertNotIn("vanished", " ".join(logged.output))
        self.assertEqual(backup.damaged_tags(rows), ["party_span"])

    async def test_every_breaking_column_is_named_in_one_pass(self) -> None:
        from dmbot.campaigns.models import CampaignError
        from dmbot.memory import backup

        main, _ = await self.store.export_set(GUILD, self.campaign.id)
        rows = main["memory"] if "memory" in main else main["sections"]["memory"]
        names = [r for r in rows if r.get("table") == "alias"]
        beliefs = [r for r in rows if r.get("table") == "belief"]
        self.assertTrue(names and beliefs)
        names[0]["created_at"] = "not a time"
        beliefs[0]["confidence"] = "high"
        with (
            self.assertLogs("dmbot.memory.backup", level="WARNING") as logged,
            self.assertRaises(CampaignError),
        ):
            backup.MemorySection().check(rows)
        said = logged.output[0]
        self.assertIn("memory_aliases.created_at", said)
        self.assertIn("memory_beliefs.confidence", said)
        self.assertIn("raised in _check_rows line", said)
        self.assertNotIn("not a time", said)
        self.assertNotIn("high", said.replace("highest", ""))


class TheDatabaseRefusingARowSaysWhere(DatabaseTest):
    """A file that passes its checks and is still refused by the database says which table and
    rule, never a value (#4858: "damaged" hid which of the ~30 constraints it was)."""

    async def test_a_refused_row_names_its_table_and_rule_and_no_value(self) -> None:
        from dmbot.campaigns.models import CampaignError
        from dmbot.memory import backup

        store = CampaignStore(self.db)
        register_backup_sections(
            store,
            forget_audio=lambda g, c: None,
            consenting=cast(
                Callable[[int], Awaitable[frozenset[int]]], AsyncMock(return_value=frozenset())
            ),
        )
        memory = MemoryStore(self.db)
        campaign = await store.create(GUILD, "Frostmaiden", DM_ID)
        await memory.add_entity(
            GUILD, campaign.id, name="Secretly Named Npc", type="npc", status=CONFIRMED, source=DM
        )
        main, _ = await store.export_set(GUILD, campaign.id)
        rows = main["sections"]["memory"]
        by_tag = backup.MemorySection().check(rows)
        by_tag["entity"] = [*by_tag["entity"], by_tag["entity"][-1]]  # (the same id twice)
        other = await store.create(GUILD, "Another", DM_ID)
        with (
            self.assertLogs("dmbot.memory.backup", level="WARNING") as logged,
            self.assertRaises(CampaignError),
        ):
            async with self.db.guild(GUILD) as conn:
                await backup.MemorySection().load(conn, GUILD, other.id, by_tag)
        text = " ".join(logged.output)
        self.assertIn("memory_entities", text)
        self.assertNotIn("Secretly", text)
