"""The start-up wizard's finish run, answered by the bot (#3955, docs/WIZARD_DESIGN.md 4.4): the
campaign is made once, the data-use choice is set, the lore and then the secrets are read through
the lore queue, and the draft is gone at the end. Every write is conditional on this worker still
holding the run. Real Postgres; invented names."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot import wizard_finish
from dmbot.campaigns import CampaignStore
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
        member = SimpleNamespace(id=DM)
        self.guild = SimpleNamespace(get_member=lambda uid: member if uid == DM else None)
        self.bot = SimpleNamespace(
            campaigns=self.campaigns,
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
