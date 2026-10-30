"""🌐 Approved sites, in ⚙️ Manage DMbot → 📚 Homebrew (#1652; CLAUDE.md, "Least privilege": every
access must be visible and revocable). A campaign says once per site that it has the right to
use homebrew from there (#1633). Here a DM sees each site with who said so and when, and can
take it back: the entries from that site stay saved but DMbot stops using them until a DM says
yes again. Nothing is deleted. DM only, asked again on every press.
"""

from __future__ import annotations

import logging

import discord

from dmbot.campaigns import Campaign
from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.rules.homebrew import Homebrew, HomebrewError
from dmbot.rules.homebrew_sources import Approval
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    _answer_first,
    _bot,
    _Button,
    _Menu,
    _Select,
    _tell,
)
from dmbot.ui.names import _md

log = logging.getLogger(__name__)

NONE_YET = "No sites yet. They appear here after you add homebrew from a link."
INTRO = "**Sites you said DMbot may use homebrew from.** Take one back and DMbot stops using it."
NOT_READY = "DMbot can't show this just now. Try again in a minute."


def entries_from(entries: list[Homebrew], domain: str) -> list[Homebrew]:
    return [e for e in entries if e.credit_domain == domain]


def _when(approval: Approval) -> str:
    return f"<t:{approval.approved_at}:D>"


def sites_text(approvals: list[Approval], names: dict[int, str], note: str = "") -> str:
    """Each site with who approved it and when ("dndbeyond.com: approved by Sam on 16 Oct")."""
    lines = [note] if note else []
    lines.append("🌐 **Approved sites**")
    if not approvals:
        lines.append(NONE_YET)
    else:
        lines.append(INTRO)
        for a in approvals:
            who = _md(names.get(a.approved_by, "a DM"))
            lines.append(f"• **{_md(a.domain)}**: approved by {who}, {_when(a)}")
    return fit_message("\n".join(lines), limit=MESSAGE_MAX - 100)


def take_back_text(domain: str, count: int) -> str:
    """One plain question: what stops, what stays."""
    stops = (
        f"the {count} {'entry' if count == 1 else 'entries'} from it"
        if count
        else "anything from it"
    )
    return (
        f"Take back **{_md(domain)}**? DMbot will stop using {stops} until a DM says yes again. "
        "Nothing is deleted."
    )


def _names(interaction: discord.Interaction, approvals: list[Approval]) -> dict[int, str]:
    """Display names of the people who approved, for those still in the server."""
    lookup = getattr(interaction.guild, "get_member", None)  # (not cached: "a DM")
    out: dict[int, str] = {}
    for a in approvals:
        member = lookup(a.approved_by) if lookup is not None else None
        if member is not None:
            out[a.approved_by] = member.display_name
    return out


async def show_sites(
    interaction: discord.Interaction, campaign: Campaign, *, note: str = ""
) -> None:
    """Draw the screen from the database as it is now."""
    from dmbot.ui.homebrew import _show

    sources = _bot(interaction).sources
    if sources is None:
        await _tell(interaction, NOT_READY)
        return
    approvals = await sources.approvals(campaign.guild_id, campaign.id)
    text = sites_text(approvals, _names(interaction, approvals), note)
    await _show(interaction, text, SitesMenu(campaign, approvals, _names(interaction, approvals)))


class SitesMenu(_Menu):
    def __init__(
        self, campaign: Campaign, approvals: list[Approval], names: dict[int, str]
    ) -> None:
        super().__init__()
        self.campaign = campaign
        if approvals:
            self.pick = _Select(
                self._picked,
                placeholder="Take back a site…",
                options=[
                    discord.SelectOption(
                        label=logic.shorten(a.domain, logic.OPTION_LABEL_MAX),
                        value=a.domain,
                        description=logic.shorten(
                            f"Approved by {names.get(a.approved_by, 'a DM')}",
                            logic.DESCRIPTION_MAX,
                        ),
                    )
                    for a in approvals[: logic.SELECT_OPTIONS_MAX]
                ],
            )
            self.add_item(self.pick)
        self.add_item(_Button(self._back, label="↩ Back to homebrew", row=1))

    async def _picked(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.homebrew import ONLY_DMS, _is_dm, _show

        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        await _answer_first(interaction, in_place=True)
        store = _bot(interaction).homebrew
        domain = self.pick.values[0]
        entries = await store.list(self.campaign.guild_id, self.campaign.id) if store else []
        count = len(entries_from(entries, domain))
        await _show(interaction, take_back_text(domain, count), TakeBack(self.campaign, domain))

    async def _back(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.homebrew import _redraw

        await _redraw(interaction, self.campaign)


class TakeBack(_Menu):
    """The one question, then Take it back or Cancel (Cancel first: the safe one)."""

    def __init__(self, campaign: Campaign, domain: str) -> None:
        super().__init__()
        self.campaign, self.domain = campaign, domain
        self.add_item(_Button(self._cancel, label="Cancel"))
        self.add_item(
            _Button(self._confirm, label="Take it back", style=discord.ButtonStyle.danger)
        )

    used = False

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        await show_sites(interaction, self.campaign, note="Nothing was changed.")

    async def _confirm(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.homebrew import ONLY_DMS, _changed, _is_dm

        if not await _is_dm(interaction, self.campaign):
            await _tell(interaction, ONLY_DMS)
            return
        if self.used:
            await _tell(interaction, "Already done.")
            return
        await _answer_first(interaction, in_place=True)
        sources = _bot(interaction).sources
        if sources is None:
            await _tell(interaction, NOT_READY)
            return
        self.used = True  # a double press takes it back once (set only once it can go through)
        c = self.campaign
        try:
            gone = await sources.revoke(c.guild_id, c.id, interaction.user.id, self.domain)
        except HomebrewError as exc:
            await show_sites(interaction, c, note=f"⚠️ {exc}")
            return
        log.info("Site approval taken back: user=%s campaign=%s", interaction.user.id, c.id)
        _changed(interaction, c)
        self.stop()
        note = (
            f"✅ Took back **{_md(self.domain)}**. Its entries are saved but skipped."
            if gone
            else f"**{_md(self.domain)}** was already taken back."
        )
        await show_sites(interaction, c, note=note)
