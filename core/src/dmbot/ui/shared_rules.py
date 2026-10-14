"""Sharing house rules between campaigns (#1190), from ⚙️ Settings: the DM's menu.

A paid campaign's DMs can share their house rules as a named set; another campaign's DM can
follow it with a share code. Only rule text travels (never story, names or notes). Following
copies the set into the follower's own campaign: stopping sharing, or deleting the publisher,
never takes it away. Every button re-checks that the person is a DM of the campaign, and
nothing changes without a press.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.rules import rule_sets
from dmbot.rules.house import HouseRuleError
from dmbot.ui.dmbot_commands import _answer_first, _bot, _Button, _Menu, _send, _tell
from dmbot.ui.house_file import GONE, NOT_READY, ONLY_DMS, _dm_campaign

log = logging.getLogger(__name__)

SHARE_LABEL = "📤 Share my house rules"
STOP_LABEL = "Stop sharing"
CODE_LABEL = "🔑 New share code"
FOLLOW_LABEL = "📥 Use shared rules"
UNFOLLOW_LABEL = "Stop using shared rules"
UNDO_LABEL = "↩️ Undo"
CONFIRM_LABEL = "Yes, use this set"
CANCEL_LABEL = "Cancel"

INTRO = (
    "📚 **Shared house rules**\nShare your rules with another table, or use rules from "
    "another DM. Only rules are shared. Never your story, names or notes."
)
CODE_NOTE = (
    "Give this code to a DM who should use your rules. Anyone with the code can use them. "
    "Old codes still work; use Stop sharing to turn them all off."
)


def _escape(text: str) -> str:
    return str(discord.utils.escape_markdown(text))


def _count(offer_content: dict[str, Any]) -> int:
    rules = offer_content.get("rules")
    return len(rules) if isinstance(rules, list) else 0


def _rules(n: int) -> str:
    return f"{n} house rule" + ("" if n == 1 else "s")


def _store(interaction: discord.Interaction) -> Any:
    return getattr(_bot(interaction), "rule_sets", None)


async def open_menu(interaction: discord.Interaction, campaign: Campaign) -> None:
    """📚 Shared house rules: what this campaign shares and follows, and the ways to change
    it. The campaign's DMs only; the answer is private."""
    await _answer_first(interaction)
    current = await _dm_campaign(interaction, campaign)
    if current is None:
        return
    store = _store(interaction)
    if store is None:
        await _tell(interaction, NOT_READY)
        return
    try:
        mine = await store.published(current.guild_id, current.id)
        follows = [
            f
            for f in await store.following(current.guild_id, current.id)
            if f.kind == rule_sets.HOUSE_RULES
        ]
    except Exception:
        log.exception("Couldn't read the shared house rules")
        await _tell(interaction, NOT_READY)
        return
    lines = [INTRO]
    sharing = mine is not None and mine.shared
    if sharing and mine is not None:
        lines.append(f"• Sharing: **{_escape(mine.name)}** (version {mine.version}).")
    elif mine is not None:
        lines.append(f"• Not sharing now. Last set: **{_escape(mine.name)}**.")
    else:
        lines.append("• Not sharing your house rules.")
    followed = follows[0] if follows else None
    if followed is not None:
        note = "" if followed.updating else " It won't get updates."
        lines.append(
            f"• Using: **{_escape(followed.name)}** from {_escape(followed.publisher)} "
            f"(version {followed.version}).{note}"
        )
    else:
        lines.append("• Not using another table's rules.")
    await _send(
        interaction,
        "\n".join(lines),
        SharedMenu(current, sharing=sharing, following=followed is not None),
    )


class ShareForm(discord.ui.Modal, title="Share your house rules"):
    name: discord.ui.TextInput[ShareForm] = discord.ui.TextInput(
        label="Name for your rules",  # 45 characters at most
        placeholder="Example: Red Table rules",
        max_length=rule_sets.NAME_MAX,
    )

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=30 * 60)
        self.campaign = campaign

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await share(interaction, self.campaign, self.name.value)


