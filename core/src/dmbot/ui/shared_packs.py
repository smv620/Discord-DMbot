"""Homebrew packs, from ⚙️ Manage DMbot → 📚 Shared rules → 📦 Homebrew packs (#1527): share the
campaign's own homebrew as a named pack, follow another table's pack, and put the followed packs
in order. Only entries travel (never story, names or notes), and entries that came from a
published book never leave their campaign (CLAUDE.md, IP rule): the Share step says which stay.

Every press re-checks that the person is a DM of the campaign, nothing changes without a press,
and a running session is told (`homebrew_changed`) so its rules cards read the new names.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.rules import homebrew_packs as packs
from dmbot.rules import pack_content, rule_sets
from dmbot.ui.dmbot_commands import (
    _answer_first,
    _bot,
    _Button,
    _Menu,
    _replace,
    _Select,
    _send,
    _tell,
)
from dmbot.ui.shared_rules import (
    NOT_READY,
    ONLY_DMS,
    _dm_campaign,
    _escape,
    _guarded,
)

log = logging.getLogger(__name__)

INTRO = (
    "📦 **Homebrew packs**\nA Homebrew pack is a named bundle of your homebrew: the spells, "
    "monsters, items and other game content in ⚙️ Manage DMbot → 📚 Homebrew. Share your "
    "pack with another table, or follow someone else's. Only homebrew is shared in a pack, "
    "never your story, names or notes."
)
SITE_MAX = 3  # sites with entries waiting that get a button on the card (a card holds 25)
SHARE_LABEL = "📤 Share my homebrew"
PUBLISH_LABEL = "🔄 Publish my changes"
CODE_LABEL = "🔑 New share code"
STOP_LABEL = "Stop sharing"
FOLLOW_LABEL = "📥 Follow a pack"
CONFIRM_LABEL = "Yes, follow this pack"
CANCEL_LABEL = "Cancel"
EARLIER_LABEL = "▲ Earlier"
LATER_LABEL = "▼ Later"
UNFOLLOW_LABEL = "Stop following"
UNDO_LABEL = "↩️ Undo"
CODE_NOTE = (
    "Any DM in this server, and anyone who has this code, can read all the shared entries. "
    "Old codes still work until you press Stop sharing."
)
NAMES_SHOWN = 5  # names quoted for entries that stay, so a long list stays short
NOT_A_PACK_CODE = "That code is for house rules. Use 📚 Shared rules → Use shared rules."
PICK_PLACEHOLDER = "Change the order or stop following…"
FOLLOW_PLACEHOLDER = "A pack shared in this server…"


def _entries(n: int) -> str:
    return f"{n} entr" + ("y" if n == 1 else "ies")


def _names(names: list[str]) -> str:
    shown = ", ".join(_escape(n) for n in names[:NAMES_SHOWN])
    more = len(names) - NAMES_SHOWN
    return shown + (f" and {more} more" if more > 0 else "")


def stays_home(left_out: list[str] | tuple[str, ...]) -> str:
    """The sentence that says which entries aren't shared and why ("" when all are): book text
    stays in one campaign (CLAUDE.md, IP rule)."""
    if not left_out:
        return ""
    return (
        f"Not shared: {_names(list(left_out))}. They come from a book or a website, and that "
        "text stays in this campaign."
    )


def _count(content: dict[str, Any]) -> int:
    """How many entries a pack's content holds."""
    entries = content.get("entries")
    return len(entries) if isinstance(entries, list) else 0


def _store(interaction: discord.Interaction) -> Any:
    store = getattr(_bot(interaction), "rule_sets", None)
    return store if isinstance(store, packs.HomebrewPackStore) else None


def _changed(interaction: discord.Interaction, campaign: Campaign) -> None:
    """A running session reads the campaign's homebrew names again."""
    changed = getattr(_bot(interaction), "homebrew_changed", None)
    if changed is not None:
        changed(campaign.guild_id, campaign.id)


