"""The relationship definitions a shared house-rules set carries (#1456), on the shared-rules
screens: the publisher's tick list (what leaves is shown in words first), the line a follower is
shown before using a set, and the one-line notes after Follow and Unfollow. The memory work is in
`dmbot.memory.set_definitions` and `dmbot.rules.set_sync`; this file is only words and one menu."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.memory import set_definitions as sd
from dmbot.ui.dmbot_commands import _Button, _Menu, _Select

MENU_MAX = 25  # a Discord menu holds this many choices
SHOWN = 4  # labels quoted in a line


def _md(text: str) -> str:
    return str(discord.utils.escape_markdown(text))


def _plural(n: int) -> str:
    return f"{n} relationship" + ("" if n == 1 else "s")


def labels(items: Sequence[str]) -> str:
    shown = [f"**{_md(i)}**" for i in items[:SHOWN]]
    more = len(items) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def offer_line(content: dict[str, Any]) -> str:
    """Shown before a table uses a set: what relationships come with it, if any."""
    found = [d["label"] for d in sd.valid_definitions(content.get("definitions"))]
    if not found:
        return ""
    return (
        f"It also brings {_plural(len(found))} ({labels(found)}). "
        "You can't change them while you use the set. If you stop, they go, unless a fact uses "
        "one.\n"
    )


def followed_note(done: sd.Synced | None) -> str:
    """After Follow: what was added, and what stayed the table's own."""
    if done is None:
        return ""
    lines = []
    if done.added:
        lines.append(f"Added {_plural(len(done.added))}: {labels(done.added)}.")
    if done.kept_own:
        lines.append(f"You already have {labels(done.kept_own)}, so DMbot kept yours.")
    if done.refused:
        rest = " The rest were added." if done.added else ""
        lines.append(f"DMbot couldn't add {labels(done.refused)}.{rest}")
    return ("\n" + "\n".join(lines)) if lines else ""


def stopped_note(done: sd.Synced | None) -> str:
    """After Unfollow: what went, and what stays because facts use it."""
    if done is None:
        return ""
    lines = []
    if done.removed:
        lines.append(f"Its {_plural(len(done.removed))} went too: {labels(done.removed)}.")
    if done.deprecated:
        lines.append(
            f"{labels(done.deprecated)} stays for the old facts that use it. "
            "You can't use it for new facts."
        )
    return ("\n" + "\n".join(lines)) if lines else ""


def tick_options(
    definitions: Sequence[dict[str, Any]], ticked: set[str]
) -> list[discord.SelectOption]:
    return [
        discord.SelectOption(
            label=d["label"][:100],
            description=" ".join(d["description"].split())[:100],
            value=d["key"],
            default=d["key"] in ticked,
        )
        for d in definitions[:MENU_MAX]
    ]


class ShareChoose(_Menu):
    """Before Share: which of the campaign's own relationships travel with the rules. Each is
    shown with its label and its description, so the DM sees what leaves. All start ticked."""

    def __init__(
        self,
        offered: Sequence[dict[str, Any]],
        done: Callable[[discord.Interaction, list[dict[str, Any]]], Any],
    ) -> None:
        super().__init__()
        self.offered = list(offered)[:MENU_MAX]
        self.ticked = {d["key"] for d in self.offered}
        self._done = done
        self.add_item(
            _Select(
                self._picked,
                placeholder="Relationships to share",
                min_values=0,
                max_values=len(self.offered),
                options=tick_options(self.offered, self.ticked),
                row=0,
            )
        )
        self.add_item(
            _Button(
                self._share, label="Share with ticked", style=discord.ButtonStyle.primary, row=1
            )
        )
        self.add_item(_Button(self._none, label="Rules only", row=1))

    async def _picked(self, interaction: discord.Interaction) -> None:
        select = next(c for c in self.children if isinstance(c, discord.ui.Select))
        self.ticked = set(select.values)
        await interaction.response.defer()

    def chosen(self) -> list[dict[str, Any]]:
        return [d for d in self.offered if d["key"] in self.ticked]

    async def _share(self, interaction: discord.Interaction) -> None:
        self.stop()
        await self._done(interaction, self.chosen())

    async def _none(self, interaction: discord.Interaction) -> None:
        self.stop()
        await self._done(interaction, [])


CHOOSE_TEXT = (
    "Pick the relationships to share with your rules. Other tables get each one's name and what "
    "it means. They don't get your facts, names or notes, and they can't change them.\n"
    "**Share with ticked** sends what is ticked. **Rules only** sends none."
)


TEXT_MAX = 1800  # under Discord's 2,000


def tick_list_text(offered: Sequence[dict[str, Any]]) -> str:
    """The question and, under it, what each relationship says (as far as the message holds)."""
    text = CHOOSE_TEXT
    shown = 0
    for d in offered[:MENU_MAX]:
        line = f"\n• {_md(sd.tick_line(d))}"
        if len(text) + len(line) > TEXT_MAX:
            break
        text += line
        shown += 1
    if shown < len(offered[:MENU_MAX]):
        text += f"\n…and {len(offered[:MENU_MAX]) - shown} more in the menu."
    return text


async def offered_for(bot: Any, campaign: Campaign) -> list[dict[str, Any]]:
    """The campaign's own relationships that may be shared, or none if the memory can't be read."""
    memory = getattr(bot, "memory", None)
    if memory is None:
        return []
    try:
        return await sd.own_definitions(memory, campaign.guild_id, campaign.id)
    except Exception:
        return []
