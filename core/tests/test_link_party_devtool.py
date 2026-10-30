"""The party backfill (#3036; dmbot.devtools.link_party): a dry run writes nothing and counts what
a run would do, a real run links each stored session's players, a second run adds nothing, the
campaign filter holds, and another server's campaign is never touched. Invented names and ids."""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.campaigns import CampaignStore
from dmbot.consent import ConsentStore
from dmbot.devtools import link_party
from dmbot.memory.models import CONFIRMED, new_id
from tests.test_memory_names import GUILD, NamesTest

OTHER, DM_OTHER = 222, 9
PLAYERS = {"Mara": 101, "Tavin": 102, "Brann": 103}


def args(**kw: object) -> Namespace:
    base = {"guild": GUILD, "campaign": None, "dry_run": False}
    return Namespace(**(base | kw))


class ThePartyBackfill(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        for name, user in PLAYERS.items():
            await self.memory.add_entity(
                GUILD, self.campaign.id, name=name, type="character", role="player_character",
                played_by=user, status=CONFIRMED, source="dm",
            )  # fmt: skip
        await self.session(GUILD, self.campaign.id, list(PLAYERS.values()), started_at=100)
        # The stub says everyone still agrees; the consent store itself is tested elsewhere.
        consent = MagicMock()
        consent.agreed = AsyncMock(return_value=frozenset(PLAYERS.values()))
        self.consent_patch = patch.object(link_party, "ConsentStore", return_value=consent)
        self.consent_patch.start()
        self.addCleanup(self.consent_patch.stop)

    async def session(
        self, guild: int, campaign_id: str, users: list[int], started_at: int
    ) -> None:
        session = new_id()
        async with self.db.guild(guild) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at,"
                " ended_at, line_count) VALUES (%s, %s, %s, %s, %s, %s)",
                (session, guild, campaign_id, started_at, started_at + 100, len(users)),
            )
            for user in users:
                await conn.execute(
                    "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id,"
                    " heard, duration_ms) VALUES (%s, %s, 5, %s, 'we meet', 1000)",
                    (guild, session, user),
                )

    async def knows(self, guild: int, campaign_id: str) -> int:
        rels = await self.memory.relations(guild, campaign_id, include_secret=True)
        return sum(1 for r in rels if r.predicate == "knows")

    async def test_a_dry_run_counts_and_writes_nothing(self) -> None:
        totals = await link_party.backfill(self.db, args(dry_run=True))

        self.assertEqual((totals.campaigns, totals.sessions, totals.speakers), (1, 1, 3))
        self.assertEqual((totals.characters, totals.pairs, totals.members), (0, 0, 0))
        self.assertEqual(await self.knows(GUILD, self.campaign.id), 0)

    async def test_a_dry_run_over_two_sessions_writes_nothing_and_a_real_run_counts_exactly(
        self,
    ) -> None:
        await self.session(GUILD, self.campaign.id, list(PLAYERS.values()), started_at=200)

        dry = await link_party.backfill(self.db, args(dry_run=True))
        real = await link_party.backfill(self.db, args())

        self.assertEqual((dry.sessions, dry.speakers), (2, 6))
        self.assertEqual((real.sessions, real.pairs, real.members), (2, 3, 3))
        self.assertEqual(await self.knows(GUILD, self.campaign.id), 3)

    async def test_a_real_run_links_each_session_and_a_second_run_adds_nothing(self) -> None:
        first = await link_party.backfill(self.db, args())
        self.assertEqual((first.pairs, first.members), (3, 3))
        self.assertEqual(await self.knows(GUILD, self.campaign.id), 3)

        second = await link_party.backfill(self.db, args())
        self.assertEqual((second.pairs, second.members), (0, 0))

    async def test_the_backfill_reads_consent_the_way_the_live_bot_does(self) -> None:
        # Real consent rows, and the bot's outside engine: a yes given for another engine (or for
        # none) does not count, so that person's character is not linked.
        self.consent_patch.stop()
        consent = ConsentStore(self.db)
        await consent.grant(GUILD, 101, outside_to="deepgram")
        await consent.grant(GUILD, 102)  # said yes, but not for this engine
        await consent.grant(GUILD, 103, outside_to="deepgram")
        settings = SimpleNamespace(outside_engine="deepgram")
        with patch.object(link_party, "load_transcription_settings", return_value=settings):
            totals = await link_party.backfill(self.db, args())

        self.assertEqual(totals.members, 2)
        rels = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        members = {r.subject_id for r in rels if r.predicate == "member_of"}
        entities = await self.memory.entities(GUILD, self.campaign.id)
        tavin = next(e.id for e in entities if e.name == "Tavin")
        self.assertNotIn(tavin, members)
        self.assertEqual(len(members), 2)

    async def test_a_campaign_filter_limits_the_run(self) -> None:
        totals = await link_party.backfill(self.db, args(campaign="not-this-one"))

        self.assertEqual((totals.campaigns, totals.pairs), (0, 0))
        self.assertEqual(await self.knows(GUILD, self.campaign.id), 0)

    async def test_another_servers_campaign_is_never_touched(self) -> None:
        other = await CampaignStore(self.db).create(OTHER, "Other table", DM_OTHER)
        await self.session(OTHER, other.id, list(PLAYERS.values()), started_at=100)

        await link_party.backfill(self.db, args())

        self.assertEqual(await self.knows(OTHER, other.id), 0)
