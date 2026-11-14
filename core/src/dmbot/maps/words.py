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
    "it. Only DMs can open the saved copy. You can take it back from the picture's page."
)
PLAYER_YES_PRIVATE = (
    "Kept. Only the DMs can see it. A DM has to say it can be used before DMbot reads it. You can "
    "take it back from the picture's page."
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
FAILED = "That didn't go through. Try the button again, or open the picture's page."
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
