"""Discord ids in backup files are strings (#4060): no number above 2^53 - 1 anywhere in a dumped
backup (the campaign file and its transcript parts), a round trip gives the same ids back, a file
from before the change (numbers) restores as it always did, and a string that is not an id refuses
the whole backup. Invented ids only."""

from __future__ import annotations

import gzip
import json
import unittest
from typing import Any

from dmbot.campaigns import CampaignError
from dmbot.campaigns import parts as pt
from dmbot.campaigns.snowflakes import JS_SAFE, read_ids, unsafe_keys, write_ids
from tests.test_transcript_parts import DM_B, GUILD_A, GUILD_B, _Base

SNOWFLAKE = 1234567890123456789  # (invented) 19 digits, past what a browser holds exactly
OTHER = 987654321098765432


def unsafe_numbers(value: Any, path: str = "") -> list[str]:
    """Every integer above JS_SAFE in the value, by its path."""
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [path] if abs(value) > JS_SAFE else []
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in unsafe_numbers(v, f"{path}/{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in unsafe_numbers(v, f"{path}[{i}]")]
    return []


class ThePureRules(unittest.TestCase):
    def test_only_numbers_a_browser_cannot_hold_become_strings(self) -> None:
        got = write_ids({"user_id": SNOWFLAKE, "n": 42, "at": 1_700_000_000_000, "ok": True})
        self.assertEqual(
            got, {"user_id": str(SNOWFLAKE), "n": 42, "at": 1_700_000_000_000, "ok": True}
        )
        self.assertEqual(
            write_ids({"speakers": [SNOWFLAKE, OTHER]}), {"speakers": [str(SNOWFLAKE), str(OTHER)]}
        )

    def test_the_largest_exact_number_stays_a_number(self) -> None:
        self.assertEqual(write_ids({"user_id": JS_SAFE}), {"user_id": JS_SAFE})
        self.assertEqual(write_ids({"user_id": JS_SAFE + 1}), {"user_id": str(JS_SAFE + 1)})

    def test_a_big_number_under_a_key_that_names_no_id_stays_a_number_and_is_named(self) -> None:
        """So the file still restores: the reader would not put a string there back."""
        value = {"mystery": SNOWFLAKE, "inner": {"user_id": OTHER}}
        got = write_ids(value)
        self.assertEqual(got, {"mystery": SNOWFLAKE, "inner": {"user_id": str(OTHER)}})
        self.assertEqual(unsafe_keys(got), {"mystery"})
        self.assertEqual(unsafe_keys(write_ids({"user_id": SNOWFLAKE})), set())

    def test_a_string_under_an_id_key_comes_back_as_the_number(self) -> None:
        got = read_ids({"guild": str(SNOWFLAKE), "session": {"speakers": [str(OTHER)]}})
        self.assertEqual(got, {"guild": SNOWFLAKE, "session": {"speakers": [OTHER]}})

    def test_an_old_files_numbers_are_left_as_they_are(self) -> None:
        old = {"guild": SNOWFLAKE, "lines": [{"user_id": OTHER}]}
        self.assertEqual(read_ids(old), old)

    def test_a_string_that_is_not_an_id_is_never_turned_into_a_number(self) -> None:
        text = {"text": str(SNOWFLAKE), "name": "123", "known_by": "a" * 32, "drive_id": "123"}
        self.assertEqual(read_ids(text), text)

    def test_a_key_that_says_id_but_holds_letters_is_left_for_the_check_to_refuse(self) -> None:
        self.assertEqual(read_ids({"user_id": "12a45"}), {"user_id": "12a45"})
        self.assertEqual(read_ids({"user_id": "1" * 21}), {"user_id": "1" * 21})  # too long


class TheBackup(_Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        sid = "a" * 32
        await self.sql(
            GUILD_A,
            "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, ended_at,"
            " line_count, speakers) VALUES (%s, %s, %s, 1000, 4600, 2, %s)",
            sid, GUILD_A, self.cid, [SNOWFLAKE, OTHER],
        )  # fmt: skip
        for n, who in enumerate((SNOWFLAKE, OTHER)):
            await self.sql(
                GUILD_A,
                "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard,"
                " text) VALUES (%s, %s, %s, %s, 'raw', 'clean')",
                GUILD_A, sid, n * 1000, who,
            )  # fmt: skip
        await self.sql(
            GUILD_A,
            "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id, file_name,"
            " imported_by, imported_at) VALUES (%s, %s, %s, %s, 'notes.txt', %s, 5)",
            GUILD_A, self.cid, sid, "e" * 32, SNOWFLAKE,
        )  # fmt: skip

    def part_json(self, raw: bytes) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(gzip.decompress(raw))
        return loaded

    async def test_no_number_above_2_53_is_in_the_campaign_file_or_any_part(self) -> None:
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        self.assertEqual(unsafe_numbers(json.loads(json.dumps(main))), [])
        for raw in files:
            self.assertEqual(unsafe_numbers(self.part_json(raw)), [])
        self.assertIn(str(SNOWFLAKE), json.dumps(self.part_json(files[0])))  # (as a string)

    async def test_a_round_trip_gives_the_same_ids_back(self) -> None:
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        restored = await self.campaigns.import_backup(
            GUILD_B, json.loads(json.dumps(main)), DM_B, parts=files
        )
        got = await self.everything(GUILD_B, restored.id)
        self.assertEqual(got, await self.everything(GUILD_A, self.cid))
        line_users = [r["user_id"] for r in next(iter(got.values()))["lines"]]
        self.assertEqual(line_users, [SNOWFLAKE, OTHER])

    async def test_a_part_from_before_the_change_with_number_ids_restores_as_it_did(self) -> None:
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        old = self.part_json(files[0])
        old = json.loads(json.dumps(old))  # a copy
        old["version"] = 1
        old["guild"] = GUILD_A
        for s in old["sessions"]:
            s["session"]["speakers"] = [int(x) for x in s["session"]["speakers"]]
            for line in s["lines"]:
                line["user_id"] = int(line["user_id"])
            if s["import"]:
                s["import"]["imported_by"] = int(s["import"]["imported_by"])
        packed = gzip.compress(json.dumps(old).encode())
        restored = await self.campaigns.import_backup(GUILD_B, main, DM_B, parts=[packed])
        got = await self.everything(GUILD_B, restored.id)
        self.assertEqual(got, await self.everything(GUILD_A, self.cid))

    async def test_a_string_id_with_a_letter_in_it_refuses_the_whole_backup(self) -> None:
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        bad = self.part_json(files[0])
        bad["sessions"][0]["lines"][0]["user_id"] = "12345678901234567a9"
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(
                GUILD_B, main, DM_B, parts=[gzip.compress(json.dumps(bad).encode())]
            )

    async def test_a_newer_part_is_refused_by_the_older_version_check(self) -> None:
        main, files = await self.campaigns.export_set(GUILD_A, self.cid)
        self.assertEqual(self.part_json(files[0])["version"], pt.PART_VERSION)
        self.assertGreaterEqual(pt.PART_VERSION, 2)
        self.assertGreaterEqual(main["version"], 6)

    def full_store(self) -> Any:
        """A store with every production section registered, over the same database."""
        from collections.abc import Awaitable, Callable
        from typing import cast
        from unittest.mock import AsyncMock

        from dmbot.bot import register_backup_sections
        from dmbot.campaigns import CampaignStore

        store = CampaignStore(self.db, clock=lambda: 1_700_000_000)
        register_backup_sections(
            store,
            forget_audio=lambda g, c: None,
            consenting=cast(Callable[[int], Awaitable[frozenset[int]]], AsyncMock()),
        )
        return store

    async def test_a_snowflake_in_the_story_memory_is_a_string_and_comes_back(self) -> None:
        from dmbot.memory.store import MemoryStore

        memory = MemoryStore(self.db)
        await memory.add_entity(
            GUILD_A, self.cid, type="character", name="Cerric", played_by=SNOWFLAKE,
            source="dm", status="confirmed", role="player_character",
        )  # fmt: skip
        store = self.full_store()
        main, files = await store.export_set(GUILD_A, self.cid)
        main = json.loads(json.dumps(main))
        self.assertEqual(unsafe_numbers(main), [])  # every section of the campaign file
        self.assertIn(str(SNOWFLAKE), json.dumps(main["sections"]["memory"]))
        restored = await store.import_backup(GUILD_B, main, DM_B, parts=files)
        found = await memory.entities(GUILD_B, restored.id)
        self.assertEqual([e.played_by for e in found if e.name == "Cerric"], [SNOWFLAKE])
