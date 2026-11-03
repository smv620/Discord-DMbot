"""Help lists every slash command and is small enough to send (the help button): each message is
under Discord's limit (one long message was over it, so Help could not be sent), every command the
bot registers is in the list and none that is gone, the DM-only ones are marked, and the Help card
has the link to the page that shows how to get to anything. Invented values only."""

from __future__ import annotations

import unittest

import discord
from discord import app_commands

from dmbot.bot import register_commands
from dmbot.discord_limits import MESSAGE_MAX
from dmbot.dm_screen import dm_bot_buttons as buttons
from dmbot.dm_screen import dm_bot_panel as panel
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import HelpButtons

DM_ONLY = {
    "/dmbot start", "/dmbot stop", "/dmbot settings", "/dmbot names", "/dmbot optionalrules",
    "/dmbot homebrew", "/dmbot recap", "/dmbot recap-upload", "/dmbot import", "/dmbot restore",
}  # fmt: skip


def registered() -> set[str]:
    """Every slash command the bot registers, as people type it (`/dmbot help`)."""
    tree: app_commands.CommandTree[discord.Client] = app_commands.CommandTree(
        discord.Client(intents=discord.Intents.none())
    )
    register_commands(tree)
    out: set[str] = set()
    for command in tree.get_commands():
        if isinstance(command, app_commands.Group):
            out |= {f"/{command.name} {sub.name}" for sub in command.commands}
        else:
            out.add(f"/{command.name}")
    return out


class TheHelpText(unittest.TestCase):
    def test_each_message_is_under_discords_limit(self) -> None:
        for part in logic.HELP_PARTS:
            self.assertLessEqual(len(part), MESSAGE_MAX)

    def test_every_registered_command_is_listed_and_none_that_is_gone(self) -> None:
        listed = {command for command, _, _ in logic.HELP_COMMANDS}
        self.assertEqual(listed, registered())

    def test_the_dm_only_commands_are_marked_and_listed_under_the_dm_heading(self) -> None:
        marked = {command for command, _, dm in logic.HELP_COMMANDS if dm}
        self.assertEqual(marked, DM_ONLY)
        text = logic.HELP_COMMANDS_TEXT
        everyone, _, dms = text.partition("**Commands for DMs only**")
        for command in DM_ONLY:
            self.assertIn(f"`{command}`", dms)
            self.assertNotIn(f"`{command}`", everyone)

    def test_the_help_still_holds_the_recording_facts(self) -> None:
        self.assertIn("Anyone in this server can read what DMbot writes down", logic.HELP_TEXT)
        self.assertIn("isn't used to train their AI", logic.HELP_TEXT)

    def test_the_text_points_at_the_where_to_find_page(self) -> None:
        self.assertIn(panel.WHERE_LABEL, logic.HELP_COMMANDS_TEXT)
        self.assertIn("DMs only", logic.HELP_COMMANDS_TEXT)


class TheLink(unittest.TestCase):
    def test_the_address_is_the_configured_sites_else_the_public_one(self) -> None:
        self.assertEqual(panel.where_url(""), "https://getdmbot.com/where")
        self.assertEqual(
            panel.where_url("https://test.example.org"), "https://test.example.org/where"
        )
        self.assertEqual(panel.where_url("http://plain.example.org"), "https://getdmbot.com/where")

    def test_the_label_fits_a_button(self) -> None:
        self.assertLessEqual(len(panel.WHERE_LABEL), 80)

    def test_the_help_command_has_the_link_button(self) -> None:
        import asyncio

        async def build() -> list[tuple[str | None, str | None]]:
            view = HelpButtons("https://test.example.org")
            return [(getattr(i, "label", None), getattr(i, "url", None)) for i in view.children]

        got = asyncio.run(build())
        self.assertIn((panel.WHERE_LABEL, "https://test.example.org/where"), got)

    def test_the_help_card_has_the_link_beside_the_rule_and_transcripts_buttons(self) -> None:
        import asyncio

        from dmbot.campaigns import Campaign

        async def build() -> list[str | None]:
            campaign = Campaign.__new__(Campaign)
            object.__setattr__(campaign, "id", "c" * 32)
            view = buttons.help_view(campaign, "https://test.example.org")
            return [getattr(i, "url", None) for i in view.children]

        urls = asyncio.run(build())
        self.assertIn("https://test.example.org/where", urls)
        self.assertIn("https://test.example.org/account/transcripts", urls)
