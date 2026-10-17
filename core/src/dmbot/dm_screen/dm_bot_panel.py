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
- **guest**: anyone else in the server. Help only; rule lookups and reading transcripts are
  open to the whole server anyway.

A server manager who is not a game master is a guest here: nothing is widened.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    GAME_MASTER = "game_master"
    PLAYER = "player"
    MEMBER = "member"
    GUEST = "guest"


class Button(StrEnum):
    """What a panel button does. The value is the action in its custom id."""

    OPEN = "open"  # on the shared panel: opens the person's own buttons, privately
    HELP = "help"
    MY_MENU = "my_menu"  # ⚙️ Menu: Stop recording me, Delete my transcripts (about the person)
    MY_CHARACTER = "my_character"  # the player's character link in this campaign
    SETTINGS = "settings"  # the campaign's Settings card, game masters only


# Each role's buttons, in the order they are drawn. A role never gets more than the one below it
# plus its own, which `test_each_role_has_what_the_weaker_ones_have` checks.
_BUTTONS: dict[Role, tuple[Button, ...]] = {
    Role.GUEST: (Button.HELP,),
    Role.MEMBER: (Button.HELP, Button.MY_MENU),
    Role.PLAYER: (Button.HELP, Button.MY_MENU, Button.MY_CHARACTER),
    Role.GAME_MASTER: (Button.HELP, Button.MY_MENU, Button.MY_CHARACTER, Button.SETTINGS),
}

LABELS: dict[Button, str] = {
    Button.OPEN: "🔓 My buttons",
    Button.HELP: "❓ Help",
    Button.MY_MENU: "⚙️ My menu",
    Button.MY_CHARACTER: "🧙 My character",
    Button.SETTINGS: "⚙️ Settings",
}

NOT_YOURS = "That one is for your game master."

# The panel's own words. Short and plain; it never names a person or a secret.
PANEL_TEXT = "**DM Bot**\nPress a button. DMbot answers only you."


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
    what every role may press: 🔓 My buttons, which answers privately with `buttons_for` the
    presser's own role, and ❓ Help."""
    return (Button.OPEN, Button.HELP)
