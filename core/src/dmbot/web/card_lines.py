"""A character's card lines for the website (#5050 slice d): who they are, where and when they were
first met, when they were last mentioned, in the same words as Discord's name card
(`memory/card_view.py` builds them). Read from what the background job kept through
`dmbot_web_card_lines`, a DM-only door: it gives nothing to anyone who is not a DM of the campaign,
and a page with nothing to show has no key. Nothing is made on open and nothing is written. The
lines are DMbot's own words, plain text for the page to show as they are; never logged.
"""

from __future__ import annotations

from typing import Any

from dmbot.memory import card_lines as words
from dmbot.memory import card_view
from dmbot.memory.card_summaries import Mention, Summary

MAX_IDS = 200  # as the database's door


def _mention(raw: Any) -> Mention | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("at"), int):
        return None
    return Mention(int(raw["at"]), 0, str(raw.get("scene") or ""))


def _lore(raw: Any) -> tuple[str, str, int] | None:
    if not isinstance(raw, dict) or not raw.get("label") or not isinstance(raw.get("at"), int):
        return None
    return str(raw["label"]), str(raw.get("heading") or ""), int(raw["at"])


def lines_of(name: str, card: dict[str, Any] | None, numbers: dict[int, int]) -> dict[str, Any]:
    """The keys to put on a row: `who` (always, one sentence or why there is none), `whoPending`
    (true while the job has not written it), and `firstMet` and `lastMentioned` when there is a
    line to say. A character only read from the lore has "First met: in your lore, …" instead."""
    # (a row the job kept is a summary however little it holds, as Discord reads it; no row is
    # "still writing")
    summary = None
    if card is not None:
        summary = Summary(
            who=card.get("who"),
            facts_key=None,
            made_at=card.get("madeAt"),
            first=_mention(card.get("first")),
            last=_mention(card.get("last")),
            scanned_at=card.get("scannedAt"),
        )
    card = card or {}
    made = card_view.view(summary, name, numbers, lore=_lore(card.get("lore")))
    out: dict[str, Any] = {"who": made.who}
    if made.who == words.STILL_WRITING:
        out["whoPending"] = True
    if made.first:
        out["firstMet"] = made.first
    if made.last:
        out["lastMentioned"] = made.last
    return out


async def attach(
    conn: Any,
    guild_id: int,
    campaign_id: str,
    rows: list[dict[str, Any]],
    *,
    only: Any = None,
) -> None:
    """Give each of `rows` (dicts with `id` and `name`) its card keys. `only` picks the rows (the
    page asks for characters). `conn` is a user connection already scoped to the guild. For anyone
    who is not a DM the door answers nothing, so no row gets a key."""
    asked = [r for r in rows if only is None or only(r)]
    for start in range(0, len(asked), MAX_IDS):
        chunk = asked[start : start + MAX_IDS]
        cur = await conn.execute(
            "SELECT dmbot_web_card_lines(%s, %s, %s) AS r",
            (guild_id, campaign_id, [str(r["id"]) for r in chunk]),
        )
        got = (await cur.fetchone() or {}).get("r")
        if not isinstance(got, dict) or not isinstance(got.get("cards"), dict):
            return
        numbers = {int(k): int(v) for k, v in (got.get("numbers") or {}).items()}
        for row in chunk:
            card = got["cards"].get(str(row["id"]))
            if card is None:
                continue  # (not a character the job has reached: the page draws nothing)
            name = str(row.get("name") or "this character")
            row.update(lines_of(name, card, numbers))
