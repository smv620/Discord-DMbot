"""The start-up wizard's finish run, answered by the bot (#3955, docs/WIZARD_DESIGN.md 4.4): the
campaign is made once, the data-use choice is set, the lore and then the secrets are read through
the lore queue, and the draft is gone at the end. Every write is conditional on this worker still
holding the run. Real Postgres; invented names."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot import wizard_finish
from dmbot.campaigns import CampaignStore
from dmbot.dm_screen import channel_sync
from dmbot.memory.store import MemoryStore
from tests.pg import DatabaseTest

GUILD = 111
DM = 1001
NOW = 1_800_000_000
RUN = "a" * 32


class FinishTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.runs = wizard_finish.WizardFinishes(self.db, clock=lambda: NOW)

        # The member cache is empty (no members intent): who is in the server is asked for.
        async def fetch_member(uid: int) -> Any:
            if uid != DM:
                raise discord.NotFound(MagicMock(status=404, reason="x"), "gone")
            return SimpleNamespace(id=uid)

        self.guild = SimpleNamespace(
            id=GUILD, get_member=lambda uid: None, fetch_member=AsyncMock(side_effect=fetch_member)
        )
        channel_sync._seen.clear()
        self.addCleanup(channel_sync._seen.clear)
        self.bot = SimpleNamespace(
            campaigns=self.campaigns,
            memory=MemoryStore(self.db),
            get_guild=lambda gid: self.guild if gid == GUILD else None,
            create_refusal=AsyncMock(return_value=None),
        )
        self.worker: asyncio.Task[None] | None = None

    async def asyncTearDown(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
        await super().asyncTearDown()

    async def sql(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(sql, args)
            return list(await cur.fetchall()) if cur.description else []

    async def draft(
        self, answers: dict[str, Any] | None = None, docs: tuple[tuple[str, str, str], ...] = (),
        *, rights: int = NOW, user: int = DM,
    ) -> None:  # fmt: skip
        await self.sql(
            "INSERT INTO wizard_drafts (guild_id, user_id, answers, rights_shared_at, created_at,"
            " updated_at, expires_at) VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s)",
            GUILD, user, json.dumps(answers or {"name": "Frostmaiden", "data_use": True}), rights,
            NOW, NOW, NOW + 604800,
        )  # fmt: skip
        for n, (slot, name, text) in enumerate(docs):
            source = "drive" if text == "@drive" else "upload"
            await self.sql(
                "INSERT INTO wizard_documents (guild_id, user_id, id, slot, name, source, text,"
                " status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 'ok', %s)",
                GUILD, user, f"{n:032x}", slot, name, source,
                "" if source == "drive" else text, NOW,
            )  # fmt: skip

    async def queue(self, run_id: str = RUN, user: int = DM) -> None:
        await self.sql(
            "INSERT INTO wizard_finishes (guild_id, user_id, id, created_at, moved_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            GUILD, user, run_id, NOW, NOW,
        )  # fmt: skip

    async def run_row(self, run_id: str = RUN) -> dict[str, Any]:
        [row] = await self.sql("SELECT * FROM wizard_finishes WHERE id = %s", run_id)
        return row

    def reader(self) -> None:
        """Stand in for the lore reader: every queued upload is finished, in the order queued,
        and the order is kept."""
        self.seen: list[tuple[str, str]] = []

        async def work() -> None:
            while True:
                rows = await self.sql(
                    "SELECT id, choice, text FROM lore_uploads WHERE state = 'waiting'"
                    " ORDER BY seq LIMIT 1"
                )
                if rows:
                    self.seen.append((rows[0]["choice"], rows[0]["text"]))
                    await self.sql(
                        "UPDATE lore_uploads SET state = 'done', text = '' WHERE id = %s",
                        rows[0]["id"],
                    )
                await asyncio.sleep(0.01)

        self.worker = asyncio.create_task(work())


class TheRun(FinishTest):
    async def test_the_campaign_is_made_the_lore_then_the_secrets_are_read_and_the_draft_goes(
        self,
    ) -> None:
        await self.draft(docs=(("secrets", "s.txt", "The red door hides it."),
                               ("lore", "l.txt", "The town of Bryn Shander.")))  # fmt: skip
        await self.queue()
        self.reader()
        with patch.object(wizard_finish, "LORE_POLL_S", 0.02):
            self.assertEqual(await wizard_finish.run_round(self.bot, GUILD), 1)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"], row["stage"]), ("done", "", "clean"))
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        self.assertEqual((campaign.name, row["made_campaign"]), ("Frostmaiden", campaign.id))
        [shared] = await self.sql("SELECT share_improve FROM campaigns WHERE id = %s", campaign.id)
        self.assertTrue(shared["share_improve"])  # the data-use choice
        # lore (what everyone knows) is read before the secrets (spoilers), whatever the order added
        self.assertEqual([c for c, _ in self.seen], ["world", "spoilers"])
        self.assertEqual(await self.sql("SELECT 1 FROM wizard_drafts"), [])
        self.assertEqual(await self.sql("SELECT 1 FROM wizard_documents"), [])

    async def test_the_right_to_use_is_recorded_on_the_campaign_with_who_and_when(self) -> None:
        await self.draft(docs=(("lore", "l.txt", "Words."),))
        await self.queue()
        self.reader()
        with patch.object(wizard_finish, "LORE_POLL_S", 0.02):
            await wizard_finish.run_round(self.bot, GUILD)
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        [conf] = await self.campaigns.confirmations(GUILD, campaign.id)
        self.assertEqual(conf["user"], str(DM))
        [up] = await self.sql("SELECT rights_by, rights_at FROM lore_uploads")
        self.assertEqual((up["rights_by"], up["rights_at"]), (DM, NOW))

    async def test_a_press_after_a_lost_run_makes_no_second_campaign(self) -> None:
        await self.draft()
        await self.queue()
        first = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        await self.sql(
            "UPDATE wizard_finishes SET state = 'failed', error = 'lost', made_campaign = %s",
            first.id,
        )
        await self.queue("b" * 32)
        await wizard_finish.run_round(self.bot, GUILD)
        self.assertEqual(len(await self.campaigns.list_campaigns(GUILD)), 1)
        row = await self.run_row("b" * 32)
        self.assertEqual((row["state"], row["made_campaign"]), ("done", first.id))

    async def test_a_run_taken_back_stops_and_writes_nothing_more(self) -> None:
        await self.draft(docs=(("lore", "l.txt", "Words."),))
        await self.queue()
        run = await self.runs.claim_next(GUILD)
        assert run is not None
        await self.sql("UPDATE wizard_finishes SET state = 'failed', error = 'lost'")
        with self.assertRaises(wizard_finish.Lost):
            await self.runs.move(GUILD, run, stage="lore", part=1, of_parts=1)
        self.assertFalse(await self.runs.end(GUILD, run, ok=True))
        self.assertEqual((await self.run_row())["error"], "lost")

    async def test_a_second_worker_cannot_move_a_run_the_first_one_still_holds(self) -> None:
        await self.queue()
        first = await self.runs.claim_next(GUILD)
        assert first is not None
        self.assertIsNone(await self.runs.claim_next(GUILD))  # (nothing else waiting)
        stale = wizard_finish.Run(first.id, DM, first.claimed_at - 1)
        with self.assertRaises(wizard_finish.Lost):
            await self.runs.move(GUILD, stale, stage="lore")


class TheRefusals(FinishTest):
    async def test_a_plan_with_no_room_ends_the_run_and_makes_nothing(self) -> None:
        await self.draft()
        await self.queue()
        self.bot.create_refusal = AsyncMock(return_value="No room.")
        await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("failed", "plan"))
        self.assertEqual(await self.campaigns.list_campaigns(GUILD), [])

    async def test_someone_who_left_the_server_gets_nothing(self) -> None:
        await self.draft(user=DM + 1)
        await self.queue(user=DM + 1)
        await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("failed", "not-allowed"))
        self.assertEqual(await self.campaigns.list_campaigns(GUILD), [])

    async def test_answers_the_door_let_through_are_checked_again(self) -> None:
        # (the door keeps the key list; the shapes are the bot's to re-check)
        await self.draft({"name": "Frostmaiden", "target": "3000"})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("failed", "bad_answers"))
        self.assertEqual(await self.campaigns.list_campaigns(GUILD), [])

    async def test_a_draft_that_is_gone_ends_the_run(self) -> None:
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        self.assertEqual((await self.run_row())["error"], "gone")

    async def test_a_run_that_keeps_stopping_the_bot_is_failed_not_retaken_for_ever(self) -> None:
        await self.draft()
        await self.queue()
        await self.sql("UPDATE wizard_finishes SET tries = %s", wizard_finish.MAX_TRIES)
        await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("failed", "failed"))
        self.assertEqual(await self.campaigns.list_campaigns(GUILD), [])


class TheDocuments(FinishTest):
    async def test_a_picked_drive_file_ends_the_run_with_its_own_word_and_the_rest_stands(
        self,
    ) -> None:
        await self.draft(docs=(("lore", "d.gdoc", "@drive"), ("secrets", "s.txt", "Words.")))
        await self.queue()
        self.reader()
        with patch.object(wizard_finish, "LORE_POLL_S", 0.02):
            await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("done", "drive_gone"))
        self.assertEqual(len(await self.campaigns.list_campaigns(GUILD)), 1)
        self.assertEqual([c for c, _ in self.seen], ["spoilers"])  # (the other document was read)

    async def test_the_progress_rows_hold_stage_codes_and_counts_only(self) -> None:
        await self.draft(docs=(("lore", "Mirelle Vane.txt", "The red door."),))
        await self.queue()
        self.reader()
        with patch.object(wizard_finish, "LORE_POLL_S", 0.02):
            await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertNotIn("Mirelle", str(row))
        self.assertNotIn("red door", str(row))
        self.assertIn(row["stage"], wizard_finish.STAGES)


class TheSweep(FinishTest):
    async def test_drafts_past_their_week_are_removed_with_their_documents(self) -> None:
        await self.draft(docs=(("lore", "l.txt", "Words."),))
        await self.sql("UPDATE wizard_drafts SET expires_at = %s", NOW - 1)
        await self.draft({"name": "Other"}, user=DM + 5)
        self.assertEqual(await self.runs.sweep(GUILD), 1)
        left = await self.sql("SELECT user_id FROM wizard_drafts")
        self.assertEqual([r["user_id"] for r in left], [DM + 5])
        self.assertEqual(await self.sql("SELECT 1 FROM wizard_documents"), [])


class TheReviewedGaps(FinishTest):
    async def test_documents_without_the_rights_confirmation_are_never_read(self) -> None:
        await self.draft(docs=(("lore", "l.txt", "Words."),), rights=0)
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("failed", "no_rights"))
        self.assertEqual(await self.sql("SELECT 1 FROM lore_uploads"), [])
        self.assertEqual(await self.campaigns.list_campaigns(GUILD), [])  # (nothing made either)
        self.assertEqual(len(await self.sql("SELECT 1 FROM wizard_drafts")), 1)  # (kept to fix)

    async def test_a_new_wizard_after_a_finished_one_makes_its_own_campaign(self) -> None:
        await self.draft({"name": "Frostmaiden"})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        await self.draft({"name": "Sunken City"})  # (the old finished row is still there)
        await self.queue("b" * 32)
        await wizard_finish.run_round(self.bot, GUILD)
        names = sorted(c.name for c in await self.campaigns.list_campaigns(GUILD))
        self.assertEqual(names, ["Frostmaiden", "Sunken City"])
        self.assertEqual(
            len(await self.sql("SELECT 1 FROM wizard_finishes WHERE state = 'done'")), 2
        )

    async def test_a_campaign_made_but_never_recorded_is_found_not_made_again(self) -> None:
        await self.draft()
        await self.queue()
        made = await self.campaigns.create(GUILD, "Frostmaiden", DM)  # (the worker then stopped)
        await self.sql("UPDATE campaigns SET created_at = %s WHERE id = %s", NOW + 5, made.id)
        await wizard_finish.run_round(self.bot, GUILD)
        self.assertEqual([c.id for c in await self.campaigns.list_campaigns(GUILD)], [made.id])
        self.assertEqual((await self.run_row())["made_campaign"], made.id)

    async def test_the_right_to_use_is_recorded_once_however_often_the_run_is_tried(self) -> None:
        await self.draft(docs=(("lore", "l.txt", "Words."),))
        await self.queue()
        self.reader()
        with patch.object(wizard_finish, "LORE_POLL_S", 0.02):
            await wizard_finish.run_round(self.bot, GUILD)
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        await self.draft(docs=(("lore", "l.txt", "Words."),))  # (a retry of the same campaign)
        draft = await self.runs.draft(GUILD, DM)
        assert draft is not None
        await wizard_finish._record_rights(self.bot, GUILD, campaign.id, DM, draft)
        self.assertEqual(len(await self.campaigns.confirmations(GUILD, campaign.id)), 1)

    async def test_a_run_that_made_its_campaign_and_lost_its_draft_is_done_not_gone(self) -> None:
        await self.queue()
        made = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        await self.sql("UPDATE wizard_finishes SET made_campaign = %s", made.id)
        await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("done", ""))

    async def test_a_server_list_that_is_not_ready_leaves_the_run_for_a_retake(self) -> None:
        await self.draft()
        await self.queue()
        self.bot.get_guild = lambda gid: None
        await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual(row["state"], "working")  # (not failed: the next claim retakes it)
        self.assertEqual(await self.campaigns.list_campaigns(GUILD), [])

    async def test_a_worker_that_lost_the_run_cannot_delete_the_draft(self) -> None:
        await self.draft()
        await self.queue()
        run = await self.runs.claim_next(GUILD)
        assert run is not None
        await self.sql("UPDATE wizard_finishes SET state = 'failed'")
        await self.runs.delete_draft(GUILD, run)
        self.assertEqual(len(await self.sql("SELECT 1 FROM wizard_drafts")), 1)

    async def test_a_document_the_reader_failed_ends_the_run_with_lore_failed_and_the_rest_stands(
        self,
    ) -> None:
        await self.draft(docs=(("lore", "a.txt", "One."), ("lore", "b.txt", "Two.")))
        await self.queue()

        async def work() -> None:
            first = True
            while True:
                rows = await self.sql("SELECT id FROM lore_uploads WHERE state = 'waiting' LIMIT 1")
                if rows:
                    await self.sql(
                        "UPDATE lore_uploads SET state = %s, text = '' WHERE id = %s",
                        "failed" if first else "done", rows[0]["id"],
                    )  # fmt: skip
                    first = False
                await asyncio.sleep(0.01)

        self.worker = asyncio.create_task(work())
        with patch.object(wizard_finish, "LORE_POLL_S", 0.02):
            await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("done", "lore_failed"))
        self.assertEqual(len(await self.sql("SELECT 1 FROM lore_uploads")), 2)  # (both tried)
        self.assertEqual(len(await self.campaigns.list_campaigns(GUILD)), 1)

    async def test_an_upload_nobody_reads_in_time_is_taken_out_and_its_words_blanked(self) -> None:
        await self.draft(docs=(("lore", "a.txt", "Secret words."),))
        await self.queue()
        with (
            patch.object(wizard_finish, "LORE_POLL_S", 0.01),
            patch.object(wizard_finish, "LORE_WAIT_S", 0.05),
        ):
            await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("done", "lore_failed"))
        [up] = await self.sql("SELECT state, text FROM lore_uploads")
        self.assertEqual((up["state"], up["text"]), ("failed", ""))

    async def test_an_upload_already_open_for_the_campaign_is_waited_for_not_collided_with(
        self,
    ) -> None:
        await self.draft(docs=(("lore", "a.txt", "One."),))
        await self.queue()
        made = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        await self.sql("UPDATE wizard_finishes SET made_campaign = %s", made.id)
        await self.sql(
            "INSERT INTO lore_uploads (guild_id, campaign_id, id, user_id, source, text, choice,"
            " rights_by, rights_at, created_at, updated_at) VALUES (%s, %s, %s, %s, 'file', 'x',"
            " 'world', %s, %s, %s, %s)",
            GUILD, made.id, "c" * 32, DM, DM, NOW, NOW, NOW,
        )  # fmt: skip
        self.reader()
        with patch.object(wizard_finish, "LORE_POLL_S", 0.02):
            await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("done", ""))


class TheMembershipAndTheCampaignSearch(FinishTest):
    async def test_a_dm_the_member_cache_lacks_is_still_found_by_asking_discord(self) -> None:
        # (no members intent: `get_member` is empty for everyone, yet the DM is in the server)
        await self.draft()
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        self.assertEqual((await self.run_row())["state"], "done")

    async def test_a_discord_hiccup_does_not_turn_a_dm_away(self) -> None:
        await self.draft()
        await self.queue()
        hiccup = discord.HTTPException(MagicMock(status=500, reason="x"), "down")
        self.guild.fetch_member = AsyncMock(side_effect=hiccup)
        await wizard_finish.run_round(self.bot, GUILD)
        self.assertEqual((await self.run_row())["state"], "done")

    async def test_an_older_campaign_with_the_same_name_is_never_adopted(self) -> None:
        old = await self.campaigns.create(GUILD, "Frostmaiden", DM)  # (made well before the run)
        await self.sql("UPDATE campaigns SET created_at = 1 WHERE id = %s", old.id)
        await self.draft({"name": "Frostmaiden", "data_use": True})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        # the name is taken, so no campaign is made and the old one is left as it is
        self.assertEqual([c.id for c in await self.campaigns.list_campaigns(GUILD)], [old.id])
        row = await self.run_row()
        self.assertEqual((row["state"], row["made_campaign"]), ("failed", ""))
        [shared] = await self.sql("SELECT share_improve FROM campaigns WHERE id = %s", old.id)
        self.assertFalse(shared["share_improve"])  # (its data-use switch was never touched)

    async def test_the_claimed_run_carries_the_time_the_page_queued_it(self) -> None:
        await self.queue()
        run = await self.runs.claim_next(GUILD)
        assert run is not None
        self.assertEqual(run.started_at, NOW)


class TheCharacters(FinishTest):
    async def made(self) -> Any:
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        found = await self.bot.memory.entities(GUILD, campaign.id)
        return [e for e in found if e.role == "player_character"]

    async def test_the_confirmed_characters_are_made_as_player_characters_with_their_players(
        self,
    ) -> None:
        chars = [
            {"full": "Cerric Vale", "party_name": "Cerric", "sheet_url": "", "player": str(DM)},
            {"full": "Mira Dawn", "party_name": "", "sheet_url": "", "player": "pending"},
        ]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        by_name = {e.name: e for e in await self.made()}
        self.assertEqual(set(by_name), {"Cerric Vale", "Mira Dawn"})
        for entity in by_name.values():
            self.assertEqual((entity.role, entity.status), ("player_character", "confirmed"))
        self.assertEqual(by_name["Cerric Vale"].played_by, DM)
        self.assertIsNone(by_name["Mira Dawn"].played_by)  # (pending: answered later)
        self.assertEqual((await self.run_row())["state"], "done")

    async def test_a_player_number_who_is_not_in_the_server_is_not_kept(self) -> None:
        chars = [{"full": "Cerric Vale", "party_name": "", "sheet_url": "", "player": "9999"}]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        [entity] = await self.made()
        self.assertIsNone(entity.played_by)  # (the page cannot be trusted with ids)

    async def test_a_retried_run_adds_no_character_twice(self) -> None:
        chars = [{"full": "Cerric Vale", "party_name": "", "sheet_url": "", "player": "pending"}]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        again = await wizard_finish._make_characters(
            self.bot, self.guild, GUILD, campaign.id, {"characters": chars}
        )
        self.assertEqual(again, 0)
        self.assertEqual(len(await self.made()), 1)

    async def test_the_progress_shows_the_party_stage_by_its_code(self) -> None:
        self.assertIn("party", wizard_finish.STAGES)
        order = list(wizard_finish.STAGES)
        self.assertLess(order.index("data_use"), order.index("party"))
        self.assertLess(order.index("party"), order.index("lore"))
