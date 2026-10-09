"""The /dmbot commands' first steps, with fake Discord interactions."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.bot import DMBot
from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.store import MAX_BACKUP_BYTES, encode_backup
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.sessions import SessionStore
from dmbot.ui import dmbot_commands as cmds
from tests.pg import DatabaseTest

GUILD, DM, OTHER_DM = 1, 7, 8


class FakeResponse:
    def __init__(self) -> None:
        self.done = False
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.deferred = False

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, text: str = "", **kw: Any) -> None:
        self.done = True
        self.sent.append((text, kw))

    async def defer(self, **kw: Any) -> None:
        self.done = True
        self.deferred = True


def fake_interaction(bot: DMBot, user_id: int = DM) -> Any:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.guild_permissions = discord.Permissions.none()
    guild = SimpleNamespace(id=GUILD, filesize_limit=10 * 1024 * 1024)
    followup = SimpleNamespace(send=AsyncMock())
    return SimpleNamespace(
        client=bot, guild=guild, user=user, response=FakeResponse(), followup=followup
    )


def attachment(raw: bytes, size: int | None = None) -> Any:
    a = MagicMock(spec=discord.Attachment)
    a.size = len(raw) if size is None else size
    a.read = AsyncMock(return_value=raw)
    return a


class CommandTests(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.consent = ConsentStore(self.db)
        self.campaigns = CampaignStore(self.db)
        self.sessions = SessionStore(self.db)
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            self.consent,
            self.campaigns,
            self.sessions,
        )
        self.bot.ears._active = SimpleNamespace(send=AsyncMock())  # type: ignore[assignment]

    async def test_first_start_shows_the_welcome(self) -> None:
        it = fake_interaction(self.bot)
        await cmds.dmbot_start.callback(it)  # type: ignore[call-arg]
        text, kw = it.response.sent[0]
        self.assertIn("never makes up the story", text)
        self.assertIsInstance(kw["view"], cmds.Welcome)
        self.assertTrue(kw["ephemeral"])

    async def test_returning_dm_sees_the_campaign_picker(self) -> None:
        await self.campaigns.create(GUILD, "Frostmaiden", DM)
        it = fake_interaction(self.bot)
        await cmds.dmbot_start.callback(it)  # type: ignore[call-arg]
        text, kw = it.response.sent[0]
        self.assertIn("**Frostmaiden**", text)
        self.assertIsInstance(kw["view"], cmds.CampaignPicker)

    async def test_restore_refuses_big_files_before_downloading(self) -> None:
        it = fake_interaction(self.bot)
        file = attachment(b"{}", size=MAX_BACKUP_BYTES + 1)
        await cmds.dmbot_restore.callback(it, file)  # type: ignore[call-arg]
        self.assertIn("too big", it.response.sent[0][0])
        file.read.assert_not_called()

    async def test_restore_rejects_files_that_are_not_backups(self) -> None:
        for raw in (b"not json", json.dumps({"hello": 1}).encode()):
            it = fake_interaction(self.bot)
            await cmds.dmbot_restore.callback(it, attachment(raw))  # type: ignore[call-arg]
            message = it.followup.send.call_args.args[0]
            self.assertIn("isn't a DMbot campaign backup", message)

    async def test_restore_offers_only_your_own_campaigns_to_replace(self) -> None:
        mine = await self.campaigns.create(GUILD, "Mine", DM)
        await self.campaigns.create(GUILD, "Theirs", OTHER_DM)
        backup = encode_backup(await self.campaigns.export(GUILD, mine.id))
        it = fake_interaction(self.bot)
        await cmds.dmbot_restore.callback(it, attachment(backup))  # type: ignore[call-arg]
        view = it.followup.send.call_args.kwargs["view"]
        self.assertIsInstance(view, cmds.RestoreChoice)
        self.assertEqual(view.replaceable_ids, [mine.id])

    async def test_backup_needs_a_campaign(self) -> None:
        it = fake_interaction(self.bot)
        await cmds.dmbot_backup.callback(it)  # type: ignore[call-arg]
        self.assertIn("no campaigns here yet", it.response.sent[0][0])

    async def test_anyone_can_download_a_complete_copy(self) -> None:
        await self.campaigns.create(GUILD, "Theirs", OTHER_DM)
        it = fake_interaction(self.bot)  # not this campaign's DM
        await cmds.dmbot_backup.callback(it)  # type: ignore[call-arg]
        sent = it.followup.send.call_args
        self.assertTrue(sent.kwargs["file"].filename.endswith(".dmbot.json.gz"))
        self.assertIn("complete copy", sent.args[0])
        self.assertIn("don't open it", sent.args[0])

    async def test_the_dm_gets_the_file(self) -> None:
        await self.campaigns.create(GUILD, "Mine", DM)
        it = fake_interaction(self.bot)
        it.user.guild_permissions = discord.Permissions(manage_guild=True)  # both: still fine
        await cmds.dmbot_backup.callback(it)  # type: ignore[call-arg]
        sent = it.followup.send.call_args.kwargs["file"]
        self.assertTrue(sent.filename.endswith(".dmbot.json.gz"))

    async def test_an_older_plain_backup_still_restores(self) -> None:
        mine = await self.campaigns.create(GUILD, "Mine", DM)
        data = await self.campaigns.export(GUILD, mine.id)
        data["version"] = 1
        it = fake_interaction(self.bot)
        await cmds.dmbot_restore.callback(it, attachment(json.dumps(data).encode()))  # type: ignore[call-arg]
        self.assertIsInstance(it.followup.send.call_args.kwargs["view"], cmds.RestoreChoice)

    async def test_a_boosted_server_is_sent_a_copy_bigger_than_ten_megabytes(self) -> None:
        await self.campaigns.create(GUILD, "Big", DM)
        it = fake_interaction(self.bot)
        it.guild.filesize_limit = 50 * 1024 * 1024
        with patch("dmbot.ui.logic.FILE_MAX", 10):  # the standard limit, made tiny
            await cmds.dmbot_backup.callback(it)  # type: ignore[call-arg]
        self.assertIn("file", it.followup.send.call_args.kwargs)

    async def test_a_backup_too_big_to_send_says_so(self) -> None:
        await self.campaigns.create(GUILD, "Huge_*one*", DM)
        limits = (
            patch("dmbot.ui.logic.FILE_MAX", 10),  # bigger than Discord sends
            patch("dmbot.campaigns.store.MAX_BACKUP_BYTES", 10),  # than a restore takes
        )
        for limit in limits:
            with self.subTest(limit), limit:
                it = fake_interaction(self.bot)
                it.guild.filesize_limit = 10  # Discord's number for this server
                await cmds.dmbot_backup.callback(it)  # type: ignore[call-arg]
                message = it.followup.send.call_args.args[0]
                self.assertIn("**Huge\\_\\*one\\*** is too big for DMbot to copy yet", message)
                self.assertIn("Nothing was lost", message)
                self.assertIn("Tell whoever runs DMbot", message)  # what to do next
                self.assertNotIn("file", it.followup.send.call_args.kwargs)


PRESS: Any = SimpleNamespace()  # a button press; the view only redraws itself


class NewCampaignButtons(unittest.IsolatedAsyncioTestCase):
    """#112: the settings are short buttons that fit a phone, one row per setting."""

    def labels(self, view: Any) -> dict[int, list[str]]:
        rows: dict[int, list[str]] = {}
        for item in list(view.children):
            rows.setdefault(item.row, []).append(item.label)
        return rows

    async def tap(self, view: Any, label: str) -> AsyncMock:
        (button,) = [i for i in list(view.children) if i.label == label]
        with patch.object(cmds, "_replace", AsyncMock()) as replaced:
            await button.callback(PRESS)
        return replaced

    async def test_one_row_per_setting_and_the_recommended_ones_are_ticked(self) -> None:
        from dmbot.ui.logic import PHONE_LABEL_MAX

        view = cmds.NewCampaignSettings("Frostmaiden")
        self.assertEqual(
            self.labels(view),
            {
                0: ["✓ Main rules: 2024", "Main rules: 2014"],
                1: ["✓ If missing: use 2014", "If missing: skip it"],
                2: ["✓ Optional rules: on", "Optional rules: off"],
                3: ["DM screen: only the DM", "✓ DM screen: players peek", "DM screen: everyone"],
                # How much DMbot says shares the last row with Create (5 rows at most);
                # Chatty isn't offered until something uses it.
                4: ["Quiet", "✓ Normal", "▶ Create campaign"],
            },
        )
        children: list[Any] = list(view.children)
        self.assertFalse(any(isinstance(i, discord.ui.Select) for i in children))
        for item in children:
            self.assertLessEqual(len(item.label or ""), PHONE_LABEL_MAX)
        ticked = [i for i in children if (i.label or "").startswith("✓")]
        self.assertTrue(all(i.style is discord.ButtonStyle.primary for i in ticked))
        ids = [i.custom_id for i in children]
        self.assertEqual(len(set(ids)), len(ids))
        again: list[Any] = list(cmds.NewCampaignSettings("Frostmaiden").children)
        self.assertEqual(ids, [i.custom_id for i in again])  # the same ids after a redraw

    async def test_one_tap_changes_a_setting(self) -> None:
        view = cmds.NewCampaignSettings("Frostmaiden")
        replaced = await self.tap(view, "Main rules: 2014")
        self.assertEqual(view.target, "2014")
        self.assertEqual(view.fallback, "2024")  # it can't be the same as the main rules
        rows = self.labels(view)
        self.assertEqual(rows[0], ["Main rules: 2024", "✓ Main rules: 2014"])
        self.assertEqual(rows[1], ["✓ If missing: use 2024", "If missing: skip it"])
        self.assertIn("**Main rules:** 2014 rules (older)", replaced.call_args.args[1])
        await self.tap(view, "Main rules: 2024")  # not the last button in its row
        self.assertEqual(view.target, "2024")
        await self.tap(view, "DM screen: only the DM")  # the first of three
        self.assertEqual(view.visibility, "private")
        await self.tap(view, "Optional rules: off")
        self.assertFalse(view.optional)

    async def test_skip_it_stays_when_the_main_rules_change(self) -> None:
        view = cmds.NewCampaignSettings("Frostmaiden")
        await self.tap(view, "If missing: skip it")
        await self.tap(view, "Main rules: 2014")
        self.assertEqual(view.fallback, "none")
        self.assertIn("✓ If missing: skip it", self.labels(view)[1])

    async def test_create_saves_what_was_chosen(self) -> None:
        view = cmds.NewCampaignSettings("Frostmaiden")
        for label in ("Main rules: 2014", "If missing: skip it", "Optional rules: off",
                      "DM screen: everyone", "Quiet"):  # fmt: skip
            await self.tap(view, label)
        create = AsyncMock(return_value=SimpleNamespace())
        it: Any = SimpleNamespace(
            guild=SimpleNamespace(id=GUILD),
            user=SimpleNamespace(id=DM),
            client=SimpleNamespace(campaigns=SimpleNamespace(create=create)),
        )
        with patch.object(cmds, "show_voice_step", AsyncMock()):
            await view._create(it)
        self.assertEqual(
            create.call_args.kwargs,
            {
                "target_ruleset": "2014",
                "fallback_ruleset": "none",
                "optional_rules_default": False,
                "dm_screen_visibility": "open",
                "dm_screen_level": "quiet",
            },
        )


