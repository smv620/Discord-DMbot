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
import psycopg

from dmbot import wizard_finish
from dmbot.campaigns import CampaignStore
from dmbot.dm_screen import channel_sync
from dmbot.memory import sheets
from dmbot.memory.sheet_store import SheetStore
from dmbot.memory.store import MemoryStore
from dmbot.rules.homebrew import HomebrewStore
from dmbot.rules.homebrew_import import HomebrewImporter
from dmbot.rules.homebrew_sources import SourceStore
from dmbot.rules.house import HouseRuleStore
from tests.pg import DatabaseTest
from tests.test_homebrew_import import URL as HOMEBREW_URL
from tests.test_homebrew_import import Fetches
from tests.test_sheets import answer as sheet_answer

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
            house_rules=HouseRuleStore(self.db),
            homebrew=HomebrewStore(self.db),
            sources=SourceStore(self.db),
            importer=HomebrewImporter(
                HomebrewStore(self.db), SourceStore(self.db), fetch=Fetches()
            ),
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


class TheProgress(FinishTest):
    """The finer counts on the finish row while the lore is read (#4213): numbers and one stage
    word, copied from the reader's own row, at most once a second, cleared between documents."""

    def slow_reader(self) -> None:
        async def work() -> None:
            while True:
                rows = await self.sql(
                    "SELECT id FROM lore_uploads WHERE state = 'waiting' ORDER BY seq LIMIT 1"
                )
                if rows:
                    for done, names in ((10, 2), (60, 5), (120, 9)):
                        await self.sql(
                            "UPDATE lore_uploads SET state = 'working', stage = 'first_read',"
                            " done = %s, total = 120, names_found = %s, links_found = 1"
                            " WHERE id = %s",
                            done, names, rows[0]["id"],
                        )  # fmt: skip
                        await asyncio.sleep(0.15)
                    await self.sql(
                        "UPDATE lore_uploads SET state = 'done', text = '' WHERE id = %s",
                        rows[0]["id"],
                    )
                await asyncio.sleep(0.01)

        self.worker = asyncio.create_task(work())

    async def watch(self) -> list[tuple[Any, ...]]:
        seen: list[tuple[Any, ...]] = []

        async def look() -> None:
            while True:
                rows = await self.sql(
                    "SELECT pass_word, pass_done, pass_total, found_names, found_links"
                    " FROM wizard_finishes"
                )
                if rows:
                    seen.append(tuple(rows[0].values()))
                await asyncio.sleep(0.01)

        self.watcher: asyncio.Task[None] = asyncio.create_task(look())
        return seen

    async def test_the_readers_counts_reach_the_finish_row_and_clear_at_the_end(self) -> None:
        await self.draft(docs=(("lore", "l.txt", "Words."),))
        await self.queue()
        self.slow_reader()
        seen = await self.watch()
        with (
            patch.object(wizard_finish, "LORE_POLL_S", 0.05),
            patch.object(wizard_finish, "PROGRESS_EVERY_S", 0.0),
        ):
            await wizard_finish.run_round(self.bot, GUILD)
        self.worker and self.worker.cancel()
        self.watcher.cancel()
        self.assertIn(("first_read", 60, 120, 5, 1), seen)
        self.assertEqual(
            await self.sql(
                "SELECT pass_word, pass_done, pass_total, found_names, found_links"
                " FROM wizard_finishes"
            ),
            [{"pass_word": "", "pass_done": 0, "pass_total": 0, "found_names": 0,
              "found_links": 0}],
        )  # fmt: skip

    async def test_at_most_one_write_a_second_for_a_run(self) -> None:
        await self.draft(docs=(("lore", "l.txt", "Words."),))
        await self.queue()
        self.slow_reader()
        seen = await self.watch()
        with patch.object(wizard_finish, "LORE_POLL_S", 0.02):  # (polls every 20 ms, cap 1 s)
            await wizard_finish.run_round(self.bot, GUILD)
        self.worker and self.worker.cancel()
        self.watcher.cancel()
        counted = {row for row in seen if row[1] > 0}
        self.assertLessEqual(len(counted), 1)  # (the whole read took under a second)

    async def test_the_state_door_sends_the_counts_and_the_stage_word_and_only_a_word_fits(
        self,
    ) -> None:
        # (the door needs a signed-in member of the server, so its text is read back instead)
        [door] = await self.sql(
            "SELECT pg_get_functiondef(p.oid) AS d FROM pg_proc p"
            " WHERE p.pronamespace = current_schema()::regnamespace"
            " AND p.proname = 'dmbot_web_wizard_finish_state'"
        )
        for key, column in (
            ("pass", "pass_word"), ("passDone", "pass_done"), ("passTotal", "pass_total"),
            ("foundNames", "found_names"), ("foundLinks", "found_links"),
        ):  # fmt: skip
            self.assertIn(f"'{key}', r.{column}", door["d"])
        await self.queue()
        with self.assertRaises(psycopg.errors.CheckViolation):  # (a sentence is not a pass word)
            await self.sql("UPDATE wizard_finishes SET pass_word = 'Belleros sold it'")


