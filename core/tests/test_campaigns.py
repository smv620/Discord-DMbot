import asyncio
import json
import unittest
from typing import Any

from psycopg import errors as pg_errors
from psycopg import sql

from dmbot.campaigns import Campaign, CampaignError, CampaignStore
from dmbot.campaigns.store import EXPORT_FORMAT, EXPORT_VERSION
from dmbot.db import Conn
from tests.pg import DatabaseTest

GUILD_A = 111
GUILD_B = 222
DM = 7
DM2 = 8


class FakeClock:
    def __init__(self, start: float = 1_700_000_000) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float = 60) -> None:
        self.now += seconds


class StoreTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.clock = FakeClock()
        self.store = CampaignStore(self.db, clock=self.clock)

    async def make(self, name: str, guild: int = GUILD_A, **kw: Any) -> Campaign:
        campaign = await self.store.create(guild, name, DM, **kw)
        self.clock.tick()
        return campaign


class CreateAndList(StoreTest):
    async def test_defaults(self) -> None:
        c = await self.make("  Rime of the   Frostmaiden ")
        self.assertEqual(c.name, "Rime of the Frostmaiden")
        self.assertEqual((c.target_ruleset, c.fallback_ruleset), ("2024", "2014"))
        self.assertTrue(c.optional_rules_default)
        self.assertEqual(c.dm_user_ids, frozenset({DM}))
        self.assertIsNone(c.last_played_at)
        self.assertIsNone(c.dm_screen_channel_id)

    async def test_names_must_be_unique_per_server_ignoring_case(self) -> None:
        await self.make("Frostmaiden")
        with self.assertRaisesRegex(CampaignError, "already has a campaign"):
            await self.make("frostmaiden")
        await self.make("Frostmaiden", guild=GUILD_B)  # other servers may reuse it

    async def test_name_validation(self) -> None:
        with self.assertRaisesRegex(CampaignError, "give the campaign a name"):
            await self.make("   ")
        with self.assertRaisesRegex(CampaignError, "too long"):
            await self.make("x" * 81)

    async def test_ruleset_validation(self) -> None:
        with self.assertRaises(CampaignError):
            await self.make("A", target_ruleset="5e-homebrew")
        with self.assertRaisesRegex(CampaignError, "different"):
            await self.make("B", target_ruleset="2024", fallback_ruleset="2024")
        c = await self.make("C", target_ruleset="2014", fallback_ruleset="none")
        self.assertEqual(c.fallback_ruleset, "none")

    async def test_list_most_recently_used_first(self) -> None:
        a = await self.make("A")
        await self.make("B")
        await self.make("C")
        self.assertEqual(
            [x.name for x in await self.store.list_campaigns(GUILD_A)], ["C", "B", "A"]
        )
        await self.store.mark_played(GUILD_A, a.id)
        names = [x.name for x in await self.store.list_campaigns(GUILD_A)]
        self.assertEqual(names, ["A", "C", "B"])
        last = await self.store.last_used(GUILD_A)
        assert last is not None
        self.assertEqual(last.id, a.id)

    async def test_last_used_empty_server(self) -> None:
        self.assertIsNone(await self.store.last_used(GUILD_A))


