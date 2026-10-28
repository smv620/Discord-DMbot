# ruff: noqa: E501  (the seed rows are SQL text, kept to a line of reading)
"""A campaign's transcripts in the extra files of its backup (#2552). Needs Postgres."""

from __future__ import annotations

import gzip
import json
from typing import Any
from unittest.mock import patch

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns import parts as pt
from dmbot.campaigns.store import BackupTooBig
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 1001, 2002
DM_A, DM_B = 11, 22
SPEAKER = 31


class _Base(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db, clock=lambda: 1_700_000_000)
        self.cid = (await self.campaigns.create(GUILD_A, "Frostmaiden", DM_A)).id

    async def sql(self, guild: int, statement: str, *args: Any) -> list[dict[str, Any]]:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(statement, args)
            return [dict(r) for r in await cur.fetchall()] if cur.description else []

    async def session(self, started: int, lines: int, *, sid: str | None = None) -> str:
        sid = sid or f"{started:032x}"
        await self.sql(
            GUILD_A,
            "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, ended_at,"
            " line_count, speakers) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            sid, GUILD_A, self.cid, started, started + 3600, lines, [SPEAKER],
        )  # fmt: skip
        for n in range(lines):
            await self.sql(
                GUILD_A,
                "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard,"
                " text) VALUES (%s, %s, %s, %s, %s, %s)",
                GUILD_A, sid, n * 1000, SPEAKER, f"raw {started} {n}", f"clean {started} {n}",
            )  # fmt: skip
        return sid

    async def everything(self, guild: int, campaign: str) -> dict[str, Any]:
        """The campaign's transcript rows without their ids, as one comparable value."""
        out: dict[str, Any] = {}
        sessions = await self.sql(
            guild,
            "SELECT id, started_at, ended_at, line_count, speakers FROM transcript_sessions"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY started_at",
            guild, campaign,
        )  # fmt: skip
        for s in sessions:
            lines = await self.sql(
                guild,
                "SELECT id, started_ms, user_id, heard, text, topic, sidebar FROM transcript_lines"
                " WHERE guild_id = %s AND session_id = %s ORDER BY id",
                guild, s["id"],
            )  # fmt: skip
            respells = await self.sql(
                guild,
                "SELECT line_id, old_text, new_text, offsets, was FROM transcript_respells"
                " WHERE guild_id = %s AND session_id = %s ORDER BY id",
                guild, s["id"],
            )  # fmt: skip
            where = {r["id"]: n for n, r in enumerate(lines)}
            out[str(s["started_at"])] = {
                "lines": [{k: v for k, v in r.items() if k != "id"} for r in lines],
                "respells": [
                    {**{k: v for k, v in r.items() if k != "line_id"}, "at": where[r["line_id"]]}
                    for r in respells
                ],
                "speakers": s["speakers"],
            }
        return out


