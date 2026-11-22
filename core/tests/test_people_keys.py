"""The people at the table are not story names, with no Discord (#4904 3b-4c, Supervisor's ruling on
#4904): the worker has no member cache, so the names come from the directory of members who agreed
to be recorded. Invented names and numbers throughout."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.campaigns import CampaignStore
from dmbot.consent import ConsentStore
from dmbot.members import MemberDirectory
from tests.pg import DatabaseTest
from tests.test_memory_names import GUILD as NAMES_GUILD
from tests.test_memory_names import NamesTest

GUILD, OTHER = 7301, 7302
MIA, BRAM, NEVER, DM = 9301, 9302, 9303, 9304


def bot(directory: Any, cache: dict[int, str] | None = None) -> Any:
    """A bot with no Discord: `name_of` answers only from `cache`, as a worker's does."""
    out: Any = SimpleNamespace(members=directory)
    shown = cache or {}
    out.name_of = lambda _g, uid: shown.get(uid, f"<@{uid}>")
    out._people_keys = DMBot._people_keys.__get__(out)
    return out


class TheKeys(IsolatedAsyncioTestCase):
    async def test_a_worker_with_no_cache_skips_the_names_in_the_directory(self) -> None:
        directory = SimpleNamespace(names=AsyncMock(return_value={MIA: ["Mia Stone", "mia_s"]}))
        keys = await bot(directory)._people_keys(GUILD, {MIA, -4})
        self.assertEqual(keys, {"mia stone", "mia", "stone", "mia_s"})  # (and -4 is no one)
        directory.names.assert_awaited_once_with(GUILD, [MIA])

    async def test_the_cache_is_used_first_and_the_directory_only_for_the_rest(self) -> None:
        directory = SimpleNamespace(names=AsyncMock(return_value={}))
        keys = await bot(directory, {BRAM: "Bram Vale"})._people_keys(GUILD, [BRAM, MIA])
        self.assertEqual(keys, {"bram vale", "bram", "vale"})
        directory.names.assert_awaited_once_with(GUILD, [MIA])  # (Bram was in the cache)

    async def test_a_directory_that_fails_adds_nothing_and_says_only_the_type(self) -> None:
        directory = SimpleNamespace(names=AsyncMock(side_effect=RuntimeError("Mia Stone's row")))
        with self.assertLogs("dmbot.bot", level="WARNING") as logs:
            keys = await bot(directory)._people_keys(GUILD, [MIA])
        self.assertEqual(keys, set())
        self.assertIn("RuntimeError", " ".join(logs.output))
        self.assertNotIn("Mia", " ".join(logs.output))

    async def test_with_no_directory_nothing_is_added(self) -> None:
        self.assertEqual(await bot(None)._people_keys(GUILD, [MIA]), set())


