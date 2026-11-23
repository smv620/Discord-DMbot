"""The website's continuity heads-ups (#3695 half 1, the core half): what Discord's continuity card
shows the DM after a session, on the page. Discord works its list out each time the card opens; the
website asks the bot to (`continuity-ask`), the bot keeps pointers for the campaign
(`continuity_hits`, migration 0605), and one database door (`dmbot_web_continuity_read`, migration
0606) works the words out of them at read time, with who is recorded read then. DM only: a
heads-up spoils the story if a player peeks. The answers (`continuity-answer`) go through the one
queue door like every website action; the bot applies them with the card's own answer, so no rule
is copied, and each comes back as a short code, never a word. The words are the story's and the
claim's, shown to the DM alone: never logged, and never a transcript line, a speaker or a
session."""

from __future__ import annotations

import re
from typing import Any

from dmbot.consent import TERMS_VERSION
from dmbot.db import Database
from dmbot.memory.continuity import _predicate_words  # (the card's own words for a kind)
from dmbot.memory.ontology import Ontology
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

_ID = re.compile(r"^[0-9a-f]{32}$")
# The card's two choices that write something; "Ignore" writes nothing anywhere, so it never
# reaches the queue (the page just hides the line).
CHOICES = {"changed": "c", "not": "n"}


def _opt(value: Any) -> str | None:
    return None if value is None else str(value)


async def read(
    db: Database, session: Session, ref: str, *, outside_engine: str | None
) -> dict[str, Any]:
    """`{total, hits: [...]}` in the order the bot kept them (the page shows five at a time), at
    most 50. Each hit: `{id, kind, level, name, now: {subject, relationship, object, details},
    earlier: {subject, predicate, detail, object, label} | None, ended: "dead" | "destroyed" |
    None, earlierSession: int | None, quote: str | None, pinned, learner: str | None,
    listed: [str] | None, choice}`. Only claims still waiting whose speakers are all recorded now;
    the earlier quote only while its own speakers are too. `not_allowed` for anyone who is not a DM
    of the campaign."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_continuity_read(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, TERMS_VERSION, outside_engine),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    hits: list[dict[str, Any]] = []
    onto = Ontology.build()  # (the core kinds; a campaign's own kind arrives with its label)
    for h in got["hits"]:
        now, before = h["now"], h["earlier"]
        hits.append(
            {
                "id": str(h["id"]),
                "kind": str(h["kind"]),
                "level": str(h["level"]),
                "name": str(h["name"]),
                "now": {
                    "subject": str(now["subject"]),
                    "relationship": str(now["relationship"]),
                    "object": str(now["object"]),
                    "details": str(now["details"]),
                },
                "earlier": None
                if before is None
                else {
                    "subject": str(before["subject"] or ""),
                    "predicate": str(before["predicate"]),
                    "detail": str(before["detail"] or ""),
                    "object": str(before["object"] or ""),
                    "label": _opt(before["label"]),
                },
                # The earlier fact in the card's words ("Bram carries Heartseeker", "Bram is the
                # niece of Tamsin"), built as `ContinuityStore` builds it, so the page copies no
                # table of kinds. None for a death (it has no earlier fact) or a secret clue.
                "earlierWords": None if before is None else _earlier_words(onto, before),
                "ended": _opt(h["ended"]),
                "earlierSession": None if h["earlierSession"] is None else int(h["earlierSession"]),
                "quote": _opt(h["quote"]),
                "pinned": bool(h["pinned"]),
                "learner": _opt(h["learner"]),
                "listed": None if h["listed"] is None else [str(n) for n in h["listed"]],
                "choice": str(h["choice"]),
            }
        )
    return {"total": int(got["total"]), "hits": hits}


def _earlier_words(onto: Ontology, before: dict[str, Any]) -> str:
    """The earlier fact as one line: its subject, the kind in words (the campaign's own label when
    it has one), its object."""
    label = _opt(before["label"])
    kind = label or _predicate_words(
        onto, str(before["predicate"]), str(before["detail"] or ""), directional=True
    )
    return " ".join(
        part for part in (str(before["subject"] or ""), kind, str(before["object"] or "")) if part
    )


async def ask(db: Database, session: Session, ref: str) -> str:
    """Ask the bot to work the campaign's heads-ups out now (the website cannot run the check):
    the id of the answer the page polls at `.../web/answers/{id}`, which carries how many were
    found. The page reads after it is done."""
    return await relationships._queue(
        db, session, ref, entity=None, action="continuity-ask", target="", text="", flag=False
    )


async def answer(db: Database, session: Session, ref: str, body: object) -> str:
    """The DM's answer: `{"claim": <32 hex>, "choice": "changed" | "not"}` ("The story changed" or
    its family of first buttons, and "Wasn't real"). The id of the answer the page polls; one the
    last ask did not list, or no longer waiting, comes back `gone`, and one a rule refused,
    `refused`."""
    claim = body.get("claim") if isinstance(body, dict) else None
    choice = body.get("choice") if isinstance(body, dict) else None
    if not isinstance(claim, str) or not _ID.fullmatch(claim) or choice not in CHOICES:
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="continuity-answer", target="",
        text=f"{claim} {CHOICES[str(choice)]}", flag=False,
    )  # fmt: skip
