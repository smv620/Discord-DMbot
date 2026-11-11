"""The "Which one?" choices of a kept claim, for the website's waiting list (#3463).

A claim kept because a name in it could be two entries (#3238, #3423) is answered by picking the
entry. The choices come from the store's own function (`claims.which_choices`), run on the facts a
database door sends (`dmbot_web_which_info`), so the website offers exactly what Discord's card
does; each option also carries its tag (`tags.tag_for`, as Discord builds it) so two entries with
the same name can be told apart ("Mara (in Termalaine)"). The DM's own words and ids only: never a
transcript line, a quote or a speaker.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from dmbot.memory import tags
from dmbot.memory.claims import which_choices
from dmbot.memory.models import Entity


def _entity(row: Mapping[str, Any]) -> Entity:
    return Entity(
        id=str(row["id"]),
        type=str(row["type"]),
        name=str(row["name"]),
        description=str(row.get("description") or ""),
        status=str(row["status"]),
        merged_into=None,
        source=str(row.get("source") or ""),
        created_at=0,
        unnamed=bool(row.get("unnamed")),
        unnamed_anchor=row.get("unnamed_anchor"),
        unnamed_role=row.get("unnamed_role"),
        unnamed_what=row.get("unnamed_what"),
    )


def _ties(
    entities: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], mine: str
) -> list[tags.TagTie]:
    """The entry's confirmed, current ties (the door leaves out ended ones, #4134) as
    `tags.tag_for` reads them (as `MemoryStore.tag_ties` builds them): the other end's name,
    whether this entry is the subject, whether the tie is secret. A tie to an entry that is not
    live is left out."""
    found: list[tags.TagTie] = []
    for r in rows:
        for here, other, outgoing in (
            (str(r["subject_id"]), str(r["object_id"]), True),
            (str(r["object_id"]), str(r["subject_id"]), False),
        ):
            if here == mine and other in entities:
                found.append(
                    tags.TagTie(
                        str(r["predicate"]),
                        str(entities[other]["name"]),
                        outgoing,
                        confirmed=True,
                        secret=bool(r["secret"]),
                        detail=str(r.get("detail") or ""),
                    )
                )
    return found


def choices_by_claim(info: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """For each kept claim in the door's answer that still has a name to pick for:
    `[{side, words, entries: [{id, name, kind, note, tag}]}]`. A claim with nothing left to pick
    for is left out."""
    entities = {str(e["id"]): e for e in info["entities"]}
    out: dict[str, list[dict[str, Any]]] = {}
    for claim in info["claims"]:
        picks = which_choices(claim, info["entities"], info["aliases"])
        if not picks:
            continue
        out[str(claim["id"])] = [
            {
                "side": p.side,
                "words": p.words,
                "entries": [
                    {
                        "id": o.id,
                        "name": o.name,
                        "kind": o.kind,
                        "note": o.note,
                        "tag": tags.tag_for(
                            _entity(entities[o.id]), _ties(entities, info["ties"], o.id), dm=True
                        ),
                    }
                    for o in p.entries
                ],
            }
            for p in picks
        ]
    return out
