"""The Story Graph's change number (#3294): `campaigns.memory_version` moves with every change to
what a viewer could see, in the same transaction, and does not move when nothing changed, so a
page that asks "did the story change?" can skip the full read. Needs the test database; invented
names throughout."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.test_memory_store import DM, GUILD_A, ConfirmedMemoryTest

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"
OTHER_DM = 8


class TheNumberMoves(ConfirmedMemoryTest):
    async def version(self) -> int:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT memory_version FROM campaigns WHERE guild_id = %s AND id = %s",
                (GUILD_A, self.c),
            )
            row = await cur.fetchone()
        assert row is not None
        return int(row["memory_version"])

    async def moved(self, before: int) -> bool:
        return await self.version() > before

    async def test_a_new_name_and_an_alias_move_it(self) -> None:
        v = await self.version()
        a = await self.add("Aldric")
        self.assertTrue(await self.moved(v))
        v = await self.version()
        await self.memory.add_alias(GUILD_A, self.c, a, "Old Al", kind="nickname", source="dm")
        self.assertTrue(await self.moved(v))

    async def test_a_connection_and_a_pin_and_their_undo_move_it(self) -> None:
        a, b = await self.add("Aldric"), await self.add("Brann")
        v = await self.version()
        written = await self.relate(a, "knows", b, status="confirmed")
        self.assertTrue(await self.moved(v))
        v = await self.version()
        await self.memory.pin(GUILD_A, self.c, written.value[0].id, True, source="dm")
        self.assertTrue(await self.moved(v))
        made = await self.memory.add_entity(
            GUILD_A, self.c, name="Cerric", type="npc", source="dm", status="confirmed"
        )
        v = await self.version()
        await self.memory.undo(GUILD_A, self.c, made.batch or 0, source="dm")
        self.assertTrue(await self.moved(v))

    async def test_a_connection_made_secret_moves_it(self) -> None:
        a, b = await self.add("Aldric"), await self.add("Brann")
        rel = (await self.relate(a, "knows", b)).value[0]
        v = await self.version()
        await self.memory.update_relation(GUILD_A, self.c, rel.id, source="dm", secret=True)
        self.assertTrue(await self.moved(v))

    async def test_a_merge_moves_it(self) -> None:
        a, b = await self.add("Aldric"), await self.add("Aldric the Bold")
        v = await self.version()
        await self.memory.merge(GUILD_A, self.c, a, b, source="dm", dm_said_same=True)
        self.assertTrue(await self.moved(v))

    async def test_reading_the_graph_does_not_move_it(self) -> None:
        a, b = await self.add("Aldric"), await self.add("Brann")
        await self.relate(a, "knows", b)
        v = await self.version()
        await self.memory.entities(GUILD_A, self.c)
        await self.memory.relations(GUILD_A, self.c, include_secret=True)
        self.assertEqual(await self.version(), v)

    async def test_a_dm_screen_setting_moves_it_only_when_it_changes(self) -> None:
        v = await self.version()
        await self.campaigns.set_dm_screen_visibility(GUILD_A, self.c, "open")
        self.assertEqual(await self.version(), v + 1)
        v = await self.version()
        await self.campaigns.set_dm_screen_visibility(GUILD_A, self.c, "open")  # (the same)
        self.assertEqual(await self.version(), v)

    async def test_a_dm_added_or_removed_moves_it(self) -> None:
        v = await self.version()
        await self.campaigns.add_dm(GUILD_A, self.c, OTHER_DM)
        self.assertEqual(await self.version(), v + 1)
        v = await self.version()
        await self.campaigns.add_dm(GUILD_A, self.c, OTHER_DM)  # (already one: nothing changes)
        self.assertEqual(await self.version(), v)
        await self.campaigns.remove_dm(GUILD_A, self.c, OTHER_DM)
        self.assertEqual(await self.version(), v + 1)

    async def test_a_new_owner_moves_it_once(self) -> None:
        v = await self.version()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = %s WHERE guild_id = %s AND id = %s",
                (OTHER_DM, GUILD_A, self.c),
            )
        self.assertEqual(await self.version(), v + 1)

    async def test_a_campaign_with_dms_can_be_deleted(self) -> None:
        await self.campaigns.add_dm(GUILD_A, self.c, OTHER_DM)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM campaigns WHERE guild_id = %s AND id = %s", (GUILD_A, self.c)
            )

    async def test_another_campaigns_change_does_not_move_this_one(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other Wastes", DM)).id
        v = await self.version()
        await self.add("Zed", campaign=other)
        self.assertEqual(await self.version(), v)


# A tripwire for raw SQL, not proof that every writer bumps (the tests above are): where a
# statement writes a `memory_*` table. Every one of these runs inside `MemoryStore._write`
# (the version moves with it), or touches a table the graph never draws. A new file that writes one
# fails here until someone decides which it is.
WRITES = re.compile(r"(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+memory_(?!changes)\w+", re.I)
INSIDE_WRITE = "runs on the change's own connection inside `_write`, so the version moves with it"
BUMPS = "moves `memory_version` itself, in the same transaction"
NOT_DRAWN = "writes lineage, claims or working data the Story Graph's doors never read"
FILES: dict[str, str] = {
    "campaigns/store.py": NOT_DRAWN + " (the heard-words cleanup of a campaign move)",
    "memory/_changes.py": INSIDE_WRITE + " (evidence rows)",
    "memory/backup.py": BUMPS + " (a backup load)",
    "memory/beliefs.py": INSIDE_WRITE,
    "memory/claims.py": NOT_DRAWN,
    "memory/contradiction_grades.py": NOT_DRAWN,
    "memory/evidence.py": NOT_DRAWN + " (evidence ids)",
    "memory/forget_readings.py": BUMPS,
    "memory/heard_backup.py": NOT_DRAWN,
    "memory/importance_backup.py": NOT_DRAWN + " (the importance rank)",
    "memory/importance_rank.py": NOT_DRAWN + " (the importance rank)",
    "memory/kind_offers.py": NOT_DRAWN + " (the DM's answers about kinds, #3033)",
    "memory/kind_offers_backup.py": NOT_DRAWN + " (the same answers in a backup)",
    "memory/knowers.py": INSIDE_WRITE,
    "memory/merge_restore.py": INSIDE_WRITE,
    "memory/report_store.py": NOT_DRAWN,
    "memory/store.py": INSIDE_WRITE + " (`_drop_person`, the scan cleanup, heard words)",
    "memory/vectors.py": NOT_DRAWN + " (search vectors)",
    "transcript/store.py": NOT_DRAWN + " (erasing a person's words)",
}


class EveryWriterIsAccountedFor(unittest.TestCase):
    def test_only_the_listed_files_write_the_graphs_tables(self) -> None:
        found = {
            p.relative_to(SRC).as_posix()
            for p in SRC.rglob("*.py")
            if "migrations" not in p.parts and WRITES.search(p.read_text(encoding="utf-8"))
        }
        self.assertEqual(
            found - set(FILES), set(), "a new file writes a graph table outside _write: review it"
        )
        self.assertEqual(set(FILES) - found, set(), "remove from the list")
