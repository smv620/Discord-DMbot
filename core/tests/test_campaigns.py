import asyncio
import gzip
import json
import random
import unittest
import uuid
from typing import Any

from psycopg import errors as pg_errors
from psycopg import sql

from dmbot.campaigns import Campaign, CampaignError, CampaignStore
from dmbot.campaigns.models import fingerprint
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
        with self.assertRaisesRegex(CampaignError, "can.t be the same"):
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
        c = await self.store.remove_dm(GUILD_A, c.id, DM2)
        self.assertEqual(c.dm_user_ids, frozenset({DM}))
        with self.assertRaisesRegex(CampaignError, "Hand the campaign over first"):
            await self.store.remove_dm(GUILD_A, c.id, DM)  # the owner (#437)
        async with self.db.guild(GUILD_A) as conn:  # a campaign from before owners
            await conn.execute("UPDATE campaigns SET owner_user_id = NULL WHERE id = %s", (c.id,))
        with self.assertRaisesRegex(CampaignError, "at least one DM"):
            await self.store.remove_dm(GUILD_A, c.id, DM)

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

    async def test_an_older_plain_backup_file_restores_in_full(self) -> None:
        from dmbot.campaigns.store import decode_backup

        original = await self.make_full()
        backup = await self.store.export(GUILD_A, original.id)
        backup["version"] = 1  # made before #164: plain JSON, no compression
        plain = json.dumps(backup, indent=1).encode()
        restored = await self.store.import_backup(GUILD_B, decode_backup(plain), DM)
        again = await self.store.export(GUILD_B, restored.id)
        self.assertEqual(again["sections"], backup["sections"])

    async def test_a_plain_version_2_file_restores_too(self) -> None:
        from dmbot.campaigns.store import decode_backup

        original = await self.make_full()
        backup = await self.store.export(GUILD_A, original.id)  # version 2, never packed
        restored = await self.store.import_backup(
            GUILD_B, decode_backup(json.dumps(backup).encode()), DM
        )
        again = await self.store.export(GUILD_B, restored.id)
        self.assertEqual(again["sections"], backup["sections"])

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


