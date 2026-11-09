"""The notice a lore upload leaves when it adds what DMbot is sure of by itself (#3878).

One live message per upload in the DM Bot channel (the DM screen is for live play, #3874), counts
only: how many names of each kind and how many connections were added, how many questions are
left, and the buttons: the Names page, the Story Graph, **↩️ Undo this upload** and, when
questions are left, a button for them. The same words go privately to the DMs. Never a name or a
word of the text; the upload's own name and date are the DM's, as they saw them.

Undo carries the server, campaign and notice in its id, so it works after a restart and in a
private message (which has no server). Only a DM of the campaign can press it, checked on every
press. It takes back exactly the changes the upload made, newest first, as the store's own Undo
does; one that something done since depends on is left, and the reply says so. It works for 7 days.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from typing import Any, NamedTuple

import discord

from dmbot.memory import background as bg
from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.mentions import NO_PINGS
from dmbot.ui import dm_links
from dmbot.ui.dmbot_commands import _bot, _tell
from dmbot.upload_notices import Notice

log = logging.getLogger(__name__)

NOT_YOURS = "Only this campaign's DM can undo this."
GONE = "I can't find this upload any more, so nothing was changed."
ALREADY = "This upload was already taken back."
TOO_LATE = "It is too late to undo this upload (7 days). Use each name's card instead."
PARTLY = (
    "↩️ Took back {done} of {total} changes from this upload. The rest were changed since, so "
    "they stay."
)
COULD_NOT = "Couldn't undo this upload just now. Nothing was changed; press again."
STOPPED = "↩️ Took back {done} of {total}; press Undo on each name's card for the rest."
NOTHING_LEFT = "↩️ Took back this upload. Its names and connections are gone."

NAMES_KEYS = ("npc", "place", "faction", "item", "event")
UNDO_LABEL = "↩️ Undo this upload"


def _plural(kind: str, count: int) -> str:
    one, many = bg.LABELS.get(kind, (kind, kind + "s"))
    return f"{count} {one if count == 1 else many}"


def notice_text(label: str, counts: Mapping[str, int], questions: int) -> str:
    """ "📜 Added **62 names** (30 NPCs, 24 places) and **48 connections** from <label>." and, when
    something is left, "**3 questions** need you." Counts only."""
    kinds = {
        k: v
        for k, v in counts.items()
        if k not in ("connections", "other_names", "shown", "held") and v
    }
    names = sum(kinds.values())
    parts = ", ".join(_plural(k, v) for k, v in sorted(kinds.items(), key=lambda x: -x[1]))
    connections = counts.get("connections", 0)
    other = counts.get("other_names", 0)
    added: list[str] = []
    if names:
        added.append(f"**{names} name{'' if names == 1 else 's'}** ({parts})")
    if connections:
        added.append(f"**{connections} connection{'' if connections == 1 else 's'}**")
    if other:
        added.append(f"**{other} other name{'' if other == 1 else 's'}**")
    what = " and ".join(added) if added else "**nothing new**"
    source = f" from {discord.utils.escape_markdown(label)}" if label else ""
    text = f"📜 Added {what}{source}."
    shown = counts.get("shown", 0)
    if shown:  # (an import's: held-back connections the table stated, #3963 c)
        text += (
            f"\n👁️ **{shown} connection{'' if shown == 1 else 's'}** said at the table "
            f"{'is' if shown == 1 else 'are'} now shown to your players."
        )
    held = counts.get("held", 0)
    if held:  # (an import's: kept hidden beside a connection DMbot already hides, #3979)
        one = held == 1
        text += (
            f"\n🙈 **{held} connection{'' if one else 's'}** {'is' if one else 'are'} kept hidden "
            f"from your players, so {'it' if one else 'they'} can't give away another hidden "
            f"connection between the same two names. To show {'it' if one else 'them'}: open "
            f"either name, ✏️ Edit a connection…, then 👁️ Who knows this?"
        )
    if questions:
        text += (
            f"\n❓ **{questions} question{'' if questions == 1 else 's'}** "
            f"need{'s' if questions == 1 else ''} you."
        )
    return text


def notice_view(
    notice: Notice,
    *,
    site_url: str,
    now: int | None = None,
    asks: Sequence[discord.ui.Item[Any]] = (),
) -> discord.ui.View:
    """The buttons: the questions left, if any (`asks`, the same buttons the private question has,
    each a persistent one), Names and Story Graph (link buttons, when the site has an https
    address), and Undo while it is still possible."""
    view = discord.ui.View(timeout=None)
    for ask in asks:
        view.add_item(ask)
    names = dm_links.site_link(site_url, "names", notice.guild_id, notice.campaign_id)
    graph = dm_links.site_link(site_url, "web", notice.guild_id, notice.campaign_id)
    if names:
        view.add_item(discord.ui.Button(label="See them on the Names page", emoji="🌐", url=names))
    if graph:
        view.add_item(discord.ui.Button(label="See them on the Story Graph", emoji="🕸️", url=graph))
    if notice.can_undo(int(time.time()) if now is None else now):
        view.add_item(UndoUploadButton(notice.guild_id, notice.campaign_id, notice.id))
    return view


class UndoUploadButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:up:undo:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32}):(?P<id>[0-9a-f]{32})",
):
    def __init__(self, guild_id: int, campaign_id: str, notice_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=UNDO_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:up:undo:{guild_id}:{campaign_id}:{notice_id}",
            )
        )
        self.guild_id, self.campaign_id, self.notice_id = guild_id, campaign_id, notice_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoUploadButton:
        return cls(int(match["guild"]), match["campaign"], match["id"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot = _bot(interaction)

        async def started() -> None:  # (the undo takes a while: answer Discord first)
            await interaction.response.defer()

        text = await undo_upload(
            bot, self.guild_id, self.campaign_id, self.notice_id, interaction.user.id,
            started=started,
        )  # fmt: skip
        if interaction.response.is_done() and text != COULD_NOT:  # (that one keeps its button)
            try:
                await interaction.edit_original_response(
                    content=text, view=None, allowed_mentions=NO_PINGS
                )
                return
            except discord.HTTPException:
                pass
        await _tell(interaction, text)


async def undo_upload(
    bot: Any,
    guild_id: int,
    campaign_id: str,
    notice_id: str,
    user_id: int,
    *,
    started: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """Undo one upload's changes for a game master, from the Discord button or the website's
    queue action: every check, the claim, the changes undone newest first as one chain, and the
    plain-words answer. `started` runs once the undo is claimed (the button answers Discord with
    it). Counts only in the log."""
    campaign = await bot.campaigns.get(guild_id, campaign_id)
    if campaign is None or bot.memory is None or bot.upload_notices is None:
        return GONE
    if user_id not in campaign.dm_user_ids:  # checked on every press
        return NOT_YOURS
    notices = bot.upload_notices
    found = await notices.get(guild_id, notice_id)
    if found is None:
        return GONE
    if found.undone_at is not None:
        return ALREADY
    if not found.can_undo(int(time.time())):
        return TOO_LATE
    claimed = await notices.claim_undo(guild_id, notice_id)
    if claimed is None:  # (a second press at the same moment)
        return ALREADY
    if started is not None:
        await started()
    done = 0
    broke = False
    for batch in reversed(claimed.changes):  # newest first, as one chain of Undos
        try:
            await bot.memory.undo(campaign.guild_id, campaign.id, batch)
            done += 1
        except (TooLateToUndo, MemoryRuleError):
            continue  # changed since: left as it is
        except Exception as exc:  # something unexpected: stop here, say so, never lose the button
            log.warning("Undoing an upload stopped (%s)", type(exc).__name__)
            broke = True
            break
    try:  # the lineage questions the upload left waiting go too (#3926); never stops the undo
        from dmbot.memory.trait_offers import TraitOfferStore

        await TraitOfferStore(bot.memory._db).drop_for_notice(
            campaign.guild_id, campaign.id, notice_id
        )
    except Exception as exc:
        log.warning("Undoing an upload's lineage questions failed (%s)", type(exc).__name__)
    if bot.lookup is not None:
        bot.lookup.drop([campaign.id])
    total = len(claimed.changes)
    if broke and done == 0:
        await notices.give_back_undo(guild_id, notice_id)  # nothing changed: again
        return COULD_NOT
    if broke:
        return STOPPED.format(done=done, total=total)
    return NOTHING_LEFT if done == total else PARTLY.format(done=done, total=total)


class DmClaims(NamedTuple):
    """Which connections an import may keep as sure (#3897): stated in the lines of these DMs, in
    the sessions that start at these times."""

    dm_ids: Collection[int]
    sessions: Collection[int]


async def add_sure(
    bot: Any,
    campaign: Any,
    stored: Mapping[str, Sequence[str]],
    label: str,
    *,
    connections: bool = True,
    source: tuple[str, str] | None = None,
    dm_claims: DmClaims | None = None,
) -> tuple[Notice, int] | None:
    """Add what DMbot is sure of from this reading, as the DM's own calls (`memory.auto_accept`),
    and keep the upload's notice with the changes it made. Returns the notice and the number of
    questions left (names and connections waiting, "Which one?" and same-person questions), or
    None when nothing could be kept (no store). `connections=False`: names only (an import's).
    `source`: ("lore" or "import", that upload's or import's id), so the website finds this notice
    by it (#3879). `dm_claims`: the DMs and this import's sessions, to also keep the connections the
    DM's own lines state, once their names are in (#3897; an import's); who may be recorded is read
    again just before. Never raises."""
    from dmbot.memory import auto_accept as auto
    from dmbot.ui.names import background_kind

    try:
        memory, links, notices = bot.memory, bot.backstory_links, bot.upload_notices
        if memory is None or notices is None:
            return None
        guild, cid = campaign.guild_id, campaign.id
        ids = {i for group in stored.values() for i in group}
        tally = auto.Tally()
        await auto.accept_names(
            memory, guild, cid, tally, narrow=True, only=ids, kind_of=background_kind
        )
        await auto.accept_other_names(memory, guild, cid, tally, only_entries=ids)
        if dm_claims is not None:  # (after the names, so the claims about them are ready)
            # (read again after the names; an import counts who is recorded in its own sessions)
            opted_in = await bot.consent.recorded_in(guild, cid)
            await auto.accept_dm_claims(
                memory, guild, cid, tally,
                dm_ids=dm_claims.dm_ids, opted_in=opted_in, sessions=dm_claims.sessions,
            )  # fmt: skip
        if (
            links is not None and connections
        ):  # (a bot without the connections store adds the names only)
            await auto.accept_connections(memory, links, guild, cid, tally, upload=label)
        if bot.lookup is not None:
            bot.lookup.drop([cid])
        counts = {**tally.kinds}
        if tally.accepted[auto.CONNECTIONS]:
            counts["connections"] = tally.accepted[auto.CONNECTIONS]
        if tally.accepted[auto.OTHER_NAMES]:
            counts["other_names"] = tally.accepted[auto.OTHER_NAMES]
        if tally.shown:
            counts["shown"] = tally.shown
        if tally.held:
            counts["held"] = tally.held
        questions = (
            tally.waiting[auto.NAMES]
            + tally.waiting[auto.CONNECTIONS]
            + tally.refused[auto.NAMES]
            + tally.refused[auto.CONNECTIONS]
            + (await links.count_which(guild, cid) if links is not None else 0)
            + len(await memory.same_person_offers(guild, cid))
        )
        notice = await notices.add(
            guild,
            cid,
            label,
            counts,
            tally.batches,
            source_kind=source[0] if source else None,
            source_id=source[1] if source else None,
        )
        return notice, questions
    except Exception:
        log.exception("Couldn't add what an upload was sure of")
        return None
