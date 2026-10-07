"""The /dmbot commands' first steps, with fake Discord interactions."""

import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.bot import DMBot
from dmbot.campaigns import CampaignStore
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
    guild = SimpleNamespace(id=GUILD)
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
        self.assertTrue(sent.kwargs["file"].filename.endswith(".dmbot.json"))
        self.assertIn("complete copy", sent.args[0])
        self.assertIn("don't open it", sent.args[0])

    async def test_the_dm_gets_the_file(self) -> None:
        await self.campaigns.create(GUILD, "Mine", DM)
        it = fake_interaction(self.bot)
        it.user.guild_permissions = discord.Permissions(manage_guild=True)  # both: still fine
        await cmds.dmbot_backup.callback(it)  # type: ignore[call-arg]
        sent = it.followup.send.call_args.kwargs["file"]
        self.assertTrue(sent.filename.endswith(".dmbot.json"))


class NewCampaignButtons(unittest.IsolatedAsyncioTestCase):
    """#112: the settings are short buttons that fit a phone, one row per setting."""

    def items(self, view: Any) -> list[Any]:
        return list(view.children)

    def labels(self, view: Any) -> dict[int, list[str]]:
        rows: dict[int, list[str]] = {}
        for item in self.items(view):
            rows.setdefault(item.row, []).append(item.label)
        return rows

    press: Any = SimpleNamespace()

    async def test_one_row_per_setting_and_the_recommended_ones_are_ticked(self) -> None:
        from dmbot.ui.logic import PHONE_LABEL_MAX

        view = cmds.NewCampaignSettings("Frostmaiden")
        self.assertEqual(
            self.labels(view),
            {
                0: ["✓ Main rules: 2024", "Main rules: 2014"],
                1: ["✓ If missing: 2014 rules", "If missing: nothing"],
                2: ["✓ Optional rules: on", "Optional rules: off"],
                3: ["DM screen: DM only", "✓ DM screen: players peek", "DM screen: everyone"],
                4: ["✅ Create campaign"],
            },
        )
        self.assertFalse(any(isinstance(i, discord.ui.Select) for i in self.items(view)))
        for item in self.items(view):
            self.assertLessEqual(len(item.label or ""), PHONE_LABEL_MAX)
        ticked = [i for i in self.items(view) if (i.label or "").startswith("✓")]
        self.assertTrue(all(i.style is discord.ButtonStyle.primary for i in ticked))

    async def test_one_tap_changes_a_setting(self) -> None:
        view = cmds.NewCampaignSettings("Frostmaiden")
        (button,) = [i for i in self.items(view) if i.label == "Main rules: 2014"]
        with patch.object(cmds, "_replace", AsyncMock()) as replaced:
            await button.callback(self.press)
        self.assertEqual(view.target, "2014")
        self.assertEqual(view.fallback, "2024")  # the old backup can't be the main rules
        rows = self.labels(view)
        self.assertEqual(rows[0], ["Main rules: 2024", "✓ Main rules: 2014"])
        self.assertEqual(rows[1], ["✓ If missing: 2024 rules", "If missing: nothing"])
        text = replaced.call_args.args[1]
        self.assertIn("**Main rules:** 2014 rules (older)", text)
        (off,) = [i for i in self.items(view) if i.label == "Optional rules: off"]
        with patch.object(cmds, "_replace", AsyncMock()):
            await off.callback(self.press)
        self.assertFalse(view.optional)
        self.assertIn("✓ Optional rules: off", self.labels(view)[2])
