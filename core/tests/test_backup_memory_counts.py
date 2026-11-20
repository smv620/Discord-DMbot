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
COUNTED = ("memory_entities", "memory_aliases", "memory_relations", "memory_knowers")


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
