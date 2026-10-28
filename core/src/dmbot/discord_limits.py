"""Discord's limits on slash commands, in one place (#1797).

Commands are registered globally, once, for every server (#1767). If Discord refuses the command
tree, that one request fails and no server gets `/dmbot`, so the tree is checked against these
limits in CI (`tests/test_command_limits.py`) and again when the bot starts. Pure Python: it reads
the payload `CommandTree` would send, and needs no Discord connection.

Source: https://discord.com/developers/docs/interactions/application-commands (the command
structure, "Application Command Option Structure" and "Application Command Option Choice
Structure"). Discord can change them; when a number here is wrong the startup error below says
which field it refused.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

MAX_CHAT_COMMANDS = 100  # top-level slash commands for one app (and 5 each for user/message menus)
MAX_MENU_COMMANDS = 5
NAME_MAX = 32
DESCRIPTION_MAX = 100
OPTIONS_MAX = 25  # options, subcommands or groups at one level
CHOICES_MAX = 25
CHOICE_NAME_MAX = 100
CHOICE_VALUE_MAX = 100  # for a text choice
TOTAL_MAX = 4000  # name, description and every option's name, description and choices, summed
# Lowercase letters, digits, "-" and "_" (Discord also allows some other scripts' letters; \w is
# the closest plain pattern, and a capital is checked apart).
NAME_PATTERN = re.compile(r"^[-_\w]{1,32}$")

# Message components: what Discord refuses a whole message (or modal) for. Docs:
# https://discord.com/developers/docs/components/reference
BUTTON_LABEL_MAX = 80
SELECT_OPTIONS_MAX = 25
OPTION_LABEL_MAX = 100
OPTION_DESCRIPTION_MAX = 100
SELECT_PLACEHOLDER_MAX = 150
ROWS_MAX = 5  # action rows in one message
ROW_BUTTONS_MAX = 5
MODAL_TITLE_MAX = 45
MODAL_INPUTS_MAX = 5
INPUT_LABEL_MAX = 45
INPUT_PLACEHOLDER_MAX = 100
INPUT_LENGTH_MAX = 4000  # a text input's max_length, and the longest value it can start with
CUSTOM_ID_MAX = 100  # a button's or select's custom_id, in characters
# A button id that carries a number (a game-clock minute, a round) has no length of its own; a
# minute count under a billion is 1,900 years of play, so nine digits stand in for "any number".
DIGITS_MAX = 9

# Option types (the same numbers Discord uses).
SUB_COMMAND, SUB_COMMAND_GROUP = 1, 2
CHAT_INPUT, USER_MENU, MESSAGE_MENU = 1, 2, 3


def shorten(text: str, limit: int) -> str:
    """Trim text to fit a Discord label, ending with an ellipsis if it was cut."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _name_problems(where: str, name: object, *, menu: bool = False) -> list[str]:
    if not isinstance(name, str) or not 1 <= len(name) <= NAME_MAX:
        return [f"{where}: the name {name!r} must be 1 to {NAME_MAX} characters"]
    if menu:  # a context menu's name may hold capitals and spaces
        return []
    out = []
    if not NAME_PATTERN.match(name):
        out.append(f"{where}: the name {name!r} may use only letters, digits, '-' and '_'")
    if name != name.lower():
        out.append(f"{where}: the name {name!r} must be lowercase")
    return out


# (Localized names and descriptions also count toward the total; DMbot uses none, so they aren't
# counted here. Add them if localizations are ever used.)
def _size(node: Mapping[str, Any]) -> int:
    """The characters Discord counts for one command: names, descriptions and choices."""
    total = len(str(node.get("name", ""))) + len(str(node.get("description", "")))
    for choice in node.get("choices") or []:
        total += len(str(choice.get("name", ""))) + len(str(choice.get("value", "")))
    for option in node.get("options") or []:
        total += _size(option)
    return total


