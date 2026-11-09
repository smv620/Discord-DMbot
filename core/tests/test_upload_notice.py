"""The notice a lore upload leaves when it adds what DMbot is sure of (#3878): the words, the
counts, and Undo this upload, which survives a restart, is for the campaign's DMs only, works once,
for 7 days, and takes back exactly what the upload added. Invented names."""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.memory.models import BACKGROUND_SOURCE
from dmbot.ui import upload_notice as un
from dmbot.upload_notices import UNDO_SECONDS, Notice, UploadNotices
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest


class Words(NamesTest):
    def test_the_counts_and_the_questions_in_plain_words(self) -> None:
        text = un.notice_text(
            "the linked page, 9 Nov",
            {"npc": 30, "place": 24, "faction": 4, "item": 2, "event": 2, "connections": 48},
            3,
        )
        self.assertIn("Added **62 names**", text)
        self.assertIn("30 NPCs", text)
        self.assertIn("**48 connections**", text)
        self.assertIn("from the linked page, 9 Nov", text)
        self.assertIn("**3 questions** need you", text)

    def test_one_of_each_and_nothing_left_say_so(self) -> None:
        text = un.notice_text("a file", {"npc": 1, "connections": 1}, 0)
        self.assertIn("**1 name**", text)
        self.assertIn("**1 connection**", text)
        self.assertNotIn("question", text)
        self.assertIn("1 question** needs you", un.notice_text("a file", {"npc": 1}, 1))
        self.assertIn("**nothing new**", un.notice_text("a file", {}, 0))

    def test_connections_said_at_the_table_are_counted_never_as_names(self) -> None:
        """#3963 (c): an import's held-back connections the table stated are shown, and said."""
        text = un.notice_text("a session", {"npc": 1, "shown": 2}, 0)
        self.assertIn("**1 name** (1 NPC)", text)
        self.assertIn("**2 connections** said at the table are now shown to your players", text)

    def test_a_connection_kept_hidden_beside_a_hidden_one_is_said(self) -> None:
        """#3979: an import's claims kept hidden beside a connection DMbot already hides."""
        text = un.notice_text("a session", {"held": 1}, 0)
        self.assertIn("**1 connection** is kept hidden from your players", text)
        self.assertIn("To show it: open either name, ✏️ Edit a connection…", text)
        self.assertNotIn("held", text)


