"""The build stamp is written once for a part's session, and on a row only when it differs
(#4060). A row that differs keeps its own (a null for "none"), an old file with a stamp on every row
restores unchanged, and a default that is not a stamp refuses the whole backup. Invented stamps."""

from __future__ import annotations

import gzip
import json
from typing import Any

from dmbot.campaigns import CampaignError
from tests.test_transcript_parts import DM_B, GUILD_A, GUILD_B, _Base

ONE = "a" * 40
TWO = "b" * 40


class TheStamps(_Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.sid = await self.session(1000, 4)

    async def stamp(self, one: str | None, *, lines: dict[int, str | None] | None = None) -> None:
        """Every row of the session made by `one`, then the given lines by their own. Written as a
        restore does (`restoring`), which keeps exactly the stamp it is given."""
        async with self.db.guild(GUILD_A, restoring=True) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET made_by_commit = %s WHERE id = %s", (one, self.sid)
            )
            await conn.execute(
                "UPDATE transcript_lines SET made_by_commit = %s WHERE session_id = %s",
                (one, self.sid),
            )
            for n, value in (lines or {}).items():
                await conn.execute(
                    "UPDATE transcript_lines SET made_by_commit = %s WHERE session_id = %s"
                    " AND started_ms = %s",
                    (value, self.sid, n * 1000),
                )

    def part(self, raw: bytes) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(gzip.decompress(raw))
        return loaded

    async def stamps_in(self, guild: int) -> list[str | None]:
        rows = await self.sql(
            guild,
            "SELECT made_by_commit AS c FROM transcript_lines ORDER BY started_ms, id",
        )
        return [r["c"] for r in rows]

    async def test_all_rows_from_one_build_hold_the_stamp_once_and_restore_with_it(self) -> None:
        await self.stamp(ONE)
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        text = gzip.decompress(files[0]).decode()
        self.assertEqual(text.count(ONE), 1)  # once, not on the session and each of its lines
        restored = await self.campaigns.import_backup(GUILD_B, main, DM_B, parts=files)
        self.assertEqual(await self.stamps_in(GUILD_B), [ONE] * 4)
        self.assertTrue(restored.id)

    async def test_two_builds_and_a_row_with_none_each_come_back_as_they_were(self) -> None:
        await self.stamp(ONE, lines={1: TWO, 2: None})
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        session = self.part(files[0])["sessions"][0]
        self.assertEqual(session["made_by_commit"], ONE)  # the most common
        self.assertEqual(session["lines"][1]["made_by_commit"], TWO)  # differs: its own
        self.assertIsNone(session["lines"][2]["made_by_commit"])  # none: an explicit null
        self.assertNotIn("made_by_commit", session["lines"][0])  # the default: off the row
        await self.campaigns.import_backup(GUILD_B, main, DM_B, parts=files)
        self.assertEqual(await self.stamps_in(GUILD_B), [ONE, TWO, None, ONE])

    async def test_an_old_part_with_a_stamp_on_every_row_restores_unchanged(self) -> None:
        await self.stamp(ONE, lines={1: TWO})
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        old = self.part(files[0])
        session = old["sessions"][0]
        default = session.pop("made_by_commit")
        for row in [session["session"], *session["lines"]]:
            row.setdefault("made_by_commit", default)  # as every earlier file had it
        old["version"] = 1
        await self.campaigns.import_backup(
            GUILD_B, main, DM_B, parts=[gzip.compress(json.dumps(old).encode())]
        )
        self.assertEqual(await self.stamps_in(GUILD_B), [ONE, TWO, ONE, ONE])

    async def test_a_default_that_is_not_a_stamp_refuses_the_whole_backup(self) -> None:
        await self.stamp(ONE)
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        for bad in ("A" * 40, "abc", 7, "g" * 40):
            broken = self.part(files[0])
            broken["sessions"][0]["made_by_commit"] = bad
            with self.subTest(bad=bad), self.assertRaises(CampaignError):
                await self.campaigns.import_backup(
                    GUILD_B, main, DM_B, parts=[gzip.compress(json.dumps(broken).encode())]
                )


class TheMemoryStamps(_Base):
    async def test_the_memory_section_holds_its_common_stamp_once(self) -> None:
        from dmbot.memory.store import MemoryStore
        from tests.test_backup_ids import TheBackup

        memory = MemoryStore(self.db)
        for name in ("Cerric", "Dorn", "Mira"):
            await memory.add_entity(
                GUILD_A, self.cid, type="character", name=name, source="dm", status="confirmed"
            )
        async with self.db.guild(GUILD_A, restoring=True) as conn:
            await conn.execute("UPDATE memory_entities SET made_by_commit = %s", (ONE,))
            await conn.execute(
                "UPDATE memory_entities SET made_by_commit = %s WHERE name = 'Dorn'", (TWO,)
            )
        store = TheBackup.full_store(self)  # type: ignore[arg-type]
        main, files = await store.export_set(GUILD_A, self.cid)
        rows = json.loads(json.dumps(main["sections"]["memory"]))
        heads = [r for r in rows if r["table"] == "defaults"]
        self.assertEqual([h["made_by"] for h in heads], [ONE])
        entities = [r for r in rows if r["table"] == "entity"]
        self.assertEqual(sorted(r.get("made_by", "-") for r in entities if r["name"] != "Dorn"),
                         ["-", "-"])  # fmt: skip
        self.assertEqual([r["made_by"] for r in entities if r["name"] == "Dorn"], [TWO])
        restored = await store.import_backup(GUILD_B, main, DM_B, parts=files)
        got = await self.sql(
            GUILD_B,
            "SELECT name, made_by_commit FROM memory_entities WHERE campaign_id = %s ORDER BY name",
            restored.id,
        )
        stamps = {
            r["name"]: r["made_by_commit"] for r in got if r["name"] in ("Cerric", "Dorn", "Mira")
        }
        self.assertEqual(stamps, {"Cerric": ONE, "Dorn": TWO, "Mira": ONE})

    async def test_a_memory_default_that_is_not_a_stamp_refuses_the_whole_backup(self) -> None:
        from tests.test_backup_ids import TheBackup

        store = TheBackup.full_store(self)  # type: ignore[arg-type]
        main, files = await store.export_set(GUILD_A, self.cid)
        for bad in ("zz", 5, None):
            rows = [{"table": "defaults", "made_by": bad}, *main["sections"]["memory"]]
            broken = {**main, "sections": {**main["sections"], "memory": rows}}
            with self.subTest(bad=bad), self.assertRaises(CampaignError):
                await store.import_backup(GUILD_B, broken, DM_B, parts=files)