async def _ready(
    interaction: discord.Interaction, campaign: Campaign
) -> tuple[Campaign, Any] | None:
    """Answer Discord, then check this person is a DM of the campaign right now and the store
    is there. Anyone who can't go on is told why."""
    await _answer_first(interaction)
    current = await _dm_campaign(interaction, campaign)
    if current is None:
        return None
    store = _store(interaction)
    if store is None:
        await _tell(interaction, NOT_READY)
        return None
    return current, store


async def _paid(interaction: discord.Interaction, current: Campaign) -> bool:
    """Sharing is a paid-plan feature: said before anything is published or a code is made."""
    refused = await _bot(interaction).plan_gate(
        "share", current.guild_id, current, interaction.user.id
    )
    if refused is not None:
        await _tell(interaction, refused)
    return refused is None


async def _own_entries(interaction: discord.Interaction, current: Campaign) -> list[Any]:
    brew = getattr(_bot(interaction), "homebrew", None)
    return list(await brew.list(current.guild_id, current.id)) if brew is not None else []


@_guarded
async def open_menu(interaction: discord.Interaction, campaign: Campaign) -> None:
    """What this campaign shares and follows, and the ways to change it. DMs only; private."""
    ready = await _ready(interaction, campaign)
    if ready is None:
        return
    current, store = ready
    text, view = await _menu(interaction, current, store)
    await _send(interaction, text, view)


async def _menu(
    interaction: discord.Interaction, current: Campaign, store: Any
) -> tuple[str, SharedPacksMenu]:
    mine = await store.published_pack(current.guild_id, current.id)
    follows = await store.followed_packs(current.guild_id, current.id)
    lines = [INTRO]
    sharing = mine is not None and mine.shared
    if sharing and mine is not None:
        lines.append(
            f"• **Sharing Status:** Sharing **{_escape(mine.name)}** (version {mine.version})."
        )
    elif mine is not None:
        lines.append(f"• **Sharing Status:** Not sharing now. Last pack: **{_escape(mine.name)}**.")
    else:
        lines.append("• **Sharing Status:** Not sharing your homebrew.")
    if mine is None or not mine.shared:  # say now what a Share would leave at home, and why
        _, left_out = pack_content.content_of(await _own_entries(interaction, current))
        if left_out:
            lines.append(f"• {stays_home(left_out)}")
    waiting = await _waiting(interaction, current)
    if follows:
        lines.append("• **Following:** the top one wins if two packs use the same name.")
        for i, f in enumerate(follows, start=1):
            note = "" if f.updating else " (no longer updated)"
            lines.append(f"  {i}. **{_escape(f.name)}** from {_escape(f.publisher)}{note}")
        lines.append("Your own homebrew always wins.")
    else:
        lines.append("• **Following:** Not following any pack.")
    if waiting:
        lines.append(waiting_text(waiting))
    return "\n".join(lines), SharedPacksMenu(
        current,
        sharing=sharing,
        follows=follows,
        last_name=mine.name if mine else "",
        waiting=waiting,
    )


async def _waiting(interaction: discord.Interaction, current: Campaign) -> dict[str, int]:
    """The followed packs' entries that wait for a site's approval, by site (#1635)."""
    reader = getattr(_bot(interaction), "homebrew_reader", None)
    return await reader.waiting(current.guild_id, current.id) if reader is not None else {}


def site_label(site: str) -> str:
    """The button for a site: "Use dndbeyond.com", cut to fit a phone (25 characters)."""
    label = f"Use {site.removeprefix('www.')}"
    return label if len(label) <= 25 else label[:24] + "…"


def waiting_text(waiting: dict[str, int]) -> str:
    """One plain line: how many entries wait, from which site, what to press, and why."""
    shown = sorted(waiting.items())[:SITE_MAX]  # (the buttons are capped the same)
    parts = ", ".join(f"{_entries(n)} from {_escape(site)}" for site, n in shown)
    more = " and more" if len(waiting) > len(shown) else ""
    press = (
        f"**{site_label(shown[0][0])}**"
        if len(waiting) == 1
        else "the **Use** button for each site"
    )
    return (
        f"• **Waiting for you:** {parts}{more}. Press {press} to say you have the right to use "
        "that site's homebrew. Until then DMbot skips them."
    )