class CampaignOwner(StoreTest):
    """Whose plan a campaign uses (#437): its creator, then whoever restores it."""

    async def test_the_creator_owns_it_and_a_co_dm_does_not(self) -> None:
        c = await self.make("Frostmaiden")
        self.assertEqual(c.owner_user_id, DM)
        c = await self.store.add_dm(GUILD_A, c.id, DM2)
        self.assertEqual(c.owner_user_id, DM)

    async def test_a_backup_has_no_owner_and_the_restorer_owns_the_copy(self) -> None:
        c = await self.make("Frostmaiden")
        backup = await self.store.export(GUILD_A, c.id)
        # A plan is a person's: no owner in the file, under any name.
        self.assertNotIn("owner_user_id", backup["campaign"])
        self.assertNotIn(DM, backup["campaign"].values())
        restored = await self.store.import_backup(GUILD_B, backup, 555)
        self.assertEqual(restored.owner_user_id, 555)

    async def test_a_backup_cannot_choose_its_owner(self) -> None:
        c = await self.make("Frostmaiden")
        backup = await self.store.export(GUILD_A, c.id)
        backup["campaign"]["owner_user_id"] = 999
        restored = await self.store.import_backup(GUILD_B, backup, 555)
        self.assertEqual(restored.owner_user_id, 555)

    async def test_replacing_a_campaign_keeps_its_owner(self) -> None:
        # A co-DM restoring over it doesn't take it onto their plan: that's a hand-over.
        c = await self.make("Frostmaiden")
        await self.store.add_dm(GUILD_A, c.id, DM2)
        backup = await self.store.export(GUILD_A, c.id)
        replaced = await self.store.import_backup(GUILD_A, backup, DM2, replace_campaign_id=c.id)
        self.assertEqual(replaced.owner_user_id, DM)
        with self.assertRaises(CampaignError):  # not a DM: refused, nothing changes
            await self.store.import_backup(GUILD_A, backup, 555, replace_campaign_id=c.id)
        unchanged = await self.store.get(GUILD_A, c.id)
        assert unchanged is not None
        self.assertEqual(unchanged.owner_user_id, DM)

    async def test_replacing_keeps_the_owner_a_dm_whatever_the_copy_says(self) -> None:
        c = await self.make("Frostmaiden")
        await self.store.add_dm(GUILD_A, c.id, DM2)
        backup = await self.store.export(GUILD_A, c.id)
        backup["sections"]["dms"] = [str(DM2)]  # a copy from when only DM2 ran it
        replaced = await self.store.import_backup(GUILD_A, backup, DM2, replace_campaign_id=c.id)
        self.assertEqual((replaced.owner_user_id, replaced.dm_user_ids), (DM, frozenset({DM, DM2})))

    async def test_the_owner_cannot_be_removed_as_a_dm(self) -> None:
        # Decided on #437: hand the campaign over first (test_campaign_handover).
        c = await self.make("Frostmaiden")
        await self.store.add_dm(GUILD_A, c.id, DM2)
        with self.assertRaisesRegex(CampaignError, "Hand the campaign over first"):
            await self.store.remove_dm(GUILD_A, c.id, DM)

    async def test_a_campaign_from_before_owners_has_none(self) -> None:
        c = await self.make("Frostmaiden")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE guild_id = %s AND id = %s",
                (GUILD_A, c.id),
            )
        old = await self.store.get(GUILD_A, c.id)
        assert old is not None
        self.assertIsNone(old.owner_user_id)
        self.assertEqual(old.dm_user_ids, frozenset({DM}))
        backup = await self.store.export(GUILD_A, c.id)  # replacing it gives it one
        replaced = await self.store.import_backup(GUILD_A, backup, DM, replace_campaign_id=c.id)
        self.assertEqual(replaced.owner_user_id, DM)


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
        raw = encode_backup(data)
        self.assertTrue(raw.startswith(b"\x1f\x8b"))  # compressed (#164)
        self.assertEqual(decode_backup(raw), data)
        self.assertEqual(decode_backup(json.dumps(data).encode()), data)  # older, plain

    def test_a_small_file_that_unpacks_too_big_is_refused(self) -> None:
        from dmbot.campaigns import store as store_mod

        bomb = gzip.compress(b" " * (store_mod.MAX_BACKUP_BYTES + 1))
        self.assertLess(len(bomb), 100_000)
        with self.assertRaisesRegex(CampaignError, "too big"):
            store_mod.decode_backup(bomb)
        raw = store_mod.encode_backup({"format": EXPORT_FORMAT})
        more = (raw + raw, raw + b"junk")  # a second part, or anything after the end
        for broken in (raw[:-10], b"\x1f\x8b" + b"junk" * 10, *more):
            with self.assertRaisesRegex(CampaignError, "isn't a DMbot campaign backup"):
                store_mod.decode_backup(broken)

    def test_a_big_campaign_fits_in_one_discord_file(self) -> None:
        """15 MB of campaign JSON (far more than any campaign so far) downloads as one
        file. Varied words, so it doesn't compress better than real text: 25 MB of such
        text comes to about 10 MB, which is why encode_backup refuses past that."""
        from dmbot.campaigns import store as store_mod
        from dmbot.ui.logic import FILE_MAX

        rng = random.Random(164)
        syllables = ["al", "be", "cor", "dan", "eth", "fi", "gor", "hal", "is", "jun", "ka"]
        words = ["".join(rng.choices(syllables, k=rng.randint(1, 4))) for _ in range(3000)]

        def row(n: int) -> dict[str, object]:
            return {
                "id": uuid.UUID(int=rng.getrandbits(128)).hex,
                "speaker_id": rng.randint(10**17, 10**18),
                "text": " ".join(rng.choices(words, k=rng.randint(5, 30))),
                "started_ms": n * 1500,
            }

        rows: list[dict[str, object]] = []
        data = {"format": EXPORT_FORMAT, "sections": {"transcripts": {"lines": rows}}}
        size = 0
        while size < 15 * 1024 * 1024:
            rows.append(row(len(rows)))
            size += len(json.dumps(rows[-1], separators=(",", ":"))) + 1
        raw = store_mod.encode_backup(data)
        self.assertLess(len(raw), FILE_MAX)
        self.assertEqual(store_mod.decode_backup(raw), data)  # and it comes back

    def test_the_streamed_copy_is_the_same_json_as_dumping_it_whole(self) -> None:
        import gzip

        from dmbot.campaigns import store as store_mod

        data = {
            "format": EXPORT_FORMAT,
            "notes": ["héllo", "日本", {"n": 1.5, "ok": None}],
            "sections": {"a": {"b": {"c": {"d": [1, [2, {"e": "x"}]]}}, "f": []}, "g": {}},
            "lines": [{"t": "ü" * 5}, {}, [], "s", 3, None],
            "empty": {},
            "7": True,
        }
        raw = store_mod.encode_backup(data)
        self.assertTrue(raw.startswith(b"\x1f\x8b"))
        self.assertEqual(
            gzip.decompress(raw),
            json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(),
        )
        self.assertEqual(store_mod.encode_backup(data), raw)  # and the same every time

    def test_exactly_the_limit_is_made_and_one_more_is_refused(self) -> None:
        from dmbot.campaigns import store as store_mod

        base = len('{"n":""}')
        edge = {"n": "a" * (store_mod.MAX_BACKUP_BYTES - base)}
        self.assertEqual(store_mod.decode_backup(store_mod.encode_backup(edge)), edge)
        over = {"n": "a" * (store_mod.MAX_BACKUP_BYTES - base + 1)}
        with self.assertRaises(store_mod.BackupTooBig):
            store_mod.encode_backup(over)

    def test_a_copy_too_big_to_upload_again_is_refused_too(self) -> None:
        from unittest.mock import patch

        from dmbot.campaigns import store as store_mod

        small = patch.object(store_mod, "MAX_BACKUP_BYTES", 40)  # packed bigger than the text
        with small, self.assertRaises(store_mod.BackupTooBig):
            store_mod.encode_backup({"n": "".join(chr(33 + i % 90) for i in range(35))})

    def test_no_copy_is_made_that_a_restore_would_refuse(self) -> None:
        from dmbot.campaigns import store as store_mod

        too_big = {"format": EXPORT_FORMAT, "notes": "x" * store_mod.MAX_BACKUP_BYTES}
        with self.assertRaisesRegex(store_mod.BackupTooBig, "too big for DMbot to copy"):
            store_mod.encode_backup(too_big)  # it would compress to almost nothing

    def test_a_file_exactly_at_the_limit_is_taken(self) -> None:
        from dmbot.campaigns import store as store_mod

        data = {"n": "a" * (store_mod.MAX_BACKUP_BYTES - len('{"n":""}'))}
        text = json.dumps(data, separators=(",", ":")).encode()
        self.assertEqual(len(text), store_mod.MAX_BACKUP_BYTES)  # exactly the cap
        self.assertEqual(store_mod.decode_backup(text), data)  # plain
        self.assertEqual(store_mod.decode_backup(store_mod.encode_backup(data)), data)  # packed

    def test_rejects_oversized_and_garbage(self) -> None:
        from dmbot.campaigns import store as store_mod

        with self.assertRaisesRegex(CampaignError, "too big"):
            store_mod.decode_backup(b" " * (store_mod.MAX_BACKUP_BYTES + 1))
        # The last: a number too long for Python's int limit raises a plain ValueError.
        for raw in (b"\xff\xfe", b"{not json", b"[" * 100_000, b"1" * 5000):
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


