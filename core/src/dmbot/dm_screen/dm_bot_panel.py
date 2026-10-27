"""Who gets which button in the DM Bot channel (#1666; docs/PLAN.md, "The DM Bot channel").

Pure, so it is tested without Discord: `role_of` works out a person's role at one campaign from
facts the caller already read, and `buttons_for` says which buttons that role gets. The same
function decides what is drawn and what is allowed on a press (the server checks again, with
the campaign as it is now), so a button hidden from a role can never be used by it either.

Roles, from the strongest to the weakest:
- **game master**: in the campaign's DM list. Every button.
- **player**: tied to *this* campaign (a confirmed character in it, or recorded in one of its
  sessions). The personal buttons, plus the campaign ones that are about the player.
- **member**: consented in this server, but not tied to this campaign. The personal buttons.
- **guest**: anyone else in the server. Help, looking up a rule and reading the transcripts,
  which are open to the whole server anyway (#1691).

A server manager who is not a game master is a guest here: the card never offers them Settings.
(The ⚙️ Settings button itself still lets a manager in, as it does on the DM screen today; the
whole-server decision on that is Supervisor's, slice 3.)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class Role(StrEnum):
    GAME_MASTER = "game_master"
    PLAYER = "player"
    MEMBER = "member"
    GUEST = "guest"


class Button(StrEnum):
    """What a panel button does. The value is the action in its custom id."""

    OPEN = "open"  # on the shared panel: opens the person's own buttons, privately
    HELP = "help"
    RULE = "rule"  # 📖 Look up a rule: the form and private answer of /dmbot rule (#1691)
    TRANSCRIPTS = "transcripts"  # 📜 Transcripts: a link to the website's page, no callback
    MY_MENU = "my_menu"  # 🗂️ Menu: Stop recording me, Delete my transcripts (about the person)
    MY_CHARACTER = "my_character"  # the player's character link in this campaign
    SETTINGS = "settings"  # the campaign's Settings card, game masters only
    NAMES = "names"  # 📇 Names: the names home card, game masters only (#1986)
    BACKSTORY = (
        "backstory"  # 📜 Upload campaign lore: names and connections from the DM's own story
    )


# Each role's buttons, in the order they are drawn. A role never gets more than the one below it
# plus its own, which `test_each_role_has_what_the_weaker_ones_have` checks.
_EVERYONE = (Button.HELP, Button.RULE, Button.TRANSCRIPTS)  # open to the whole server (#1691)
_BUTTONS: dict[Role, tuple[Button, ...]] = {
    Role.GUEST: _EVERYONE,
    Role.MEMBER: (*_EVERYONE, Button.MY_MENU),
    Role.PLAYER: (*_EVERYONE, Button.MY_MENU, Button.MY_CHARACTER),
    Role.GAME_MASTER: (
        *_EVERYONE,
        Button.MY_MENU,
        Button.MY_CHARACTER,
        Button.SETTINGS,
        Button.NAMES,
        Button.BACKSTORY,
    ),
}

# Only the shared panel's own buttons are labelled here; the card's buttons are DMbot's existing
# menus and keep the labels they have everywhere else (🗂️ Menu, 📜 My character sheet, ⚙️ Settings).
LABELS: dict[Button, str] = {
    Button.OPEN: "👤 DMbot Main Menu",
    Button.HELP: "❓ Help",
    Button.RULE: "📖 Look up a rule",
    Button.TRANSCRIPTS: "📜 Transcripts",
}

# The website's Transcripts page, used when the bot isn't given its own address (WEB_SITE_URL).
TRANSCRIPTS_URL = "https://getdmbot.com/account/transcripts"


def transcripts_url(site_url: str = "") -> str:
    """Where 📜 Transcripts goes: the configured website's page, else the public one."""
    parts = urlsplit(site_url.strip())
    if parts.scheme.lower() != "https" or not parts.netloc:  # Discord refuses a bad link whole
        return TRANSCRIPTS_URL
    return f"https://{parts.netloc}/account/transcripts"


NOT_YOURS = "Only this campaign's DM can use that. Ask your DM."

# The panel's own words. Short and plain; it never names a person or a secret.
PANEL_TEXT = (
    "**DMbot**\n👤 Press DMbot Main Menu to see what you can do. Only you see the answer. "
    "❓ Help explains DMbot.\nRecording only starts for people who said yes."
)


@dataclass(frozen=True, slots=True)
class Standing:
    """What the caller read about one person and one campaign."""

    is_game_master: bool
    in_campaign: bool  # a confirmed character in it, or recorded in one of its sessions
    consented: bool  # agreed to be recorded in this server


def role_of(who: Standing) -> Role:
    if who.is_game_master:
        return Role.GAME_MASTER
    if who.in_campaign:
        return Role.PLAYER
    if who.consented:
        return Role.MEMBER
    return Role.GUEST


def buttons_for(role: Role) -> tuple[Button, ...]:
    return _BUTTONS[role]


def may_press(role: Role, button: Button) -> bool:
    """The check on every press: the same table that decides what is drawn."""
    return button in _BUTTONS[role]


def shared_panel() -> tuple[Button, ...]:
    """What the one shared message shows. A message cannot differ per reader, so it holds only
    what every role may press: 👤 DMbot Main Menu, which answers privately with `buttons_for` the
    presser's own role, and ❓ Help."""
    return (Button.OPEN, Button.HELP)