class Isolation(StoreTest):
    """No server can see or change another server's campaigns."""

    async def test_other_server_cannot_read(self) -> None:
        c = await self.make("Secret", guild=GUILD_A)
        self.assertIsNone(await self.store.get(GUILD_B, c.id))
        self.assertEqual(await self.store.list_campaigns(GUILD_B), [])

    async def test_other_server_cannot_change_or_delete(self) -> None:
        c = await self.make("Secret", guild=GUILD_A)
        attempts = [
            self.store.rename(GUILD_B, c.id, "Stolen"),
            self.store.set_rulesets(GUILD_B, c.id, "2014", "none"),
            self.store.add_dm(GUILD_B, c.id, 999),
            self.store.remove_dm(GUILD_B, c.id, DM),
            self.store.set_dm_screen(GUILD_B, c.id, 5),
            self.store.set_last_voice_channel(GUILD_B, c.id, 5),
            self.store.mark_played(GUILD_B, c.id),
            self.store.set_optional_rule(GUILD_B, c.id, "xge-sleep", False),
            self.store.optional_rule_overrides(GUILD_B, c.id),
            self.store.export(GUILD_B, c.id),
            self.store.delete(GUILD_B, c.id),
        ]
        for attempt in attempts:
            with self.assertRaisesRegex(CampaignError, "doesn't exist in this server"):
                await attempt
        unchanged = await self.store.get(GUILD_A, c.id)
        self.assertEqual(unchanged, c)

    async def test_import_cannot_replace_another_servers_campaign(self) -> None:
        theirs = await self.make("Theirs", guild=GUILD_B)
        mine = await self.make("Mine", guild=GUILD_A)
        backup = await self.store.export(GUILD_A, mine.id)
        with self.assertRaisesRegex(CampaignError, "doesn't exist in this server"):
            await self.store.import_backup(GUILD_A, backup, DM, replace_campaign_id=theirs.id)
        self.assertEqual(await self.store.get(GUILD_B, theirs.id), theirs)

    async def test_campaigns_in_one_server_keep_separate_settings(self) -> None:
        a = await self.make("A")
        b = await self.make("B")
        await self.store.set_optional_rule(GUILD_A, a.id, "xge-sleep", False)
        await self.store.set_dm_screen(GUILD_A, a.id, 42)
        self.assertEqual(await self.store.optional_rule_overrides(GUILD_A, b.id), {})
        b_now = await self.store.get(GUILD_A, b.id)
        assert b_now is not None
        self.assertIsNone(b_now.dm_screen_channel_id)


class Settings(StoreTest):
    async def test_rename(self) -> None:
        a = await self.make("A")
        await self.make("B")
        self.assertEqual((await self.store.rename(GUILD_A, a.id, "  Alpha ")).name, "Alpha")
        with self.assertRaisesRegex(CampaignError, "already has"):
            await self.store.rename(GUILD_A, a.id, "b")
        self.assertEqual((await self.store.rename(GUILD_A, a.id, "ALPHA")).name, "ALPHA")

    async def test_dms(self) -> None:
        c = await self.make("A")
        c = await self.store.add_dm(GUILD_A, c.id, DM2)
        self.assertEqual(c.dm_user_ids, frozenset({DM, DM2}))
        c = await self.store.remove_dm(GUILD_A, c.id, DM)
        self.assertEqual(c.dm_user_ids, frozenset({DM2}))
        with self.assertRaisesRegex(CampaignError, "at least one DM"):
            await self.store.remove_dm(GUILD_A, c.id, DM2)

    async def test_channels_and_played(self) -> None:
        c = await self.make("A")
        c = await self.store.set_dm_screen(GUILD_A, c.id, 100)
        c = await self.store.set_last_voice_channel(GUILD_A, c.id, 200)
        c = await self.store.mark_played(GUILD_A, c.id)
        self.assertEqual((c.dm_screen_channel_id, c.last_voice_channel_id), (100, 200))
        self.assertEqual(c.last_played_at, int(self.clock.now))

    async def test_optional_rules(self) -> None:
        c = await self.make("A")
        await self.store.set_optional_rule(GUILD_A, c.id, "xge-sleep", False)
        await self.store.set_optional_rule(GUILD_A, c.id, "tce-custom-origin", True)
        await self.store.set_optional_rule(GUILD_A, c.id, "xge-sleep", True)
        overrides = await self.store.optional_rule_overrides(GUILD_A, c.id)
        self.assertEqual(overrides, {"xge-sleep": True, "tce-custom-origin": True})
        c = await self.store.set_optional_rules_default(GUILD_A, c.id, False)
        self.assertFalse(c.optional_rules_default)
        with self.assertRaises(ValueError):
            await self.store.set_optional_rule(GUILD_A, c.id, "bad id; drop", True)