class CodeForm(discord.ui.Modal, title="Use a shared set"):
    code: discord.ui.TextInput[CodeForm] = discord.ui.TextInput(
        label="The share code",  # 45 characters at most
        placeholder="Paste the code here",
        max_length=40,
    )

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=30 * 60)
        self.campaign = campaign

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await look_up(interaction, self.campaign, self.code.value)


async def share(interaction: discord.Interaction, campaign: Campaign, name: str) -> None:
    """Share the campaign's house rules as a set, then give its first code. Paid plans only."""
    await _answer_first(interaction)
    current = await _dm_campaign(interaction, campaign)
    store = _store(interaction)
    if current is None:
        return
    if store is None:
        await _tell(interaction, NOT_READY)
        return
    bot = _bot(interaction)
    refused = await bot.plan_gate("share", current.guild_id, current, interaction.user.id)
    if refused is not None:
        await _tell(interaction, refused)
        return
    if bot.house_rules is None:
        await _tell(interaction, NOT_READY)
        return
    try:
        rules = await bot.house_rules.list(current.guild_id, current.id)
        links = getattr(bot, "house_file_links", None)
        link = await links.get(current.guild_id, current.id) if links is not None else None
        shared = await store.publish(
            current.guild_id,
            current.id,
            interaction.user.id,
            name,
            rules,
            getattr(link, "link", None),
        )
        code = await store.make_code(current.guild_id, current.id, interaction.user.id)
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    await _tell(
        interaction,
        f"✅ Sharing **{_escape(shared.name)}**.\n`{code}`\n{CODE_NOTE}",
    )


async def new_code(interaction: discord.Interaction, campaign: Campaign) -> None:
    await _answer_first(interaction)
    current = await _dm_campaign(interaction, campaign)
    store = _store(interaction)
    if current is None or store is None:
        return
    try:
        code = await store.make_code(current.guild_id, current.id, interaction.user.id)
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    await _tell(interaction, f"`{code}`\n{CODE_NOTE}")


async def stop_sharing(interaction: discord.Interaction, campaign: Campaign) -> None:
    await _answer_first(interaction)
    current = await _dm_campaign(interaction, campaign)
    store = _store(interaction)
    if current is None or store is None:
        return
    try:
        had = await store.stop_sharing(current.guild_id, current.id, interaction.user.id)
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    await _tell(
        interaction,
        "Done. Your rules aren't shared now and their codes stopped working. Tables using "
        "them keep their copy."
        if had
        else rule_sets.NOT_SHARED,
    )


async def look_up(interaction: discord.Interaction, campaign: Campaign, code: str) -> None:
    """Find the set a code opens and show what it holds, before anything is copied."""
    await _answer_first(interaction)
    current = await _dm_campaign(interaction, campaign)
    store = _store(interaction)
    if current is None:
        return
    if store is None:
        await _tell(interaction, NOT_READY)
        return
    try:
        offer = await store.by_code(current.guild_id, interaction.user.id, code)
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    await _tell(
        interaction,
        f"**{_escape(offer.name)}** from {_escape(offer.publisher)}: "
        f"{_rules(_count(offer.content))} (version {offer.version}).\n"
        "If you use it, DMbot will check these rules too. Your own rules win if they "
        "disagree. You can stop any time.",
        view=ConfirmFollow(current, offer),
    )


