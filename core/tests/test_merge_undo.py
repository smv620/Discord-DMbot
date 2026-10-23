"""Undo of a merge hands back what the merge moved outside the change log (#2438): the
merged-away name's private notes and D&D Beyond sheet, and the beliefs of a character
perspective (#2433). Invented names throughout."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import CONFIRMED, new_id
from tests.test_memory_store import GUILD_A, MemoryTest

LINK = "https://www.dndbeyond.com/characters/123456"


class MergeHelpers(MemoryTest):
    async def names(self) -> tuple[str, str]:
        keep = await self.add("Brother Aldric", status=CONFIRMED)
        gone = await self.add("Aldric the Monk", status=CONFIRMED)
        return keep, gone

    async def merge(self, keep: str, gone: str) -> int:
        written = await self.memory.merge(
            GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True
        )
        assert written.batch is not None
        return written.batch

    async def undo(self, batch: int) -> None:
        await self.memory.undo(GUILD_A, self.c, batch)

    async def note(self, entry: str, user: int, body: str, orphaned: int | None = None) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO npc_notes (guild_id, campaign_id, entry_id, user_id, name, body,"
                " updated_at, orphaned_at) VALUES (%s, %s, %s, %s, '', %s, 100, %s)",
                (GUILD_A, self.c, entry, user, body, orphaned),
            )

    async def notes(self) -> dict[tuple[str, int], tuple[str, int | None]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT entry_id, user_id, body, orphaned_at FROM npc_notes WHERE campaign_id = %s",
                (self.c,),
            )
            return {
                (r["entry_id"], r["user_id"]): (r["body"], r["orphaned_at"])
                for r in await cur.fetchall()
            }

    async def sheet(self, entry: str, player: int = 777) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, url, player_id)"
                " VALUES (%s, %s, %s, %s, %s)",
                (GUILD_A, self.c, entry, LINK, player),
            )

    async def sheets(self) -> dict[str, int]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT entity_id, player_id FROM character_sheets WHERE campaign_id = %s",
                (self.c,),
            )
            return {r["entity_id"]: r["player_id"] for r in await cur.fetchall()}


class MergeThenUndo(MergeHelpers):
    async def test_notes_go_back_to_the_name_they_came_from(self) -> None:
        keep, gone = await self.names()
        await self.note(gone, 1, "only on the monk", orphaned=500)  # no twin: moves whole
        await self.note(keep, 2, "on aldric")  # a twin: joined
        await self.note(gone, 2, "on the monk")
        await self.note(keep, 3, "kept only")
        batch = await self.merge(keep, gone)
        self.assertEqual(
            await self.notes(),
            {
                (keep, 1): (
                    "only on the monk",
                    500,
                ),  # the kept name is not listed: the clock runs on
                (keep, 2): ("on aldric\n\non the monk", None),
                (keep, 3): ("kept only", None),
            },
        )
        await self.undo(batch)
        self.assertEqual(
            await self.notes(),
            {
                (gone, 1): ("only on the monk", 500),
                (keep, 2): ("on aldric", None),
                (gone, 2): ("on the monk", None),
                (keep, 3): ("kept only", None),
            },
        )

    async def test_a_note_written_on_since_stays_where_it_is(self) -> None:
        keep, gone = await self.names()
        await self.note(gone, 1, "only on the monk")
        await self.note(keep, 2, "on aldric")
        await self.note(gone, 2, "on the monk")
        batch = await self.merge(keep, gone)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE npc_notes SET body = body || ' and more', updated_at = 999"
                " WHERE campaign_id = %s",
                (self.c,),
            )
        await self.undo(batch)  # the undo itself is not refused
        self.assertEqual(
            await self.notes(),
            {
                (keep, 1): ("only on the monk and more", None),
                (keep, 2): ("on aldric\n\non the monk and more", None),
            },
        )

    async def test_a_note_deleted_since_is_not_brought_back(self) -> None:
        keep, gone = await self.names()
        await self.note(gone, 1, "only on the monk")
        batch = await self.merge(keep, gone)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM npc_notes WHERE campaign_id = %s", (self.c,))
        await self.undo(batch)
        self.assertEqual(await self.notes(), {})

    async def test_no_note_words_are_kept_in_the_restore_record(self) -> None:
        keep, gone = await self.names()
        await self.note(keep, 1, "a private thought about aldric")
        await self.note(gone, 1, "a private thought about the monk")
        await self.merge(keep, gone)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT before::text AS t FROM merge_restores")
            text = " ".join(r["t"] for r in await cur.fetchall())
        self.assertTrue(text)
        self.assertNotIn("private thought", text)

    async def test_undoing_the_undo_merges_again_and_the_notes_and_sheet_follow(self) -> None:
        keep, gone = await self.names()
        await self.note(gone, 1, "only on the monk")
        await self.note(keep, 2, "on aldric")
        await self.note(gone, 2, "on the monk")
        await self.sheet(gone)
        merged = await self.merge(keep, gone)
        first = await self.notes(), await self.sheets()
        undone = await self.memory.undo(GUILD_A, self.c, merged)
        assert undone.batch is not None
        self.assertEqual(list(await self.sheets()), [gone])
        await self.memory.undo(GUILD_A, self.c, undone.batch)  # redo
        self.assertEqual((await self.notes(), await self.sheets()), first)

    async def test_several_merges_in_one_batch_each_give_theirs_back(self) -> None:
        keep, one = await self.names()
        two = await self.add("Aldric of the Lower Quay", status=CONFIRMED)
        await self.note(one, 1, "from one")
        await self.note(two, 1, "from two")
        await self.sheet(two)
        written = await self.memory.merge_many(GUILD_A, self.c, keep, [one, two], source="dm")
        assert written.batch is not None
        self.assertEqual((await self.notes())[(keep, 1)][0], "from one\n\nfrom two")
        await self.undo(written.batch)
        self.assertEqual(
            await self.notes(), {(one, 1): ("from one", None), (two, 1): ("from two", None)}
        )
        self.assertEqual(list(await self.sheets()), [two])

    async def test_a_note_too_long_to_join_loses_its_mark_again(self) -> None:
        keep, gone = await self.names()
        await self.note(keep, 1, "k" * 3000)
        await self.note(gone, 1, "g" * 3000)
        batch = await self.merge(keep, gone)
        self.assertIsNotNone((await self.notes())[(gone, 1)][1])
        await self.undo(batch)
        self.assertEqual((await self.notes())[(gone, 1)], ("g" * 3000, None))

    async def test_the_sheet_goes_back_to_the_name_it_came_from(self) -> None:
        keep, gone = await self.names()
        await self.sheet(gone)
        batch = await self.merge(keep, gone)
        self.assertEqual(list(await self.sheets()), [keep])
        await self.undo(batch)
        self.assertEqual(list(await self.sheets()), [gone])

    async def test_a_forgotten_sheet_and_a_dropped_old_one_come_back(self) -> None:
        keep, gone = await self.names()
        await self.sheet(gone, 777)  # the merged name's; the kept name has an old one nobody plays
        await self.sheet(keep, 888)
        batch = await self.merge(keep, gone)
        self.assertEqual(await self.sheets(), {keep: 777})
        await self.undo(batch)
        self.assertEqual(await self.sheets(), {gone: 777, keep: 888})

    async def test_a_sheet_refreshed_since_is_left_alone(self) -> None:
        keep, gone = await self.names()
        await self.sheet(gone)
        batch = await self.merge(keep, gone)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE character_sheets SET url = %s WHERE campaign_id = %s",
                ("https://www.dndbeyond.com/characters/654321", self.c),
            )
        await self.undo(batch)
        self.assertEqual(list(await self.sheets()), [keep])

    async def test_a_pruned_batch_leaves_no_restore_rows(self) -> None:
        keep, gone = await self.names()
        await self.note(gone, 1, "only on the monk")
        await self.merge(keep, gone)
        self.now += 400 * 86400
        await self.memory.prune_changes(GUILD_A, self.c)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM merge_restores")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)


class MergeFoldsPerspectives(MergeHelpers):
    async def perspective(self, entry: str) -> str:
        pid = new_id()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, entity_id,"
                " created_at) VALUES (%s, %s, %s, 'npc', %s, 1)",
                (GUILD_A, self.c, pid, entry),
            )
        return pid

    async def believes(self, pid: str, rel: str, seq: int, confidence: float = 0.5) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id,"
                " relation_id, confidence, status, seq, recorded_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, 'theory', %s, 1)",
                (GUILD_A, self.c, new_id(), pid, rel, confidence, seq),
            )

    async def holders(self) -> dict[str, list[tuple[str, float]]]:
        """Who holds which beliefs: perspective's entry → [(relation, confidence)]."""
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT p.entity_id, b.relation_id, b.confidence FROM memory_beliefs b"
                " JOIN memory_perspectives p ON p.id = b.perspective_id AND p.guild_id = b.guild_id"
                " AND p.campaign_id = b.campaign_id WHERE p.entity_id IS NOT NULL"
                " AND b.campaign_id = %s ORDER BY b.seq",
                (self.c,),
            )
            out: dict[str, list[tuple[str, float]]] = {}
            for r in await cur.fetchall():
                out.setdefault(r["entity_id"], []).append((r["relation_id"], r["confidence"]))
            return out

    async def rel(self) -> str:
        a, b = await self.add("Mira"), await self.add("Rook")
        written: Any = await self.relate(a, "knows", b, status=CONFIRMED)
        return str(written.value[0].id)

    async def test_beliefs_of_the_merged_character_move_to_a_perspective_of_the_kept_one(
        self,
    ) -> None:
        keep, gone = await self.names()
        rel = await self.rel()
        await self.believes(await self.perspective(gone), rel, 10, 0.25)
        batch = await self.merge(keep, gone)
        self.assertEqual(await self.holders(), {keep: [(rel, 0.25)]})
        await self.undo(batch)
        self.assertEqual(await self.holders(), {gone: [(rel, 0.25)]})

    async def test_when_both_hold_beliefs_the_newest_for_a_pair_stands(self) -> None:
        keep, gone = await self.names()
        rel = await self.rel()
        await self.believes(await self.perspective(keep), rel, 10, 0.25)
        await self.believes(await self.perspective(gone), rel, 20, 0.75)
        batch = await self.merge(keep, gone)
        self.assertEqual(await self.holders(), {keep: [(rel, 0.25), (rel, 0.75)]})
        await self.undo(batch)
        self.assertEqual(await self.holders(), {keep: [(rel, 0.25)], gone: [(rel, 0.75)]})