def _option_problems(where: str, option: Mapping[str, Any], depth: int) -> list[str]:
    name = option.get("name")
    here = f"{where} {name}"
    out = _name_problems(here, name)
    description = option.get("description")
    if not isinstance(description, str) or not 1 <= len(description) <= DESCRIPTION_MAX:
        out.append(f"{here}: the description must be 1 to {DESCRIPTION_MAX} characters")
    kind = option.get("type")
    options = option.get("options") or []
    if len(options) > OPTIONS_MAX:
        out.append(f"{here}: {len(options)} options, at most {OPTIONS_MAX}")
    if kind == SUB_COMMAND_GROUP:
        if depth >= 1:
            out.append(f"{here}: a group inside a group (only 2 levels are allowed)")
        for sub in options:
            if sub.get("type") != SUB_COMMAND:
                out.append(f"{here}: a group may hold subcommands only")
    if kind == SUB_COMMAND and any(
        o.get("type") in (SUB_COMMAND, SUB_COMMAND_GROUP) for o in options
    ):
        out.append(f"{here}: a subcommand may not hold subcommands")
    choices = option.get("choices") or []
    if len(choices) > CHOICES_MAX:
        out.append(f"{here}: {len(choices)} choices, at most {CHOICES_MAX}")
    for choice in choices:
        label = choice.get("name")
        if not isinstance(label, str) or not 1 <= len(label) <= CHOICE_NAME_MAX:
            out.append(
                f"{here}: the choice name {label!r} must be 1 to {CHOICE_NAME_MAX} characters"
            )
        value = choice.get("value")
        if isinstance(value, str) and len(value) > CHOICE_VALUE_MAX:
            out.append(f"{here}: the choice value {value!r} is over {CHOICE_VALUE_MAX} characters")
    for sub in options:
        out.extend(_option_problems(here, sub, depth + 1))
    return out


def command_problems(payload: Mapping[str, Any]) -> list[str]:
    """What Discord would refuse in one command's payload, as plain lines naming the command and
    the limit. Empty: fine."""
    kind = payload.get("type", CHAT_INPUT)
    name = payload.get("name")
    where = f"/{name}" if kind == CHAT_INPUT else f"menu {name!r}"
    out = _name_problems(where, name, menu=kind != CHAT_INPUT)
    if kind == CHAT_INPUT:
        description = payload.get("description")
        if not isinstance(description, str) or not 1 <= len(description) <= DESCRIPTION_MAX:
            out.append(f"{where}: the description must be 1 to {DESCRIPTION_MAX} characters")
        options = payload.get("options") or []
        if len(options) > OPTIONS_MAX:
            out.append(f"{where}: {len(options)} options or subcommands, at most {OPTIONS_MAX}")
        for option in options:
            out.extend(_option_problems(where, option, 0))
        size = _size(payload)
        if size > TOTAL_MAX:
            out.append(f"{where}: {size} characters in all, at most {TOTAL_MAX}")
    return out


def tree_problems(payloads: Sequence[Mapping[str, Any]]) -> list[str]:
    """Everything Discord would refuse in a whole command tree (the payloads it would send)."""
    out: list[str] = []
    chat = [p for p in payloads if p.get("type", CHAT_INPUT) == CHAT_INPUT]
    if len(chat) > MAX_CHAT_COMMANDS:
        out.append(f"{len(chat)} slash commands, at most {MAX_CHAT_COMMANDS}")
    for kind, label in ((USER_MENU, "user"), (MESSAGE_MENU, "message")):
        menus = [p for p in payloads if p.get("type") == kind]
        if len(menus) > MAX_MENU_COMMANDS:
            out.append(f"{len(menus)} {label} context menus, at most {MAX_MENU_COMMANDS}")
    names = [(p.get("type", CHAT_INPUT), p.get("name")) for p in payloads]
    for dup in {n for n in names if names.count(n) > 1}:
        out.append(f"the name {dup[1]!r} is used twice")
    for payload in payloads:
        out.extend(command_problems(payload))
    return out


class UnboundedId(ValueError):
    """A custom_id pattern with a part that has no longest value (like `.+` or `[^:]+`)."""


def longest_custom_id(pattern: str) -> int:
    """How many characters the longest custom_id matching `pattern` can have. A number
    (`\\d+`, `\\d*`) counts as `DIGITS_MAX` digits; any other unbounded part raises `UnboundedId`,
    because then no id length can be promised."""
    # Private, but the only way to measure a pattern (Python 3.11+); loaded by name because it has
    # no stubs, and here rather than when the module is imported, so starting the bot never
    # depends on it (#1830).
    _regex_parser: Any = importlib.import_module("re._parser")
    bounded = re.sub(
        r"\\d([+*])", lambda m: f"\\d{{{1 if m[1] == '+' else 0},{DIGITS_MAX}}}", pattern
    )
    longest = _regex_parser.parse(bounded).getwidth()[1]
    if longest >= _regex_parser.MAXREPEAT:
        raise UnboundedId(pattern)
    return int(longest)


BUTTON, SELECT, TEXT_INPUT = 2, 3, 4  # component types (the same numbers Discord uses)