class TheSet(_Base):
    async def seed(self) -> None:
        one = await self.session(1000, 3)
        two = await self.session(5000, 2)
        lines = await self.sql(
            GUILD_A, "SELECT id FROM transcript_lines WHERE session_id = %s ORDER BY id", one
        )
        await self.sql(
            GUILD_A,
            "INSERT INTO transcript_respells (guild_id, campaign_id, batch, session_id, line_id,"
            " user_id, old_text, new_text, offsets, was, made_at)"
            " VALUES (%s, %s, 1, %s, %s, %s, 'clene', 'clean', %s, %s, 5)",
            GUILD_A, self.cid, one, lines[1]["id"], DM_A, [0], ["clene"],
        )  # fmt: skip
        await self.sql(
            GUILD_A,
            "INSERT INTO transcript_suggestions (guild_id, campaign_id, session_id, started_ms,"
            " user_id, suggested, base_text, created_at) VALUES (%s, %s, %s, 1000, %s, 'fix', 'x', 5)",
            GUILD_A, self.cid, one, SPEAKER,
        )  # fmt: skip
        await self.sql(
            GUILD_A,
            "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id, file_name,"
            " imported_by, imported_at) VALUES (%s, %s, %s, %s, 'notes.txt', %s, 5)",
            GUILD_A, self.cid, two, "e" * 32, DM_A,
        )  # fmt: skip
        await self.sql(
            GUILD_A,
            "UPDATE transcript_lines SET sidebar = 'question', sidebar_ref = 'r', topic = 'off_topic'"
            " WHERE session_id = %s AND started_ms = 0",
            two,
        )

    async def test_a_backup_is_a_main_file_that_names_its_parts_plus_the_parts(self) -> None:
        await self.seed()
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        self.assertEqual(main["parts"]["count"], len(files))
        self.assertEqual(len(files), 1)
        self.assertNotIn("transcript", json.dumps(main["sections"]).replace("transcripts", ""))
        self.assertNotIn("raw 1000", json.dumps(main))  # the lines are in the extra files only
        # the plain export is the main file only, as before
        self.assertNotIn("parts", await self.campaigns.export(GUILD_A, self.cid))

    async def test_a_campaign_with_no_transcripts_is_one_file(self) -> None:
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        self.assertEqual((files, "parts" in main), ([], False))

    async def test_a_restore_tells_how_far_through_the_transcripts_files_it_is(self) -> None:
        await self.seed()
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        told: list[tuple[int, int]] = []
        await self.campaigns.import_backup(
            GUILD_B,
            main,
            DM_B,
            parts=files,
            progress=lambda done, total: told.append((done, total)),
        )
        total = len(files)
        self.assertEqual(told, [(0, total), *[(n, total) for n in range(1, total + 1)]])
        # a copy with no transcripts files tells nothing (there is nothing to count)
        plain, _none = await self.campaigns.export_set(GUILD_A, self.cid)
        told.clear()
        await self.campaigns.import_backup(
            GUILD_B, plain, DM_B, parts=[], progress=lambda d, t: told.append((d, t))
        )
        self.assertEqual(told, [])

    async def test_the_set_restores_into_another_server_row_for_row_with_new_ids(self) -> None:
        await self.seed()
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        restored = await self.campaigns.import_backup(GUILD_B, main, DM_B, parts=files)
        got = await self.everything(GUILD_B, restored.id)
        self.assertEqual(got, await self.everything(GUILD_A, self.cid))
        self.assertEqual(len(got), 2)
        old_ids = {r["id"] for r in await self.sql(GUILD_A, "SELECT id FROM transcript_sessions")}
        new_ids = {r["id"] for r in await self.sql(GUILD_B, "SELECT id FROM transcript_sessions")}
        self.assertFalse(old_ids & new_ids)  # made again: restoring next to the original is safe
        (imp,) = await self.sql(GUILD_B, "SELECT file_name FROM session_imports")
        self.assertEqual(imp["file_name"], "notes.txt")
        (sug,) = await self.sql(GUILD_B, "SELECT suggested FROM transcript_suggestions")
        self.assertEqual(sug["suggested"], "fix")
        (side,) = await self.sql(
            GUILD_B,
            "SELECT sidebar, sidebar_ref, topic FROM transcript_lines WHERE sidebar IS NOT NULL",
        )
        self.assertEqual(
            (side["sidebar"], side["sidebar_ref"], side["topic"]), ("question", "r", "off_topic")
        )

    async def test_a_restore_without_the_parts_is_a_campaign_without_transcripts(self) -> None:
        await self.seed()
        main, _ = await self.campaigns.export_set(GUILD_A, self.cid)
        restored = await self.campaigns.import_backup(GUILD_B, main, DM_B)
        self.assertEqual(await self.everything(GUILD_B, restored.id), {})

    async def test_a_restore_over_a_campaign_replaces_its_transcripts_with_the_copys(self) -> None:
        await self.seed()
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        await self.session(9000, 1)  # a session the copy does not have
        await self.campaigns.import_backup(
            GUILD_A, main, DM_A, replace_campaign_id=self.cid, parts=files
        )
        self.assertEqual(sorted(await self.everything(GUILD_A, self.cid)), ["1000", "5000"])

    async def test_a_restore_over_a_campaign_without_parts_keeps_its_transcripts(self) -> None:
        await self.seed()
        main, _ = await self.campaigns.export_set(GUILD_A, self.cid)
        await self.campaigns.import_backup(GUILD_A, main, DM_A, replace_campaign_id=self.cid)
        self.assertEqual(sorted(await self.everything(GUILD_A, self.cid)), ["1000", "5000"])

    async def test_many_sessions_make_several_small_parts_and_all_come_back(self) -> None:
        for n in range(6):
            await self.session(1000 + n * 100, 5)
        with (
            patch.object(pt, "PART_TEXT_BYTES", 2500),
            patch("dmbot.campaigns.store.PART_TEXT_BYTES", 2500),
        ):
            main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        self.assertGreater(len(files), 1)
        self.assertEqual(main["parts"]["count"], len(files))
        restored = await self.campaigns.import_backup(
            GUILD_B,
            main,
            DM_B,
            parts=list(reversed(files)),  # any order: the numbers decide
        )
        self.assertEqual(
            await self.everything(GUILD_B, restored.id), await self.everything(GUILD_A, self.cid)
        )

    async def test_a_session_too_big_for_a_part_refuses_the_copy_whole(self) -> None:
        await self.session(1000, 5)
        with patch("dmbot.campaigns.store.PART_TEXT_BYTES", 100), self.assertRaises(BackupTooBig):
            await self.campaigns.export_set(GUILD_A, self.cid)

    async def test_missing_repeated_or_foreign_parts_are_refused_and_nothing_is_written(
        self,
    ) -> None:
        for n in range(4):
            await self.session(1000 + n * 100, 5)
        with patch("dmbot.campaigns.store.PART_TEXT_BYTES", 3500):
            main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        self.assertGreater(len(files), 2)
        _, other_files = await self.campaigns.export_set(GUILD_A, self.cid)
        for bad in (files[:-1], [*files, files[0]], [files[0], files[0], *files[2:]], other_files):
            with self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD_B, main, DM_B, parts=bad)
        self.assertEqual(len(await self.campaigns.list_campaigns(GUILD_B)), 0)
        with self.assertRaises(CampaignError):  # parts for a copy that names none
            await self.campaigns.import_backup(GUILD_B, {**main, "parts": None}, DM_B, parts=files)

    async def test_a_damaged_part_is_refused_whole(self) -> None:
        await self.seed()
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        good = json.loads(gzip.decompress(files[0]))

        def pack(doc: object) -> bytes:
            return gzip.compress(json.dumps(doc).encode())

        def with_(change: Any) -> bytes:
            doc = json.loads(json.dumps(good))
            change(doc)
            return pack(doc)

        damages = (
            b"not gzip at all",
            gzip.compress(b"[1, 2"),
            pack({"format": "other"}),
            with_(lambda d: d.update(sessions="no")),
            with_(lambda d: d["sessions"][0].pop("lines")),
            with_(lambda d: d["sessions"][0].update(extra=1)),
            with_(lambda d: d["sessions"][0]["lines"][0].update(nonsense=1)),  # not a column
            with_(lambda d: d["sessions"][0]["lines"][0].update(id=7)),  # ids are made here
            with_(lambda d: d["sessions"][0]["lines"][0].update(started_ms="soon")),  # wrong type
            with_(lambda d: d["sessions"][0]["lines"][0].update(topic="gossip")),  # a CHECK
            with_(lambda d: d["sessions"][0]["respells"][0].update(line=99)),
            with_(lambda d: d["sessions"][0]["session"].update(guild_id=5)),  # a scope column
        )
        for damaged in damages:
            with self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD_B, main, DM_B, parts=[damaged])
        self.assertEqual(len(await self.campaigns.list_campaigns(GUILD_B)), 0)

    async def test_an_older_copy_with_fewer_columns_restores_into_the_newer_table(self) -> None:
        await self.seed()
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        doc = json.loads(gzip.decompress(files[0]))
        for s in doc["sessions"]:
            for line in s["lines"]:
                line.pop("typed")  # a column that an older DMbot did not have: its default applies
                line.pop("topic")
        restored = await self.campaigns.import_backup(
            GUILD_B, main, DM_B, parts=[gzip.compress(json.dumps(doc).encode())]
        )
        rows = await self.sql(
            GUILD_B,
            "SELECT DISTINCT typed, topic FROM transcript_lines WHERE guild_id = %s",
            GUILD_B,
        )
        self.assertEqual([(r["typed"], r["topic"]) for r in rows], [(False, "game")])
        self.assertTrue(restored.id)

    async def test_the_lines_are_unabridged_raw_words_sidebar_and_all(self) -> None:
        await self.seed()
        _, files = await self.campaigns.export_set(GUILD_A, self.cid)
        text = gzip.decompress(files[0]).decode()
        self.assertIn("raw 1000 0", text)  # the raw words, not only the cleaned ones
        self.assertIn('"sidebar": "question"', text)