class AnswerBeforeTheLock(unittest.IsolatedAsyncioTestCase):
    """#88: /dmbot stop and Restore answer before waiting for the session lock, which a
    /dmbot start setting up the DM screen can hold for a few seconds."""

    def bot_with_a_held_lock(self) -> tuple[Any, asyncio.Lock]:
        lock = asyncio.Lock()
        locked_out = AsyncMock(return_value="✅ Stopped listening.")

        async def stop_session(*_: Any, **__: Any) -> str:
            async with lock:
                return str(await locked_out())

        bot = SimpleNamespace(
            stop_session=stop_session,
            session_lock=lambda _gid: lock,
            is_campaign_playing=AsyncMock(return_value=False),
            campaigns=SimpleNamespace(import_backup=AsyncMock(return_value=MagicMock(name="c"))),
        )
        return bot, lock

    async def test_stop_defers_first(self) -> None:
        bot, lock = self.bot_with_a_held_lock()
        it = fake_interaction(bot)
        await lock.acquire()  # a start is setting up the DM screen
        stopping = asyncio.create_task(cmds.dmbot_stop.callback(it))  # type: ignore[call-arg]
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertTrue(it.response.deferred)  # answered while waiting
        lock.release()
        await stopping
        self.assertIn("Stopped listening", it.followup.send.await_args.args[0])

    async def test_restore_says_restoring_first(self) -> None:
        bot, lock = self.bot_with_a_held_lock()
        it = fake_interaction(bot)
        response = it.response

        async def answered(**_: Any) -> None:
            response.done = True

        response.edit_message = AsyncMock(side_effect=answered)
        it.edit_original_response = AsyncMock()
        choice = cmds.RestoreChoice({}, "Frostmaiden", [])
        await lock.acquire()
        restoring = asyncio.create_task(choice.restore(it, None))
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertEqual(it.response.edit_message.await_args.kwargs["content"], "Restoring…")
        bot.campaigns.import_backup.assert_not_awaited()  # still waiting for the lock
        lock.release()
        await restoring
        self.assertIn("Restored", it.edit_original_response.await_args.kwargs["content"])


