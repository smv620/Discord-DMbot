"""One-click install (#104): the permission list, the link, and the welcome on join."""

import pathlib
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot import install
from dmbot.dm_screen import messages
from dmbot.dm_screen.rules import missing_required

APP_ID = 1234567890
EVERYTHING = install.permissions()
README = pathlib.Path(__file__).resolve().parents[2] / "README.md"


def without(flag: str) -> discord.Permissions:
    perms = install.permissions()
    setattr(perms, flag, False)
    return perms


# ---- the list and the number ----------------------------------------------------


def test_permission_number_is_the_documented_one() -> None:
    assert install.permissions().value == 2251800085335056


def test_readme_lists_the_same_number_and_every_permission() -> None:
    text = README.read_text(encoding="utf-8")
    assert str(install.permissions().value) in text
    for needed in install.PERMISSIONS:
        assert f"**{needed.label}**" in text, needed.label


def test_list_has_no_administrator_and_no_duplicates() -> None:
    flags = [p.flag for p in install.PERMISSIONS]
    assert "administrator" not in flags
    assert len(flags) == len(set(flags))


def test_dm_screen_uses_the_same_names() -> None:
    assert missing_required(set()) == [
        "View Channels",
        "Send Messages",
        "Read Message History",
        "Manage Channels",
        "Manage Roles",
    ]


# ---- the link -------------------------------------------------------------------


def test_install_link_has_the_scopes_and_permissions() -> None:
    link = install.install_link(APP_ID)
    assert link is not None
    assert f"client_id={APP_ID}" in link
    assert "permissions=2251800085335056" in link
    assert "scope=bot+applications.commands" in link


def test_no_link_before_the_application_id_is_known() -> None:
    install.configure(None)
    assert install.install_link() is None


def test_configured_id_is_used() -> None:
    install.configure(APP_ID)
    try:
        assert install.install_link() == install.install_link(APP_ID)
    finally:
        install.configure(None)


# ---- what's missing -------------------------------------------------------------


def test_nothing_missing_with_every_permission() -> None:
    assert install.missing(EVERYTHING) == []


def test_administrator_covers_everything() -> None:
    assert install.missing(discord.Permissions(administrator=True)) == []


def test_view_channel_is_found_despite_discord_pys_old_name() -> None:
    assert install.missing(without("view_channel")) == ["View Channels"]


def test_missing_lists_discords_names_in_order() -> None:
    assert install.missing(discord.Permissions(view_channel=True, connect=True)) == [
        "Send Messages",
        "Read Message History",
        "Speak",
        "Manage Channels",
        "Manage Roles",
        "Pin Messages",
    ]


def test_missing_can_check_a_subset() -> None:
    perms = without("pin_messages")
    assert install.missing(perms, ["manage_roles", "manage_channels"]) == []
    assert install.missing(perms, ["pin_messages"]) == ["Pin Messages"]


# ---- wording --------------------------------------------------------------------


def test_human_list() -> None:
    assert install.human_list(["A"]) == "**A**"
    assert install.human_list(["A", "B"]) == "**A** and **B**"
    assert install.human_list(["A", "B", "C"]) == "**A**, **B** and **C**"


def test_missing_note_names_whats_missing_and_gives_the_link() -> None:
    link = install.install_link(APP_ID)
    note = install.missing_note(["Manage Roles", "Pin Messages"], link)
    assert "**Manage Roles** and **Pin Messages**" in note
    assert str(link) in note and "Authorize" in note
    assert "/dmbot start" in note


def test_without_a_link_the_note_says_where_to_click() -> None:
    assert "Server Settings → Roles → DMbot" in install.fix_hint(None)


def test_welcome_says_what_dmbot_does_and_how_to_start() -> None:
    text = install.welcome()
    assert "/dmbot start" in text
    assert "never makes rulings or story" in text


def test_dm_screen_permission_message_gives_the_link() -> None:
    install.configure(APP_ID)
    try:
        text = messages.needs_permissions(["Manage Roles"])
    finally:
        install.configure(None)
    assert "**Manage Roles**" in text
    assert f"client_id={APP_ID}" in text and "Authorize" in text


# ---- posting the welcome when DMbot joins ---------------------------------------


def text_channel(channel_id: int, position: int, can_post: bool) -> Any:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.position = position
    channel.permissions_for = lambda _me: discord.Permissions(
        view_channel=can_post, send_messages=can_post
    )
    channel.send = AsyncMock()
    return channel


def guild_with(perms: discord.Permissions, system: Any, channels: list[Any]) -> Any:
    me = MagicMock(spec=discord.Member)
    me.guild_permissions = perms
    guild = MagicMock(spec=discord.Guild)
    guild.me = me
    guild.system_channel = system
    guild.text_channels = channels
    return guild


class WelcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_welcome_goes_to_the_system_channel(self) -> None:
        system = text_channel(1, 5, can_post=True)
        other = text_channel(2, 0, can_post=True)
        outcome = await install.post_welcome(guild_with(EVERYTHING, system, [other, system]))
        system.send.assert_awaited_once()
        other.send.assert_not_called()
        assert "Thanks for adding DMbot" in system.send.call_args.args[0]
        assert "has every permission" in outcome

    async def test_missing_permissions_are_named(self) -> None:
        system = text_channel(1, 0, can_post=True)
        outcome = await install.post_welcome(guild_with(without("manage_roles"), system, [system]))
        text = system.send.call_args.args[0]
        assert "**Manage Roles**" in text
        assert "missing: Manage Roles" in outcome

    async def test_falls_back_to_the_first_channel_it_can_post_in(self) -> None:
        system = text_channel(1, 0, can_post=False)
        hidden = text_channel(2, 1, can_post=False)
        second = text_channel(4, 3, can_post=True)
        first = text_channel(3, 2, can_post=True)
        await install.post_welcome(guild_with(EVERYTHING, system, [second, hidden, first]))
        first.send.assert_awaited_once()
        second.send.assert_not_called()

    async def test_nowhere_to_post_is_reported_not_raised(self) -> None:
        nope = text_channel(1, 0, can_post=False)
        outcome = await install.post_welcome(guild_with(EVERYTHING, None, [nope]))
        assert "nowhere to post" in outcome

    async def test_a_failed_post_is_reported_not_raised(self) -> None:
        system = text_channel(1, 0, can_post=True)
        system.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=403), "no"))
        outcome = await install.post_welcome(guild_with(EVERYTHING, system, [system]))
        assert "couldn't post" in outcome
