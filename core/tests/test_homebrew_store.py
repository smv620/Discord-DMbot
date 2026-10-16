"""Homebrew in the database (#1520): the DM's own entries, who may change them, the name that is
unique in a campaign whatever its case, the limit, that another DM's change isn't undone by a stale
one, that no other campaign or server ever sees them, that they go into backups and come back
exactly, and that they go out with the campaign."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import Campaign, CampaignError, CampaignStore
from dmbot.rules import homebrew
from dmbot.rules.homebrew import (
    HOMEBREW_MAX,
    Homebrew,
    HomebrewError,
    HomebrewSection,
    HomebrewStore,
)
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 111, 222
DM, CO_DM, PLAYER = 7, 8, 9


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


class HomebrewTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.clock = Clock()
        self.campaigns = CampaignStore(self.db, clock=self.clock)
        self.campaigns.register_section(HomebrewSection())
        self.store = HomebrewStore(self.db, clock=self.clock)
        self.campaign = await self.campaigns.create(GUILD_A, "Frostmaiden", DM)

    async def add(
        self,
        name: str = "Hollow King",
        text: str = "A crowned skeleton. Its crown can't be touched.",
        *,
        who: int = DM,
        kind: str = "monster",
        campaign: Campaign | None = None,
        **more: Any,
    ) -> Homebrew:
        self.clock.now += 60
        c = campaign or self.campaign
        return await self.store.add(c.guild_id, c.id, who, kind=kind, name=name, text=text, **more)

    async def names(self, campaign: Campaign | None = None) -> list[str]:
        c = campaign or self.campaign
        return [e.name for e in await self.store.list(c.guild_id, c.id)]


class Writing(HomebrewTest):
    async def test_an_entry_is_kept_as_the_dm_wrote_it_and_is_hidden_from_players_by_default(
        self,
    ) -> None:
        saved = await self.add(
            "  Hollow   King ", "Line one.\n\n\n\nLine two.", summary="  A crowned skeleton ."
        )
        self.assertEqual(saved.name, "Hollow King")  # spacing cleaned
        self.assertEqual(saved.text, "Line one.\n\nLine two.")  # breaks kept, blank lines squeezed
        self.assertEqual(saved.summary, "A crowned skeleton .")
        self.assertFalse(saved.players_may_know)  # a monster is a spoiler until the DM says so
        self.assertEqual((saved.created_by, saved.version), (DM, 1))
        self.assertEqual(await self.store.get(GUILD_A, self.campaign.id, saved.id), saved)
        self.assertEqual(await self.store.list(GUILD_A, self.campaign.id), [saved])

    async def test_the_dm_can_say_players_may_know(self) -> None:
        saved = await self.add("Moonblade", "A silver sword.", kind="item", players_may_know=True)
        self.assertTrue(saved.players_may_know)

    async def test_the_line_shown_is_the_summary_or_the_start_of_the_text(self) -> None:
        a = await self.add("A", "word " * 100, summary="Short.")
        b = await self.add("B", "word " * 100)
        self.assertEqual(a.shown(), "Short.")
        self.assertEqual(len(b.shown()), homebrew.SUMMARY_MAX)
        self.assertTrue(b.shown().endswith("…"))

    async def test_bad_input_is_refused_in_plain_words(self) -> None:
        for kwargs, words in (
            ({"name": "   "}, "name box was empty"),
            ({"name": "x" * 81}, "name is too long"),
            ({"text": "  \n "}, "text box was empty"),
            ({"text": "x" * 2001}, "Keep the text under 2000"),
            ({"summary": "x" * 161}, "summary is too long"),
            ({"kind": "dragon"}, "isn't one of the kinds"),
        ):
            with self.assertRaisesRegex(HomebrewError, words):
                await self.add(**kwargs)
        self.assertEqual(await self.names(), [])

    async def test_invisible_direction_characters_are_removed(self) -> None:
        saved = await self.add("Evil‮King", "Text‮ here\x00")
        self.assertEqual((saved.name, saved.text), ("EvilKing", "Text here"))

    async def test_a_name_is_unique_in_a_campaign_whatever_its_case(self) -> None:
        await self.add("Hollow King")
        with self.assertRaisesRegex(HomebrewError, "already has homebrew with that name"):
            await self.add("hollow  KING")
        other = await self.campaigns.create(GUILD_A, "Strahd", DM)
        await self.add("Hollow King", campaign=other)  # another campaign may use it

    async def test_a_campaign_holds_at_most_200(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO homebrew_entries (guild_id, campaign_id, kind, name, name_key, text,"
                " created_by, created_at, updated_at)"
                " SELECT %s, %s, 'rule', 'R' || n, 'r' || n, 'x', 7, 1, 1"
                " FROM generate_series(1, %s) AS n",
                (GUILD_A, self.campaign.id, HOMEBREW_MAX),
            )
        with self.assertRaisesRegex(HomebrewError, "already has 200"):
            await self.add("One too many")

    async def test_edit_changes_the_entry_and_counts_the_change(self) -> None:
        saved = await self.add()
        self.clock.now += 60
        edited = await self.store.edit(
            GUILD_A,
            self.campaign.id,
            DM,
            saved.id,
            kind="item",
            name="Hollow Crown",
            text="Now a crown.",
            players_may_know=True,
        )
        self.assertEqual((edited.kind, edited.name, edited.version), ("item", "Hollow Crown", 2))
        self.assertTrue(edited.players_may_know)
        self.assertGreater(edited.updated_at, saved.updated_at)
        self.assertEqual(edited.created_at, saved.created_at)

    async def test_an_edit_cannot_take_another_entrys_name(self) -> None:
        await self.add("Hollow King")
        other = await self.add("Pale Queen")
        with self.assertRaisesRegex(HomebrewError, "already has homebrew with that name"):
            await self.store.edit(
                GUILD_A,
                self.campaign.id,
                DM,
                other.id,
                kind="monster",
                name="HOLLOW KING",
                text="x",
            )
        # ...but may change its own name's case.
        done = await self.store.edit(
            GUILD_A, self.campaign.id, DM, other.id, kind="monster", name="PALE QUEEN", text="x"
        )
        self.assertEqual(done.name, "PALE QUEEN")

    async def test_a_stale_edit_or_removal_does_not_undo_another_dms_change(self) -> None:
        saved = await self.add()
        await self.store.edit(
            GUILD_A, self.campaign.id, DM, saved.id, kind="monster", name="Hollow King", text="v2"
        )
        with self.assertRaisesRegex(HomebrewError, "Another DM changed that entry"):
            await self.store.edit(
                GUILD_A,
                self.campaign.id,
                DM,
                saved.id,
                kind="monster",
                name="Hollow King",
                text="stale",
                unchanged_since=1,
            )
        with self.assertRaisesRegex(HomebrewError, "Another DM changed that entry"):
            await self.store.remove(GUILD_A, self.campaign.id, DM, saved.id, unchanged_since=1)
        (now,) = await self.store.list(GUILD_A, self.campaign.id)
        self.assertEqual(now.text, "v2")

    async def test_remove_returns_the_entry_and_put_back_restores_it(self) -> None:
        saved = await self.add("Hollow King", players_may_know=True, summary="Crowned.")
        gone = await self.store.remove(GUILD_A, self.campaign.id, DM, saved.id)
        self.assertEqual(gone.name, "Hollow King")
        self.assertEqual(await self.names(), [])
        with self.assertRaisesRegex(HomebrewError, "isn't here any more"):
            await self.store.remove(GUILD_A, self.campaign.id, DM, saved.id)
        back = await self.store.put_back(GUILD_A, self.campaign.id, DM, gone)
        self.assertEqual(
            (back.name, back.text, back.summary, back.players_may_know, back.created_at),
            (gone.name, gone.text, gone.summary, True, gone.created_at),
        )
        # Putting it back again, or over a new entry with the name, is refused like an add.
        with self.assertRaisesRegex(HomebrewError, "already has homebrew with that name"):
            await self.store.put_back(GUILD_A, self.campaign.id, DM, gone)


class WhoMayChange(HomebrewTest):
    async def test_only_the_campaigns_dms_may_add_edit_or_remove(self) -> None:
        saved = await self.add()
        for call in (
            self.store.add(
                GUILD_A,
                self.campaign.id,
                PLAYER,
                kind="rule",
                name="Sneaky",
                text="Managers decide",
            ),
            self.store.edit(
                GUILD_A, self.campaign.id, PLAYER, saved.id, kind="rule", name="X", text="Y"
            ),
            self.store.remove(GUILD_A, self.campaign.id, PLAYER, saved.id),
            self.store.put_back(GUILD_A, self.campaign.id, PLAYER, saved),
        ):
            with self.assertRaisesRegex(HomebrewError, "Only this campaign's DMs"):
                await call
        self.assertEqual(await self.names(), ["Hollow King"])


class Isolation(HomebrewTest):
    async def test_another_campaign_sees_nothing_and_can_change_nothing(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Strahd", DM)
        mine = await self.add()
        self.assertEqual(await self.names(other), [])
        self.assertIsNone(await self.store.get(GUILD_A, other.id, mine.id))
        with self.assertRaisesRegex(HomebrewError, "isn't here any more"):
            await self.store.remove(GUILD_A, other.id, DM, mine.id)
        with self.assertRaisesRegex(HomebrewError, "isn't here any more"):
            await self.store.edit(
                GUILD_A, other.id, DM, mine.id, kind="rule", name="Sneaky", text="x"
            )
        self.assertEqual(await self.names(), ["Hollow King"])

    async def test_another_server_sees_nothing_and_can_change_nothing(self) -> None:
        mine = await self.add()
        self.assertEqual(await self.store.list(GUILD_B, self.campaign.id), [])
        for call in (
            self.store.add(GUILD_B, self.campaign.id, DM, kind="rule", name="Sneaky", text="x"),
            self.store.remove(GUILD_B, self.campaign.id, DM, mine.id),
        ):
            with self.assertRaisesRegex(HomebrewError, "campaign isn't here"):
                await call
        self.assertEqual(await self.names(), ["Hollow King"])

    async def test_without_a_server_set_the_database_shows_none(self) -> None:
        await self.add()
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM homebrew_entries")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # row-level security, not just the queries' filters

    async def test_deleting_the_campaign_deletes_its_entries_and_only_its_own(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Strahd", DM)
        await self.add("Frostmaiden thing")
        await self.add("Strahd thing", campaign=other)
        await self.campaigns.delete(GUILD_A, self.campaign.id)
        self.assertEqual(await self.names(other), ["Strahd thing"])
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM homebrew_entries")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)


class Backups(HomebrewTest):
    async def test_entries_go_into_a_backup_and_come_back_exactly(self) -> None:
        a = await self.add("Hollow King", "Line one.\nLine two.", summary="Crowned.")
        b = await self.add("Moonblade", "A sword.", kind="item", players_may_know=True)
        backup = await self.campaigns.export(GUILD_A, self.campaign.id)
        rows = backup["sections"]["homebrew"]
        self.assertEqual([r["name"] for r in rows], ["Hollow King", "Moonblade"])
        self.assertEqual(rows[0]["by"], str(DM))
        self.assertNotIn("id", rows[0])  # ids mean nothing in another campaign
        self.assertNotIn("version", rows[0])
        restored = await self.campaigns.import_backup(GUILD_B, backup, PLAYER)
        back = {e.name: e for e in await self.store.list(GUILD_B, restored.id)}
        self.assertEqual(back["Hollow King"].text, a.text)
        self.assertEqual(back["Hollow King"].summary, "Crowned.")
        self.assertFalse(back["Hollow King"].players_may_know)
        self.assertTrue(back["Moonblade"].players_may_know)
        self.assertEqual(
            (back["Moonblade"].created_at, back["Moonblade"].kind), (b.created_at, "item")
        )
        again = await self.campaigns.export(GUILD_B, restored.id)
        self.assertEqual(again["sections"]["homebrew"], rows)
        self.assertEqual(len(await self.store.list(GUILD_A, self.campaign.id)), 2)  # untouched

    async def test_a_backup_holds_only_its_own_campaigns_entries(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Strahd", DM)
        await self.add("Mine")
        await self.add("Theirs", campaign=other)
        mine = await self.campaigns.export(GUILD_A, self.campaign.id)
        self.assertEqual([r["name"] for r in mine["sections"]["homebrew"]], ["Mine"])

    async def test_a_damaged_file_is_refused_before_anything_is_written(self) -> None:
        await self.add("Hollow King")
        good = await self.campaigns.export(GUILD_A, self.campaign.id)
        row = good["sections"]["homebrew"][0]
        for bad in (
            {**row, "kind": "dragon"},
            {**row, "name": "  Spaced   out"},  # not in its cleaned form
            {**row, "players_may_know": "yes"},
            {**row, "by": "0"},
            {**row, "updated_at": row["created_at"] - 1},
            {k: v for k, v in row.items() if k != "text"},
            {**row, "extra": 1},
        ):
            damaged = {**good, "sections": {**good["sections"], "homebrew": [bad]}}
            with self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD_B, damaged, PLAYER)
        twice = {
            **good,
            "sections": {**good["sections"], "homebrew": [row, {**row, "name": "HOLLOW king"}]},
        }
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, twice, PLAYER)
        too_many = {
            **good,
            "sections": {**good["sections"], "homebrew": [row] * (HOMEBREW_MAX + 1)},
        }
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, too_many, PLAYER)
        self.assertEqual(await self.campaigns.list_campaigns(GUILD_B), [])