class ExportImport(StoreTest):
    async def make_full(self) -> Campaign:
        c = await self.make("Frostmaiden", target_ruleset="2024", fallback_ruleset="2014")
        await self.store.add_dm(GUILD_A, c.id, DM2)
        await self.store.set_optional_rule(GUILD_A, c.id, "xge-sleep", False)
        await self.store.set_dm_screen(GUILD_A, c.id, 100)
        return await self.store.mark_played(GUILD_A, c.id)

    async def test_round_trip_into_another_server(self) -> None:
        original = await self.make_full()
        backup = await self.store.export(GUILD_A, original.id)
        backup = json.loads(json.dumps(backup))  # must survive a real file
        self.assertEqual((backup["format"], backup["version"]), (EXPORT_FORMAT, EXPORT_VERSION))

        restored = await self.store.import_backup(GUILD_B, backup, 555)
        self.assertNotEqual(restored.id, original.id)
        self.assertEqual(restored.guild_id, GUILD_B)
        self.assertEqual(restored.name, original.name)
        self.assertEqual(restored.last_played_at, original.last_played_at)
        self.assertEqual(restored.created_at, original.created_at)
        self.assertEqual(restored.dm_user_ids, frozenset({DM, DM2, 555}))
        self.assertIsNone(restored.dm_screen_channel_id)  # channels don't travel
        self.assertEqual(
            await self.store.optional_rule_overrides(GUILD_B, restored.id), {"xge-sleep": False}
        )
        # The original is untouched and still private to its server.
        self.assertEqual(await self.store.get(GUILD_A, original.id), original)
        self.assertIsNone(await self.store.get(GUILD_A, restored.id))

    async def test_restore_as_new_in_same_server_gets_a_free_name(self) -> None:
        original = await self.make_full()
        backup = await self.store.export(GUILD_A, original.id)
        r1 = await self.store.import_backup(GUILD_A, backup, DM)
        r2 = await self.store.import_backup(GUILD_A, backup, DM)
        self.assertEqual(r1.name, "Frostmaiden (restored)")
        self.assertEqual(r2.name, "Frostmaiden (restored 2)")

    async def test_replace_existing(self) -> None:
        original = await self.make_full()
        backup = await self.store.export(GUILD_A, original.id)
        await self.store.set_optional_rule(GUILD_A, original.id, "tce-extra", True)
        await self.store.set_rulesets(GUILD_A, original.id, "2014", "none")
        replaced = await self.store.import_backup(
            GUILD_A, backup, DM, replace_campaign_id=original.id
        )
        self.assertEqual(replaced.id, original.id)
        self.assertEqual((replaced.target_ruleset, replaced.fallback_ruleset), ("2024", "2014"))
        self.assertEqual(
            await self.store.optional_rule_overrides(GUILD_A, original.id), {"xge-sleep": False}
        )
        self.assertEqual(replaced.dm_screen_channel_id, 100)  # this server's channel kept

    async def test_rejects_bad_backups_without_changing_anything(self) -> None:
        original = await self.make_full()
        good = await self.store.export(GUILD_A, original.id)

        def variant(**changes: Any) -> dict[str, Any]:
            data: dict[str, Any] = json.loads(json.dumps(good))
            for path, value in changes.items():
                target: Any = data
                *parents, leaf = path.split("__")
                for p in parents:
                    target = target[p]
                target[leaf] = value
            return data

        cases: list[tuple[object, str]] = [
            ("not a dict", "damaged"),
            ({"format": "something-else"}, "damaged"),
            (variant(version=EXPORT_VERSION + 1), "newer DMbot"),
            (variant(version="1"), "damaged"),
            (variant(campaign__name=5), "damaged"),
            (variant(campaign__name=""), "give the campaign a name"),
            (variant(campaign__target_ruleset="9999"), "main ruleset"),
            (variant(campaign__created_at=-1), "damaged"),
            (variant(campaign__optional_rules_default="yes"), "damaged"),
            (variant(sections__mystery=[]), "newer DMbot"),
            (variant(sections__dms=["not-an-id"]), "damaged"),
            (variant(sections__dms="7"), "damaged"),
            (variant(sections__optional_rules=[{"rule": "x;y", "enabled": True}]), "damaged"),
        ]
        before = await self.store.list_campaigns(GUILD_B)
        for data, message in cases:
            with (
                self.subTest(message=message, data=str(data)[:60]),
                self.assertRaisesRegex(CampaignError, message),
            ):
                await self.store.import_backup(GUILD_B, data, DM)
        self.assertEqual(await self.store.list_campaigns(GUILD_B), before)

    async def test_failed_replace_rolls_back(self) -> None:
        original = await self.make_full()
        backup = await self.store.export(GUILD_A, original.id)
        backup["sections"]["optional_rules"].append({"rule": "ok-rule", "enabled": True})
        backup["sections"]["dms"].append("broken")  # fails during load, after clears
        with self.assertRaises(CampaignError):
            await self.store.import_backup(GUILD_A, backup, DM, replace_campaign_id=original.id)
        after = await self.store.get(GUILD_A, original.id)
        self.assertEqual(after, original)
        self.assertEqual(
            await self.store.optional_rule_overrides(GUILD_A, original.id), {"xge-sleep": False}
        )