def _over(where: str, what: str, value: object, limit: int) -> list[str]:
    if isinstance(value, str) and len(value) > limit:
        return [f"{where}: the {what} is {len(value)} characters, at most {limit}"]
    return []


def component_problems(rows: Sequence[Mapping[str, Any]], *, where: str = "view") -> list[str]:
    """What Discord would refuse in a view's or a modal's components (`to_components()`), as lines
    naming the part and the limit. Empty: fine."""
    out: list[str] = []
    if len(rows) > ROWS_MAX:
        out.append(f"{where}: {len(rows)} rows, at most {ROWS_MAX}")
    for number, row in enumerate(rows, 1):
        children = row.get("components") or []
        if sum(1 for c in children if c.get("type") == BUTTON) > ROW_BUTTONS_MAX:
            out.append(f"{where} row {number}: more than {ROW_BUTTONS_MAX} buttons")
        for child in children:
            kind = child.get("type")
            label = child.get("label")
            if kind == BUTTON:
                out += _over(f"{where} button {label!r}", "label", label, BUTTON_LABEL_MAX)
            elif kind == SELECT:
                spot = f"{where} menu {str(child.get('placeholder'))[:30]!r}"
                out += _over(spot, "placeholder", child.get("placeholder"), SELECT_PLACEHOLDER_MAX)
                options = child.get("options") or []
                if len(options) > SELECT_OPTIONS_MAX:
                    out.append(f"{spot}: {len(options)} choices, at most {SELECT_OPTIONS_MAX}")
                for option in options:
                    here = f"{spot} choice {str(option.get('label'))[:30]!r}"
                    out += _over(here, "label", option.get("label"), OPTION_LABEL_MAX)
                    out += _over(
                        here, "description", option.get("description"), OPTION_DESCRIPTION_MAX
                    )
            elif kind == TEXT_INPUT:
                here = f"{where} box {str(label)[:30]!r}"
                out += _over(here, "label", label, INPUT_LABEL_MAX)
                out += _over(here, "placeholder", child.get("placeholder"), INPUT_PLACEHOLDER_MAX)
                if (child.get("max_length") or 0) > INPUT_LENGTH_MAX:
                    out.append(f"{here}: max_length over {INPUT_LENGTH_MAX}")
                limit = min(child.get("max_length") or INPUT_LENGTH_MAX, INPUT_LENGTH_MAX)
                out += _over(here, "starting text", child.get("value"), limit)
    return out


def modal_problems(title: str, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """`component_problems` for a modal, plus its title and its box count."""
    out = _over(f"form {title[:30]!r}", "title", title, MODAL_TITLE_MAX)
    if len(rows) > MODAL_INPUTS_MAX:
        out.append(f"form {title[:30]!r}: {len(rows)} boxes, at most {MODAL_INPUTS_MAX}")
    return out + component_problems(rows, where=f"form {title[:30]!r}")


MESSAGE_MAX = (
    2000  # characters in one message's text; Discord refuses the whole send above it (#1835)
)
ELLIPSIS = "…"


_MARKERS = re.compile(r"(?<!\\)(\*\*|`)")  # bold or a backtick that is not escaped with a backslash


def _formatting_open(line: str) -> bool:
    """True when a line leaves bold or a backtick open: cutting there would break the rest."""
    found = [m.group(1) for m in _MARKERS.finditer(line)]
    return found.count("**") % 2 == 1 or found.count("`") % 2 == 1


def fit_message(text: str, *, limit: int = MESSAGE_MAX) -> str:
    """The text unchanged if it fits, else cut at a line break (or a space) and ended with "…".

    A cut at a fixed character can land inside a word or a `**`, which breaks the formatting of
    everything after it, so the cut backs up to a line or a word, and a last line that would
    leave bold or a backtick open is dropped. The result is never over `limit` (counted in
    characters). Only `**` and backticks are watched; one escaped with a backslash is not a marker.
    """
    if len(text) <= limit:
        return text
    if limit < 2:
        return ELLIPSIS[:limit]
    room = text[: limit - 1]
    cut = room.rfind("\n")
    if cut <= 0:
        cut = room.rfind(" ")
    head = room[:cut] if cut > 0 else room  # one long word: nothing better than a hard cut
    last = head.rsplit("\n", 1)
    while len(last) == 2 and _formatting_open(last[1]):
        head = last[0]
        last = head.rsplit("\n", 1)
    while len(last) == 1 and _formatting_open(head):  # one line left: drop its last marker
        marker = list(_MARKERS.finditer(head))[-1]
        head = head[: marker.start()] + head[marker.end() :]
    return head.rstrip() + ELLIPSIS