class TheHouseRules(FinishTest):
    """Step 7's house-rule documents are read into the campaign (#4211) through the House rules
    page's own reader; the typed box is stored by the page as a document and read the same way."""

    async def rules(self) -> list[tuple[int, str]]:
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        return sorted(
            (r.number, r.rule) for r in await self.bot.house_rules.list(GUILD, campaign.id)
        )

    async def finish(self, docs: tuple[tuple[str, str, str], ...]) -> dict[str, Any]:
        await self.draft(docs=docs)
        await self.queue()
        self.reader()
        with patch.object(wizard_finish, "LORE_POLL_S", 0.02):
            await wizard_finish.run_round(self.bot, GUILD)
        return await self.run_row()

    async def test_a_numbered_file_is_added_with_its_numbers(self) -> None:
        text = (
            "House rules\n3. Potions are bonus.\n"
            "7. Crits max the die. (instead of: Crits double the dice.)"
        )
        row = await self.finish((("house", "rules.txt", text),))
        self.assertEqual((row["state"], row["error"], row["stage"]), ("done", "", "clean"))
        self.assertEqual(await self.rules(), [(3, "Potions are bonus."), (7, "Crits max the die.")])

    async def test_a_typed_list_with_no_numbers_is_numbered_in_order(self) -> None:
        text = "# my rules\nFlanking gives advantage.\n\nHealing potions are a bonus action."
        await self.finish((("house", "Typed house rules", text),))
        self.assertEqual(
            await self.rules(),
            [(1, "Flanking gives advantage."), (2, "Healing potions are a bonus action.")],
        )

    async def test_a_document_with_no_rule_keeps_its_word_and_the_campaign_stands(self) -> None:
        row = await self.finish((("house", "empty.txt", "# only a note\n"),
                                 ("house", "ok.txt", "1. One rule.")))  # fmt: skip
        self.assertEqual(row["state"], "done")
        self.assertEqual(row["error"], "house_unreadable")
        self.assertIn("house_unreadable:1", row["notes"])
        self.assertEqual(await self.rules(), [(1, "One rule.")])

    async def test_two_plain_documents_keep_every_rule(self) -> None:
        await self.finish((
            ("house", "a.txt", "Flanking gives advantage.\nPotions are bonus."),
            ("house", "Typed house rules", "Crits max the die.\nNo evil gods."),
        ))  # fmt: skip
        self.assertEqual(len(await self.rules()), 4)

    async def test_two_numbered_files_with_the_same_numbers_keep_every_rule(self) -> None:
        await self.finish((
            ("house", "a.txt", "1. First A.\n2. Second A."),
            ("house", "b.txt", "1. First B.\n2. Second B."),
        ))  # fmt: skip
        self.assertEqual(
            sorted(r for _, r in await self.rules()),
            ["First A.", "First B.", "Second A.", "Second B."],
        )

    async def test_bullets_and_numbers_with_a_bracket_are_dropped_from_a_plain_list(self) -> None:
        await self.finish((("house", "t", "- Flanking gives advantage.\n1) Potions are bonus."),))
        self.assertEqual(
            sorted(r for _, r in await self.rules()),
            ["Flanking gives advantage.", "Potions are bonus."],
        )

    async def test_no_house_rules_store_gives_the_failed_word_and_the_campaign_stands(self) -> None:
        self.bot.house_rules = None
        row = await self.finish((("house", "rules.txt", "1. One rule."),))
        self.assertEqual((row["state"], row["error"]), ("done", "house_failed"))

    async def test_a_retaken_run_adds_nothing_twice(self) -> None:
        docs = (("house", "rules.txt", "1. One rule.\n2. Another."),)
        await self.finish(docs)
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        from dmbot import wizard_house

        again = await wizard_house.read_documents(
            self.bot.house_rules, GUILD, campaign.id, DM, [{"text": docs[0][2], "name": "r"}]
        )
        self.assertEqual(again[0], 0)
        self.assertEqual(len(await self.rules()), 2)

    async def test_house_is_a_stage_and_nothing_is_left_for_the_pages(self) -> None:
        self.assertIn("house", wizard_finish.STAGES)
        row = await self.finish((("house", "rules.txt", "1. One rule."),))
        self.assertNotEqual(row["error"], wizard_finish.DOCS_LEFT)

    async def test_a_homebrew_file_is_loaded_and_a_file_with_no_entry_keeps_its_word(self) -> None:
        row = await self.finish((
            ("homebrew", "h.txt", "## Hollow King (monster)\n\nIt rules the crypt.\n"),
            ("homebrew", "empty.txt", "# a note only\n"),
        ))  # fmt: skip
        self.assertEqual((row["state"], row["error"]), ("done", "homebrew_unreadable"))
        self.assertIn("homebrew_unreadable:1", row["notes"])
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        self.assertEqual(
            [e.name for e in await self.bot.homebrew.list(GUILD, campaign.id)], ["Hollow King"]
        )

    async def test_a_homebrew_link_is_added_and_a_bad_one_is_counted(self) -> None:
        await self.draft(
            {"name": "Frostmaiden", "data_use": True,
             "homebrew_links": [HOMEBREW_URL, "https://example.com/x"]},
        )  # fmt: skip
        await self.queue()
        self.reader()
        with patch.object(wizard_finish, "LORE_POLL_S", 0.02):
            await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("done", "homebrew_link_failed"))
        self.assertIn("homebrew_link_failed:1", row["notes"])
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        self.assertEqual(len(await self.bot.homebrew.list(GUILD, campaign.id)), 1)

    async def test_links_without_the_right_to_use_confirmation_read_nothing(self) -> None:
        await self.draft({"name": "Frostmaiden", "homebrew_links": [HOMEBREW_URL]}, rights=0)
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("failed", "no_rights"))
        self.assertEqual(await self.sql("SELECT 1 FROM site_approvals"), [])

    async def test_a_homebrew_document_without_the_confirmation_reads_nothing(self) -> None:
        await self.draft(docs=(("homebrew", "h.txt", "## Hollow King (monster)\n\nWords.\n"),),
                         rights=0)  # fmt: skip
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        self.assertEqual((await self.run_row())["error"], "no_rights")

    async def test_a_second_run_does_not_double_the_notes(self) -> None:
        notes = wizard_finish._with_pairs("homebrew_unreadable:1", {"homebrew_unreadable": 1})
        self.assertEqual(notes, "homebrew_unreadable:1")

    async def test_the_door_refuses_finish_for_links_without_the_confirmation(self) -> None:
        [door] = await self.sql(
            "SELECT pg_get_functiondef(p.oid) AS d FROM pg_proc p"
            " WHERE p.pronamespace = current_schema()::regnamespace"
            " AND p.proname = 'dmbot_web_wizard_finish'"
        )
        self.assertIn("jsonb_array_length(d.answers->'homebrew_links')", door["d"])

    async def test_homebrew_is_a_stage_and_the_door_takes_the_links_key(self) -> None:
        self.assertIn("homebrew", wizard_finish.STAGES)
        [door] = await self.sql(
            "SELECT pg_get_functiondef(p.oid) AS d FROM pg_proc p"
            " WHERE p.pronamespace = current_schema()::regnamespace"
            " AND p.proname = 'dmbot_web_wizard_set'"
        )
        self.assertIn("'homebrew_links'", door["d"])
        from dmbot.web import wizard

        self.assertEqual(
            wizard.check_patch({"homebrew_links": [" https://a.example/x ", ""]}),
            {"homebrew_links": ["https://a.example/x"]},
        )