class ReviewHardening(StoreTest):
    """Cases from the reviewer and perf-qa agents' review of the first version."""

    async def test_bad_ids_and_timestamps_get_friendly_errors(self) -> None:
        c = await self.make("A")
        good = await self.store.export(GUILD_A, c.id)
        cases: list[tuple[str, Any, Any]] = [
            ("dms", ["²"], None),  # unicode digit: isdigit() is True, int() fails
            ("dms", [str(2**63)], None),  # doesn't fit SQLite INTEGER
            ("dms", ["0"], None),
            ("created_at", None, 2**63),
            ("last_played_at", None, 2**70),
        ]
        for field, dms, ts in cases:
            data = json.loads(json.dumps(good))
            if dms is not None:
                data["sections"][field] = dms
            else:
                data["campaign"][field] = ts
            with self.subTest(field=field), self.assertRaisesRegex(CampaignError, "damaged"):
                await self.store.import_backup(GUILD_B, data, DM)

    async def test_only_a_dm_can_replace_a_campaign(self) -> None:
        c = await self.make("A")
        backup = await self.store.export(GUILD_A, c.id)
        with self.assertRaisesRegex(CampaignError, "Only this campaign's DM"):
            await self.store.import_backup(GUILD_A, backup, 999, replace_campaign_id=c.id)

    async def test_long_names_get_a_free_restored_name(self) -> None:
        c = await self.make("x" * 80)
        backup = await self.store.export(GUILD_A, c.id)
        r = await self.store.import_backup(GUILD_A, backup, DM)
        self.assertTrue(r.name.endswith(" (restored)"))
        self.assertLessEqual(len(r.name), 80)


class BackupFiles(unittest.TestCase):
    def test_encode_decode(self) -> None:
        from dmbot.campaigns.store import decode_backup, encode_backup

        data = {"format": EXPORT_FORMAT, "campaign": {"name": "Ærth & Frost"}}
        self.assertEqual(decode_backup(encode_backup(data)), data)

    def test_rejects_oversized_and_garbage(self) -> None:
        from dmbot.campaigns import store as store_mod

        with self.assertRaisesRegex(CampaignError, "too big"):
            store_mod.decode_backup(b" " * (store_mod.MAX_BACKUP_BYTES + 1))
        for raw in (b"\xff\xfe", b"{not json", b"[" * 100_000):
            with self.assertRaisesRegex(CampaignError, "isn't a DMbot campaign backup"):
                store_mod.decode_backup(raw)


