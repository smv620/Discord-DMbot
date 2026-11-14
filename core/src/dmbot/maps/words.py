"""The words of the right-to-use question for a map (MapBot slice 3b, #4344; the ux-critic's text,
Supervisor's ruling on #4344). One place, so Discord and the website (WebDev's Maps page, #4347) say
the same thing: change a string here and the test says where else it is used. Pure: no Discord, no
database. Plain words only: the person never reads "audience", "row" or "store"."""

from __future__ import annotations

# Where a map came from decides what the person is told about who can see it (design 3.1, 8.1):
# one posted in the table's chat was seen by the table already; any other is for the DMs.
QUESTION = "Is this a map you're allowed to use and save in this campaign?"
_CANT_CHECK = "DMbot can't check who made a picture or what you've agreed with them,"
NO_CHECK = f"{_CANT_CHECK} so it goes by your answer."
SEEN_BY_TABLE = "Everyone at the table has seen it. DMs can use it in this campaign."
SEEN_BY_DMS = "Only the DMs of this campaign can see it."
YES_LABEL = "Yes, keep it as a map"
NO_LABEL = "No, don't keep it"

DM_YES_CHAT = "Kept as a map. DMbot can now use it. Everyone at the table saw it in chat."
DM_YES_PRIVATE = "Kept as a map. Only you and the other DMs can see it."
PLAYER_YES_CHAT = (
    "Kept. A DM has to say it can be used before DMbot reads it. Everyone at the table already saw "
    "it. Only DMs can open the saved copy. You can take it back with the button below."
)
PLAYER_YES_PRIVATE = (
    "Kept. Only the DMs can see it. A DM has to say it can be used before DMbot reads it. You can "
    "take it back with the button below."
)
NO_CHAT = (
    "OK. DMbot didn't keep it. The picture is still in the chat, "
    "and its line stays in the transcript."
)
NO_PRIVATE = "OK. DMbot didn't keep it."

GONE = "That picture isn't kept any more."
NO_CONSENT = "You've stopped DMbot recording pictures, so it isn't keeping this one."
ANSWERED = "That's already answered."
NOT_YOURS = "Only the person who posted it can answer this."
FAILED = "That didn't go through. Try the button again."
FULL = "DMbot has no room to keep another map. Ask your DM to free some room."


def dms_could_not_ask(player: str) -> str:
    """What the DMs are told when the person's private messages are closed."""
    return f"DMbot couldn't ask {player}. The map is not being used."


def question(from_chat: bool) -> str:
    """The whole question, with the line on who can see the map."""
    seen = SEEN_BY_TABLE if from_chat else SEEN_BY_DMS
    return f"**{QUESTION}**\n{NO_CHECK}\n{seen}"


def said_yes(is_dm: bool, from_chat: bool) -> str:
    """What the person is told after a yes: a DM's yes lets DMbot use the map, a player's keeps it
    and waits for a DM."""
    if is_dm:
        return DM_YES_CHAT if from_chat else DM_YES_PRIVATE
    return PLAYER_YES_CHAT if from_chat else PLAYER_YES_PRIVATE


def said_no(from_chat: bool) -> str:
    return NO_CHAT if from_chat else NO_PRIVATE


# The DM's side (slice 3b-2a): a map a player kept waits for a DM's yes.
QUESTIONS_LABEL = "Maps waiting for your yes"
USE_YES_LABEL = "Yes, use it"
USE_NO_LABEL = "No, don't use it"
REMOVE_LABEL = "Remove it"
NOTHING_WAITING = "No maps are waiting for you."
DM_ONLY = "Only the DMs of this campaign can answer this."


def dm_title(player: str) -> str:
    return f"A map from {player}: use it?"


def dm_body(player: str) -> str:
    """The question to a DM: what each button does, and that the claim is the player's."""
    return (
        "Use this map? **Yes** lets DMbot read it. **No** keeps the picture but DMbot never reads "
        "it. **Remove** takes it off your maps; the picture stays.\n"
        f"{player} says they may use it."
    )


def waiting_line(count: int) -> str:
    if count == 1:
        return "A map is waiting for your yes."
    return f"{count} maps are waiting for your yes."


def answered_by(dm: str, yes: bool) -> str:
    """The second DM to press: the first answer stands."""
    return f"{dm} already answered: {'yes' if yes else 'no'}."


def done_yes() -> str:
    return "OK. DMbot will use this map."


def done_no() -> str:
    return "OK. DMbot keeps the picture and never reads it as a map."


def done_removed() -> str:
    return "Removed from your maps. The picture is still kept."


UNDO_LABEL = "Undo"
UNDONE = "↩️ Undone."
UNDO_FAILED = "Couldn't undo: that map was changed again since."


# The poster's side after a yes (slice 3b-2b): taking it back.
TAKE_BACK_LABEL = "Take it back"
TOOK_BACK = "OK. It's no longer a map. The picture is still kept."
TAKE_BACK_UNDO_FAILED = "Couldn't undo: that was changed again since."
NOT_YOUR_CHANGE = "Only the person who made this change can undo it."
_NOT_USING = "Not answered yet, so DMbot isn't using this map."
REMINDER = _NOT_USING + " You can still answer the question above."


# Sending a map to DMbot yourself (#4372): `/dmbot map`, or a picture in a private chat.
WHICH_CAMPAIGN = "Which campaign is this map for?"
NO_CAMPAIGN_HERE = "You're not in a campaign on this server yet. Ask your DM to add you."
NO_CAMPAIGN_PRIVATE = "DMbot can't find a campaign for you. Ask your DM to add you."
BUSY = "DMbot is busy. Try again in a few minutes."
EXPIRED = "DMbot let that picture go. Send it again."
WRONG_TYPE = "DMbot can only keep pictures: PNG, JPG, WebP or GIF."
SAVING = "Saving your map…"
FIRST_25 = "Showing the first 25."


def too_big(mb: int) -> str:
    return f"That picture is too big. Send one under {mb} MB."
