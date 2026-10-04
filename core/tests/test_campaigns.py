import json
import sqlite3
import unittest
from typing import Any

from dmbot.campaigns import Campaign, CampaignError, CampaignStore
from dmbot.campaigns.store import EXPORT_FORMAT, EXPORT_VERSION
from dmbot.db import apply_migrations, connect

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


class StoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.clock = FakeClock()
        self.store = CampaignStore(":memory:", clock=self.clock)

    async def asyncTearDown(self) -> None:
        self.store.close()

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

    async def test_delete_removes_everything(self) -> None:
        c = await self.make("A")
        await self.store.set_optional_rule(GUILD_A, c.id, "xge-sleep", False)
        await self.store.delete(GUILD_A, c.id)
        self.assertIsNone(await self.store.get(GUILD_A, c.id))
        db = self.store._db
        for table in ("campaign_dms", "campaign_optional_rules"):
            count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, 0, table)


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


class SectionRegistry(StoreTest):
    async def test_custom_section_is_exported_imported_and_deleted(self) -> None:
        db = self.store._db
        db.execute("CREATE TABLE notes (campaign_id TEXT, guild_id INTEGER, body TEXT)")

        class Notes:
            name = "notes"

            def dump(self, conn: sqlite3.Connection, guild_id: int, cid: str) -> list[Any]:
                rows = conn.execute(
                    "SELECT body FROM notes WHERE guild_id = ? AND campaign_id = ?",
                    (guild_id, cid),
                )
                return [r["body"] for r in rows]

            def load(
                self, conn: sqlite3.Connection, guild_id: int, cid: str, rows: list[Any]
            ) -> None:
                for body in rows:
                    conn.execute("INSERT INTO notes VALUES (?, ?, ?)", (cid, guild_id, str(body)))

            def clear(self, conn: sqlite3.Connection, guild_id: int, cid: str) -> None:
                conn.execute(
                    "DELETE FROM notes WHERE guild_id = ? AND campaign_id = ?", (guild_id, cid)
                )

        self.store.register_section(Notes())
        with self.assertRaises(ValueError):
            self.store.register_section(Notes())

        c = await self.make("A")
        db.execute("INSERT INTO notes VALUES (?, ?, ?)", (c.id, GUILD_A, "Cerric owes Brom 5 gp"))
        backup = await self.store.export(GUILD_A, c.id)
        self.assertEqual(backup["sections"]["notes"], ["Cerric owes Brom 5 gp"])
        restored = await self.store.import_backup(GUILD_B, backup, DM)
        rows = db.execute(
            "SELECT body FROM notes WHERE guild_id = ? AND campaign_id = ?",
            (GUILD_B, restored.id),
        ).fetchall()
        self.assertEqual([r[0] for r in rows], ["Cerric owes Brom 5 gp"])
        await self.store.delete(GUILD_A, c.id)
        self.assertEqual(
            db.execute("SELECT COUNT(*) FROM notes WHERE guild_id = ?", (GUILD_A,)).fetchone()[0],
            0,
        )


class Migrations(unittest.TestCase):
    def test_runs_once_and_survives_reopen(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "dmbot.sqlite"
            store = CampaignStore(path)
            store.close()
            conn = connect(path)
            from dmbot.campaigns.store import MIGRATIONS

            self.assertEqual(apply_migrations(conn, MIGRATIONS), [])
            conn.close()
            CampaignStore(path).close()  # reopening doesn't fail on existing tables

    def test_failed_migration_leaves_no_trace(self) -> None:
        conn = connect(":memory:")
        bad = [("m1", "CREATE TABLE t (x INTEGER); CREATE TABLE t (x INTEGER)")]
        with self.assertRaises(sqlite3.OperationalError):
            apply_migrations(conn, bad)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        self.assertNotIn("t", tables)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 0)