class DMScreenLevel(StoreTest):
    """How much DMbot says in the DM screen (#504)."""

    async def test_default_set_and_change(self) -> None:
        c = await self.make("A")
        self.assertEqual(c.dm_screen_level, "normal")
        c = await self.make("B", dm_screen_level="quiet")
        self.assertEqual(c.dm_screen_level, "quiet")
        c = await self.store.set_dm_screen_level(GUILD_A, c.id, "chatty")
        self.assertEqual(c.dm_screen_level, "chatty")
        with self.assertRaisesRegex(CampaignError, "how much DMbot says: quiet or normal"):
            await self.make("C", dm_screen_level="loud")
        with self.assertRaisesRegex(CampaignError, "doesn't exist in this server"):
            await self.store.set_dm_screen_level(GUILD_B, c.id, "quiet")

    async def test_backup_round_trip_and_old_backups(self) -> None:
        c = await self.make("A", dm_screen_level="quiet")
        backup = await self.store.export(GUILD_A, c.id)
        self.assertEqual(backup["campaign"]["dm_screen_level"], "quiet")
        restored = await self.store.import_backup(GUILD_B, backup, DM)
        self.assertEqual(restored.dm_screen_level, "quiet")

        old = json.loads(json.dumps(backup))
        del old["campaign"]["dm_screen_level"]  # made before the setting existed
        self.assertEqual(
            (await self.store.import_backup(GUILD_B, old, DM)).dm_screen_level, "normal"
        )

        bad = json.loads(json.dumps(backup))
        bad["campaign"]["dm_screen_level"] = "shouting"
        with self.assertRaisesRegex(CampaignError, "damaged"):
            await self.store.import_backup(GUILD_B, bad, DM)

        replaced = await self.store.import_backup(GUILD_A, backup, DM, replace_campaign_id=c.id)
        self.assertEqual(replaced.dm_screen_level, "quiet")
        c = await self.store.set_dm_screen_level(GUILD_A, c.id, "chatty")
        replaced = await self.store.import_backup(GUILD_A, old, DM, replace_campaign_id=c.id)
        self.assertEqual(replaced.dm_screen_level, "normal")  # restore keeps the backup's


