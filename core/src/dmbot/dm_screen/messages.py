"""Everything the DM screen feature says to people. Plain words (CLAUDE.md, principle 0)."""

from __future__ import annotations

from dmbot.dm_screen.rules import Exposure

PEEK_LABEL = "Peek behind the DM screen"
HIDE_LABEL = "Hide the DM screen from me"
YES_LABEL = "Yes, show me"
CANCEL_LABEL = "Cancel"
HELP_CARD_TITLE = "🛡️ **DM screen for "  # also used to find old cards to replace

# Buttons on the help card that let the DM change who can see the screen.
VISIBILITY_BUTTONS = {
    "private": ("🔒", "Only the DM"),
    "peek": ("👀", "Players can peek"),
    "open": ("📖", "Everyone in the server"),
}

WHO_CAN_SEE = {
    "private": "Only the DM.",
    "peek": "The DM. Players can choose to peek, after a spoiler warning.",
    "open": "Everyone in the server. They can read but not post.",
}

CAMPAIGN_GONE = "I can't find that campaign anymore. Ask your DM to set it up again."
SCREEN_GONE = "This campaign doesn't have a DM screen right now. Ask your DM to set it up."
PEEK_WARNING = (
    "**Peek behind the DM screen?**\n"
    "You'll see the DM's private notes, old ones too. They may spoil surprises.\n"
    "Your DM will see that you peeked. You can hide it again any time."
)
PEEK_CANCELLED = "OK, the DM screen stays hidden."
PEEK_EXPIRED = (
    "This expired. Press 👀 **Peek behind the DM screen** again if you still want to look."
)
PEEK_PRIVATE = "Your DM keeps this DM screen private."
ALREADY_PEEKING = "You're peeking at the DM screen right now. Want to hide it again?"
HIDE_DONE = "Done. The DM screen is hidden from you again."
HIDE_NOT_PEEKING = "You weren't peeking, so there's nothing to hide."
HIDE_IS_DM = "You're a DM of this campaign, so the DM screen stays visible to you."
HIDE_OPEN = "The DM screen is open to everyone, so it can't be hidden just for you."
PLAYER_FAILED = "Sorry, DMbot couldn't change the DM screen for you. Let your DM know."
NOT_THE_DM = "Only this campaign's DM (or a server manager) can change who can see the DM screen."
SOMETHING_WENT_WRONG = "Sorry, something went wrong changing the DM screen. Please try again."


def lost_access(channel_id: int) -> str:
    return (
        f"I can't see this campaign's DM screen (<#{channel_id}>) anymore. Give DMbot "
        "**View Channel** there again (Edit Channel → Permissions), or delete that channel "
        "and I'll make a new one."
    )


FORBIDDEN_HERE = (
    "Discord blocked me from setting up the DM screen here. This channel's category may "
    "not let DMbot manage channels. Try running the command from a channel outside that "
    "category, or ask a server admin to check the category's permissions for DMbot."
)
CANT_PIN = (
    "📌 I couldn't pin the 🛡️ DM screen card, so it may scroll out of sight. "
    "Ask a server admin to turn on **Pin Messages** for DMbot "
    "(Server Settings → Roles → DMbot). I'll pin it next time you run `/dmbot start`."
)


def needs_permissions(missing: list[str]) -> str:
    names = " and ".join(f"**{name}**" for name in missing)
    return (
        f"I need {names} to set up the DM screen. Ask a server admin to turn "
        f"{'it' if len(missing) == 1 else 'them'} on for DMbot "
        "(Server Settings → Roles → DMbot), then try again."
    )


def discord_error(reason: str) -> str:
    return (
        f"Discord didn't let me set up the DM screen ({reason}). Try again in a minute. "
        "If it keeps happening, check the server or category isn't full of channels."
    )


def topic(campaign_name: str, visibility: str) -> str:
    who = {
        "private": "Only the DM can see this.",
        "peek": "Hidden from players unless they choose to peek. It may spoil the game.",
        "open": "Everyone in this server can see this. It may spoil surprises.",
    }[visibility]
    return f"DM screen for {campaign_name}. {who}"


def help_card(campaign_name: str, visibility: str) -> str:
    lines = [
        f"{HELP_CARD_TITLE}{campaign_name}**",
        "DMbot writes notes for the DM here. It never makes rulings or story. "
        "The DM decides everything.",
        f"**Who can see this:** {WHO_CAN_SEE[visibility]}",
        "**To start listening:** run `/dmbot start`.",
    ]
    if visibility != "open":
        lines.append("Server owners and admins can always see every channel.")
    lines.append("**DM:** press a button below to change who can see this.")
    if visibility == "peek":
        lines.append(f"**Peeking?** Press 🙈 **{HIDE_LABEL}** below to stop.")
    return "\n".join(lines)


def visibility_changed(visibility: str, *, was: str) -> str:
    text = f"Done. **Who can see the DM screen:** {WHO_CAN_SEE[visibility]}"
    if was == "peek" and visibility == "private":
        text += " Players who were peeking can't see it anymore."
    return text


def peek_invite() -> str:
    return (
        "👀 **Curious what the DM sees?** You can peek behind the DM screen. "
        "It may spoil surprises."
    )


def peek_done(channel_mention: str) -> str:
    return (
        f"You can now see {channel_mention}. To hide it again, press **{PEEK_LABEL}** "
        f"again in the voice chat, or **{HIDE_LABEL}** below."
    )


def peek_is_dm(channel_mention: str) -> str:
    return f"You're the DM, so you can already see {channel_mention}."


def peek_open(channel_mention: str) -> str:
    return f"The DM screen is open to everyone: {channel_mention}."


def peeked_note(name: str) -> str:
    return f"👀 {name} peeked behind the DM screen."


def unpeeked_note(name: str) -> str:
    return f"🙈 {name} stopped peeking."


def exposure_warning(exposure: Exposure) -> str | None:
    """A warning for the DM, or None if nobody unexpected can see the screen."""
    if not exposure:
        return None
    who: list[str] = []
    if exposure.everyone:
        who.append("everyone in the server")
    who += [f"<@&{role_id}>" for role_id in exposure.roles]
    who += [f"<@{user_id}>" for user_id in exposure.members]
    return (
        f"⚠️ These can see this DM screen: {', '.join(who)}. To hide it from them: "
        "Edit Channel → Permissions → pick each one → set **View Channel** to ✗."
    )
