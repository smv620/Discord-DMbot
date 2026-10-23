"""Moving a campaign to the new owner's other server (#2532 slice C2): the campaign's row changes
server in one transaction and every row that hangs off it follows (migration 0215), so nothing is
lost, a failure leaves it where it was, and a session, a changed offer, a shared rule set or a
lapsed plan stops the move. The notices never tell the old owner why."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.moves import (
    BUSY,
    CHANGED_HANDS,
    NO_PAID_ROOM,
    SESSION_RUNNING,
    SHARES_RULES,
    HandoverMover,
    Moved,
)
from dmbot.dm_screen.handover import MOVE_FAILED, MOVE_FAILED_NEW_OWNER
from dmbot.memory.models import new_id
from dmbot.recaps.audio.files import AudioFiles
from tests.test_campaign_handover import BUYER, CO_DM, GUILD, NOW, OTHER_GUILD, OWNER, HandoverTest

HEARD_OK, HEARD_NOT = 31, 32  # one speaker who agreed in the new server, one who did not


class Moving(HandoverTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = NOW
        self.told: list[Moved] = []
        self.forgotten: list[str] = []
        self.audio_root = Path(tempfile.mkdtemp())
        self.audio = AudioFiles(self.audio_root)

        async def told(moved: Moved) -> None:
            self.told.append(moved)

        async def consenting(guild: int) -> frozenset[int]:
            return frozenset({HEARD_OK, BUYER})

        self.mover = HandoverMover(
            self.store,
            clock=lambda: self.now,
            consenting=consenting,
            audio=self.audio,
            forgotten=self.forgotten.append,
            told=told,
        )
        self.offer_id = await self.ask_for_move()

    async def ask_for_move(self) -> int:
        """An offer by email, claimed by BUYER who asked for OTHER_GUILD (as the website does)."""
        offer = await self.store.offer_handover_by_email(
            GUILD, self.campaign.id, OWNER, NOW, from_name="Oskar", token_hash="a" * 64
        )
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE campaign_handover_offers SET to_user_id = %s, to_name = 'Bea',"
                " move_to_guild_id = %s WHERE guild_id = %s AND id = %s",
                (BUYER, OTHER_GUILD, GUILD, offer.id),
            )
        return offer.id

    async def move(self) -> str:
        return await self.mover.move(GUILD, self.offer_id)

    async def row_of_offer(self) -> dict[str, Any] | None:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT * FROM campaign_handover_offers WHERE guild_id = %s AND id = %s",
                (GUILD, self.offer_id),
            )
            return await cur.fetchone()

    async def campaigns_in(self, guild: int) -> list[Any]:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute("SELECT id, name, owner_user_id FROM campaigns")
            return list(await cur.fetchall())

    async def sql(self, guild: int, statement: str, *args: Any) -> None:
        async with self.db.guild(guild) as conn:
            await conn.execute(statement, args)

    async def running_session(self) -> None:
        await self.sql(
            GUILD,
            "INSERT INTO active_sessions (guild_id, campaign_id, voice_channel_id,"
            " screen_channel_id, started_at, started_by) VALUES (%s, %s, 1, 2, 3, %s)",
            GUILD, self.campaign.id, OWNER,
        )  # fmt: skip

    async def seed(self) -> None:
        """Rows in the tables a backup misses as well as those it carries."""
        g, c = GUILD, self.campaign.id
        a, b, link, session = new_id(), new_id(), new_id(), new_id()
        for key, name in ((a, "Aaa"), (b, "Bbb")):
            await self.sql(
                g,
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
                " status, source, created_at) VALUES (%s, %s, %s, 'npc', %s, '', 'confirmed',"
                " 'dm', 1)",
                g, c, key, name,
            )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id, predicate,"
            " object_id, detail, confidence, status, source, mention_ids, secret, created_at,"
            " definition_version) VALUES (%s, %s, %s, %s, 'knows', %s, '', 1.0, 'confirmed',"
            " 'dm', '{}', false, 1, 1)",
            g, c, link, a, b,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, ended_at,"
            " line_count) VALUES (%s, %s, %s, 100, 200, 2)",
            session, g, c,
        )  # fmt: skip
        for speaker in (HEARD_OK, HEARD_NOT):
            await self.sql(
                g,
                "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard,"
                " duration_ms) VALUES (%s, %s, %s, %s, 'we meet', 1000)",
                g, session, speaker, speaker,
            )  # fmt: skip
            await self.sql(
                g,
                "INSERT INTO memory_heard (guild_id, campaign_id, entity_id, session_started_at,"
                " speaker_id, times) VALUES (%s, %s, %s, 100, %s, 2)",
                g, c, a, speaker,
            )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id, state, title,"
            " status, approved_at, created_at) VALUES (%s, %s, 100, %s, 'ready', 'Done',"
            " 'posted', 5, 1)",
            g, c, OWNER,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO recap_jobs (guild_id, campaign_id, session_at, dm_user_id, created_at)"
            " VALUES (%s, %s, 300, %s, 1)",  # queued: not ready, no summary
            g, c, OWNER,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO npc_notes (guild_id, campaign_id, entry_id, user_id, name, body,"
            " updated_at, orphaned_at) VALUES (%s, %s, %s, %s, '', 'Likes cheese', 5, NULL)",
            g, c, a, OWNER,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, url, sheet, source,"
            " fetched_at, player_id, ask_mismatch) VALUES (%s, %s, %s,"
            " 'https://www.dndbeyond.com/characters/1', %s::jsonb, 'dndbeyond', 1234, %s, false)",
            g, c, b, json.dumps({"name": "Aldric"}), BUYER,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO game_clocks (guild_id, campaign_id, minute, last_long_rest, channel_id,"
            " message_id, updated_at) VALUES (%s, %s, 5, 5, 11, 12, 1)",
            g, c,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO npcs_channel_messages (guild_id, campaign_id, entry_id, message_id)"
            " VALUES (%s, %s, %s, 77)",
            g, c, a,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO rule_set_follows (guild_id, campaign_id, set_id, kind, set_name,"
            " publisher, version, content, updating, followed_by, followed_at, code)"
            " VALUES (%s, %s, %s, 'homebrew', 'Their rules', 'Pat', 2, '{}'::jsonb, TRUE, %s, 5,"
            " 'ABCDEFGHIJ')",
            g, c, "p" * 32, OWNER,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO npc_roster (guild_id, campaign_id, entry_id, view, updated_at)"
            " VALUES (%s, %s, %s, '{}'::jsonb, 1)",
            g, c, a,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO recap_requests (guild_id, campaign_id, id, session_at, user_id, kind,"
            " body, created_at) VALUES (%s, %s, %s, 100, %s, 'instructions', 'Shorter', 1)",
            g, c, "b" * 32, OWNER,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO session_usage (guild_id, campaign_id, session_started_at, owner_user_id,"
            " updated_at) VALUES (%s, %s, 100, %s, 1)",
            g, c, OWNER,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO rule_sets (guild_id, campaign_id, id, kind, name, status, created_by,"
            " created_at) VALUES (%s, %s, %s, 'homebrew', 'Mine', 'unshared', %s, 1)",
            g, c, "d" * 32, OWNER,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO rule_set_codes (guild_id, campaign_id, code, set_id, active, created_at)"
            " VALUES (%s, %s, 'ABCDEFGHIJ', %s, false, 1)",
            g, c, "d" * 32,
        )  # fmt: skip
        doc, chapter = "e" * 32, "f" * 32
        await self.sql(
            g,
            "INSERT INTO adventure_documents (guild_id, campaign_id, id, file_name, fingerprint,"
            " characters, chunks, added_by, added_at)"
            " VALUES (%s, %s, %s, 'a.txt', repeat('0', 64), 1, 0, %s, 1)",
            g, c, doc, OWNER,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO plan_nodes (guild_id, campaign_id, id, document_id, kind, name, name_key,"
            " summary, quote, chunk_ids, position, created_at)"
            " VALUES (%s, %s, %s, %s, 'chapter', 'One', 'one', '', '', '{}', 0, 1)",
            g, c, chapter, doc,
        )  # fmt: skip
        await self.sql(
            g,
            "INSERT INTO plan_position (guild_id, campaign_id, id, document_id, chapter_id, set_by,"
            " created_at) VALUES (%s, %s, %s, %s, %s, %s, 1)",
            g, c, "9" * 32, doc, chapter, OWNER,
        )  # fmt: skip
        self.audio_file(GUILD, "recaps/100/recap-voice.mp3")

    def audio_file(self, guild: int, relative: str) -> Path:
        path = self.audio_root / str(guild) / self.campaign.id / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"voice")
        return path

    async def campaign_tables(self) -> list[str]:
        # Read from the schema, not from `campaigns/tables.py`: the move must carry what exists,
        # whatever any list says, and a table nobody listed still has to show up here.
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT table_name FROM information_schema.columns"
                " WHERE table_schema = current_schema() AND column_name = 'campaign_id'"
                " ORDER BY 1"
            )
            return [str(r["table_name"]) for r in await cur.fetchall()]

    async def snapshot(self, guild: int, tables: list[str]) -> dict[str, list[str]]:
        """Every row of the campaign in `guild`, one text each without its server."""
        out: dict[str, list[str]] = {}
        async with self.db.guild(guild) as conn:
            for table in tables:
                column = "id" if table == "campaigns" else "campaign_id"
                cur = await conn.execute(
                    f"SELECT (to_jsonb(t) - 'guild_id')::text AS r FROM {table} t"
                    f" WHERE {column} = %s ORDER BY 1",
                    (self.campaign.id,),
                )
                out[table] = [str(r["r"]) for r in await cur.fetchall()]
            cur = await conn.execute(
                "SELECT (to_jsonb(l) - 'guild_id')::text AS r FROM transcript_lines l"
                " WHERE session_id IN (SELECT id FROM transcript_sessions WHERE campaign_id = %s)"
                " ORDER BY 1",
                (self.campaign.id,),
            )
            out["transcript_lines"] = [str(r["r"]) for r in await cur.fetchall()]
        return out

    # ---- the move -----------------------------------------------------------

    async def test_every_row_of_the_campaign_arrives_and_none_is_left_in_the_old_server(
        self,
    ) -> None:
        await self.seed()
        await self.store.add_dm(GUILD, self.campaign.id, CO_DM)
        tables = await self.campaign_tables()
        before = await self.snapshot(GUILD, tables)
        for table in (
            "memory_entities",
            "memory_relations",
            "transcript_sessions",
            "transcript_lines",
            "recap_jobs",
            "npc_notes",
            "character_sheets",
            "rule_set_follows",
            "memory_heard",
            "npc_roster",
            "recap_requests",
            "session_usage",
            "rule_set_codes",
            "plan_position",
        ):
            self.assertTrue(before[table], table)
        self.assertEqual(await self.mover.waiting(GUILD), [self.offer_id])

        self.assertEqual(await self.move(), "moved")

        left = await self.snapshot(GUILD, tables)
        self.assertEqual({t: r for t, r in left.items() if r}, {}, "left in the old server")
        after = await self.snapshot(OTHER_GUILD, tables)
        # What is meant to differ: the campaign row (owner, name, channels), its DMs (the new
        # owner joins), the offer (done), the posted-message list and the clock's message ids
        # (the old server's), and the counts of names said by a speaker not recorded there.
        different = {
            "campaigns",
            "campaign_dms",
            "campaign_handover_offers",
            "npcs_channel_messages",
            "game_clocks",
            "memory_heard",
        }
        for table in set(before) - different:
            self.assertEqual(after[table], before[table], table)
        self.assertEqual(after["npcs_channel_messages"], [])
        self.assertEqual(after["campaign_handover_offers"], [])

    async def test_the_campaign_has_a_new_owner_and_the_old_one_stays_a_dm(self) -> None:
        await self.store.add_dm(GUILD, self.campaign.id, CO_DM)
        await self.sql(
            GUILD,
            "UPDATE campaigns SET dm_screen_channel_id = 5, dmbchat_channel_id = 6"
            " WHERE guild_id = %s AND id = %s",
            GUILD, self.campaign.id,
        )  # fmt: skip
        self.assertEqual(await self.move(), "moved")
        [copy] = await self.campaigns_in(OTHER_GUILD)
        self.assertEqual((copy["id"], copy["owner_user_id"]), (self.campaign.id, BUYER))
        made = await self.store.get(OTHER_GUILD, copy["id"])
        assert made is not None
        self.assertEqual(made.dm_user_ids, frozenset({OWNER, BUYER, CO_DM}))
        self.assertIsNone(made.dm_screen_channel_id)  # the old server's channels are not carried
        self.assertEqual(await self.campaigns_in(GUILD), [])
        self.assertIsNone(await self.row_of_offer())
        self.assertEqual(self.forgotten, [self.campaign.id])
        [told] = self.told
        self.assertEqual(told.result, "moved")

    async def test_a_name_the_new_server_already_has_is_renamed_with_moved(self) -> None:
        await self.store.create(OTHER_GUILD, "Frostmaiden", CO_DM)
        self.assertEqual(await self.move(), "moved")
        names = sorted(c["name"] for c in await self.campaigns_in(OTHER_GUILD))
        self.assertEqual(names, ["Frostmaiden", "Frostmaiden (moved)"])

    async def test_counts_of_names_said_are_kept_only_for_who_agreed_in_the_new_server(
        self,
    ) -> None:
        await self.seed()
        self.assertEqual(await self.move(), "moved")  # (a speaker not recorded there is no failure)
        async with self.db.guild(OTHER_GUILD) as conn:
            cur = await conn.execute("SELECT speaker_id FROM memory_heard")
            self.assertEqual([r["speaker_id"] for r in await cur.fetchall()], [HEARD_OK])

    async def test_a_followed_rule_set_keeps_updating_with_its_code(self) -> None:
        await self.seed()
        self.assertEqual(await self.move(), "moved")
        async with self.db.guild(OTHER_GUILD) as conn:
            cur = await conn.execute("SELECT updating, code FROM rule_set_follows")
            self.assertEqual(await cur.fetchall(), [{"updating": True, "code": "ABCDEFGHIJ"}])

    async def test_a_clock_does_not_point_at_the_old_servers_messages(self) -> None:
        await self.seed()
        self.assertEqual(await self.move(), "moved")
        async with self.db.guild(OTHER_GUILD) as conn:
            cur = await conn.execute("SELECT channel_id, message_id FROM game_clocks")
            self.assertEqual(await cur.fetchall(), [{"channel_id": None, "message_id": None}])

    # ---- recap audio --------------------------------------------------------

    async def test_recap_audio_is_copied_before_and_the_old_files_removed_after(self) -> None:
        await self.seed()
        self.assertEqual(await self.move(), "moved")
        new = self.audio_root / str(OTHER_GUILD) / self.campaign.id / "recaps/100/recap-voice.mp3"
        self.assertEqual(new.read_bytes(), b"voice")
        self.assertFalse((self.audio_root / str(GUILD) / self.campaign.id).exists())

    async def test_a_failure_in_the_move_leaves_the_old_audio_and_no_copy(self) -> None:
        await self.seed()
        with patch.object(CampaignStore, "move_in", side_effect=RuntimeError("boom")):
            self.assertEqual(await self.move(), "reopened")
        old = self.audio_root / str(GUILD) / self.campaign.id / "recaps/100/recap-voice.mp3"
        self.assertTrue(old.is_file())
        self.assertFalse((self.audio_root / str(OTHER_GUILD) / self.campaign.id).exists())
        self.assertEqual(await self.campaigns_in(OTHER_GUILD), [])

    # ---- refusals and failures ----------------------------------------------

    async def assertWhole(self, reason: str) -> None:
        """The campaign is where it was, and the offer is open again."""
        [kept] = await self.campaigns_in(GUILD)
        self.assertEqual(kept["owner_user_id"], OWNER)
        self.assertEqual(await self.campaigns_in(OTHER_GUILD), [])
        offer = await self.row_of_offer()
        assert offer is not None
        self.assertEqual((offer["status"], offer["move_to_guild_id"]), ("open", None))
        self.assertEqual((offer["to_user_id"], offer["move_claimed_at"]), (0, None))
        # who had accepted, and the plain reason, are kept for that person's page
        self.assertEqual((offer["move_failed_for"], offer["move_failed_reason"]), (BUYER, reason))

    async def test_a_crash_leaves_the_campaign_whole_and_opens_the_offer(self) -> None:
        await self.seed()
        tables = ["transcript_sessions", "memory_entities", "recap_jobs"]
        before = await self.snapshot(GUILD, tables)
        with patch.object(CampaignStore, "move_in", side_effect=RuntimeError("boom")):
            self.assertEqual(await self.move(), "reopened")
        await self.assertWhole("DMbot couldn't move it this time.")
        self.assertEqual(await self.snapshot(GUILD, tables), before)

    async def test_the_old_owner_is_never_told_why_but_the_new_owner_is(self) -> None:
        await self.running_session()
        self.assertEqual(await self.move(), "reopened")
        [told] = self.told
        self.assertEqual((told.result, told.reason), ("reopened", SESSION_RUNNING))
        self.assertNotIn("{reason}", MOVE_FAILED)  # the owner's words have no place for it
        self.assertIn("{reason}", MOVE_FAILED_NEW_OWNER)
        self.assertEqual(told.campaign_name, self.campaign.name)

    async def test_a_running_session_stops_the_move(self) -> None:
        await self.running_session()
        self.assertEqual(await self.move(), "reopened")
        await self.assertWhole(SESSION_RUNNING)

    async def test_a_plan_that_lapsed_since_they_accepted_stops_the_move(self) -> None:
        async with self.db.plan_writer(BUYER) as conn:
            await conn.execute(
                "UPDATE entitlements SET status = 'lapsed', lapsed_at = 1 WHERE user_id = %s",
                (BUYER,),
            )
        self.assertEqual(await self.move(), "reopened")
        await self.assertWhole(NO_PAID_ROOM)

    async def test_a_campaign_with_a_shared_rule_set_does_not_move_yet(self) -> None:
        await self.sql(
            GUILD,
            "INSERT INTO rule_sets (guild_id, campaign_id, id, kind, name, status, created_by,"
            " created_at) VALUES (%s, %s, %s, 'house_rules', 'Ours', 'shared', %s, %s)",
            GUILD, self.campaign.id, "c" * 32, OWNER, NOW,
        )  # fmt: skip
        self.assertEqual(await self.move(), "reopened")
        await self.assertWhole(SHARES_RULES)

    async def test_a_rule_set_that_is_not_shared_does_not_stop_it(self) -> None:
        await self.sql(
            GUILD,
            "INSERT INTO rule_sets (guild_id, campaign_id, id, kind, name, status, created_by,"
            " created_at) VALUES (%s, %s, %s, 'house_rules', 'Ours', 'unshared', %s, %s)",
            GUILD, self.campaign.id, "c" * 32, OWNER, NOW,
        )  # fmt: skip
        self.assertEqual(await self.move(), "moved")

    async def test_an_offer_ended_between_the_lease_and_the_lock_moves_nothing(self) -> None:
        real_take = HandoverMover._take

        async def take_then_end(
            mover: HandoverMover, guild_id: int, offer_id: int, now: int
        ) -> Any:
            offer = await real_take(mover, guild_id, offer_id, now)
            await self.sql(
                GUILD,
                "UPDATE campaign_handover_offers SET status = 'withdrawn' WHERE guild_id = %s"
                " AND id = %s",
                GUILD, offer_id,
            )  # fmt: skip
            return offer

        with patch.object(HandoverMover, "_take", take_then_end):
            self.assertEqual(await self.move(), "skipped")
        [kept] = await self.campaigns_in(GUILD)
        self.assertEqual(kept["owner_user_id"], OWNER)
        self.assertEqual(await self.campaigns_in(OTHER_GUILD), [])
        self.assertEqual(self.told, [])

    async def test_another_runs_lease_taken_before_the_lock_stops_this_one(self) -> None:
        real_take = HandoverMover._take

        async def take_then_retake(
            mover: HandoverMover, guild_id: int, offer_id: int, now: int
        ) -> Any:
            offer = await real_take(mover, guild_id, offer_id, now)
            await self.sql(
                GUILD,
                "UPDATE campaign_handover_offers SET move_claimed_at = %s WHERE guild_id = %s"
                " AND id = %s",
                now + 1, GUILD, offer_id,
            )  # fmt: skip
            return offer

        with patch.object(HandoverMover, "_take", take_then_retake):
            self.assertEqual(await self.move(), "skipped")
        self.assertEqual(await self.campaigns_in(OTHER_GUILD), [])

    async def test_two_sweeps_at_once_move_it_once(self) -> None:
        results = await asyncio.gather(self.move(), self.move(), self.move())
        self.assertEqual(sorted(results), ["moved", "skipped", "skipped"])
        self.assertEqual(len(await self.campaigns_in(OTHER_GUILD)), 1)

    async def test_a_move_that_never_ran_is_opened_again_after_an_hour(self) -> None:
        self.now = NOW + 7200
        await self.sql(
            GUILD, "UPDATE campaign_handover_offers SET move_asked_at = %s WHERE guild_id = %s",
            NOW, GUILD,
        )  # fmt: skip
        self.assertEqual(await self.mover.reopen_stuck(GUILD), 1)
        [told] = self.told
        self.assertEqual(told.result, "reopened")
        offer = await self.row_of_offer()
        assert offer is not None
        self.assertIsNone(offer["move_to_guild_id"])

    async def test_a_move_just_asked_for_is_not_opened_again(self) -> None:
        await self.sql(
            GUILD, "UPDATE campaign_handover_offers SET move_asked_at = %s WHERE guild_id = %s",
            NOW, GUILD,
        )  # fmt: skip
        self.assertEqual(await self.mover.reopen_stuck(GUILD), 0)

    # ---- work in the background --------------------------------------------

    async def test_a_recap_job_a_worker_holds_stops_the_move(self) -> None:
        await self.seed()
        await self.sql(
            GUILD,
            "UPDATE recap_jobs SET leased_to = %s WHERE guild_id = %s AND session_at = 100",
            NOW + 300, GUILD,
        )  # fmt: skip
        self.assertEqual(await self.move(), "reopened")
        await self.assertWhole(BUSY)

    async def test_a_recap_waiting_for_its_audio_stops_the_move(self) -> None:
        await self.seed()
        await self.sql(
            GUILD,
            "UPDATE recap_jobs SET audio = 'queued', audio_tries = 0, audio_next_at = 0"
            " WHERE guild_id = %s AND session_at = 100",
            GUILD,
        )  # fmt: skip
        self.assertEqual(await self.move(), "reopened")
        await self.assertWhole(BUSY)

    async def test_the_job_the_move_waits_for_is_the_one_the_audio_worker_picks(self) -> None:
        """One text for the pick, the lease and the move: the seeded job is really due."""
        from dmbot.recaps.store import RecapStore

        await self.seed()
        await self.sql(
            GUILD,
            "UPDATE recap_jobs SET audio = 'queued', audio_tries = 0, audio_next_at = 0"
            " WHERE guild_id = %s AND session_at = 100",
            GUILD,
        )  # fmt: skip
        store = RecapStore(self.db, clock=lambda: float(NOW))
        self.assertEqual(await store.audio_due(GUILD), [(self.campaign.id, 100)])
        self.assertEqual(await self.move(), "reopened")  # the move stops for the same job
        await self.assertWhole(BUSY)

    async def test_an_import_being_read_stops_the_move(self) -> None:
        await self.seed()
        sid = (await self.snapshot(GUILD, ["transcript_sessions"]))["transcript_sessions"][0]
        session_id = json.loads(sid)["id"]
        await self.sql(
            GUILD,
            "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id, file_name,"
            " imported_by, imported_at, state, claimed_at) VALUES (%s, %s, %s, %s, 'f.txt', %s, 1,"
            " 'working', 1)",
            GUILD, self.campaign.id, session_id, "a" * 32, OWNER,
        )  # fmt: skip
        self.assertEqual(await self.move(), "reopened")
        await self.assertWhole(BUSY)

    async def test_a_session_winding_down_in_the_bot_stops_the_move(self) -> None:
        self.mover._busy = lambda guild, campaign: True
        self.assertEqual(await self.move(), "reopened")
        await self.assertWhole(BUSY)

    async def test_an_audio_file_finished_after_the_copy_still_arrives(self) -> None:
        await self.seed()
        real = CampaignStore.move_in

        async def late_file(store: CampaignStore, *a: Any, **kw: Any) -> str:
            self.audio_file(GUILD, "recaps/300/recap-mix.mp3")  # the worker finishes meanwhile
            return await real(store, *a, **kw)

        with patch.object(CampaignStore, "move_in", late_file):
            self.assertEqual(await self.move(), "moved")
        base = self.audio_root / str(OTHER_GUILD) / self.campaign.id / "recaps"
        self.assertTrue((base / "300/recap-mix.mp3").is_file())
        self.assertTrue((base / "100/recap-voice.mp3").is_file())
        self.assertFalse((self.audio_root / str(GUILD) / self.campaign.id).exists())

    async def test_a_campaign_that_changed_hands_ends_the_offer_and_tells_only_the_new_owner(
        self,
    ) -> None:
        await self.sql(
            GUILD,
            "UPDATE campaigns SET owner_user_id = %s WHERE guild_id = %s AND id = %s",
            CO_DM, GUILD, self.campaign.id,
        )  # fmt: skip
        self.assertEqual(await self.move(), "ended")
        offer = await self.row_of_offer()
        assert offer is not None
        self.assertEqual(offer["status"], "withdrawn")
        [told] = self.told
        self.assertEqual((told.result, told.reason), ("ended", CHANGED_HANDS))
        self.assertEqual(await self.campaigns_in(OTHER_GUILD), [])

    async def test_a_claim_made_after_the_checks_waits_for_the_move_and_finds_nothing(self) -> None:
        """The worker's lease update takes a lock on the job's row, which the move holds."""
        await self.seed()
        real = CampaignStore.move_in
        inside = asyncio.Event()
        took: dict[str, Any] = {}

        async def slow(store: CampaignStore, *a: Any, **kw: Any) -> str:
            inside.set()
            await asyncio.sleep(0.5)  # the move holds its locks meanwhile
            return await real(store, *a, **kw)

        async def worker() -> None:
            await inside.wait()
            began = asyncio.get_running_loop().time()
            async with self.db.guild(GUILD) as conn:  # a second connection
                cur = await conn.execute(
                    "UPDATE recap_jobs SET leased_to = %s WHERE guild_id = %s AND campaign_id = %s"
                    " AND session_at = 300 RETURNING session_at",
                    (NOW + 300, GUILD, self.campaign.id),
                )
                took["rows"] = await cur.fetchall()
            took["waited"] = asyncio.get_running_loop().time() - began

        with patch.object(CampaignStore, "move_in", slow):
            moved, _ = await asyncio.gather(self.move(), worker())
        self.assertEqual(moved, "moved")
        self.assertEqual(took["rows"], [])  # the job is no longer in the old server to lease
        self.assertGreater(took["waited"], 0.3)  # it waited for the move to commit
