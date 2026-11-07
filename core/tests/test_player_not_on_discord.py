"""A character played by someone who is not on Discord (#3690): the player is a speaker of the
campaign without Discord (a negative number, #3305). Only THIS campaign's own speaker may play a
character, a backup keeps the link, no reader builds a mention or a message from a negative
number, and a speaker row that went away is "a player not on Discord", never an error. Invented
fixtures only."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from dmbot.bot import DMBot, register_backup_sections
from dmbot.campaigns import CampaignStore
from dmbot.consent import TERMS_VERSION, ConsentStore
from dmbot.memory.models import CONFIRMED, DM, PLAYER_CHARACTER
from dmbot.memory.store import MemoryStore, placeholder_name
from tests.test_import_consent import GHOST, WITH_GHOST, plan
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_imports import MIRA, WithImports


class WithGhost(WithImports):
    """A campaign with a speaker without Discord, made by the import's own door."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        mapping: dict[str, Any] = {"Mira (mira_v)": str(MIRA), GHOST: {"confirmed": True}}
        await self.confirm(plan(mapping, consentVersion=TERMS_VERSION), ("a.txt", WITH_GHOST))
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT user_id FROM campaign_speakers WHERE guild_id = %s AND campaign_id = %s",
                (THURSDAY.id, self.one.id),
            )
            self.ghost = int((await cur.fetchone() or {})["user_id"])

    async def make(self, campaign: str, who: int | None) -> Any:
        return await self.memory.add_entity(
            THURSDAY.id, campaign, type=PLAYER_CHARACTER, name="Tess", source=DM,
            status=CONFIRMED, played_by=who,
        )  # fmt: skip


class TheStore(WithGhost):
    async def test_a_speaker_of_this_campaign_may_play_a_character(self) -> None:
        self.assertLess(self.ghost, 0)
        written = await self.make(self.one.id, self.ghost)
        self.assertEqual(written.value.played_by, self.ghost)
        # and the party / person entry that mirrors it is named without a made-up Discord id
        people = [
            e for e in await self.memory.entities(THURSDAY.id, self.one.id) if e.type == "person"
        ]
        self.assertIn(placeholder_name(self.ghost), {e.name for e in people})
        self.assertEqual(placeholder_name(self.ghost), f"Player {-self.ghost} (no Discord)")

    async def test_the_same_number_is_refused_in_another_campaign(self) -> None:
        with self.assertRaises(ValueError):
            await self.make(self.two.id, self.ghost)

    async def test_a_number_with_no_speaker_row_and_zero_are_refused(self) -> None:
        for bad in (-424242, 0, -(2**63)):
            with self.assertRaises(ValueError, msg=str(bad)):
                await self.make(self.one.id, bad)

    async def test_a_discord_user_still_works_as_before(self) -> None:
        written = await self.make(self.one.id, MIRA)
        self.assertEqual(written.value.played_by, MIRA)

    async def test_confirming_a_suggestion_takes_the_same_rule(self) -> None:
        added = await self.memory.add_entity(
            THURSDAY.id, self.one.id, type="character", name="Brin", source=DM, status="proposed"
        )
        done = await self.memory.confirm_entity(
            THURSDAY.id, self.one.id, added.value.id, PLAYER_CHARACTER, source=DM,
            played_by=self.ghost,
        )  # fmt: skip
        self.assertEqual(done.value.played_by, self.ghost)
        with self.assertRaises(ValueError):
            await self.memory.confirm_entity(
                THURSDAY.id, self.one.id, added.value.id, PLAYER_CHARACTER, source=DM,
                played_by=-7,
            )  # fmt: skip

    async def test_a_speaker_row_that_went_away_leaves_a_player_not_on_discord(self) -> None:
        written = await self.make(self.one.id, self.ghost)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "DELETE FROM campaign_speakers WHERE guild_id = %s AND campaign_id = %s",
                (THURSDAY.id, self.one.id),
            )
        # readers never fail on it: the character is still read, with its player as a number
        found = [e for e in await self.memory.entities(THURSDAY.id, self.one.id)]
        [tess] = [e for e in found if e.id == written.value.id]
        self.assertEqual(tess.played_by, self.ghost)
        bot = SimpleNamespace(get_guild=lambda _g: None)
        self.assertEqual(DMBot.name_of(cast(Any, bot), THURSDAY.id, self.ghost),
                         "a player without Discord")  # fmt: skip


class TheReaders(WithGhost):
    async def test_no_mention_or_member_lookup_comes_from_a_negative_number(self) -> None:
        looked: list[int] = []
        guild = SimpleNamespace(get_member=lambda uid: looked.append(uid))
        bot = SimpleNamespace(get_guild=lambda _g: guild)
        shown = DMBot.name_of(cast(Any, bot), THURSDAY.id, self.ghost)
        self.assertEqual(shown, "a player without Discord")
        self.assertNotIn("<@", shown)
        self.assertEqual(looked, [])  # no member fetch

    async def test_a_character_of_a_player_without_discord_has_no_sheet_to_ask_about(self) -> None:
        from dmbot.memory.sheet_store import SheetRefused, SheetStore

        written = await self.make(self.one.id, self.ghost)
        sheets = SheetStore(self.db)
        self.assertEqual(await sheets.sheets(THURSDAY.id, self.one.id), [])
        with self.assertRaises(SheetRefused):
            async with self.db.guild(THURSDAY.id) as conn:
                await sheets._require_character(conn, THURSDAY.id, self.one.id, written.value.id)


class TheBackup(WithGhost):
    async def test_the_link_to_the_speaker_comes_back_with_a_restore(self) -> None:
        written = await self.make(self.one.id, self.ghost)
        store = CampaignStore(self.db)
        register_backup_sections(
            store, forget_audio=lambda g, c: None, consenting=ConsentStore(self.db).consenting
        )
        data, parts = await store.export_set(THURSDAY.id, self.one.id)
        copy = await store.import_backup(THURSDAY.id, data, ALICE.id, parts=parts)
        # the numbers are kept as they were (see transcript/import_consent.py), so the link holds
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT e.played_by, (SELECT count(*) FROM campaign_speakers s"
                " WHERE s.guild_id = e.guild_id AND s.campaign_id = e.campaign_id"
                " AND s.user_id = e.played_by) AS speakers FROM memory_entities e"
                " WHERE e.guild_id = %s AND e.campaign_id = %s AND e.type = 'character'"
                " AND e.name = 'Tess'",
                (THURSDAY.id, copy.id),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((int(row["played_by"]), int(row["speakers"])), (self.ghost, 1))
        self.assertTrue(written.value.id)

    async def test_a_file_whose_player_has_no_speaker_is_refused_as_damaged(self) -> None:
        from dmbot.campaigns.models import CampaignError

        await self.make(self.one.id, self.ghost)
        store = CampaignStore(self.db)
        register_backup_sections(
            store, forget_audio=lambda g, c: None, consenting=ConsentStore(self.db).consenting
        )
        data, parts = await store.export_set(THURSDAY.id, self.one.id)
        self.assertTrue(data["sections"]["campaign_speakers"])
        data["sections"]["campaign_speakers"] = []  # the player's speaker is not in the file
        with self.assertRaises(CampaignError):
            await store.import_backup(THURSDAY.id, data, ALICE.id, parts=parts)