class ShareForm(discord.ui.Modal, title="Share your homebrew"):
    name: discord.ui.TextInput[ShareForm] = discord.ui.TextInput(
        label="Name for your pack",  # 45 characters at most
        placeholder="Example: Frostvale homebrew",
        max_length=rule_sets.NAME_MAX,
    )

    def __init__(self, campaign: Campaign, name: str = "") -> None:
        super().__init__(timeout=30 * 60)
        self.campaign = campaign
        self.name.default = name or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await share(interaction, self.campaign, self.name.value)


class CodeForm(discord.ui.Modal, title="Follow a homebrew pack"):
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


@_guarded
async def share(interaction: discord.Interaction, campaign: Campaign, name: str) -> None:
    """Share the campaign's own homebrew as a pack, then give its first code. Paid plans only
    (the plan is checked before anything is read or written)."""
    ready = await _ready(interaction, campaign)
    if ready is None:
        return
    current, store = ready
    if not await _paid(interaction, current):
        return
    entries = await _own_entries(interaction, current)
    done = await store.publish_pack(
        current.guild_id, current.id, interaction.user.id, name, entries
    )
    code = await store.make_pack_code(current.guild_id, current.id, interaction.user.id)
    shared = [e for e in entries if not e.from_book and e.source is None]
    lines = [f"✅ Sharing **{_escape(done.pack.name)}**: {_entries(len(shared))}."]
    if done.left_out:
        lines.append(stays_home(done.left_out))
    lines += [CODE_NOTE, f"`{code}`"]  # the code last, so it is easy to copy on a phone
    await _tell(interaction, "\n".join(lines))


@_guarded
async def publish_changes(interaction: discord.Interaction, campaign: Campaign) -> None:
    """A new version of the pack from the homebrew as it is now. Tables following it take it at
    their next session start."""
    ready = await _ready(interaction, campaign)
    if ready is None:
        return
    current, store = ready
    if not await _paid(interaction, current):
        return
    entries = await _own_entries(interaction, current)
    done = await store.publish_pack_changes(
        current.guild_id, current.id, interaction.user.id, entries
    )
    await _tell(
        interaction,
        f"✅ **{_escape(done.pack.name)}** is now version {done.pack.version}. Tables following "
        f"it get the change at their next session. {stays_home(done.left_out)}".strip(),
    )


@_guarded
async def new_code(interaction: discord.Interaction, campaign: Campaign) -> None:
    ready = await _ready(interaction, campaign)
    if ready is None:
        return
    current, store = ready
    if not await _paid(interaction, current):
        return
    code = await store.make_pack_code(current.guild_id, current.id, interaction.user.id)
    await _tell(interaction, f"{CODE_NOTE}\n`{code}`")


@_guarded
async def stop_sharing(interaction: discord.Interaction, campaign: Campaign) -> None:
    ready = await _ready(interaction, campaign)
    if ready is None:
        return
    current, store = ready
    had = await store.stop_sharing_pack(current.guild_id, current.id, interaction.user.id)
    await _tell(
        interaction,
        "Done. Your pack isn't shared now and its codes stopped working. Tables following it "
        "keep their copy."
        if had
        else packs.NOT_SHARED,
    )


@_guarded
async def look_up(interaction: discord.Interaction, campaign: Campaign, code: str) -> None:
    """Find the pack a code opens and show what it holds, before anything is copied."""
    ready = await _ready(interaction, campaign)
    if ready is None:
        return
    current, store = ready
    offer = await store.by_code(current.guild_id, interaction.user.id, code)
    await _offer(interaction, current, offer)