class DMScreenVisibility(StoreTest):
    """Per-campaign DM-screen visibility (PLAN.md; requested on PR #61 for #30)."""

    async def test_default_is_peek(self) -> None:
        c = await self.make("A")
        self.assertEqual(c.dm_screen_visibility, "peek")

    async def test_set_at_create_and_change(self) -> None:
        c = await self.make("A", dm_screen_visibility="private")
        self.assertEqual(c.dm_screen_visibility, "private")
        c = await self.store.set_dm_screen_visibility(GUILD_A, c.id, "open")
        self.assertEqual(c.dm_screen_visibility, "open")

    async def test_rejects_unknown_values(self) -> None:
        with self.assertRaisesRegex(CampaignError, "who can see the DM screen"):
            await self.make("A", dm_screen_visibility="secret")
        c = await self.make("B")
        with self.assertRaisesRegex(CampaignError, "who can see the DM screen"):
            await self.store.set_dm_screen_visibility(GUILD_A, c.id, "everyone")

    async def test_other_server_cannot_change_it(self) -> None:
        c = await self.make("A", guild=GUILD_A)
        with self.assertRaisesRegex(CampaignError, "doesn't exist in this server"):
            await self.store.set_dm_screen_visibility(GUILD_B, c.id, "open")

    async def test_backup_round_trip_and_old_backups(self) -> None:
        c = await self.make("A", dm_screen_visibility="private")
        backup = await self.store.export(GUILD_A, c.id)
        self.assertEqual(backup["campaign"]["dm_screen_visibility"], "private")
        restored = await self.store.import_backup(GUILD_B, backup, DM)
        self.assertEqual(restored.dm_screen_visibility, "private")

        old = json.loads(json.dumps(backup))
        del old["campaign"]["dm_screen_visibility"]  # made before the setting existed
        self.assertEqual(
            (await self.store.import_backup(GUILD_B, old, DM)).dm_screen_visibility, "peek"
        )

        bad = json.loads(json.dumps(backup))
        bad["campaign"]["dm_screen_visibility"] = "spoilers-for-all"
        with self.assertRaisesRegex(CampaignError, "damaged"):
            await self.store.import_backup(GUILD_B, bad, DM)

        replaced = await self.store.import_backup(GUILD_A, old, DM, replace_campaign_id=c.id)
        self.assertEqual(replaced.dm_screen_visibility, "peek")


class DeleteAndChildRows(StoreTest):
    async def count(self, guild: int, table: str) -> int:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(
                sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))
            )
            row = await cur.fetchone()
            assert row is not None
            return int(row["n"])

    async def test_delete_removes_everything(self) -> None:
        c = await self.make("A")
        await self.store.set_optional_rule(GUILD_A, c.id, "xge-sleep", False)
        await self.store.delete(GUILD_A, c.id)
        self.assertIsNone(await self.store.get(GUILD_A, c.id))
        for table in ("campaign_dms", "campaign_optional_rules"):
            self.assertEqual(await self.count(GUILD_A, table), 0, table)

    async def test_child_rows_cannot_point_at_another_servers_campaign(self) -> None:
        c = await self.make("A", guild=GUILD_A)
        with self.assertRaises(pg_errors.ForeignKeyViolation):
            async with self.db.guild(GUILD_B) as conn:
                await conn.execute(
                    "INSERT INTO campaign_dms (campaign_id, guild_id, user_id) VALUES (%s, %s, 1)",
                    (c.id, GUILD_B),
                )

    async def test_section_bug_becomes_friendly_error_and_rolls_back(self) -> None:
        class Broken:
            name = "broken"

            async def dump(self, conn: Conn, guild_id: int, cid: str) -> list[Any]:
                return [{"x": 1}]

            async def load(self, conn: Conn, guild_id: int, cid: str, rows: list[Any]) -> None:
                rows[0]["missing"]  # KeyError: a section that forgot to validate

            async def clear(self, conn: Conn, guild_id: int, cid: str) -> None:
                return None

        self.store.register_section(Broken())
        c = await self.make("A")
        backup = await self.store.export(GUILD_A, c.id)
        with self.assertRaisesRegex(CampaignError, "damaged"):
            await self.store.import_backup(GUILD_B, backup, DM)
        self.assertEqual(await self.store.list_campaigns(GUILD_B), [])