class Undoing(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.notices = UploadNotices(self.db)
        self.bot.upload_notices = self.notices
        self.bot.lookup = None
        self.mira = await self.name("Mirelle")
        self.oak = await self.name("Oakmere")

    async def name(self, name: str) -> str:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name=name, role="npc",
            source=BACKGROUND_SOURCE,
        )  # fmt: skip
        return made.value.id

    async def added(self) -> Notice:
        batches = []
        for entity in (self.mira, self.oak):
            written = await self.memory.confirm_entity(
                GUILD, self.campaign.id, entity, "npc", source="dm"
            )
            batches.append(written.batch)
        return await self.notices.add(
            GUILD, self.campaign.id, "a file, 9 Nov", {"npc": 2}, [b for b in batches if b]
        )

    def button(self, notice: Notice) -> un.UndoUploadButton:
        return un.UndoUploadButton(GUILD, self.campaign.id, notice.id)

    async def statuses(self) -> dict[str, str]:
        found = await self.memory.entities(
            GUILD, self.campaign.id, statuses=("proposed", "confirmed")
        )
        return {e.name: e.status for e in found}

    async def test_the_button_takes_back_exactly_what_the_upload_added(self) -> None:
        notice = await self.added()
        other = await self.name("Dorn")  # waiting, not the upload's
        self.assertEqual(set((await self.statuses()).values()), {"confirmed", "proposed"})
        press = self.it(DM)
        await self.button(notice).callback(press)
        found = await self.statuses()
        self.assertEqual((found["Mirelle"], found["Oakmere"]), ("proposed", "proposed"))
        self.assertEqual(found["Dorn"], "proposed")
        self.assertIn(un.NOTHING_LEFT, press.edit_original_response.await_args.kwargs["content"])
        self.assertIsNotNone(other)

    async def test_a_non_dm_is_refused_and_nothing_changes(self) -> None:
        notice = await self.added()
        press = self.it(MANAGER)
        await self.button(notice).callback(press)
        self.assertEqual(set((await self.statuses()).values()), {"confirmed"})
        self.assertIn(
            un.NOT_YOURS,
            [t for t, _ in press.response.sent]
            + [c.args[0] for c in press.followup.send.await_args_list],
        )

    async def test_a_second_press_does_nothing(self) -> None:
        notice = await self.added()
        await self.button(notice).callback(self.it(DM))
        again = self.it(DM)
        await self.button(notice).callback(again)
        self.assertIn(
            un.ALREADY,
            [t for t, _ in again.response.sent]
            + [c.args[0] for c in again.followup.send.await_args_list],
        )

    async def test_after_seven_days_it_is_too_late_and_the_button_goes(self) -> None:
        notice = await self.added()
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE upload_notices SET created_at = created_at - %s WHERE id = %s",
                (UNDO_SECONDS + 5, notice.id),
            )
        old = await self.notices.get(GUILD, notice.id)
        assert old is not None
        self.assertFalse(old.can_undo(old.created_at + UNDO_SECONDS + 5))
        view = un.notice_view(old, site_url="https://getdmbot.com")
        self.assertFalse(any("up:undo" in str(getattr(c, "custom_id", "")) for c in view.children))
        press = self.it(DM)
        await self.button(notice).callback(press)
        self.assertEqual(set((await self.statuses()).values()), {"confirmed"})

    async def test_it_survives_a_restart_the_id_holds_everything(self) -> None:
        notice = await self.added()
        custom_id = self.button(notice).item.custom_id
        match = re.fullmatch(
            un.UndoUploadButton.__discord_ui_compiled_template__.pattern, custom_id or ""
        )
        self.assertIsNotNone(match)
        assert match is not None
        rebuilt = await un.UndoUploadButton.from_custom_id(
            cast(Any, SimpleNamespace()),
            cast(Any, SimpleNamespace()),
            match,
        )
        await rebuilt.callback(self.it(DM))
        self.assertEqual(set((await self.statuses()).values()), {"proposed"})

    async def test_what_was_changed_since_stays_and_the_reply_says_so(self) -> None:
        notice = await self.added()
        # Something done since depends on the first change: it is left as it is.
        real = self.memory.undo
        calls: list[int] = []

        async def undo(guild: int, campaign: str, batch: int, **kw: Any) -> Any:
            calls.append(batch)
            if len(calls) == 1:
                from dmbot.memory.models import MemoryRuleError

                raise MemoryRuleError("changed since")
            return await real(guild, campaign, batch, **kw)

        cast(Any, self.memory).undo = undo
        press = self.it(DM)
        await self.button(notice).callback(press)
        content = press.edit_original_response.await_args.kwargs["content"]
        self.assertIn("Took back 1 of 2", content)
        self.assertEqual(calls, sorted(notice.changes, reverse=True))  # newest first

    async def test_an_unexpected_error_with_nothing_done_gives_the_button_back(self) -> None:
        notice = await self.added()

        async def boom(guild: int, campaign: str, batch: int, **kw: Any) -> Any:
            raise RuntimeError("down")

        cast(Any, self.memory).undo = boom
        press = self.it(DM)
        await self.button(notice).callback(press)
        sent = [t for t, _ in press.response.sent] + [
            c.args[0] for c in press.followup.send.await_args_list
        ]
        self.assertIn(un.COULD_NOT, sent)
        again = await self.notices.get(GUILD, notice.id)
        assert again is not None
        self.assertIsNone(again.undone_at)  # (the claim is given back: it can be pressed again)
        self.assertEqual(set((await self.statuses()).values()), {"confirmed"})

    async def test_an_unexpected_error_after_some_were_undone_says_how_many(self) -> None:
        notice = await self.added()
        real = self.memory.undo
        calls: list[int] = []

        async def undo(guild: int, campaign: str, batch: int, **kw: Any) -> Any:
            calls.append(batch)
            if len(calls) == 2:
                raise RuntimeError("down")
            return await real(guild, campaign, batch, **kw)

        cast(Any, self.memory).undo = undo
        press = self.it(DM)
        await self.button(notice).callback(press)
        content = press.edit_original_response.await_args.kwargs["content"]
        self.assertIn("Took back 1 of 2; press Undo on each name's card", content)

    async def test_the_notice_view_has_the_links_and_undo_and_no_words(self) -> None:
        notice = await self.added()
        view = un.notice_view(notice, site_url="https://getdmbot.com")
        urls = [getattr(c, "url", "") for c in view.children if getattr(c, "url", "")]
        self.assertEqual(len(urls), 2)
        self.assertTrue(all(u.startswith("https://getdmbot.com/account/") for u in urls))
        plain = un.notice_view(notice, site_url="")  # (no https address: no link buttons)
        self.assertFalse(any(getattr(c, "url", "") for c in plain.children))
        self.assertTrue(plain.children)  # (Undo is still there)
        _ = AsyncMock


