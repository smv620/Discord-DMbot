"""The pronunciation rules (#1413): how the voice should say the campaign's names.

The caller (the audio step, #1383) reads the campaign's names from memory and passes them in as
plain values; nothing here touches the database. Only **confirmed, non-secret** names of things
and places are used (main names and other names), and never a person's real name: the voice
dictionary is sent to a provider, so a secret name must never reach it, and a name no one gave a
"said as" spelling for needs no rule (the voice says plain names well enough).

Rules take the shape of the provider's pronunciation-dictionary endpoint: alias rules, text to
text (no phonetic alphabet), for example "Ka'zeth" said as "Kah-zeth".
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory.models import name_key

MAX_RULES = 200
MAX_TEXT = 80  # characters in a name or its spelling
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class SpokenName:
    """One name as memory holds it. `said_as` is the DM's spelling for the voice, if any."""

    text: str
    said_as: str | None = None
    secret: bool = False
    confirmed: bool = True
    person: bool = False  # a player's own name, never sent


@dataclass(frozen=True, slots=True)
class Rule:
    string_to_replace: str
    alias: str

    def as_request(self) -> dict[str, str]:
        return {"string_to_replace": self.string_to_replace, "type": "alias", "alias": self.alias}


def _plain(text: str) -> str:
    return " ".join(_CONTROL.sub(" ", text).split())


def _mentions(text: str, secret_keys: Collection[str]) -> bool:
    """Whether a secret name is in the text, however it is joined to other letters: a possessive,
    a plural or a hyphen ("Vaelithor's Keep", "Vaelithor-Keep"). Names are compared the way they
    are everywhere (case, accents, apostrophes and hyphens ignored); a name written with a space
    where the secret has none ("Ka Zeth" for "Ka'zeth") is a different thing heard and is not
    caught. A false alarm only drops a rule; a leak would send a secret to the provider."""
    compact = name_key(text).replace(" ", "")
    return any(key in compact for key in secret_keys)


def rules(names: Sequence[SpokenName]) -> list[Rule]:
    """The rules for these names, in the order given, at most `MAX_RULES`. A name is left out
    when it is secret, a guess, a person, or has no spelling of its own; and so is any name or
    spelling that contains a secret name of the campaign (the whole list is checked, so a secret
    held by another entry can't come through a longer name)."""
    secret_keys = {key for n in names if n.secret and len(key := name_key(n.text)) >= 2} | {
        key for n in names if n.secret and n.said_as and len(key := name_key(n.said_as)) >= 2
    }
    out: list[Rule] = []
    seen: set[str] = set()
    for n in names:
        if n.secret or not n.confirmed or n.person or not n.said_as:
            continue
        text, alias = _plain(n.text), _plain(n.said_as)
        if not text or not alias or alias == text or max(len(text), len(alias)) > MAX_TEXT:
            continue
        if _mentions(text, secret_keys) or _mentions(alias, secret_keys):
            continue
        if text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append(Rule(text, alias))
        if len(out) == MAX_RULES:
            break
    return out


def dictionary_request(name: str, names: Sequence[SpokenName]) -> dict[str, Any]:
    """The body of the provider's "create a dictionary from rules" call for these names."""
    return {
        "name": _plain(name)[:MAX_TEXT] or "names",
        "rules": [r.as_request() for r in rules(names)],
    }