class SharedConfirmations(StoreTest):
    """Who confirmed the right to use shared material, and when (CLAUDE.md, IP rule;
    #252)."""

    async def record(self, c: Campaign, text: str = "x", user: int = DM) -> None:
        await self.store.record_confirmation(GUILD_A, c.id, user, "names_list", fingerprint(text))
        self.clock.tick()

    async def test_recorded_with_a_fingerprint_only(self) -> None:
        c = await self.make("A")
        now = int(self.clock())
        await self.record(c, "Ulfgar lives.")
        (row,) = await self.store.confirmations(GUILD_A, c.id)
        self.assertEqual(
            (row["user"], row["purpose"], row["fingerprint"], row["at"], row["restored"]),
            (str(DM), "names_list", fingerprint("Ulfgar lives."), now, False),
        )
        self.assertNotIn("Ulfgar", str(row))  # never the text
        with self.assertRaises(ValueError):
            await self.store.record_confirmation(GUILD_A, c.id, DM, "anything", "0" * 64)
        with self.assertRaises(ValueError):  # a fingerprint, never the text itself
            await self.store.record_confirmation(GUILD_A, c.id, DM, "names_list", "Ulfgar")

    async def test_another_server_or_campaign_sees_none(self) -> None:
        c = await self.make("A")
        other = await self.make("B")
        await self.record(c)
        self.assertEqual(await self.store.confirmations(GUILD_A, other.id), [])
        with self.assertRaisesRegex(CampaignError, "doesn't exist in this server"):
            await self.store.confirmations(GUILD_B, c.id)
        with self.assertRaisesRegex(CampaignError, "doesn't exist in this server"):
            await self.store.record_confirmation(GUILD_B, c.id, DM, "names_list", "0" * 64)

    async def test_backups_carry_them_marked_restored(self) -> None:
        c = await self.make("A")
        await self.record(c)
        backup = await self.store.export(GUILD_A, c.id)
        (saved,) = backup["sections"]["confirmations"]
        restored = await self.store.import_backup(GUILD_B, backup, DM2)
        (row,) = await self.store.confirmations(GUILD_B, restored.id)
        self.assertEqual(row, {**saved, "restored": True})  # what the file says
        again = await self.store.export(GUILD_B, restored.id)  # and stays so, copied on
        self.assertTrue(again["sections"]["confirmations"][0]["restored"])
        # Replacing a campaign: its own confirmations give way to the backup's.
        await self.record(c, "a newer document")
        replaced = await self.store.import_backup(GUILD_A, backup, DM, replace_campaign_id=c.id)
        rows = await self.store.confirmations(GUILD_A, replaced.id)
        self.assertEqual([r["fingerprint"] for r in rows], [saved["fingerprint"]])
        old = json.loads(json.dumps(backup))
        del old["sections"]["confirmations"]  # made before #252
        restored = await self.store.import_backup(GUILD_B, old, DM)
        self.assertEqual(await self.store.confirmations(GUILD_B, restored.id), [])

    async def test_a_damaged_confirmation_is_refused(self) -> None:
        c = await self.make("A")
        await self.record(c)
        backup = await self.store.export(GUILD_A, c.id)
        bad: list[tuple[str | None, Any]] = [
            ("user", "x"), ("user", 7), ("purpose", "other"), ("fingerprint", "abc"),
            ("fingerprint", "A" * 64), ("fingerprint", "a" * 63), ("at", True), ("at", -1),
            ("id", ""), ("id", "a-b"), (None, "x"), ("twice", None),
        ]  # fmt: skip
        for field, value in bad:
            with self.subTest(field=field, value=value):
                damaged = json.loads(json.dumps(backup))
                rows = damaged["sections"]["confirmations"]
                if field == "twice":
                    rows.append(dict(rows[0]))  # a backup is complete: none quietly lost
                elif field is None:
                    rows[0] = value  # not even a row
                else:
                    rows[0][field] = value
                with self.assertRaisesRegex(CampaignError, "damaged"):
                    await self.store.import_backup(GUILD_B, damaged, DM)


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
        await self.store.record_confirmation(GUILD_A, c.id, DM, "names_list", "0" * 64)
        await self.store.delete(GUILD_A, c.id)
        self.assertIsNone(await self.store.get(GUILD_A, c.id))
        for table in ("campaign_dms", "campaign_optional_rules", "shared_confirmations"):
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