class ImportAdds(NamesTest):
    """The end of an import adds what its scans were sure of (#3878); consent is checked again."""

    MIRA = 7

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.notices = UploadNotices(self.db)
        self.bot.upload_notices = self.notices
        self.bot.lookup = None
        self.bot.post_upload_notice = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.set_recorded({self.MIRA})
        self.place = await self.scanned("Karsk", "place")
        self.idea = await self.scanned("Wondering", "concept")

    def set_recorded(self, who: set[int]) -> None:
        """Who counts as recorded in this campaign's imported sessions (`recorded_in`)."""
        self.bot.consent = SimpleNamespace(  # type: ignore[assignment]
            recorded_in=AsyncMock(return_value=frozenset(who)),
            consenting=AsyncMock(return_value=frozenset()),  # (an import must not use this)
        )

    async def scanned(self, name: str, kind: str) -> str:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, type=kind, name=name, source="scan"
        )
        return made.value.id

    async def status(self, entry: str) -> str:
        found = await self.memory.entities(
            GUILD, self.campaign.id, statuses=("proposed", "confirmed")
        )
        return next(e.status for e in found if e.id == entry)

    async def test_a_name_with_a_kind_is_added_and_one_without_stays_a_question(self) -> None:
        from dmbot.bot import DMBot

        done = await DMBot._add_sure_import(
            self.bot, self.campaign, {self.MIRA}, {self.place, self.idea}, {"imp1"}
        )
        self.assertTrue(done)
        self.assertEqual(await self.status(self.place), "confirmed")
        self.assertEqual(await self.status(self.idea), "proposed")
        notice = self.bot.post_upload_notice.await_args.args[1]  # type: ignore[attr-defined]
        self.assertEqual(notice.counts, {"place": 1})
        self.assertEqual(self.bot.post_upload_notice.await_args.args[2], 1)  # type: ignore[attr-defined]

    async def test_if_a_speaker_stopped_since_nothing_is_added(self) -> None:
        from dmbot.bot import DMBot

        done = await DMBot._add_sure_import(
            self.bot, self.campaign, {self.MIRA, 8}, {self.place}, {"imp1"}
        )
        self.assertFalse(done)
        self.assertEqual(await self.status(self.place), "proposed")
        self.bot.post_upload_notice.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_the_shared_undo_function_takes_back_what_the_import_added(self) -> None:
        from dmbot.bot import DMBot

        await DMBot._add_sure_import(self.bot, self.campaign, {self.MIRA}, {self.place}, {"imp1"})
        notice = self.bot.post_upload_notice.await_args.args[1]  # type: ignore[attr-defined]
        text = await un.undo_upload(self.bot, GUILD, self.campaign.id, notice.id, DM)
        self.assertEqual(text, un.NOTHING_LEFT)
        self.assertEqual(await self.status(self.place), "proposed")
        again = await un.undo_upload(self.bot, GUILD, self.campaign.id, notice.id, DM)
        self.assertEqual(again, un.ALREADY)
        other = await un.undo_upload(self.bot, GUILD, self.campaign.id, notice.id, MANAGER)
        self.assertEqual(other, un.NOT_YOURS)

    async def test_a_same_named_entry_already_waiting_is_not_the_imports(self) -> None:
        from dmbot.bot import DMBot

        await self.scanned("Karsk", "place")  # (a second, from elsewhere: not in the import's set)
        before = await self.memory.entities(GUILD, self.campaign.id, statuses=("proposed",))
        other = next(e.id for e in before if e.name == "Karsk" and e.id != self.place)
        await DMBot._add_sure_import(self.bot, self.campaign, {self.MIRA}, {self.place}, {"imp1"})
        self.assertEqual(await self.status(self.place), "confirmed")
        self.assertEqual(await self.status(other), "proposed")

    async def test_when_nothing_was_added_the_usual_list_is_left_to_post(self) -> None:
        from dmbot.bot import DMBot

        done = await DMBot._add_sure_import(
            self.bot, self.campaign, {self.MIRA}, {self.idea}, {"imp1"}
        )
        self.assertFalse(done)  # (no kind: a question, not an empty notice)
        self.bot.post_upload_notice.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_an_import_notice_records_its_import_and_a_lore_one_its_upload(self) -> None:
        from dmbot.bot import DMBot
        from dmbot.ui.upload_notice import add_sure

        await DMBot._add_sure_import(self.bot, self.campaign, {self.MIRA}, {self.place}, {"imp1"})
        notice = self.bot.post_upload_notice.await_args.args[1]  # type: ignore[attr-defined]
        found = await self.notices.get(GUILD, notice.id)
        assert found is not None
        self.assertEqual((found.source_kind, found.source_id), ("import", "imp1"))
        later = await self.scanned("Orrin", "place")
        done = await add_sure(
            self.bot, self.campaign, {"names": [later]}, "a file", source=("lore", "up42")
        )
        assert done is not None
        again = await self.notices.get(GUILD, done[0].id)
        assert again is not None
        self.assertEqual((again.source_kind, again.source_id), ("lore", "up42"))

    async def test_two_batches_in_one_report_name_no_source(self) -> None:
        from dmbot.bot import DMBot

        await DMBot._add_sure_import(
            self.bot, self.campaign, {self.MIRA}, {self.place}, {"imp1", "imp2"}
        )
        notice = self.bot.post_upload_notice.await_args.args[1]  # type: ignore[attr-defined]
        found = await self.notices.get(GUILD, notice.id)
        assert found is not None
        self.assertIsNone(found.source_kind)
        self.assertIsNone(found.source_id)

    async def test_a_connection_the_dms_line_states_is_added_with_the_names(self) -> None:
        from dmbot.bot import DMBot
        from dmbot.memory.claims import ClaimStore, Extracted

        orrin = await self.scanned("Orrin", "place")
        self.set_recorded({self.MIRA, DM})
        await ClaimStore(self.memory).propose(
            GUILD, self.campaign.id,
            [Extracted("Karsk", "is in", "Orrin", "", ("1",), "dm_said")],
            {"1": DM}, opted_in={DM}, prompt_version="p", model="m", session_started_at=5,
        )  # fmt: skip
        done = await DMBot._add_sure_import(
            self.bot, self.campaign, {self.MIRA}, {self.place, orrin}, {"imp1"}, {5}
        )
        self.assertTrue(done)
        notice = self.bot.post_upload_notice.await_args.args[1]  # type: ignore[attr-defined]
        self.assertEqual(notice.counts.get("connections"), 1)
        self.assertGreaterEqual(len(notice.changes), 2)  # (the names and the connection: one Undo)

    async def test_a_confirmed_speaker_with_no_discord_account_does_not_stop_the_import(
        self,
    ) -> None:
        from dmbot.bot import DMBot

        self.set_recorded({self.MIRA, -5})  # (-5: confirmed by the uploader, no account)
        done = await DMBot._add_sure_import(
            self.bot, self.campaign, {self.MIRA, -5}, {self.place}, {"imp1"}
        )
        self.assertTrue(done)
        self.assertEqual(await self.status(self.place), "confirmed")

    async def test_a_speaker_with_a_no_on_record_adds_nothing(self) -> None:
        from dmbot.bot import DMBot

        self.set_recorded({self.MIRA})  # (-5 has a "no": recorded_in leaves them out)
        done = await DMBot._add_sure_import(
            self.bot, self.campaign, {self.MIRA, -5}, {self.place}, {"imp1"}
        )
        self.assertFalse(done)
        self.assertEqual(await self.status(self.place), "proposed")