class TheLinks(FinishTest):
    """A web link in a document step (#4206): lore links go to the lore reader as link uploads (its
    own guarded fetch); house and homebrew links are opened once with the same guarded fetch, and a
    link that cannot be read keeps its word while the campaign stands."""

    async def add_doc(
        self, slot: str, address: str, name: str = "A page", source: str = "link"
    ) -> None:
        await self.sql(
            "INSERT INTO wizard_documents (guild_id, user_id, id, slot, name, source, text, status,"
            " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 'ok', %s)",
            GUILD, DM, f"{abs(hash((slot, address))) % 10**30:032x}", slot, name, source, address,
            NOW,
        )  # fmt: skip

    async def finish(self) -> dict[str, Any]:
        await self.queue()
        self.reader()
        with patch.object(wizard_finish, "LORE_POLL_S", 0.02):
            await wizard_finish.run_round(self.bot, GUILD)
        return await self.run_row()

    async def test_a_lore_link_is_queued_as_a_link_upload_with_no_text(self) -> None:
        await self.draft()
        await self.add_doc("lore", "https://docs.google.com/document/d/abcdefghijklmnopqrstu/edit")
        row = await self.finish()
        self.assertEqual((row["state"], row["error"]), ("done", ""))
        # (the stand-in reader blanks the text and keeps the link until it ends: checked at queue)
        [up] = await self.sql("SELECT source, text, link, choice FROM lore_uploads")
        self.assertEqual((up["source"], up["text"], up["choice"]), ("link", "", "world"))
        self.assertTrue(up["link"].startswith("https://docs.google.com/"))

    async def test_a_bad_address_in_a_lore_step_is_a_failed_read_and_nothing_is_queued(
        self,
    ) -> None:
        await self.draft()
        await self.add_doc("lore", "http://example.com/page")
        row = await self.finish()
        self.assertEqual((row["state"], row["error"]), ("done", "lore_failed"))
        self.assertEqual(await self.sql("SELECT 1 FROM lore_uploads"), [])

    async def test_a_house_link_is_read_once_and_its_rules_are_added(self) -> None:
        await self.draft()
        await self.add_doc("house", "https://docs.google.com/document/d/abcdefghijklmnopqrstu/edit")
        calls: list[str] = []

        async def fake(guild_id: int, link: str) -> tuple[Any, str | None]:
            calls.append(link)
            return SimpleNamespace(text="1. Flanking gives advantage."), None

        with patch("dmbot.ui.name_lists.read_link_once", fake):
            row = await self.finish()
        self.assertEqual((row["state"], row["error"]), ("done", ""))
        self.assertEqual(len(calls), 1)
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        rules = await self.bot.house_rules.list(GUILD, campaign.id)
        self.assertEqual([r.rule for r in rules], ["Flanking gives advantage."])

    async def test_a_house_link_that_cannot_be_read_keeps_its_word_and_the_campaign_stands(
        self,
    ) -> None:
        await self.draft()
        await self.add_doc("house", "https://example.com/private")

        async def nothing(guild_id: int, link: str) -> tuple[Any, str | None]:
            return None, "That page needs a sign-in."

        with patch("dmbot.ui.name_lists.read_link_once", nothing):
            row = await self.finish()
        self.assertEqual((row["state"], row["error"]), ("done", "doc_link_failed"))
        self.assertIn("doc_link_failed:1", row["notes"])
        self.assertEqual(len(await self.campaigns.list_campaigns(GUILD)), 1)


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

    async def aliases_of(self, name: str) -> list[tuple[str, str, str]]:
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        entity = next(e for e in await self.made() if e.name == name)
        found = await self.bot.memory.aliases(GUILD, campaign.id, entity_id=entity.id)
        return sorted((a.text, a.kind, a.status) for a in found)

    async def test_a_full_name_with_no_short_name_gets_its_first_name_as_a_confirmed_one(
        self,
    ) -> None:
        chars = [
            {"full": "Cerric Vanya'elen", "party_name": "", "sheet_url": "", "player": "pending"}
        ]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        self.assertEqual(
            await self.aliases_of("Cerric Vanya'elen"),
            [("Cerric", "short", "confirmed"), ("Cerric Vanya'elen", "full", "confirmed")],
        )

    async def test_a_short_name_the_dm_typed_is_the_one_kept(self) -> None:
        chars = [
            {"full": "Cerric Vanya'elen", "party_name": "Ric", "sheet_url": "", "player": "pending"}
        ]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        texts = [t for t, _, _ in await self.aliases_of("Cerric Vanya'elen")]
        self.assertEqual(texts, ["Cerric Vanya'elen", "Ric"])  # (no "Cerric" beside it)

    async def test_two_characters_with_one_first_name_get_none(self) -> None:
        chars = [
            {"full": "Mira Dawn", "party_name": "", "sheet_url": "", "player": "pending"},
            {"full": "Mira Vale", "party_name": "", "sheet_url": "", "player": "pending"},
        ]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        for name in ("Mira Dawn", "Mira Vale"):
            self.assertEqual([t for t, _, _ in await self.aliases_of(name)], [name])

    async def test_a_one_word_name_a_title_or_a_name_another_character_has_gets_none(self) -> None:
        chars = [
            {"full": "Zed", "party_name": "", "sheet_url": "", "player": "pending"},
            {"full": "Captain Rook", "party_name": "", "sheet_url": "", "player": "pending"},
            {"full": "Dane", "party_name": "", "sheet_url": "", "player": "pending"},
            {"full": "Dane Holt", "party_name": "", "sheet_url": "", "player": "pending"},
        ]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        for name in ("Zed", "Captain Rook", "Dane Holt"):
            self.assertEqual([t for t, _, _ in await self.aliases_of(name)], [name])

    async def test_a_creature_an_epithet_or_a_too_short_first_word_gets_none(self) -> None:
        chars = [
            {"full": "Goblin Sam", "party_name": "", "sheet_url": "", "player": "pending"},
            {"full": "Red Ilsa", "party_name": "", "sheet_url": "", "player": "pending"},
            {"full": "Al Brook", "party_name": "", "sheet_url": "", "player": "pending"},
            {"full": "jon Hale", "party_name": "", "sheet_url": "", "player": "pending"},
        ]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        for name in ("Goblin Sam", "Red Ilsa", "Al Brook", "jon Hale"):
            self.assertEqual([t for t, _, _ in await self.aliases_of(name)], [name])

    async def test_another_characters_short_name_counts_as_taken(self) -> None:
        chars = [
            {"full": "Mira Dawn", "party_name": "", "sheet_url": "", "player": "pending"},
            {"full": "Tom Vale", "party_name": "Mira", "sheet_url": "", "player": "pending"},
        ]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        self.assertEqual([t for t, _, _ in await self.aliases_of("Mira Dawn")], ["Mira Dawn"])
        self.assertIn("Mira", [t for t, _, _ in await self.aliases_of("Tom Vale")])

    async def test_an_ordinary_first_name_is_added(self) -> None:
        chars = [{"full": "Wit Sparrow", "party_name": "", "sheet_url": "", "player": "pending"}]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        self.assertIn("Wit", [t for t, _, _ in await self.aliases_of("Wit Sparrow")])

    async def test_a_retried_run_adds_the_first_name_once(self) -> None:
        chars = [
            {"full": "Cerric Vanya'elen", "party_name": "", "sheet_url": "", "player": "pending"}
        ]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        await wizard_finish._make_characters(
            self.bot, self.guild, GUILD, campaign.id, {"characters": chars}
        )
        texts = [t for t, _, _ in await self.aliases_of("Cerric Vanya'elen")]
        self.assertEqual(texts.count("Cerric"), 1)

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

    async def test_a_player_id_out_of_range_or_not_confirmed_is_never_bound(self) -> None:
        chars = [
            {"full": "Zero", "party_name": "", "sheet_url": "", "player": "0"},
            {"full": "Huge", "party_name": "", "sheet_url": "", "player": "9" * 30},
        ]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("done", ""))
        self.assertEqual([e.played_by for e in await self.made()], [None, None])

    async def test_a_discord_error_does_not_bind_a_player_it_could_not_confirm(self) -> None:
        chars = [{"full": "Cerric Vale", "party_name": "", "sheet_url": "", "player": str(DM)}]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        hiccup = discord.HTTPException(MagicMock(status=500, reason="x"), "down")
        calls = {"n": 0}

        async def flaky(uid: int) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                return SimpleNamespace(id=uid)  # (the sender is found)
            raise hiccup

        self.guild.fetch_member = AsyncMock(side_effect=flaky)
        await wizard_finish.run_round(self.bot, GUILD)
        [entity] = await self.made()
        self.assertIsNone(entity.played_by)  # (unconfirmed: left pending, not guessed)

    async def test_a_bot_is_never_bound_as_a_player(self) -> None:
        chars = [{"full": "Cerric Vale", "party_name": "", "sheet_url": "", "player": str(DM)}]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        self.guild.fetch_member = AsyncMock(return_value=SimpleNamespace(id=DM, bot=True))
        await wizard_finish.run_round(self.bot, GUILD)
        [entity] = await self.made()
        self.assertIsNone(entity.played_by)

    async def test_a_retry_adds_the_short_name_a_half_made_character_lacks(self) -> None:
        chars = [
            {"full": "Cerric Vale", "party_name": "Cerric", "sheet_url": "", "player": "pending"}
        ]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        [entity] = await self.made()
        await self.sql(
            "DELETE FROM memory_aliases WHERE entity_id = %s AND kind = 'short'", entity.id
        )
        again = await wizard_finish._make_characters(
            self.bot, self.guild, GUILD, campaign.id, {"characters": chars}
        )
        self.assertEqual(again, 0)  # (no second character)
        aliases = await self.sql(
            "SELECT text FROM memory_aliases WHERE entity_id = %s AND kind = 'short'", entity.id
        )
        self.assertEqual([a["text"] for a in aliases], ["Cerric"])  # (and its short name is back)

    async def test_a_same_name_npc_does_not_stop_the_player_character_being_made(self) -> None:
        made = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        await self.bot.memory.add_entity(
            GUILD, made.id, type="character", name="Cerric Vale", source="dm", status="confirmed",
            role="npc",
        )  # fmt: skip
        chars = [{"full": "Cerric Vale", "party_name": "", "sheet_url": "", "player": "pending"}]
        count = await wizard_finish._make_characters(
            self.bot, self.guild, GUILD, made.id, {"characters": chars}
        )
        self.assertEqual(count, 1)  # (only a player character of that name counts as already there)
        everyone = await self.bot.memory.entities(GUILD, made.id)
        found = sorted(e.role for e in everyone if e.name == "Cerric Vale")
        self.assertEqual(found, ["npc", "player_character"])  # (the NPC is left as it was)

    async def test_a_short_name_equal_to_the_full_name_is_not_added_twice(self) -> None:
        chars = [{"full": "Cerric", "party_name": "cerric", "sheet_url": "", "player": "pending"}]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        [entity] = await self.made()
        shorts = await self.sql(
            "SELECT 1 FROM memory_aliases WHERE entity_id = %s AND kind = 'short'", entity.id
        )
        self.assertEqual(shorts, [])

    async def test_nothing_to_make_is_nothing_made(self) -> None:
        made = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        none = await wizard_finish._make_characters(
            self.bot, self.guild, GUILD, made.id, {"characters": []}
        )
        self.assertEqual(none, 0)
        self.bot.memory = None
        gone = await wizard_finish._make_characters(
            self.bot, self.guild, GUILD, made.id, {"characters": [{"full": "A"}]}
        )
        self.assertEqual(gone, 0)

    async def test_a_party_stage_that_breaks_leaves_the_campaign_and_ends_with_its_own_word(
        self,
    ) -> None:
        chars = [{"full": "Cerric Vale", "party_name": "", "sheet_url": "", "player": "pending"}]
        await self.draft({"name": "Frostmaiden", "characters": chars}, docs=(("lore", "l", "W."),))
        await self.queue()
        self.reader()
        self.bot.memory.entities = AsyncMock(side_effect=OSError("down"))
        with patch.object(wizard_finish, "LORE_POLL_S", 0.02):
            await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("done", "party_failed"))
        self.assertEqual(len(await self.campaigns.list_campaigns(GUILD)), 1)
        self.assertEqual([c for c, _ in self.seen], ["world"])  # (the lore was still read)

    async def test_the_run_is_at_the_party_stage_while_the_characters_are_made(self) -> None:
        chars = [{"full": "Cerric Vale", "party_name": "", "sheet_url": "", "player": "pending"}]
        await self.draft({"name": "Frostmaiden", "characters": chars})
        await self.queue()
        seen: list[str] = []
        real = wizard_finish._make_characters

        async def look(*args: Any) -> int:
            seen.append((await self.run_row())["stage"])
            return await real(*args)

        with patch.object(wizard_finish, "_make_characters", look):
            await wizard_finish.run_round(self.bot, GUILD)
        self.assertEqual(seen, ["party"])


