"""Turn each homebrew entry on or off for the table (#1629): the entry is kept, but DMbot uses it
nowhere (lookups, alerts, the sidebar, the players' card, the spotter, a published pack). The
file says `off` and reads it back; the backup carries it; a pack update never turns a copy on.
The store tests need Postgres."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.rules import homebrew_file as hf
from dmbot.rules import pack_content
from dmbot.rules.homebrew import (
    FileEntry,
    Homebrew,
    HomebrewError,
    HomebrewSection,
    HomebrewStore,
)
from dmbot.rules.homebrew_packs import HomebrewReader
from dmbot.ui import homebrew as ui
from tests.pg import DatabaseTest

GUILD, DM, PLAYER = 111, 7, 8


class APack(unittest.TestCase):
    def test_an_off_entry_is_not_offered_in_a_pack(self) -> None:
        def entry(name: str, enabled: bool) -> Homebrew:
            return Homebrew(
                id=hash(name) % 1000, campaign_id="c", kind="item", name=name, text="x",
                summary=None, created_by=7, created_at=1, updated_at=1,
                enabled=enabled,
            )  # fmt: skip

        content, _ = pack_content.content_of([entry("On", True), entry("Off", False)])
        self.assertEqual([e["name"] for e in content["entries"]], ["On"])


class Stored(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frost", DM)
        self.store = HomebrewStore(self.db)

    async def add(self, name: str, kind: str = "monster", **kw: Any) -> Homebrew:
        return await self.store.add(
            GUILD, self.campaign.id, DM, kind=kind, name=name, text="x", **kw
        )

    async def test_entries_are_on_by_default(self) -> None:
        self.assertTrue((await self.add("Hollow King")).enabled)

    async def test_a_dm_turns_one_off_and_on_and_it_is_kept(self) -> None:
        entry = await self.add("Hollow King")
        off = await self.store.set_enabled(GUILD, self.campaign.id, DM, entry.id, False)
        self.assertFalse(off.enabled)
        self.assertEqual(off.version, entry.version + 1)
        self.assertEqual(off.text, entry.text)  # nothing else changed
        on = await self.store.set_enabled(GUILD, self.campaign.id, DM, entry.id, True)
        self.assertTrue(on.enabled)
        self.assertEqual(len(await self.store.list(GUILD, self.campaign.id)), 1)

    async def test_only_a_dm_and_not_from_an_old_view(self) -> None:
        entry = await self.add("Hollow King")
        with self.assertRaises(HomebrewError):
            await self.store.set_enabled(GUILD, self.campaign.id, PLAYER, entry.id, False)
        await self.store.set_enabled(GUILD, self.campaign.id, DM, entry.id, False)
        with self.assertRaises(HomebrewError):  # shown the old version
            await self.store.set_enabled(
                GUILD, self.campaign.id, DM, entry.id, True, unchanged_since=entry.version
            )
        self.assertFalse((await self.store.get(GUILD, self.campaign.id, entry.id)).enabled)  # type: ignore[union-attr]

    async def test_another_campaign_cannot_be_reached(self) -> None:
        entry = await self.add("Hollow King")
        other = await self.campaigns.create(GUILD, "Other", DM)
        with self.assertRaises(HomebrewError):
            await self.store.set_enabled(GUILD, other.id, DM, entry.id, False)

    async def test_the_usable_read_skips_off_entries_and_the_plain_read_does_not(self) -> None:
        a, b = await self.add("Alpha"), await self.add("Beta")
        await self.store.set_enabled(GUILD, self.campaign.id, DM, b.id, False)
        every = [e.name for e in await self.store.list(GUILD, self.campaign.id)]
        usable = [e.name for e in await self.store.list(GUILD, self.campaign.id, usable_only=True)]
        self.assertEqual((every, usable), (["Alpha", "Beta"], ["Alpha"]))
        self.assertIsNotNone(await self.store.find(GUILD, self.campaign.id, "beta"))
        self.assertIsNone(await self.store.find(GUILD, self.campaign.id, "beta", usable_only=True))
        self.assertIsNotNone(
            await self.store.find(GUILD, self.campaign.id, "alpha", usable_only=True)
        )
        assert a.id != b.id

    async def test_removing_and_putting_back_keeps_it_off(self) -> None:
        entry = await self.add("Hollow King")
        off = await self.store.set_enabled(GUILD, self.campaign.id, DM, entry.id, False)
        gone = await self.store.remove(GUILD, self.campaign.id, DM, off.id)
        back = await self.store.put_back(GUILD, self.campaign.id, DM, gone)
        self.assertFalse(back.enabled)

    async def test_the_file_round_trips_the_state(self) -> None:
        a, b = await self.add("Alpha"), await self.add("Beta")
        await self.store.set_enabled(GUILD, self.campaign.id, DM, b.id, False)
        text = hf.write("Frost", await self.store.list(GUILD, self.campaign.id), "2024")
        for e in (a, b):
            await self.store.remove(GUILD, self.campaign.id, DM, e.id)
        loaded = await self.store.load_file(GUILD, self.campaign.id, DM, hf.parse(text).entries)
        self.assertEqual(loaded.added, 2)
        states = {e.name: e.enabled for e in await self.store.list(GUILD, self.campaign.id)}
        self.assertEqual(states, {"Alpha": True, "Beta": False})

    async def test_loading_a_file_that_says_nothing_keeps_the_state_and_off_changes_it(
        self,
    ) -> None:
        entry = await self.add("Alpha")
        await self.store.set_enabled(GUILD, self.campaign.id, DM, entry.id, False)
        same = await self.store.load_file(
            GUILD, self.campaign.id, DM, [FileEntry("monster", "Alpha", "x")]
        )
        self.assertEqual((same.same, same.updated), (1, 0))  # nothing said: left as it was
        self.assertFalse((await self.store.find(GUILD, self.campaign.id, "Alpha")).enabled)  # type: ignore[union-attr]
        turned = await self.store.load_file(
            GUILD, self.campaign.id, DM, [FileEntry("monster", "Alpha", "x", enabled=True)]
        )
        self.assertEqual(turned.updated, 1)
        self.assertTrue((await self.store.find(GUILD, self.campaign.id, "Alpha")).enabled)  # type: ignore[union-attr]

    async def test_the_backup_carries_it_and_an_old_backup_means_on(self) -> None:
        on, off = await self.add("Alpha"), await self.add("Beta")
        await self.store.set_enabled(GUILD, self.campaign.id, DM, off.id, False)
        async with self.db.guild(GUILD) as conn:
            rows = await HomebrewSection().dump(conn, GUILD, self.campaign.id)
        by_name = {r["name"]: r for r in rows}
        self.assertNotIn("enabled", by_name["Alpha"])  # (on is the default)
        self.assertIs(by_name["Beta"]["enabled"], False)
        checked = {c.name: c.enabled for c in HomebrewSection().check(rows)}
        self.assertEqual(checked, {"Alpha": True, "Beta": False})
        with self.assertRaises(CampaignError):
            HomebrewSection().check([{**by_name["Beta"], "enabled": "no"}])
        assert on.id != off.id

    async def test_the_reader_skips_off_entries_for_every_use(self) -> None:
        on, off = await self.add("Alpha"), await self.add("Beta")
        await self.store.set_enabled(GUILD, self.campaign.id, DM, off.id, False)
        reader = HomebrewReader(self.store, None)
        self.assertEqual([e.name for e in await reader.list(GUILD, self.campaign.id)], ["Alpha"])
        self.assertIsNone(await reader.find(GUILD, self.campaign.id, "Beta"))
        self.assertIsNotNone(await reader.find(GUILD, self.campaign.id, "Alpha"))
        assert on.id != off.id

    async def test_the_rule_lookup_and_the_players_card_and_the_spotter_use_the_reader(
        self,
    ) -> None:
        from dmbot.bot import DMBot
        from dmbot.config import Settings
        from dmbot.consent import ConsentStore
        from dmbot.sessions import SessionStore
        from dmbot.ui import rule_lookup

        await self.add("Alpha")
        beta = await self.add("Beta")
        await self.store.set_enabled(GUILD, self.campaign.id, DM, beta.id, False)
        bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            ConsentStore(self.db),
            self.campaigns,
            SessionStore(self.db),
            homebrew=self.store,
        )
        interaction: Any = SimpleNamespace(client=bot)
        # /dmbot rule and the players' card: the entry is not found while it is off.
        self.assertIsNotNone(await rule_lookup._homebrew(interaction, self.campaign, "alpha"))
        self.assertIsNone(await rule_lookup._homebrew(interaction, self.campaign, "beta"))
        # The rules cards' spotter is built from the same read.
        table = cast(Any, SimpleNamespace(
            guild_id=GUILD, campaign_id=self.campaign.id, homebrew_read=0, homebrew_spotter=None
        ))  # fmt: skip
        await bot._load_homebrew_spotter(table)
        spotter = table.homebrew_spotter
        self.assertIsNotNone(spotter)
        self.assertEqual([m.name for m in spotter.find("the Alpha attacks")], ["Alpha"])
        self.assertEqual(spotter.find("the Beta attacks"), [])


class TheMenu(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frost", DM)
        self.store = HomebrewStore(self.db)
        self.entry = await self.store.add(
            GUILD, self.campaign.id, DM, kind="monster", name="Hollow King", text="x"
        )

    def test_the_list_marks_an_off_entry(self) -> None:
        off = Homebrew(**{**self.entry.__dict__, "enabled": False}) if False else None
        assert off is None
        text = ui.list_text(self.campaign, [self.entry], 0, kind=self.entry.kind)
        self.assertNotIn("(off)", text)

    async def test_the_button_says_what_pressing_it_does(self) -> None:
        menu = ui.EntryMenu(self.campaign, self.entry, 0)
        labels = [str(getattr(i, "label", "")) for i in menu.children]
        self.assertIn("Turn off", labels)
        off = await self.store.set_enabled(GUILD, self.campaign.id, DM, self.entry.id, False)
        labels = [
            str(getattr(i, "label", "")) for i in ui.EntryMenu(self.campaign, off, 0).children
        ]
        self.assertIn("Turn on", labels)
        self.assertIn("(off)", ui.list_text(self.campaign, [off], 0, kind=off.kind))
        self.assertIn("It is off", ui.entry_text(off))
        self.assertNotIn("It is off", ui.entry_text(self.entry))

    async def test_pressing_it_turns_the_entry_off_then_on(self) -> None:
        bot = SimpleNamespace(
            homebrew=self.store,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=self.campaign)),
            homebrew_changed=lambda *a: None,
            rule_sets=None,
        )
        shown: list[str] = []

        async def edit(*, content: str, **_: Any) -> None:
            shown.append(content)

        it: Any = SimpleNamespace(
            client=bot,
            guild=SimpleNamespace(id=GUILD),
            user=SimpleNamespace(id=DM),
            response=SimpleNamespace(
                defer=AsyncMock(), is_done=lambda: True, edit_message=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=edit,
        )
        menu = ui.EntryMenu(self.campaign, self.entry, 0)
        await menu._enabled(it)
        stored = await self.store.get(GUILD, self.campaign.id, self.entry.id)
        assert stored is not None
        self.assertFalse(stored.enabled)
        self.assertTrue(any("Turned off" in s for s in shown))


class Migration(DatabaseTest):
    async def test_every_entry_becomes_on_the_hidden_flag_goes_and_a_hidden_one_is_kept(
        self,
    ) -> None:
        from dmbot.db import Database, drop_schema
        from dmbot.schema import MIGRATIONS
        from tests.pg import TEST_URL

        before = [m for m in MIGRATIONS if m[0] < "0112"]
        await self.db.close()  # start again from a schema as it was before 0112
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        one = await CampaignStore(self.db).create(GUILD, "One", DM)
        async with self.db.guild(GUILD) as conn:
            for name, known in (("Hollow King", False), ("Moonblade", True)):
                await conn.execute(
                    "INSERT INTO homebrew_entries (guild_id, campaign_id, kind, name, name_key,"
                    " text, created_by, created_at, updated_at, players_may_know)"
                    " VALUES (%s, %s, 'monster', %s, %s, 'words', %s, 1, 1, %s)",
                    (GUILD, one.id, name, name.casefold(), DM, known),
                )
        await self.db.migrate()
        entries = await HomebrewStore(self.db).list(GUILD, one.id)
        self.assertEqual(
            [(e.name, e.enabled) for e in entries], [("Hollow King", True), ("Moonblade", True)]
        )
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM information_schema.columns"
                " WHERE table_name = 'homebrew_entries' AND column_name = 'players_may_know'"
                " AND table_schema = current_schema()"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