class TheDirectory(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1_700_000_000.0
        self.directory = MemberDirectory(self.db, clock=lambda: self.now)
        consent = ConsentStore(self.db)
        await consent.grant(GUILD, MIA)
        await consent.grant(OTHER, BRAM)
        await self.directory.remember(GUILD, MIA, "Mia Stone", "mia_s")

    async def test_the_names_of_people_who_agreed_come_back_by_server(self) -> None:
        self.assertEqual(
            await self.directory.names(GUILD, [MIA, BRAM, NEVER]), {MIA: ["Mia Stone", "mia_s"]}
        )
        self.assertEqual(await self.directory.names(OTHER, [MIA]), {})  # (never across servers)
        self.assertEqual(await self.directory.names(GUILD, []), {})

    async def test_someone_who_stopped_is_not_returned_even_by_a_stale_row(self) -> None:
        await ConsentStore(self.db).revoke(GUILD, MIA)  # (the website's stop, with the bot down)
        self.assertEqual(await self.directory.names(GUILD, [MIA]), {})

    async def test_a_new_name_replaces_the_old_one(self) -> None:
        await self.directory.remember(GUILD, MIA, "Mia Reed", "mia_s")
        self.assertEqual(await self.directory.names(GUILD, [MIA]), {MIA: ["Mia Reed", "mia_s"]})

    async def test_a_dm_without_consent_is_kept_and_returned_but_a_stranger_is_not(self) -> None:
        campaign = await CampaignStore(self.db).create(GUILD, "One", DM)
        await self.directory.remember(GUILD, DM, "Oskar Vane", "oskar_v")
        await self.directory.remember(GUILD, NEVER, "Nobody", "nobody")  # (no consent, no DM)
        self.assertEqual(
            await self.directory.names(GUILD, [DM, NEVER]), {DM: ["Oskar Vane", "oskar_v"]}
        )
        self.assertIsNotNone(campaign)
        await self.directory.forget(GUILD, MIA)  # (another person stopping keeps the DM)
        self.assertEqual(await self.directory.names(GUILD, [DM]), {DM: ["Oskar Vane", "oskar_v"]})

    async def test_the_sweep_keeps_a_player_of_a_confirmed_character(self) -> None:
        from dmbot.memory.models import DM as DM_SOURCE
        from dmbot.memory.store import MemoryStore

        campaign = await CampaignStore(self.db).create(GUILD, "One", DM)
        await MemoryStore(self.db).add_entity(
            GUILD, campaign.id, type="character", name="Rook", source=DM_SOURCE,
            status="confirmed", role="player_character", played_by=MIA,
        )  # fmt: skip
        self.now += 100 * 86400
        self.assertEqual(await self.directory.sweep(GUILD, 90 * 86400), 0)  # Mia plays Rook
        self.assertEqual(await self.directory.names(GUILD, [MIA]), {MIA: ["Mia Stone", "mia_s"]})

    async def test_the_sweep_drops_names_not_written_for_90_days_and_keeps_the_rest(self) -> None:
        await ConsentStore(self.db).grant(GUILD, BRAM)
        self.now += 80 * 86400
        await self.directory.remember(GUILD, BRAM, "Bram Vale", "bram_v")  # (seen lately)
        self.now += 20 * 86400  # Mia's name is now 100 days old, Bram's 20
        self.assertEqual(await self.directory.sweep(GUILD, 90 * 86400), 1)
        self.assertEqual(await self.directory.names(GUILD, [MIA]), {})
        self.assertEqual(await self.directory.names(GUILD, [BRAM]), {BRAM: ["Bram Vale", "bram_v"]})


class TheScanWithNoCache(NamesTest):
    """The headless "Mia Stone": a worker's name scan, which has no Discord, still skips the people
    at the table because the directory knows their names (before this, it proposed them)."""

    LINES = (
        "We met Mia Stone and Oskar Vane at the gate.",
        "Oskar Vane nodded to Mia Stone, then to Mia.",
        "Stone said nothing; Oskar Vane smiled.",
    )

    async def proposed(self) -> list[str]:
        made = await self.bot._propose_new_names(
            NAMES_GUILD, self.campaign.id, list(self.LINES), {MIA}, None
        )
        return sorted(made)

    async def test_the_table_is_skipped_from_the_directory_and_the_story_name_is_not(self) -> None:
        self.bot.members = MemberDirectory(self.db)
        await self.consent.grant(NAMES_GUILD, MIA)
        await self.bot.members.remember(NAMES_GUILD, MIA, "Mia Stone", "mia_s")
        self.assertEqual(self.bot.name_of(NAMES_GUILD, MIA), f"<@{MIA}>")  # (no cache: a worker's)
        self.assertEqual(await self.proposed(), ["Oskar Vane"])

    async def test_without_a_directory_row_the_player_would_be_proposed(self) -> None:
        # (the control: what the worker did before the directory was read)
        self.bot.members = MemberDirectory(self.db)
        proposed = await self.proposed()
        self.assertIn("Oskar Vane", proposed)
        self.assertTrue(any("Mia" in name or "Stone" in name for name in proposed))


class TheImportLabels(NamesTest):
    """The second source (#5023): the label an import file gave a person the DM tied it to, for
    someone with no consent row and so no name in the directory."""

    LINES = (
        "We met Cerric Vale and Oskar Vane at the gate.",
        "Oskar Vane nodded to Cerric Vale, then to Cerric.",
        "Vale said nothing; Oskar Vane smiled.",
    )

    async def tie(self, campaign_id: str, label: str, user: int) -> None:
        async with self.db.guild(NAMES_GUILD) as conn:
            await conn.execute(
                "INSERT INTO import_speakers (guild_id, campaign_id, label, user_id, updated_at)"
                " VALUES (%s, %s, %s, %s, 1)",
                (NAMES_GUILD, campaign_id, label, user),
            )

    async def proposed(self, users: set[int]) -> list[str]:
        made = await self.bot._propose_new_names(
            NAMES_GUILD, self.campaign.id, list(self.LINES), users, None
        )
        return sorted(made)

    async def test_a_confirmed_speaker_with_no_consent_row_is_skipped_by_their_label(self) -> None:
        self.bot.members = MemberDirectory(self.db)  # (and the directory knows nobody here)
        await self.tie(self.campaign.id, "Cerric Vale", MIA)
        self.assertEqual(await self.proposed({MIA}), ["Oskar Vane"])

    async def test_a_label_that_is_a_known_characters_name_hides_only_itself(self) -> None:
        from dmbot.memory.models import CONFIRMED, NewName

        self.bot.members = MemberDirectory(self.db)
        await self.memory.add_names(
            NAMES_GUILD, self.campaign.id, [NewName("Cerric Vale", "character", CONFIRMED)],
            source="dm",
        )  # fmt: skip
        await self.tie(self.campaign.id, "Cerric Vale", MIA)
        lines = ("We met Mira Vale and Cerric Vale.", "Mira Vale nodded to Cerric Vale.")
        made = await self.bot._propose_new_names(
            NAMES_GUILD, self.campaign.id, list(lines), {MIA}, None
        )
        self.assertEqual(sorted(made), ["Mira Vale"])  # (not hidden by "Vale")

    async def test_someone_not_tied_and_another_campaigns_labels_skip_nothing(self) -> None:
        self.bot.members = MemberDirectory(self.db)
        other = await self.campaigns.create(NAMES_GUILD, "Other", 77)
        await self.tie(other.id, "Cerric Vale", MIA)  # (another campaign's tie)
        await self.tie(self.campaign.id, "Cerric Vale", BRAM)  # (tied to someone not asked about)
        proposed = await self.proposed({MIA})
        self.assertTrue(any("Cerric" in n or "Vale" in n for n in proposed))