class SectionRegistry(StoreTest):
    async def test_custom_section_is_exported_imported_and_deleted(self) -> None:
        async with self.db.unscoped() as conn:
            await conn.execute("CREATE TABLE notes (campaign_id TEXT, guild_id BIGINT, body TEXT)")

        class Notes:
            name = "notes"

            async def dump(self, conn: Conn, guild_id: int, cid: str) -> list[Any]:
                cur = await conn.execute(
                    "SELECT body FROM notes WHERE guild_id = %s AND campaign_id = %s",
                    (guild_id, cid),
                )
                return [r["body"] for r in await cur.fetchall()]

            async def load(self, conn: Conn, guild_id: int, cid: str, rows: list[Any]) -> None:
                for body in rows:
                    await conn.execute(
                        "INSERT INTO notes VALUES (%s, %s, %s)", (cid, guild_id, str(body))
                    )

            async def clear(self, conn: Conn, guild_id: int, cid: str) -> None:
                await conn.execute(
                    "DELETE FROM notes WHERE guild_id = %s AND campaign_id = %s", (guild_id, cid)
                )

        self.store.register_section(Notes())
        with self.assertRaises(ValueError):
            self.store.register_section(Notes())

        c = await self.make("A")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO notes VALUES (%s, %s, %s)", (c.id, GUILD_A, "Cerric owes Brom 5 gp")
            )
        backup = await self.store.export(GUILD_A, c.id)
        self.assertEqual(backup["sections"]["notes"], ["Cerric owes Brom 5 gp"])
        restored = await self.store.import_backup(GUILD_B, backup, DM)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute(
                "SELECT body FROM notes WHERE campaign_id = %s", (restored.id,)
            )
            self.assertEqual([r["body"] for r in await cur.fetchall()], ["Cerric owes Brom 5 gp"])
        await self.store.delete(GUILD_A, c.id)
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM notes WHERE guild_id = %s", (GUILD_A,)
            )
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)


class Concurrency(StoreTest):
    async def test_cancelled_call_leaves_the_store_usable(self) -> None:
        c = await self.make("A")

        async def slow() -> None:
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute("SELECT pg_sleep(5)")

        task = asyncio.create_task(slow())
        await asyncio.sleep(0.2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(
            (await self.store.rename(GUILD_A, c.id, "Still works")).name, "Still works"
        )

    async def test_error_rolls_back_and_store_stays_usable(self) -> None:
        with self.assertRaises(RuntimeError):
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "INSERT INTO consent (guild_id, user_id, granted_at) VALUES (%s, 1, 0)",
                    (GUILD_A,),
                )
                raise RuntimeError("boom")
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM consent")
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)
        await self.make("Still works")

    async def test_same_name_created_at_once_gives_one_campaign(self) -> None:
        results = await asyncio.gather(
            *(self.store.create(GUILD_A, "Frost", DM) for _ in range(5)),
            return_exceptions=True,
        )
        made = [r for r in results if isinstance(r, Campaign)]
        refused = [r for r in results if isinstance(r, CampaignError)]
        self.assertEqual((len(made), len(refused)), (1, 4))

    async def test_last_dm_cannot_be_removed_by_two_removals_at_once(self) -> None:
        c = await self.make("A")
        c = await self.store.add_dm(GUILD_A, c.id, DM2)
        results = await asyncio.gather(
            self.store.remove_dm(GUILD_A, c.id, DM),
            self.store.remove_dm(GUILD_A, c.id, DM2),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(r, CampaignError) for r in results), 1)
        after = await self.store.get(GUILD_A, c.id)
        assert after is not None
        self.assertEqual(len(after.dm_user_ids), 1)
