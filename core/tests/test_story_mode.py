"""The Shared story switch (#1692): Off by default, turning it on needs the DM's confirmation of
the right to use the material (recorded with who and when, asked once), DMs only, one campaign's
setting never touches another's, the migration on a table that already holds rows, the backup
round trip, and the Settings buttons. The store tests need Postgres."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import psycopg

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.models import (
    SHARED_STORY,
    STORY_GUIDE,
    STORY_NEEDS_CONFIRM,
    STORY_OFF,
    STORY_WATCH,
)
from dmbot.db import Database, drop_schema
from dmbot.dm_screen import story_mode as ui
from dmbot.schema import MIGRATIONS
from tests.answer_first import Timeline
from tests.pg import TEST_URL, DatabaseTest

GUILD_A, GUILD_B = 111, 222
DM, OTHER, PLAYER = 7, 8, 9
CLOCK = 1_800_000_000


class Setup(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = float(CLOCK)
        self.campaigns = CampaignStore(self.db, clock=lambda: self.now)
        self.a = await self.campaigns.create(GUILD_A, "Frostmaiden", DM)
        self.b = await self.campaigns.create(GUILD_B, "Elsewhere", OTHER)

    async def mode(self, guild: int, campaign_id: str) -> str:
        found = await self.campaigns.get(guild, campaign_id)
        assert found is not None
        return found.story_mode


class Store(Setup):
    async def test_it_is_off_by_default(self) -> None:
        self.assertEqual(self.a.story_mode, STORY_OFF)
        self.assertFalse(await self.campaigns.story_confirmed(GUILD_A, self.a.id))

    async def test_turning_it_on_without_a_confirmation_stays_off_and_asks(self) -> None:
        for mode in (STORY_WATCH, STORY_GUIDE):
            with self.assertRaises(CampaignError) as raised:
                await self.campaigns.set_story_mode(GUILD_A, self.a.id, mode)
            self.assertEqual(str(raised.exception), STORY_NEEDS_CONFIRM)
        self.assertEqual(await self.mode(GUILD_A, self.a.id), STORY_OFF)
        self.assertEqual(await self.campaigns.confirmations(GUILD_A, self.a.id), [])

    async def test_the_confirmation_is_recorded_with_who_and_when(self) -> None:
        saved = await self.campaigns.set_story_mode(
            GUILD_A, self.a.id, STORY_WATCH, confirmed_by=DM
        )
        self.assertEqual(saved.story_mode, STORY_WATCH)
        (row,) = await self.campaigns.confirmations(GUILD_A, self.a.id)
        self.assertEqual((row["user"], row["purpose"], row["at"]), (str(DM), SHARED_STORY, CLOCK))
        self.assertEqual(len(row["fingerprint"]), 64)  # a fingerprint of the words, not the words

    async def test_on_off_then_on_again_does_not_ask_twice(self) -> None:
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_WATCH, confirmed_by=DM)
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_OFF)
        self.assertTrue(await self.campaigns.story_confirmed(GUILD_A, self.a.id))  # kept
        again = await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_GUIDE)
        self.assertEqual(again.story_mode, STORY_GUIDE)
        again = await self.campaigns.set_story_mode(
            GUILD_A,
            self.a.id,
            STORY_WATCH,
            confirmed_by=OTHER,  # (asked again? no)
        )
        self.assertEqual(len(await self.campaigns.confirmations(GUILD_A, self.a.id)), 1)

    async def test_another_campaigns_setting_is_untouched(self) -> None:
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_GUIDE, confirmed_by=DM)
        self.assertEqual(await self.mode(GUILD_B, self.b.id), STORY_OFF)
        self.assertEqual(await self.campaigns.confirmations(GUILD_B, self.b.id), [])
        with self.assertRaises(CampaignError):  # and not through the wrong server
            await self.campaigns.set_story_mode(GUILD_B, self.a.id, STORY_OFF)

    async def test_only_the_three_modes_exist(self) -> None:
        with self.assertRaises(CampaignError):
            await self.campaigns.set_story_mode(GUILD_A, self.a.id, "loud", confirmed_by=DM)


class Backup(Setup):
    async def test_the_mode_and_its_confirmation_come_back_from_a_backup(self) -> None:
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_GUIDE, confirmed_by=DM)
        data = await self.campaigns.export(GUILD_A, self.a.id)
        self.assertEqual(data["campaign"]["story_mode"], STORY_GUIDE)
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)  # the same DM
        self.assertEqual(restored.story_mode, STORY_GUIDE)
        (row,) = await self.campaigns.confirmations(GUILD_B, restored.id)
        self.assertEqual(row["user"], str(DM))  # the record travels with it
        # ...but a record that came from a file is not one pressed here: turning it on again
        # after Off asks again (CLAUDE.md, IP rule).
        self.assertFalse(await self.campaigns.story_confirmed(GUILD_B, restored.id))

    async def test_someone_else_restoring_the_file_starts_with_it_off(self) -> None:
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_WATCH, confirmed_by=DM)
        data = await self.campaigns.export(GUILD_A, self.a.id)
        restored = await self.campaigns.import_backup(GUILD_B, data, PLAYER)
        self.assertEqual(restored.story_mode, STORY_OFF)
        with self.assertRaises(CampaignError):  # and has to confirm for themselves
            await self.campaigns.set_story_mode(GUILD_B, restored.id, STORY_WATCH)

    async def test_a_forged_file_cannot_switch_it_on_for_someone_else(self) -> None:
        data = await self.campaigns.export(GUILD_A, self.a.id)
        data["campaign"]["story_mode"] = STORY_GUIDE
        data["sections"]["confirmations"] = [
            {
                "id": "forged1",
                "user": str(DM),  # somebody else's name on it
                "purpose": SHARED_STORY,
                "fingerprint": "0" * 64,
                "at": CLOCK,
                "restored": False,
            }
        ]
        restored = await self.campaigns.import_backup(GUILD_B, data, PLAYER)
        self.assertEqual(restored.story_mode, STORY_OFF)
        self.assertFalse(await self.campaigns.story_confirmed(GUILD_B, restored.id))

    async def test_a_mode_without_a_confirmation_in_the_file_is_restored_off(self) -> None:
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_WATCH, confirmed_by=DM)
        data = await self.campaigns.export(GUILD_A, self.a.id)
        data["sections"]["confirmations"] = []
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        self.assertEqual(restored.story_mode, STORY_OFF)

    async def test_an_old_backup_has_it_off_and_a_bad_one_is_refused(self) -> None:
        data = await self.campaigns.export(GUILD_A, self.a.id)
        del data["campaign"]["story_mode"]  # made before the switch existed
        older = await self.campaigns.import_backup(GUILD_B, data, PLAYER)
        self.assertEqual(older.story_mode, STORY_OFF)
        data["campaign"]["story_mode"] = "loud"
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_B, data, PLAYER)

    async def test_replacing_a_campaign_with_a_backup_sets_its_mode(self) -> None:
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_WATCH, confirmed_by=DM)
        data = await self.campaigns.export(GUILD_A, self.a.id)
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_OFF)
        replaced = await self.campaigns.import_backup(
            GUILD_A, data, DM, replace_campaign_id=self.a.id
        )
        self.assertEqual(replaced.story_mode, STORY_WATCH)

    async def test_replacing_a_campaign_with_a_file_that_says_off_turns_it_off(self) -> None:
        data = await self.campaigns.export(GUILD_A, self.a.id)  # made while Off
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_GUIDE, confirmed_by=DM)
        replaced = await self.campaigns.import_backup(
            GUILD_A, data, DM, replace_campaign_id=self.a.id
        )
        self.assertEqual(replaced.story_mode, STORY_OFF)

    async def test_a_damaged_file_leaves_the_campaign_as_it_was(self) -> None:
        await self.campaigns.set_story_mode(GUILD_A, self.a.id, STORY_WATCH, confirmed_by=DM)
        data = await self.campaigns.export(GUILD_A, self.a.id)
        data["campaign"]["story_mode"] = "loud"
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(GUILD_A, data, DM, replace_campaign_id=self.a.id)
        found = await self.campaigns.get(GUILD_A, self.a.id)
        assert found is not None
        self.assertEqual(found.story_mode, STORY_WATCH)


class Migration(Setup):
    async def test_the_migration_on_a_table_that_already_holds_rows(self) -> None:
        """A campaign made before 0121 has story_mode 'off' after it, with no backfill."""
        if not TEST_URL:
            self.skipTest("needs the test database")
        schema = f"t_{uuid.uuid4().hex[:16]}"
        before = MIGRATIONS[: [n for n, _ in MIGRATIONS].index("0121_story_mode")]
        db = await Database.open(TEST_URL, schema=schema, max_size=2, migrations=before)
        try:
            old = await CampaignStore(db).create(GUILD_A, "Old table", DM)
            self.assertEqual(old.story_mode, STORY_OFF)  # (the column isn't there yet)
            await db.migrate(MIGRATIONS)
            found = await CampaignStore(db).get(GUILD_A, old.id)
            assert found is not None
            self.assertEqual(found.story_mode, STORY_OFF)
            async with db.guild(GUILD_A) as conn:  # the database itself refuses a bad mode
                with self.assertRaises(psycopg.errors.CheckViolation):
                    await conn.execute(
                        "UPDATE campaigns SET story_mode = 'loud' WHERE id = %s", (old.id,)
                    )
        finally:
            await db.close()
            await drop_schema(TEST_URL, schema)


def press(user: int, guild: int = GUILD_A) -> Any:
    sent: dict[str, Any] = {}

    async def send_message(content: str = "", **kw: Any) -> None:
        sent.update(content=content, **kw)

    async def edit_message(**kw: Any) -> None:
        sent.update(edited=True, **kw)

    async def defer(**kw: Any) -> None:
        pass

    member = MagicMock(spec=discord.Member)
    member.id = user
    it = MagicMock()
    it.guild = SimpleNamespace(id=guild)
    it.guild_id = guild
    it.user = member
    it.response.send_message = send_message
    it.response.edit_message = edit_message
    it.response.is_done = lambda: False  # (a press that was not deferred)
    it.response.defer = defer
    it.edit_original_response = edit_message  # (after a defer, the card is changed in place)
    it.sent = sent
    return it


class Buttons(Setup):
    def bot(self) -> Any:
        return SimpleNamespace(campaigns=self.campaigns)

    def pressing(self, user: int, guild: int = GUILD_A) -> Any:
        it = press(user, guild)
        it.client = self.bot()
        return it

    def labels(self, it: Any) -> list[str]:
        return [str(getattr(getattr(c, "item", c), "label", "")) for c in it.sent["view"].children]

    async def test_the_card_shows_three_buttons_and_says_nothing_reads_it_yet(self) -> None:
        it = self.pressing(DM)
        await ui.StoryButton(self.a.id).callback(it)
        self.assertEqual(self.labels(it), ["Off", "Watch the story", "Guide me"])
        self.assertTrue(it.sent["ephemeral"])
        self.assertIn(
            "Not working yet. DMbot can keep your adventure, but nothing reads it",
            it.sent["content"],
        )
        self.assertIn("DMbot remembers your table's story only.", it.sent["content"])

    async def test_a_non_dm_is_refused_everywhere(self) -> None:
        for button in (
            ui.StoryButton(self.a.id),
            ui.StoryChoiceButton(self.a.id, STORY_WATCH),
            ui.StoryYesButton(self.a.id, STORY_WATCH),
            ui.StoryNoButton(self.a.id),
        ):
            it = self.pressing(PLAYER)
            await button.callback(it)
            self.assertEqual(it.sent["content"], ui.ONLY_DMS, type(button).__name__)
        self.assertEqual(await self.mode(GUILD_A, self.a.id), STORY_OFF)
        self.assertEqual(await self.campaigns.confirmations(GUILD_A, self.a.id), [])

    async def test_another_servers_press_is_refused(self) -> None:
        it = self.pressing(DM, guild=GUILD_B)
        await ui.StoryButton(self.a.id).callback(it)
        self.assertEqual(it.sent["content"], ui.GONE)

    async def test_the_first_time_it_warns_and_saves_only_on_i_have_the_right(self) -> None:
        asked = self.pressing(DM)
        await ui.StoryChoiceButton(self.a.id, STORY_WATCH).callback(asked)
        self.assertIn("Only give DMbot material you have the right to use.", asked.sent["content"])
        self.assertIn("DMbot doesn't check this.", asked.sent["content"])
        self.assertEqual(self.labels(asked), [ui.RIGHT_LABEL, ui.NOT_NOW_LABEL])
        self.assertEqual(await self.mode(GUILD_A, self.a.id), STORY_OFF)  # not yet
        no = self.pressing(DM)
        await ui.StoryNoButton(self.a.id).callback(no)
        self.assertEqual(await self.mode(GUILD_A, self.a.id), STORY_OFF)
        yes = self.pressing(DM)
        await ui.StoryYesButton(self.a.id, STORY_WATCH).callback(yes)
        self.assertEqual(await self.mode(GUILD_A, self.a.id), STORY_WATCH)
        (row,) = await self.campaigns.confirmations(GUILD_A, self.a.id)
        self.assertEqual(row["user"], str(DM))

    async def test_after_that_it_saves_at_once_and_off_keeps_the_record(self) -> None:
        await ui.StoryYesButton(self.a.id, STORY_WATCH).callback(self.pressing(DM))
        await ui.StoryChoiceButton(self.a.id, STORY_OFF).callback(self.pressing(DM))
        self.assertEqual(await self.mode(GUILD_A, self.a.id), STORY_OFF)
        guide = self.pressing(DM)
        await ui.StoryChoiceButton(self.a.id, STORY_GUIDE).callback(guide)
        self.assertEqual(await self.mode(GUILD_A, self.a.id), STORY_GUIDE)  # no second warning
        self.assertEqual(len(await self.campaigns.confirmations(GUILD_A, self.a.id)), 1)

    async def test_a_failed_save_keeps_the_buttons(self) -> None:
        self.campaigns.set_story_mode = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        it = self.pressing(DM)
        await ui.StoryYesButton(self.a.id, STORY_WATCH).callback(it)
        self.assertEqual(it.sent["content"], ui.COULDNT)
        self.assertEqual(self.labels(it), ["Off", "Watch the story", "Guide me"])

    async def test_saving_answers_discord_before_the_database(self) -> None:
        line = Timeline()
        it = self.pressing(DM)
        it.response, it.edit_original_response = line.response, line.edit_original_response
        self.campaigns.set_story_mode = line.slow("set_story_mode", self.a)  # type: ignore[method-assign]
        await ui.StoryYesButton(self.a.id, STORY_WATCH).callback(it)
        line.assert_answered_first()
        self.assertIn("slow:set_story_mode", line.events)

    async def test_the_settings_line_says_it_does_not_work_yet(self) -> None:
        from dataclasses import replace

        off = ui.line(self.a)
        on = ui.line(replace(self.a, story_mode=STORY_WATCH))
        self.assertIn("Coming soon", off)
        self.assertNotIn("not working yet", off)
        self.assertIn("(not working yet)", on)

    async def test_the_buttons_come_back_from_their_ids_after_a_restart(self) -> None:
        for cls, custom_id in (
            (ui.StoryButton, f"dmbot:story:{self.a.id}"),
            (ui.StoryChoiceButton, f"dmbot:storyset:{self.a.id}:guide"),
            (ui.StoryYesButton, f"dmbot:storyyes:{self.a.id}:watch"),
            (ui.StoryNoButton, f"dmbot:storyno:{self.a.id}"),
        ):
            match = cls.__discord_ui_compiled_template__.fullmatch(custom_id)
            assert match is not None
            made = await cls.from_custom_id(MagicMock(), MagicMock(), match)
            self.assertEqual(made.campaign_id, self.a.id)

    async def test_settings_has_the_button_and_a_line_for_dms_only(self) -> None:
        from dmbot.dm_screen import settings

        view = settings.settings_view(self.a, None, DM)
        self.assertIn(
            "Shared story", [getattr(getattr(c, "item", c), "label", "") for c in view.children]
        )
        self.assertIn("Shared story:", settings.settings_text(self.a, None, DM))
        player_view = settings.settings_view(self.a, None, PLAYER)
        self.assertNotIn(
            "Shared story",
            [getattr(getattr(c, "item", c), "label", "") for c in player_view.children],
        )
        self.assertNotIn("Shared story:", settings.settings_text(self.a, None, PLAYER))