class ConfirmFollow(_Menu):
    def __init__(self, campaign: Campaign, offer: rule_sets.Offer) -> None:
        super().__init__()
        self.campaign = campaign
        self.offer = offer
        self.add_item(_Button(self._yes, label=CONFIRM_LABEL, style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._no, label=CANCEL_LABEL))

    async def _yes(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction)
        current = await _dm_campaign(interaction, self.campaign)  # still a DM, still here
        store = _store(interaction)
        if current is None or store is None:
            return
        try:
            follow = await store.follow(
                current.guild_id, current.id, interaction.user.id, self.offer
            )
        except HouseRuleError as exc:
            await _tell(interaction, str(exc))
            return
        self.stop()
        await _tell(
            interaction,
            f"✅ Now using **{_escape(follow.name)}**. DMbot checks it along with your own rules.",
        )

    async def _no(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _tell(interaction, "Okay, nothing changed.")


class ConfirmStop(_Menu):
    def __init__(self, campaign: Campaign) -> None:
        super().__init__()
        self.campaign = campaign
        self.add_item(
            _Button(self._yes, label="Yes, stop sharing", style=discord.ButtonStyle.danger)
        )
        self.add_item(_Button(self._no, label=CANCEL_LABEL))

    async def _yes(self, interaction: discord.Interaction) -> None:
        self.stop()
        await stop_sharing(interaction, self.campaign)

    async def _no(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _tell(interaction, "Okay, nothing changed.")


class UndoUnfollow(_Menu):
    def __init__(self, campaign: Campaign, follow: rule_sets.Follow) -> None:
        super().__init__()
        self.campaign = campaign
        self.follow = follow
        self.add_item(_Button(self._undo, label=UNDO_LABEL))

    async def _undo(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction)
        current = await _dm_campaign(interaction, self.campaign)
        store = _store(interaction)
        if current is None or store is None:
            return
        try:
            await store.restore(current.guild_id, current.id, interaction.user.id, self.follow)
        except HouseRuleError as exc:
            await _tell(interaction, str(exc))
            return
        self.stop()
        await _tell(interaction, f"Back to using **{_escape(self.follow.name)}**.")


class SharedMenu(_Menu):
    def __init__(self, campaign: Campaign, *, sharing: bool, following: bool) -> None:
        super().__init__()
        self.campaign = campaign
        if sharing:
            self.add_item(_Button(self._code, label=CODE_LABEL, style=discord.ButtonStyle.primary))
            self.add_item(_Button(self._stop, label=STOP_LABEL, style=discord.ButtonStyle.danger))
        else:
            self.add_item(
                _Button(self._share, label=SHARE_LABEL, style=discord.ButtonStyle.primary)
            )
        if following:
            self.add_item(
                _Button(self._unfollow, label=UNFOLLOW_LABEL, style=discord.ButtonStyle.danger)
            )
        else:
            self.add_item(_Button(self._follow, label=FOLLOW_LABEL))

    async def _share(self, interaction: discord.Interaction) -> None:
        # Ask about the plan before the form, so nobody types a name only to be refused.
        # A modal must be the first answer, so the check has to finish inside Discord's
        # 3 seconds (plan_gate fails open if it is slow).
        bot = _bot(interaction)
        refused = await bot.plan_gate(
            "share", self.campaign.guild_id, self.campaign, interaction.user.id
        )
        if refused is not None:
            await _tell(interaction, refused)
            return
        await interaction.response.send_modal(ShareForm(self.campaign))

    async def _follow(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CodeForm(self.campaign))

    async def _code(self, interaction: discord.Interaction) -> None:
        await new_code(interaction, self.campaign)

    async def _stop(self, interaction: discord.Interaction) -> None:
        # Stopping turns off every code handed out, so it asks first.
        await _tell(
            interaction,
            "Stop sharing? Every share code will stop working. Tables already using your "
            "rules keep their copy.",
            view=ConfirmStop(self.campaign),
        )

    async def _unfollow(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction)
        current = await _dm_campaign(interaction, self.campaign)
        store = _store(interaction)
        if current is None or store is None:
            return
        try:
            follow = await store.unfollow(current.guild_id, current.id, interaction.user.id)
        except HouseRuleError as exc:
            await _tell(interaction, str(exc))
            return
        await _tell(
            interaction,
            f"Stopped using **{_escape(follow.name)}**. Your own rules are unchanged.",
            view=UndoUnfollow(current, follow),
        )


__all__ = ["GONE", "ONLY_DMS", "open_menu"]