async def _offer(
    interaction: discord.Interaction, current: Campaign, offer: rule_sets.Offer
) -> None:
    if offer.kind != packs.HOMEBREW:
        await _tell(interaction, NOT_A_PACK_CODE)
        return
    total = _count(offer.content)
    listed = offer.content.get("entries")
    names = [
        str(e["name"])
        for e in (listed[:NAMES_SHOWN] if isinstance(listed, list) else [])
        if isinstance(e, dict) and "name" in e
    ]
    await _send(
        interaction,
        f"**{_escape(offer.name)}** from {_escape(offer.publisher)}: {_entries(total)} "
        f"(update {offer.version}).\n"
        + (f"> {_names(names)}\n" if names else "")
        + await _sites_note(interaction, current, offer.content)
        + "If you follow it, DMbot will use these when it looks things up. Your own homebrew wins "
        "if a name matches. You can stop any time.",
        ConfirmFollow(current, offer),
    )


async def _sites_note(
    interaction: discord.Interaction, current: Campaign, content: dict[str, Any]
) -> str:
    """Before following: the sites the pack's entries come from that this campaign hasn't
    approved, so the DM knows what will wait (#1635)."""
    try:
        listed = pack_content.valid_content(content)["entries"]  # (the same check the readers use)
    except pack_content.BadPack:
        return ""
    counts: dict[str, int] = {}
    for e in listed:
        if isinstance(e.get("domain"), str):
            counts[e["domain"]] = counts.get(e["domain"], 0) + 1
    sources = getattr(_bot(interaction), "sources", None)
    if sources is not None:
        for site in list(counts):
            if await sources.approved(current.guild_id, current.id, site):
                del counts[site]
    if not counts:
        return ""
    parts = ", ".join(
        f"{_entries(n)} from {_escape(s)}" for s, n in sorted(counts.items())[:SITE_MAX]
    )
    return (
        f"**{parts} will wait.** After you follow, press the **Use** button for each site to "
        "turn them on.\n"
    )