class RestoreEndsWithAnOutcome(unittest.IsolatedAsyncioTestCase):
    """#88: after "Restoring…", every path ends with the menu saying what happened."""

    async def restore_with(self, **bot_kw: Any) -> str:
        defaults: dict[str, Any] = {
            "session_lock": lambda _gid: asyncio.Lock(),
            "is_campaign_playing": AsyncMock(return_value=False),
            "campaigns": SimpleNamespace(import_backup=AsyncMock(return_value=MagicMock())),
        }
        bot = SimpleNamespace(**{**defaults, **bot_kw})
        it = fake_interaction(bot)  # type: ignore[arg-type]
        response = it.response

        async def answered(**_: Any) -> None:
            response.done = True

        response.edit_message = AsyncMock(side_effect=answered)
        it.edit_original_response = AsyncMock()
        choice = cmds.RestoreChoice({}, "Frostmaiden", [])
        await choice.restore(it, "c" * 32)
        self.assertTrue(choice.is_finished())  # its timeout won't cover the outcome
        return str(it.edit_original_response.await_args.kwargs["content"])

    async def test_playing_right_now(self) -> None:
        text = await self.restore_with(is_campaign_playing=AsyncMock(return_value=True))
        self.assertIn("playing right now", text)
        self.assertIn("`/dmbot restore` again", text)

    async def test_a_check_that_fails(self) -> None:
        with self.assertLogs("dmbot.ui.dmbot_commands", "ERROR"):
            text = await self.restore_with(is_campaign_playing=AsyncMock(side_effect=OSError()))
        self.assertEqual(text, cmds.RESTORE_FAILED)

    async def test_a_damaged_backup(self) -> None:
        bad = SimpleNamespace(import_backup=AsyncMock(side_effect=CampaignError("damaged")))
        self.assertEqual(await self.restore_with(campaigns=bad), "damaged")

    async def test_an_unexpected_error(self) -> None:
        broken = SimpleNamespace(import_backup=AsyncMock(side_effect=RuntimeError("db")))
        with self.assertLogs("dmbot.ui.dmbot_commands", "ERROR"):
            text = await self.restore_with(campaigns=broken)
        self.assertEqual(text, cmds.RESTORE_FAILED)

    async def test_stop_that_fails_doesnt_hang(self) -> None:
        bot = SimpleNamespace(stop_session=AsyncMock(side_effect=RuntimeError("bug")))
        it = fake_interaction(bot)  # type: ignore[arg-type]
        with self.assertLogs("dmbot.ui.dmbot_commands", "ERROR"):
            await cmds.dmbot_stop.callback(it)  # type: ignore[call-arg]
        self.assertEqual(it.followup.send.await_args.args[0], cmds.STOP_COMMAND_FAILED)


