"""Delete my past transcripts (#1532): a person's own words leave every transcript in one server,
both versions, with what exists only because of them; other people's lines, other servers, the
DM's confirmed facts and the session rows stay; a failure deletes nothing."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.memory.models import Heard
from dmbot.memory.store import MemoryStore
from dmbot.transcript.models import (
    SIDEBAR_ANSWER,
    SIDEBAR_QUESTION,
    VIA_TYPED,
    Line,
    Lineage,
    line_ref,
)
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.suggestions import SuggestionStore
from tests.pg import DatabaseTest

GUILD, OTHER_GUILD = 1, 2
DM, ME, FRIEND = 7, 8, 9
START = 1_700_000_000


def line(seconds: int, user: int, text: str, cleaned: str | None = None, **more: Any) -> Line:
    return Line(START * 1000 + seconds * 1000, user, text, cleaned or text, **more)


class DeleteTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.store = TranscriptStore(self.db)
        self.memory = MemoryStore(self.db)
        self.suggestions = SuggestionStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.other = await self.campaigns.create(GUILD, "Sunken City", DM)
        self.far = await self.campaigns.create(OTHER_GUILD, "Far Away", DM)
        self.first = await self.store.open_session(GUILD, self.campaign.id, START)
        self.second = await self.store.open_session(GUILD, self.other.id, START + 10)
        self.elsewhere = await self.store.open_session(OTHER_GUILD, self.far.id, START)
        await self.store.add_lines(
            GUILD,
            self.first,
            [
                line(1, ME, "I attack the gobblin", "I attack the goblin"),
                line(2, FRIEND, "Roll for it"),
                line(3, ME, "Twenty two"),
            ],
        )
        await self.store.add_lines(GUILD, self.second, [line(1, ME, "Hello"), line(2, DM, "Hi")])
        await self.store.add_lines(OTHER_GUILD, self.elsewhere, [line(1, ME, "Far away words")])

    async def users(self, guild: int, session: str) -> list[int]:
        return [x.user_id for x in await self.store.lines(guild, session)]

    async def test_counts_cover_every_campaign_in_the_server_only(self) -> None:
        counted = await self.store.person_counts(GUILD, ME)
        self.assertEqual((counted.lines, counted.sessions), (3, 2))
        nobody = await self.store.person_counts(GUILD, 4242)
        self.assertEqual((nobody.lines, nobody.sessions), (0, 0))

    async def test_my_lines_go_and_everyone_elses_stay(self) -> None:
        gone = await self.store.delete_person(GUILD, ME)
        self.assertEqual((gone.lines, gone.sessions), (3, 2))
        self.assertEqual(await self.users(GUILD, self.first), [FRIEND])
        self.assertEqual(await self.users(GUILD, self.second), [DM])
        # Another server is untouched.
        self.assertEqual(await self.users(OTHER_GUILD, self.elsewhere), [ME])
        again = await self.store.delete_person(GUILD, ME)
        self.assertEqual((again.lines, again.sessions), (0, 0))

    async def test_both_versions_leave_with_the_line(self) -> None:
        await self.store.delete_person(GUILD, ME)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM transcript_lines WHERE heard LIKE %s OR text LIKE %s",
                ("%gobblin%", "%goblin%"),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_session_counts_and_speakers_are_recomputed_and_rows_stay(self) -> None:
        await self.store.delete_person(GUILD, ME)
        first = await self.store.session(GUILD, self.first)
        second = await self.store.session(GUILD, self.second)
        assert first is not None and second is not None
        self.assertEqual((first.lines, first.speakers), (1, (FRIEND,)))
        self.assertEqual((second.lines, second.speakers), (1, (DM,)))
        far = await self.store.session(OTHER_GUILD, self.elsewhere)
        assert far is not None
        self.assertEqual(far.lines, 1)

    async def test_my_sidebar_questions_and_the_replies_to_them_go_too(self) -> None:
        await self.store.add_lines(
            GUILD,
            self.first,
            [
                Line(
                    START * 1000 + 9_000, ME, "how far", "How far is it?",
                    sidebar=SIDEBAR_QUESTION, lineage=Lineage(ref="abc123", via=VIA_TYPED),
                ),
                Line(
                    START * 1000 + 9_500, ME, "Far.", "Far.",
                    sidebar=SIDEBAR_ANSWER, lineage=Lineage(reply_to="abc123", model="m"),
                ),
            ],
        )  # fmt: skip
        gone = await self.store.delete_person(GUILD, ME)
        self.assertEqual(gone.lines, 5)
        self.assertEqual(await self.users(GUILD, self.first), [FRIEND])

    async def test_my_fix_suggestions_in_every_state_go(self) -> None:
        lines = await self.store.lines(GUILD, self.first)
        mine = next(x for x in lines if x.user_id == ME)
        made = await self.suggestions.suggest(
            GUILD, self.campaign.id, self.first, ME, mine.started_ms, "I attack the goblin"
        )
        assert not isinstance(made, str)
        await self.store.delete_person(GUILD, ME)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM transcript_suggestions")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_mentions_of_my_lines_go_but_other_mentions_stay(self) -> None:
        name = (
            await self.memory.add_entity(
                GUILD, self.campaign.id, type="place", name="Bryn Shander", source="dm",
                status="confirmed",
            )
        ).value  # fmt: skip
        mine = await self.store.lines(GUILD, self.first)
        my_line = next(x for x in mine if x.user_id == ME)
        friends = next(x for x in mine if x.user_id == FRIEND)
        for ref in (
            line_ref(self.first, my_line.started_ms, ME),
            line_ref(self.first, friends.started_ms, FRIEND),
        ):
            await self.memory.add_mention(
                GUILD, self.campaign.id, name.id, line_ref=ref, span=(0, 4),
                confidence=0.9, method="exact", source="dm",
            )  # fmt: skip
        await self.store.delete_person(GUILD, ME)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT line_ref FROM memory_mentions")
            left = [r["line_ref"] for r in await cur.fetchall()]
        self.assertEqual(left, [line_ref(self.first, friends.started_ms, FRIEND)])

    async def test_my_name_counts_and_unconfirmed_claims_go_but_confirmed_stay(self) -> None:
        name = (
            await self.memory.add_entity(
                GUILD, self.campaign.id, type="place", name="Bryn Shander", source="dm",
                status="confirmed",
            )
        ).value  # fmt: skip
        await self.memory.add_session_heard(
            GUILD, self.campaign.id, START, [Heard(name.id, ME, 2), Heard(name.id, FRIEND, 1)]
        )
        async with self.db.guild(GUILD) as conn:
            for n, status in enumerate(("proposed", "rejected", "confirmed")):
                await conn.execute(
                    "INSERT INTO memory_claims (guild_id, campaign_id, id, subject, relationship,"
                    " object, how, lines, speaker_ids, source, prompt_version, model, status,"
                    " created_at) VALUES (%s, %s, %s, 'a', 'b', 'c', 'dm_said', '{1}', %s,"
                    " 'x', 'v', 'm', %s, 1)",
                    (GUILD, self.campaign.id, f"{n:032x}", [ME], status),
                )
        await self.store.delete_person(GUILD, ME)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT speaker_id FROM memory_heard")
            heard = [r["speaker_id"] for r in await cur.fetchall()]
            cur = await conn.execute("SELECT status FROM memory_claims")
            claims = [r["status"] for r in await cur.fetchall()]
        self.assertEqual(heard, [FRIEND])
        self.assertEqual(claims, ["confirmed"])  # the DM's own fact stays

    async def test_a_deleted_claim_leaves_nothing_in_the_undo_log(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO memory_claims (guild_id, campaign_id, id, subject, relationship,"
                " object, details, how, lines, speaker_ids, source, prompt_version, model,"
                " status, created_at) VALUES (%s, %s, %s, 'a', 'b', 'c', 'my words', 'dm_said',"
                " '{1}', %s, 'x', 'v', 'm', 'proposed', 1)",
                (GUILD, self.campaign.id, "a" * 32, [ME, FRIEND]),  # cites others too
            )
            await conn.execute(
                "INSERT INTO memory_changes (guild_id, campaign_id, version, batch, table_name,"
                " row_id, op, after, source, made_at) VALUES (%s, %s, 1, 1, 'memory_claims', %s,"
                " 'insert', '{\"details\": \"my words\"}', 'extractor', 1)",
                (GUILD, self.campaign.id, "a" * 32),
            )
        await self.store.delete_person(GUILD, ME)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT (SELECT count(*) FROM memory_claims) AS claims,"
                " (SELECT count(*) FROM memory_changes WHERE table_name = 'memory_claims') AS log"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["claims"], row["log"]), (0, 0))

    async def test_suggestions_go_even_when_no_line_is_left(self) -> None:
        lines = await self.store.lines(GUILD, self.first)
        mine = next(x for x in lines if x.user_id == ME)
        made = await self.suggestions.suggest(
            GUILD, self.campaign.id, self.first, ME, mine.started_ms, "A fix"
        )
        assert not isinstance(made, str)
        async with self.db.guild(GUILD) as conn:  # their lines went another way (a removed save)
            await conn.execute("DELETE FROM transcript_lines WHERE user_id = %s", (ME,))
        gone = await self.store.delete_person(GUILD, ME)
        self.assertEqual(gone.lines, 0)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM transcript_suggestions")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_i_am_taken_out_of_an_aliass_who_uses_it(self) -> None:
        me = (
            await self.memory.add_entity(
                GUILD, self.campaign.id, type="character", role="player_character",
                name="Mira", source="dm",
                status="confirmed", played_by=ME,
            )
        ).value  # fmt: skip
        place = (
            await self.memory.add_entity(
                GUILD, self.campaign.id, type="place", name="Bryn Shander", source="dm",
                status="confirmed",
            )
        ).value  # fmt: skip
        await self.memory.add_alias(
            GUILD, self.campaign.id, place.id, "Bryn", kind="nickname", source="dm",
            status="confirmed", used_by=me.id,
        )  # fmt: skip
        self.assertEqual(await self.memory.forget_alias_user(GUILD, ME), 1)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT used_by FROM memory_aliases WHERE entity_id = %s AND text = 'Bryn'",
                (place.id,),
            )
            row = await cur.fetchone()
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_changes WHERE table_name = 'memory_aliases'"
                " AND op = 'update'"
            )
            logged = await cur.fetchone()
        assert row is not None and logged is not None
        self.assertIsNone(row["used_by"])  # the alias stays; the person is out of it
        self.assertEqual(logged["n"], 0)  # and an Undo can't put them back

    async def test_a_failure_deletes_nothing(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "CREATE OR REPLACE FUNCTION boom() RETURNS trigger LANGUAGE plpgsql AS"
                " $$ BEGIN RAISE EXCEPTION 'no'; END $$"
            )
        # Make the last step (recounting a session) fail: the whole transaction must roll back.
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "CREATE TRIGGER fail_recount BEFORE UPDATE ON transcript_sessions"
                " FOR EACH ROW EXECUTE FUNCTION boom()"
            )
        try:
            with self.assertRaises(Exception):  # noqa: B017  (any database error)
                await self.store.delete_person(GUILD, ME)
        finally:
            async with self.db.guild(GUILD) as conn:
                await conn.execute("DROP TRIGGER fail_recount ON transcript_sessions")
        counted = await self.store.person_counts(GUILD, ME)
        self.assertEqual((counted.lines, counted.sessions), (3, 2))


class Reference(DatabaseTest):
    def test_a_line_is_named_by_session_time_and_speaker(self) -> None:
        self.assertEqual(line_ref("abc", 1_700_000_001_000, 8), "abc:1700000001000:8")
