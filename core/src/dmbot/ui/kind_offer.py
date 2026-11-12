"""The question "make these words a kind of connection?" (#3033): after a session, when the same
words sit on three or more of a campaign's connections that have no kind of their own, the DM is
asked once, in their private chat, marked as from DMbot, with Yes, Not now and Never.

The question in the session's collected message has one button (the collected message gives each
question one); it opens the question with its three answers, for the DM only (checked on every
press, the DM screen can be seen by players). Counts and the table's own words only: the words are
the ones already kept on the campaign's connections, and nothing said at the table is shown. Logs
carry counts, never the words. A Yes moves the connections in one change and offers an Undo.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, ClassVar

import discord

from dmbot.dm_questions import KINDS, Ask
from dmbot.memory import kind_offers
from dmbot.memory.models import DM, MemoryRuleError, TooLateToUndo
from dmbot.ui import walk_cards
from dmbot.ui.dmbot_commands import _bot, _tell
from dmbot.walk import Walk

log = logging.getLogger(__name__)

ONLY_DMS = "Only a DM of this campaign can answer this."
NOT_AVAILABLE = "DMbot can't do that right now. Try again in a minute."
FAILED = "Couldn't do that. Try again in a minute."
GONE = "That question is out of date. DMbot asks again after a later session if it still applies."
YES, LATER, NEVER = "Yes", "Not now", "Never for these words"


def key_of(words_key: str) -> str:
    """A short fingerprint of the words, so a button's id never carries them (an id holds 100
    characters, and the words are the DM's to read, not to be repeated everywhere)."""
    return hashlib.sha1(words_key.encode()).hexdigest()[:12]


def question(offer: kind_offers.Offer) -> str:
    """The question, as Supervisor approved it: the words, how often, the three answers."""
    return (
        f"🔗 The table keeps saying “{offer.words}” ({offer.count} times). "
        "Make it a kind of connection of its own?\n"
        "_Yes_ moves those connections to it (one Undo). _Not now_ asks again after 3 more. "
        "_Never_ stops asking about these words."
    )


def ask_for(guild_id: int, campaign_id: str, offer: kind_offers.Offer) -> Ask:
    """The question for the DM's private message after a session."""
    text = question(offer)
    line = f"the words “{offer.words}” can be a kind of connection"
    # Where the campaign's people may read it (the DM Bot channel, when no DM can be reached
    # privately): no words from the table, only that there is something to look at.
    public = (
        "🔗 A phrase the table keeps saying could become a kind of connection. "
        "Press the button to look at it."
    )
    return Ask(
        KINDS,
        text,
        line,
        lambda: OpenButton(guild_id, campaign_id),
        lambda: OpenButton(guild_id, campaign_id),
        public,
        count=1,  # (one offer is put at a time; the walk counts what waits when it is pressed)
    )


class OpenButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:kindask:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32})",
):
    """In the collected question: shows the question with its three answers. Works after a
    restart (the server and campaign are in the id)."""

    def __init__(self, guild_id: int, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Make a kind",
                emoji="🔗",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:kindask:{guild_id}:{campaign_id}",
            )
        )
        self.guild_id, self.campaign_id = guild_id, campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> OpenButton:
        return cls(int(match["guild"]), match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await open_question(interaction, self.guild_id, self.campaign_id)


class AnswerButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:kindans:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32}):"
    r"(?P<answer>yes|later|never):(?P<words>[0-9a-f]{12})",
):
    """Yes, Not now or Never for the words with this fingerprint."""

    STYLE: ClassVar[dict[str, discord.ButtonStyle]] = {
        "yes": discord.ButtonStyle.success,
        "later": discord.ButtonStyle.secondary,
        "never": discord.ButtonStyle.secondary,
    }
    LABEL: ClassVar[dict[str, str]] = {"yes": YES, "later": LATER, "never": NEVER}

    def __init__(self, guild_id: int, campaign_id: str, answer: str, words: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=self.LABEL[answer],
                style=self.STYLE[answer],
                custom_id=f"dmbot:kindans:{guild_id}:{campaign_id}:{answer}:{words}",
            )
        )
        self.guild_id, self.campaign_id, self.answer, self.words = (
            guild_id,
            campaign_id,
            answer,
            words,
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> AnswerButton:
        return cls(int(match["guild"]), match["campaign"], match["answer"], match["words"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await answer(interaction, self.guild_id, self.campaign_id, self.answer, self.words)


class UndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:kindundo:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})",
):
    """Takes the move back (the change log's one Undo)."""

    def __init__(self, guild_id: int, campaign_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:kindundo:{guild_id}:{campaign_id}:{batch}",
            )
        )
        self.guild_id, self.campaign_id, self.batch = guild_id, campaign_id, batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoButton:
        return cls(int(match["guild"]), match["campaign"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await undo(interaction, self.guild_id, self.campaign_id, self.batch)


async def _is_dm(interaction: discord.Interaction, guild_id: int, campaign_id: str) -> bool:
    """Whether the presser is one of the campaign's DMs; tells them why not when they are not.
    The interaction is already answered."""
    bot = _bot(interaction)
    campaign = await bot.campaigns.get(guild_id, campaign_id)
    if campaign is None or interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, ONLY_DMS)
        return False
    if bot.memory is None:
        await _tell(interaction, NOT_AVAILABLE)
        return False
    return True


async def open_question(interaction: discord.Interaction, guild_id: int, campaign_id: str) -> None:
    await interaction.response.defer(ephemeral=True)
    if not await _is_dm(interaction, guild_id, campaign_id):
        return
    memory = _bot(interaction).memory
    assert memory is not None
    offer = await memory.kind_offer(guild_id, campaign_id)
    if offer is None:
        await _tell(interaction, GONE)
        return
    view = discord.ui.View(timeout=None)
    for which in ("yes", "later", "never"):
        view.add_item(AnswerButton(guild_id, campaign_id, which, key_of(offer.words_key)))
    await _tell(interaction, question(offer), view=view)


async def apply_answer(
    memory: Any, guild_id: int, campaign_id: str, offer: kind_offers.Offer, which: str
) -> tuple[str, UndoButton | None]:
    """What Yes, Not now or Never does, and the words to show for it (and an Undo for a Yes).
    The one place the answer is made: the question's own buttons and the "Check everything" walk
    both come here, so an answer in either is the same answer. Raises `MemoryRuleError`."""
    if which == "yes":
        made = await memory.make_kind_of_words(guild_id, campaign_id, offer.words_key, source=DM)
        log.info("Kind offer: made a kind, %d connection(s) moved", offer.count)
        undo = UndoButton(guild_id, campaign_id, made.batch) if made.batch is not None else None
        return (
            f"🔗 Done. {offer.count} connections now read “{offer.words}” as a kind of their own.",
            undo,
        )
    state = kind_offers.NEVER if which == "never" else kind_offers.LATER
    await memory.answer_kind_offer(guild_id, campaign_id, offer.words_key, state, seen=offer.count)
    log.info("Kind offer: answered %s", state)
    return (
        "🔗 Okay, DMbot won't ask about these words again."
        if which == "never"
        else "🔗 Okay. DMbot will ask again once the table has said them 3 more times.",
        None,
    )


async def answer(
    interaction: discord.Interaction, guild_id: int, campaign_id: str, which: str, words: str
) -> None:
    """The DM's answer. The offer is found again from the fingerprint, so a press on words that
    changed since the question says so instead of answering for other words."""
    await interaction.response.defer(ephemeral=True)
    if not await _is_dm(interaction, guild_id, campaign_id):
        return
    memory = _bot(interaction).memory
    assert memory is not None
    offer = next(
        (
            o
            for o in await memory.kind_offer_list(guild_id, campaign_id)
            if key_of(o.words_key) == words
        ),
        None,
    )
    if offer is None:
        await _tell(interaction, GONE)
        return
    try:
        text, undo = await apply_answer(memory, guild_id, campaign_id, offer, which)
        view: discord.ui.View | None = None
        if undo is not None:  # (no empty view for Not now and Never: they never had one)
            view = discord.ui.View(timeout=None)
            view.add_item(undo)
        await _tell(interaction, text, view=view)
    except MemoryRuleError as exc:
        await _tell(interaction, f"Couldn't do that. {exc}")
    except Exception:  # (the database was unreachable, say): the reply must not hang
        log.exception("Kind offer: the answer could not be saved")
        await _tell(interaction, FAILED)


async def undo(
    interaction: discord.Interaction, guild_id: int, campaign_id: str, batch: int
) -> None:
    await interaction.response.defer(ephemeral=True)
    if not await _is_dm(interaction, guild_id, campaign_id):
        return
    memory = _bot(interaction).memory
    assert memory is not None
    try:
        await memory.undo(guild_id, campaign_id, batch, source=DM)
    except TooLateToUndo as exc:  # (the store's own plain words)
        await _tell(interaction, f"Couldn't undo that. {exc}")
        return
    except Exception:
        log.exception("Kind offer: the undo failed")
        await _tell(interaction, FAILED)
        return
    await _tell(interaction, "↩️ Undone. The connections read as they did before.")


WALK_FINISHED = "✅ Kinds of connection: checked."  # (the line a "Check everything" walk leaves)


def walk_kind(memory: Any, guild_id: int, campaign_id: str) -> walk_cards.Kind:
    """The kinds-of-connection offers as a part of "✅ Check everything" (#4192): the same
    question, the same three answers and Undo, one offer at a time."""

    def choice(label: str, style: discord.ButtonStyle, which: str) -> walk_cards.Choice:
        async def do(offer: kind_offers.Offer) -> walk_cards.Answer:
            text, undo = await apply_answer(memory, guild_id, campaign_id, offer, which)
            return walk_cards.Answer(text, undo)

        return walk_cards.Choice(label, style, do)

    return walk_cards.Kind(
        waiting=lambda: memory.kind_offer_list(guild_id, campaign_id),
        key=lambda offer: offer.words_key,
        text=lambda offer, left: question(offer),
        choices=lambda: [
            choice(YES, discord.ButtonStyle.success, "yes"),
            choice(LATER, discord.ButtonStyle.secondary, "later"),
            choice(NEVER, discord.ButtonStyle.secondary, "never"),
        ],
        finished=WALK_FINISHED,
    )


async def start_walk_part(
    interaction: discord.Interaction, guild_id: int, campaign_id: str, walk: Walk
) -> None:
    """The kinds part of a walk. The presser was checked as a DM when the walk began."""
    memory = _bot(interaction).memory
    if memory is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    await walk_cards.start(interaction, walk_kind(memory, guild_id, campaign_id), walk)