class CampaignPickersFitAPhone(unittest.TestCase):
    """#282: every campaign picker cuts names for a phone, and the DM can still tell
    the choices apart (restored copies differ only at the end of their names)."""

    def test_long_names_are_cut_and_told_apart(self) -> None:
        from dmbot.ui import names as names_ui
        from dmbot.ui import transcripts
        from dmbot.ui.logic import NAME_LABEL_MAX, PHONE_LABEL_MAX
        from tests.test_ui_logic import campaign

        long = "The Very Long and Winding Campaign of the Western Marches"
        ends = ["", " (restored)", " (restored 2)", " (restored 3)"]
        campaigns = [campaign(id=f"c{n}", name=long + end) for n, end in enumerate(ends)]
        views: list[Any] = [
            cmds.CampaignPicker(campaigns),
            cmds.BackupPicker(campaigns),
            cmds.RestoreChoice({}, long, campaigns),
            names_ui.CampaignChoice(campaigns),
            transcripts.CampaignPicker(campaigns, {c.id: 1 for c in campaigns}),  # #287
        ]
        for view in views:
            with self.subTest(type(view).__name__):
                children: list[Any] = list(view.children)
                (menu,) = [i for i in children if isinstance(i, discord.ui.Select)]
                labels = [o.label for o in menu.options]
                self.assertTrue(all(len(label) <= NAME_LABEL_MAX for label in labels))
                self.assertEqual(len(set(labels)), len(labels))  # never two the same
                self.assertTrue(all(o.description for o in menu.options))
                for item in children:
                    if not isinstance(item, discord.ui.Select):
                        self.assertLessEqual(len(item.label or ""), PHONE_LABEL_MAX)
                self.assertLessEqual(len(menu.placeholder or ""), PHONE_LABEL_MAX)

    def test_a_campaign_without_saved_transcripts_says_so(self) -> None:
        from dmbot.ui import transcripts
        from tests.test_ui_logic import campaign

        # #317: played with transcripts off, or restored from a backup, is not enough.
        played = campaign()
        restored = campaign(id="restored", name="Restored copy")
        new = campaign(id="new", name="Brand new", last_played_at=None)
        view: Any = transcripts.CampaignPicker([played, restored, new], {played.id: 2})
        (menu,) = [i for i in list(view.children) if isinstance(i, discord.ui.Select)]
        self.assertTrue((menu.options[0].description or "").startswith("Last played"))
        self.assertEqual(menu.options[1].description, "No transcripts yet")
        self.assertEqual(menu.options[2].description, "No transcripts yet")
