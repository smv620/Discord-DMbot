"""The words an entry is searched by (#2733, #2664): the one place they are put together, so the
numbers the bot keeps (`embed_sources.EntitySource`) and the text the Names page's reranker reads
(`web/names_smart.py`) and the sidebar's (`meaning.entry_texts`) are the same words.

The database gives the parts (`dmbot_entity_parts`, schema.py, which already leaves out what the DM
keeps secret and what the players don't know yet); this makes a sentence of them. The words of a
core relationship ("is an ally of") are in code, a campaign's own are in its table, so they cannot
be put together in the database alone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from dmbot.memory.ontology import CORE_PREDICATES

CONNECTIONS_MAX = 10  # connections in the text: a long list drowns what the entry is
_CORE = {t.key: (t.label, t.symmetric) for t in CORE_PREDICATES}


def _connection(link: Mapping[str, Any]) -> str | None:
    """One connection in plain words ("is an ally of Brannoc"), or None for a connection that
    reads from the other side and is not the same both ways (it is that entry's to carry)."""
    key = str(link["predicate"])
    core = _CORE.get(key)
    label = link.get("label") or (core[0] if core else key.replace("_", " "))
    symmetric = link.get("symmetric")
    if symmetric is None:
        symmetric = core[1] if core else False
    if not link.get("outgoing") and not symmetric:
        return None
    return f"{label} {link['other']}"


def compose(parts: Mapping[str, Any]) -> str:
    """The text for one entry from `dmbot_entity_parts`:
    "Mira Voss: A ferrywoman. Also called the Ferrywoman. Connections: is an ally of Brannoc."."""
    text = f"{parts['name']}: {parts['description']}"
    aliases: Sequence[str] = parts.get("aliases") or []
    if aliases:
        text += f" Also called {', '.join(aliases)}."
    lines: list[str] = []
    for link in parts.get("links") or []:
        line = _connection(link)
        if line is not None and line not in lines:
            lines.append(line)
    if lines:
        text += f" Connections: {'; '.join(lines[:CONNECTIONS_MAX])}."
    return text