class TheSupplements(FinishTest):
    async def rules(self) -> dict[str, bool]:
        rows = await self.sql("SELECT rule_id, enabled FROM campaign_optional_rules")
        return {r["rule_id"]: r["enabled"] for r in rows}

    async def run_with(self, answers: dict[str, Any]) -> None:
        await self.draft({"name": "Frostmaiden", **answers})
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        self.assertEqual((await self.run_row())["state"], "done")

    async def test_a_ticked_book_turns_its_rules_on_and_the_other_books_rules_off(self) -> None:
        from dmbot.rules import optional

        await self.run_with({"supplements": ["tasha"]})
        chosen = await self.rules()
        for rule in optional.CATALOG:
            self.assertEqual(chosen[rule.id], rule.source == optional.TASHA, rule.id)

    async def test_no_book_ticked_turns_every_catalog_rule_off(self) -> None:
        await self.run_with({"supplements": []})
        self.assertTrue(await self.rules())
        self.assertFalse(any((await self.rules()).values()))

    async def test_a_skipped_step_keeps_the_campaigns_defaults(self) -> None:
        await self.run_with({})
        self.assertEqual(await self.rules(), {})  # (no stored choice: the default follows)

    async def test_a_book_with_no_rules_in_the_catalog_changes_nothing(self) -> None:
        from dmbot.rules import optional

        await self.run_with({"supplements": ["bigby", "ravnica"]})
        self.assertFalse(any((await self.rules()).values()))  # (and the catalog books are off)
        self.assertEqual(set(await self.rules()), {r.id for r in optional.CATALOG})

    async def test_the_rules_stage_comes_after_the_data_choice_and_before_the_party(self) -> None:
        order = list(wizard_finish.STAGES)
        self.assertLess(order.index("data_use"), order.index("rules"))
        self.assertLess(order.index("rules"), order.index("party"))


