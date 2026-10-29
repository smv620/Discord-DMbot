"""`/dmbot migrate`: move a paid account to a new Discord account (#1230).

Two buttons. **Get a move code** (on the old account) emails a code to the address on file and
says only that it did; **Import from another Discord account** (on the new account) asks for
the code, shows what will move and asks to confirm. The rules are in `dmbot.account_move`; the
move is one database transaction. Recording consent does not move: the new account says yes
for itself the first time it joins a table. Nothing here is shown to anyone else.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

import discord

from dmbot import account_move
from dmbot.account_move import IssuedCode, MoveError, Preview
from dmbot.accounts import payment_email
from dmbot.ui.dmbot_commands import (
    _answer_first,
    _bot,
    _Button,
    _Menu,
    _send,
    _tell,
    dmbot_group,
)

log = logging.getLogger(__name__)

INTRO = (
    "**Move to a new Discord account**\n"
    "Moves your plan, its hours and the campaigns you own. Your card moves too, and your old "
    "account is left empty. You can move once every 30 days.\n"
    "Recording agreements don't move. Your new account says yes again at its first table.\n"
    "1. Old account: tap **Get a move code**. We email it to the address you pay with.\n"
    "2. New account: run `/dmbot migrate`, tap **Import my account**, and type the code.\n"
    "Want to give campaigns to someone else? Use **Hand over** in ⚙️ Settings → 🗂️ "
    "Campaign management. It doesn't "
    "move your plan."
)
EMAILED = (
    "We've emailed a code to the address you pay with. It works once, for 30 minutes. "
    "Check your spam too."
)
NO_EMAIL = (
    "We don't have the email you pay with yet. It's saved when a payment goes through. Try "
    "again later, or tell us on the website's hello page."
)
NOT_READY = (
    "Something went wrong and nothing moved. Try again in a minute. If it keeps happening, "
    "tell us on the website."
)
EMAIL_FAILED = "The code couldn't be emailed just now. Try again in a few minutes."
GET_LABEL = "📧 Get a move code"
IMPORT_LABEL = "📥 Import my account"
CONFIRM_LABEL = "Yes, move it here"
CANCEL_LABEL = "Cancel"


CAMPAIGNS_SHOWN = 8


def confirm_text(preview: Preview, old_user_id: int) -> str:
    from dmbot import plans

    plan = next((p for p in plans.load().by_id.values() if p.id == preview.plan), None)
    plan_name = plan.name if plan is not None else preview.plan
    shown = preview.campaigns[:CAMPAIGNS_SHOWN]
    names = "\n".join(f"• {discord.utils.escape_markdown(n)}" for n in shown) or "• (none)"
    more = len(preview.campaigns) - len(shown)
    if more > 0:
        names += f"\n…and {more} more"
    return (
        "Move this to your account?\n"
        f"From: <@{old_user_id}>\n"
        f"Plan: **{discord.utils.escape_markdown(plan_name)}**, with its hours. The card that "
        "pays for it moves too.\n"
        f"Campaigns:\n{names}\n"
        "The old account is left empty. You can't move again for 30 days. Recording "
        "agreements don't move."
    )


@dmbot_group.command(
    name="migrate", description="Move your plan and campaigns to a new Discord account"
)
async def dmbot_migrate(interaction: discord.Interaction) -> None:
    await _send(interaction, INTRO, MigrateMenu())


class MigrateMenu(_Menu):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(_Button(self._get, label=GET_LABEL, style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._import, label=IMPORT_LABEL))

    async def _get(self, interaction: discord.Interaction) -> None:
        await get_code(interaction)

    async def _import(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CodeForm())


async def _payment_email(interaction: discord.Interaction) -> str | None:
    """Only the address the payment company has: a code goes nowhere else."""
    bot = _bot(interaction)
    if bot.meter is None:
        return None
    return await payment_email(bot.meter.db, interaction.user.id)


async def get_code(interaction: discord.Interaction) -> None:
    """Make a code and email it. The screen never shows the code or the address."""
    await _answer_first(interaction)
    bot = _bot(interaction)
    if bot.account_moves is None or bot.mailer is None:
        await _tell(interaction, NOT_READY)
        return
    try:
        why = await bot.account_moves.can_move(interaction.user.id)
        if why is not None:
            await _tell(interaction, why)
            return
        email = await _payment_email(interaction)
        if not email:
            await _tell(interaction, NO_EMAIL)
            return
        code = await bot.account_moves.make_code(interaction.user.id)
        result = await _send_code(bot, interaction.user.id, email, code)
    except MoveError as exc:
        await _tell(interaction, str(exc))
        return
    except Exception:
        log.exception("Couldn't make a move code")
        await _tell(interaction, NOT_READY)
        return
    # Each ask has its own email kind, so "already_sent" can't be right: nobody got this code.
    if result != "sent":
        await _give_back(bot, interaction.user.id, code)
        await _tell(interaction, EMAIL_FAILED)
        return
    await _tell(interaction, EMAILED)


async def _give_back(bot: Any, user_id: int, code: IssuedCode) -> None:
    """The email didn't go: no code should be waiting that nobody has seen, and the ask is given
    back. A failure to do even that is logged, never shown with the code."""
    try:
        await bot.account_moves.cancel_code(user_id, code)
    except Exception:
        log.exception("Couldn't give back a move code")


async def _send_code(bot: Any, user_id: int, email: str, code: IssuedCode) -> str:
    """Email the code. A mailer that raises is a failed send (the caller gives the code back),
    not a reason to leave the code live."""
    try:
        return str(
            await bot.mailer.send(
                user_id=user_id,
                # One email for each code, so a new code is never hidden. The kind is stored and
                # logged, so it holds a random nonce and nothing derived from the code.
                kind=f"move-code:{secrets.token_hex(6)}",
                to=email,
                subject="Your DMbot move code",
                body=(
                    f"Your DMbot move code: {code[:4]}-{code[4:]}\n\n"
                    "It works once and lasts 30 minutes.\n"
                    'Type it on your NEW Discord account: run /dmbot migrate, tap "Import my '
                    'account", and enter it.\n'
                    "The code moves your plan, its hours and your campaigns. Never give it to "
                    "anyone else.\n"
                    "Didn't ask for this? Ignore this email. Nothing changes."
                ),
            )
        )
    except Exception:
        log.exception("Couldn't send a move code")  # (what was raised, never the code)
        return "failed"


class CodeForm(discord.ui.Modal, title="Import my account"):
    code: discord.ui.TextInput[CodeForm] = discord.ui.TextInput(
        label="The move code from your email",  # 45 characters at most
        placeholder="For example: ABCD-2345",
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await look_up(interaction, self.code.value)


async def look_up(interaction: discord.Interaction, code: str) -> None:
    """Check the code and show what would move, before anything does."""
    await _answer_first(interaction)
    bot = _bot(interaction)
    if bot.account_moves is None:
        await _tell(interaction, NOT_READY)
        return
    try:
        old = await bot.account_moves.find(interaction.user.id, code)
        preview = await bot.account_moves.preview(old)
    except MoveError as exc:
        await _tell(interaction, str(exc))
        return
    except Exception:
        log.exception("Couldn't check a move code")
        await _tell(interaction, NOT_READY)
        return
    await _send(
        interaction, confirm_text(preview, old), ConfirmMove(old, interaction.user.id, code)
    )


class ConfirmMove(_Menu):
    def __init__(self, old_user_id: int, new_user_id: int, code: str) -> None:
        super().__init__()
        self.old, self.new, self.code = old_user_id, new_user_id, code
        self.add_item(_Button(self._yes, label=CONFIRM_LABEL, style=discord.ButtonStyle.danger))
        self.add_item(_Button(self._no, label=CANCEL_LABEL))

    async def _mine(self, interaction: discord.Interaction) -> bool:
        """Only the person who asked can press: the menu is theirs alone."""
        if interaction.user.id == self.new:
            return True
        await _tell(interaction, "This one isn't yours.")
        return False

    async def _yes(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction)
        if not await self._mine(interaction):
            return
        bot = _bot(interaction)
        if bot.account_moves is None:
            await _tell(interaction, NOT_READY)
            return
        self.stop()  # one press only
        try:
            await bot.account_moves.move(self.old, interaction.user.id, self.code)
        except MoveError as exc:
            await _tell(interaction, f"{exc} Start again with `/dmbot migrate`.")
            return
        except Exception:
            log.exception("An account move failed")
            await _tell(interaction, f"{NOT_READY} Start again with `/dmbot migrate`.")
            return
        await _tell(
            interaction,
            "✅ Moved. Your plan, hours and campaigns are on this account now. You'll be asked "
            "to agree to recording again the first time you join a table.",
        )
        await bot.after_account_move(self.old, interaction.user.id)

    async def _no(self, interaction: discord.Interaction) -> None:
        if not await self._mine(interaction):
            return
        self.stop()
        await _tell(interaction, "Okay, nothing moved.")


__all__ = ["account_move", "dmbot_migrate"]