class ConfirmFollow(_Menu):
    def __init__(self, campaign: Campaign, offer: rule_sets.Offer) -> None:
        super().__init__()
        self.campaign = campaign
        self.offer = offer
        self.add_item(_Button(self._yes, label=CONFIRM_LABEL, style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._no, label=CANCEL_LABEL))

    @_guarded
    async def _yes(self, interaction: discord.Interaction) -> None:
        ready = await _ready(interaction, self.campaign)  # still a DM, still here
        if ready is None:
            return
        current, store = ready
        follow = await store.follow_pack(
            current.guild_id, current.id, interaction.user.id, self.offer
        )
        self.stop()  # one press only
        _changed(interaction, current)
        waiting = await _waiting(interaction, current)
        await _tell(
            interaction,
            f"✅ Now following **{_escape(follow.name)}**. DMbot reads it after your own homebrew."
            + (f"\n{waiting_text(waiting)}" if waiting else ""),
        )

    async def _no(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _tell(interaction, "Okay, nothing changed.")


class RightToUsePack(_Menu):
    """The right-to-use step for a site whose entries a followed pack waits with (#1635): the
    approval is kept for the site in this campaign (who and when), and its entries turn on."""

    def __init__(self, campaign: Campaign, site: str) -> None:
        super().__init__()
        self.campaign, self.site = campaign, site
        self.add_item(_Button(self._no, label=CANCEL_LABEL))  # first: the safe one
        self.add_item(
            _Button(self._yes, label="I have the right", style=discord.ButtonStyle.danger)
        )

    used = False

    async def _no(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _tell(interaction, "Okay, nothing changed.")

    @_guarded
    async def _yes(self, interaction: discord.Interaction) -> None:
        ready = await _ready(interaction, self.campaign)  # still a DM, still here
        if ready is None or self.used:
            return
        current, _ = ready
        sources = getattr(_bot(interaction), "sources", None)
        if sources is None:
            await _tell(interaction, NOT_READY)
            return
        self.used = True  # a double press records the confirmation once
        try:
            await sources.approve(current.guild_id, current.id, interaction.user.id, self.site)
        except Exception:
            self.used = False  # nothing was recorded: the button works again
            raise
        self.stop()
        _changed(interaction, current)
        log.info(
            "Site approved for a followed pack: user=%s campaign=%s",
            interaction.user.id,
            current.id,
        )
        await _tell(interaction, f"✅ DMbot will use the entries from {_escape(self.site)} now.")


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
    def __init__(self, campaign: Campaign, follow: rule_sets.Follow, at: int) -> None:
        super().__init__()
        self.campaign = campaign
        self.follow = follow
        self.at = at
        self.add_item(_Button(self._undo, label=UNDO_LABEL))

    @_guarded
    async def _undo(self, interaction: discord.Interaction) -> None:
        ready = await _ready(interaction, self.campaign)
        if ready is None:
            return
        current, store = ready
        await store.restore_pack(
            current.guild_id, current.id, interaction.user.id, self.follow, self.at
        )
        self.stop()
        _changed(interaction, current)
        await _tell(
            interaction,
            f"Back to following **{_escape(self.follow.name)}**. Open 📦 Homebrew packs to see "
            "the order.",
        )


class PackActions(_Menu):
    """What to do with one followed pack: earlier, later, or stop following."""

    def __init__(self, campaign: Campaign, follow: rule_sets.Follow) -> None:
        super().__init__()
        self.campaign = campaign
        self.follow = follow
        self.add_item(_Button(self._earlier, label=EARLIER_LABEL))
        self.add_item(_Button(self._later, label=LATER_LABEL))
        self.add_item(
            _Button(self._unfollow, label=UNFOLLOW_LABEL, style=discord.ButtonStyle.danger)
        )

    async def _move(self, interaction: discord.Interaction, step: int) -> None:
        await _answer_first(interaction, in_place=True)  # the refreshed menu replaces this one
        ready = await _ready(interaction, self.campaign)
        if ready is None:
            return
        current, store = ready
        await store.move_pack(
            current.guild_id, current.id, interaction.user.id, self.follow.set_id, step
        )
        _changed(interaction, current)
        text, view = await _menu(interaction, current, store)
        await _replace(interaction, text, view)

    @_guarded
    async def _earlier(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, -1)

    @_guarded
    async def _later(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, 1)

    @_guarded
    async def _unfollow(self, interaction: discord.Interaction) -> None:
        ready = await _ready(interaction, self.campaign)
        if ready is None:
            return
        current, store = ready
        gone, at = await store.unfollow_pack(
            current.guild_id, current.id, interaction.user.id, self.follow.set_id
        )
        _changed(interaction, current)
        await _send(
            interaction,
            f"Stopped following **{_escape(gone.name)}**. Your own homebrew is unchanged.",
            UndoUnfollow(current, gone, at),
        )


class FollowChoose(_Menu):
    """Packs other campaigns in this server share, or a share code from anywhere."""

    def __init__(self, campaign: Campaign, offers: list[rule_sets.Offer]) -> None:
        super().__init__()
        self.campaign = campaign
        self.offers = {o.set_id: o for o in offers[:25]}
        self.pick: _Select | None = None
        if self.offers:
            self.pick = _Select(
                self._picked,
                placeholder=FOLLOW_PLACEHOLDER,
                options=[
                    discord.SelectOption(
                        label=o.name[:100], value=o.set_id, description=o.publisher[:100] or None
                    )
                    for o in self.offers.values()
                ],
            )
            self.add_item(self.pick)
        self.add_item(_Button(self._code, label="🔑 I have a share code"))

    @_guarded
    async def _picked(self, interaction: discord.Interaction) -> None:
        ready = await _ready(interaction, self.campaign)
        if ready is None:
            return
        current, _ = ready
        offer = self.offers.get(self.pick.values[0]) if self.pick is not None else None
        if offer is None:
            await _tell(interaction, rule_sets.NO_SUCH_SET)
            return
        await _offer(interaction, current, offer)

    async def _code(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CodeForm(self.campaign))


class SharedPacksMenu(_Menu):
    def __init__(
        self,
        campaign: Campaign,
        *,
        sharing: bool,
        follows: list[rule_sets.Follow],
        last_name: str = "",
        waiting: dict[str, int] | None = None,
    ) -> None:
        super().__init__()
        self.campaign = campaign
        self.last_name = last_name  # offered again when the DM shares after a stop
        self.follows = {f.set_id: f for f in follows}
        if sharing:
            self.add_item(_Button(self._publish, label=PUBLISH_LABEL))
            self.add_item(_Button(self._code, label=CODE_LABEL, style=discord.ButtonStyle.primary))
            self.add_item(_Button(self._stop, label=STOP_LABEL, style=discord.ButtonStyle.danger))
        else:
            self.add_item(
                _Button(self._share, label=SHARE_LABEL, style=discord.ButtonStyle.primary)
            )
        if len(follows) < pack_content.PACKS_MAX:
            self.add_item(_Button(self._follow, label=FOLLOW_LABEL))
        for site in sorted(waiting or {})[:SITE_MAX]:  # one button a site, to say it once
            self.add_item(_Button(partial(self._site, site), label=site_label(site), row=2))
        self.pick: _Select | None = None
        if follows:
            self.pick = _Select(
                self._pick,
                placeholder=PICK_PLACEHOLDER,
                options=[
                    discord.SelectOption(label=f"{i}. {f.name}"[:100], value=f.set_id)
                    for i, f in enumerate(follows, start=1)
                ],
            )
            self.add_item(self.pick)

    async def _site(self, site: str, interaction: discord.Interaction) -> None:
        from dmbot.ui.homebrew import SITE_WARNING  # (the one wording of the right-to-use step)

        if interaction.user.id not in self.campaign.dm_user_ids:
            await _tell(interaction, ONLY_DMS)
            return
        await _send(
            interaction, SITE_WARNING.format(site=site), RightToUsePack(self.campaign, site)
        )

    async def _share(self, interaction: discord.Interaction) -> None:
        # Ask about the plan before the form, so nobody types a name only to be refused. A modal
        # must be the first answer, so the check has to finish inside Discord's 3 seconds.
        if interaction.user.id not in self.campaign.dm_user_ids:  # a quick first filter; the
            await _tell(interaction, ONLY_DMS)  # real check is at submit (stale data is fine)
            return
        bot = _bot(interaction)
        refused = await bot.plan_gate(
            "share", self.campaign.guild_id, self.campaign, interaction.user.id
        )
        if refused is not None:
            await _tell(interaction, refused)
            return
        await interaction.response.send_modal(ShareForm(self.campaign, self.last_name))

    @_guarded
    async def _follow(self, interaction: discord.Interaction) -> None:
        ready = await _ready(interaction, self.campaign)
        if ready is None:
            return
        current, store = ready
        offers = await store.packs_here(current.guild_id, current.id)
        followed = set(self.follows)
        available = [o for o in offers if o.set_id not in followed]
        await _send(
            interaction,
            (
                "Pick a pack another table in this server shares, or paste a share code."
                if available
                else "No other table in this server is sharing a pack yet. If a DM gave you a "
                "share code, press the button."
            ),
            FollowChoose(current, available),
        )

    async def _publish(self, interaction: discord.Interaction) -> None:
        await publish_changes(interaction, self.campaign)

    async def _code(self, interaction: discord.Interaction) -> None:
        await new_code(interaction, self.campaign)

    async def _stop(self, interaction: discord.Interaction) -> None:
        # Stopping turns off every code handed out, so it asks first. Anyone may see the question;
        # only a DM's "yes" does anything (`stop_sharing` checks on the press).
        await _send(
            interaction,
            "Stop sharing? Every share code will stop working. Tables already following your "
            "pack keep their copy.",
            ConfirmStop(self.campaign),
        )

    @_guarded
    async def _pick(self, interaction: discord.Interaction) -> None:
        ready = await _ready(interaction, self.campaign)
        if ready is None:
            return
        current, store = ready
        chosen = self.pick.values[0] if self.pick is not None else ""
        follow = next(
            (
                f
                for f in await store.followed_packs(current.guild_id, current.id)
                if f.set_id == chosen
            ),
            None,
        )
        if follow is None:
            await _tell(interaction, packs.NOT_FOLLOWING)
            return
        await _send(
            interaction,
            f"**{_escape(follow.name)}**: move it up or down? The pack higher in the list wins "
            "when two packs use the same name.",
            PackActions(current, follow),
        )