class TheDocumentsNotReadYet(FinishTest):
    async def test_ruleset_documents_are_not_dropped_in_silence(self) -> None:
        docs = (("ruleset", "r.txt", "My rules."),)
        await self.draft(docs=docs)
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        row = await self.run_row()
        self.assertEqual((row["state"], row["error"]), ("done", "docs_left"))
        self.assertEqual(
            len(await self.campaigns.list_campaigns(GUILD)), 1
        )  # (the campaign stands)
        self.assertEqual(await self.sql("SELECT 1 FROM lore_uploads"), [])  # (none was read)

    async def test_a_run_with_only_lore_and_secrets_ends_clean(self) -> None:
        await self.draft(docs=(("lore", "l.txt", "Words."),))
        await self.queue()
        self.reader()
        with patch.object(wizard_finish, "LORE_POLL_S", 0.02):
            await wizard_finish.run_round(self.bot, GUILD)
        self.assertEqual((await self.run_row())["error"], "")

    async def test_a_lore_failure_keeps_its_own_word_over_docs_left(self) -> None:
        docs = (("lore", "l.txt", "Words."), ("house", "h.txt", "No flanking."))
        await self.draft(docs=docs)
        await self.queue()
        with (
            patch.object(wizard_finish, "LORE_POLL_S", 0.01),
            patch.object(wizard_finish, "LORE_WAIT_S", 0.03),
        ):
            await wizard_finish.run_round(self.bot, GUILD)
        self.assertEqual((await self.run_row())["error"], "lore_failed")


NUMBER, OTHER_NUMBER, THIRD = 12345678, 87654321, 5550001
LINK = sheets.sheet_url(NUMBER)


class TheSheets(FinishTest):
    """Public D&D Beyond links given in the wizard (#4205): linked and read once, whatever D&D
    Beyond says, and the campaign stands."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.reads: list[int] = []
        self.errors: dict[int, sheets.SheetError] = {}

        async def fetch_sheet(character: int) -> dict[str, Any]:
            self.reads.append(character)
            if character in self.errors:
                raise self.errors[character]
            return sheets.parse(sheet_answer())

        self.store = SheetStore(self.db)
        self.changed: list[str] = []
        self.bot.sheets = self.store
        self.bot.fetch_sheet = fetch_sheet
        self.bot.sheets_changed = lambda g, c: self.changed.append(c)

    def party(self, *links: str) -> dict[str, Any]:
        names = ("Cerric Vale", "Mira Dawn", "Wit Sparrow")
        return {
            "name": "Frostmaiden",
            "characters": [
                {"full": n, "party_name": "", "sheet_url": link, "player": str(DM)}
                for n, link in zip(names, links, strict=False)
            ],
        }

    async def finish(self, answers: dict[str, Any]) -> dict[str, Any]:
        await self.draft(answers)
        await self.queue()
        await wizard_finish.run_round(self.bot, GUILD)
        return await self.run_row()

    async def made(self) -> Any:
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        found = await self.bot.memory.entities(GUILD, campaign.id)
        return [e for e in found if e.role == "player_character"]

    async def sheet_of(self, name: str) -> Any:
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        entity = next(e for e in await self.made() if e.name == name)
        return await self.store.sheet(GUILD, campaign.id, entity.id)

    async def test_a_public_link_is_linked_and_read_once_and_the_row_is_clean(self) -> None:
        row = await self.finish(self.party(LINK))
        self.assertEqual((row["state"], row["error"], row["notes"]), ("done", "", ""))
        self.assertEqual(self.reads, [NUMBER])
        found = await self.sheet_of("Cerric Vale")
        self.assertEqual(found.url, LINK)
        self.assertIsNotNone(found.sheet)
        self.assertEqual(len(self.changed), 1)

    async def test_a_private_sheet_keeps_the_link_and_the_campaign_stands(self) -> None:
        self.errors[NUMBER] = sheets.SheetError("no", refused=True)
        row = await self.finish(self.party(LINK))
        self.assertEqual((row["state"], row["error"]), ("done", "sheet_private"))
        self.assertEqual(row["notes"], "sheet_private:1")
        found = await self.sheet_of("Cerric Vale")
        self.assertEqual((found.url, found.sheet), (LINK, None))  # (read again later)
        self.assertEqual(len(await self.campaigns.list_campaigns(GUILD)), 1)

    async def test_a_slow_or_busy_read_keeps_the_link_with_its_own_word(self) -> None:
        self.errors[NUMBER] = sheets.SheetError("later", limited=True)
        row = await self.finish(self.party(LINK))
        self.assertEqual(
            (row["state"], row["error"], row["notes"]), ("done", "sheet_slow", "sheet_slow:1")
        )
        self.assertEqual((await self.sheet_of("Cerric Vale")).url, LINK)

    async def test_each_word_has_its_count_and_the_first_problem_is_the_rows_word(self) -> None:
        self.errors[NUMBER] = sheets.SheetError("no", refused=True)
        self.errors[OTHER_NUMBER] = sheets.SheetError("later", limited=True)
        self.errors[THIRD] = sheets.SheetError("no", refused=True)
        links = (LINK, sheets.sheet_url(OTHER_NUMBER), sheets.sheet_url(THIRD))
        row = await self.finish(self.party(*links))
        self.assertEqual(row["error"], "sheet_private")
        self.assertEqual(row["notes"], "sheet_private:2 sheet_slow:1")

    async def test_a_link_that_is_not_a_dndbeyond_character_is_never_fetched(self) -> None:
        row = await self.finish(self.party("https://evil.example.com/characters/12345678"))
        self.assertEqual((row["error"], row["notes"]), ("sheet_bad", "sheet_bad:1"))
        self.assertEqual(self.reads, [])
        self.assertIsNone(await self.sheet_of("Cerric Vale"))

    async def test_only_the_normalised_character_is_fetched(self) -> None:
        row = await self.finish(self.party(f"{LINK}/share?x=1"))
        self.assertEqual(row["error"], "")
        self.assertEqual(self.reads, [NUMBER])
        self.assertEqual((await self.sheet_of("Cerric Vale")).url, LINK)

    async def test_a_retaken_run_reads_nothing_twice(self) -> None:
        answers = self.party(LINK)
        await self.finish(answers)
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        await self.queue("b" * 32)  # (the retake: a new run on the same campaign)
        run = await self.runs.claim_next(GUILD)
        assert run is not None
        word = await wizard_finish._read_sheets(
            self.bot, self.runs, GUILD, run, campaign.id, answers
        )
        self.assertEqual((word, self.reads), ("", [NUMBER]))  # (still the one read)

    async def waits(self) -> list[dict[str, Any]]:
        return await self.sql("SELECT entity_id, url FROM sheet_waits")

    async def test_a_link_on_a_character_with_no_player_yet_waits_and_is_counted(self) -> None:
        answers = self.party(LINK)
        answers["characters"][0]["player"] = "pending"
        row = await self.finish(answers)
        self.assertEqual((row["state"], row["error"]), ("done", "sheet_waiting"))
        self.assertEqual(row["notes"], "sheet_waiting:1")
        self.assertEqual(self.reads, [])  # (nothing is fetched for a link that waits)
        self.assertIsNone(await self.sheet_of("Cerric Vale"))
        [wait] = await self.waits()
        self.assertEqual(wait["url"], LINK)

    async def give_a_player(self, name: str, player: int = DM) -> str:
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        entity = next(e for e in await self.made() if e.name == name)
        await self.sql("UPDATE memory_entities SET played_by = %s WHERE id = %s", player, entity.id)
        return str(campaign.id)

    async def waiting_link(self) -> None:
        answers = self.party(f"{LINK}/share?x=1")
        answers["characters"][0]["player"] = "pending"
        await self.finish(answers)

    async def test_a_waiting_link_is_read_once_its_character_has_a_player(self) -> None:
        await self.waiting_link()
        self.assertEqual(await wizard_finish.run_sheet_waits(self.bot, GUILD), 0)  # no player yet
        self.assertEqual(self.reads, [])
        await self.give_a_player("Cerric Vale")
        self.assertEqual(await wizard_finish.run_sheet_waits(self.bot, GUILD), 1)
        self.assertEqual(self.reads, [NUMBER])
        found = await self.sheet_of("Cerric Vale")
        self.assertEqual(found.url, LINK)
        self.assertIsNotNone(found.sheet)
        self.assertEqual(await self.waits(), [])  # (the row goes)
        self.assertEqual(await wizard_finish.run_sheet_waits(self.bot, GUILD), 0)

    async def test_a_waiting_private_sheet_keeps_its_link_and_the_wait_goes(self) -> None:
        await self.waiting_link()
        await self.give_a_player("Cerric Vale")
        self.errors[NUMBER] = sheets.SheetError("no", refused=True)
        self.assertEqual(await wizard_finish.run_sheet_waits(self.bot, GUILD), 1)
        found = await self.sheet_of("Cerric Vale")
        self.assertEqual((found.url, found.sheet), (LINK, None))
        self.assertEqual(await self.waits(), [])

    async def test_one_waiting_sheet_is_read_each_round_oldest_first(self) -> None:
        answers = self.party(LINK, sheets.sheet_url(OTHER_NUMBER))
        for c in answers["characters"]:
            c["player"] = "pending"
        await self.finish(answers)
        await self.give_a_player("Cerric Vale")
        await self.give_a_player("Mira Dawn")
        await wizard_finish.run_sheet_waits(self.bot, GUILD)
        self.assertEqual(len(self.reads), 1)
        await wizard_finish.run_sheet_waits(self.bot, GUILD)
        self.assertEqual(sorted(self.reads), sorted([NUMBER, OTHER_NUMBER]))

    async def test_a_wait_for_an_entry_that_was_turned_down_is_not_read(self) -> None:
        await self.waiting_link()
        await self.give_a_player("Cerric Vale")
        await self.sql("UPDATE memory_entities SET status = 'rejected' WHERE name = 'Cerric Vale'")
        self.assertEqual(await wizard_finish.run_sheet_waits(self.bot, GUILD), 0)
        self.assertEqual(self.reads, [])

    async def test_a_link_nobody_got_a_player_for_is_let_go_after_ninety_days(self) -> None:
        await self.waiting_link()
        await self.sql("UPDATE sheet_waits SET created_at = 1")
        await wizard_finish.run_sheet_waits(self.bot, GUILD)
        self.assertEqual(await self.waits(), [])

    async def test_a_link_goes_with_its_campaign(self) -> None:
        await self.waiting_link()
        [campaign] = await self.campaigns.list_campaigns(GUILD)
        await self.sql("DELETE FROM campaigns WHERE id = %s", campaign.id)
        self.assertEqual(await self.waits(), [])

    async def test_no_store_means_no_sweep(self) -> None:
        self.bot.sheets = None
        self.assertEqual(await wizard_finish.run_sheet_waits(self.bot, GUILD), 0)

    async def test_a_character_that_was_not_made_is_skipped(self) -> None:
        answers = self.party(LINK)
        answers["characters"][0]["full"] = ""  # (nothing to make: refused as empty)
        row = await self.finish(answers)
        self.assertEqual((row["state"], row["error"]), ("done", ""))
        self.assertEqual(self.reads, [])

    async def test_the_stage_breaking_never_stops_the_run(self) -> None:
        self.bot.fetch_sheet = AsyncMock(side_effect=RuntimeError("boom"))
        row = await self.finish(self.party(LINK))
        self.assertEqual((row["state"], row["error"]), ("done", "sheets_failed"))
        self.assertEqual(len(await self.campaigns.list_campaigns(GUILD)), 1)

    async def test_the_progress_row_holds_the_stage_and_counts_only(self) -> None:
        moves: list[tuple[str, int, int]] = []
        real = wizard_finish.WizardFinishes.move

        async def spy(
            self_: Any,
            guild_id: int,
            run: Any,
            *,
            stage: str,
            part: int = 0,
            of_parts: int = 0,
            **kw: Any,
        ) -> None:
            moves.append((stage, part, of_parts))
            await real(self_, guild_id, run, stage=stage, part=part, of_parts=of_parts, **kw)

        with patch.object(wizard_finish.WizardFinishes, "move", spy):
            await self.finish(self.party(LINK, sheets.sheet_url(OTHER_NUMBER)))
        self.assertIn(("sheets", 1, 2), moves)
        self.assertIn(("sheets", 2, 2), moves)
        self.assertIn("sheets", wizard_finish.STAGES)

    async def test_no_log_line_holds_a_link(self) -> None:
        self.errors[NUMBER] = sheets.SheetError("no", refused=True)
        with self.assertLogs(level="INFO") as logged:
            await self.finish(self.party(LINK))
        text = "\n".join(logged.output)
        self.assertNotIn("dndbeyond", text)
        self.assertNotIn(str(NUMBER), text)
