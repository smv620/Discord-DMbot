"""How many movers hold more than one place of the same kind (#3363), read only, counts only.

    python -m dmbot.devtools.movers_check --guild GUILD_ID [--campaign CAMPAIGN_ID]

Since #3485 a new "lives in", "works in" or "was last seen in" ends the older one of the same kind
for a character, NPC, creature, deity or group (the party). That rule does not touch rows written
before it. This counts, for the server (or one campaign), the movers that still hold two or more
current places of one kind, split by the mover's kind and by how many places it holds, so the
owner's "in two towns" report can be checked on the real campaign. It changes nothing and fixes
nothing: closing the older rows as history is a design note first.

What is counted: confirmed, current (no end) `located_in` connections from a mover to a place
(not an item and not another mover), of one kind of tie each ("lives in", "works in", "was last
seen in"; "comes from" and a past tie are never limited). A place inside another place is counted as
two, and a hidden (secret) connection is counted although the rule would never close a visible one
for it, so the numbers are an upper bound in those two ways. In one way they undercount: a mover,
a place or a connection that is only proposed, rejected or merged away is not looked at. A group
counts only for its sightings, as the rule does. It shares the `whereabouts` rules on purpose, so a
change there is a change here. Connect as the normal `dmbot` user (the database refuses a
superuser); the command only ever runs one SELECT.
One line of counts: no names, no ids, no words of any session.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from dmbot.db import Database
from dmbot.memory import place_words, whereabouts

log = logging.getLogger(__name__)

# (campaign id, mover id, mover kind, place kind, detail) of one current located_in connection
Row = tuple[str, str, str, str, str]


@dataclass
class Counts:
    campaigns: set[str] = field(default_factory=set)
    movers_with_a_place: int = 0
    over: Counter[tuple[str, int]] = field(default_factory=Counter)  # (mover kind, places) -> n

    @property
    def movers_over(self) -> int:
        return sum(self.over.values())


def count_movers(rows: Iterable[Row]) -> Counts:
    """The counts for these connections. Pure, so it is tested without a database."""
    out = Counts()
    held: Counter[tuple[str, str, str]] = Counter()  # (campaign, mover, slot word) -> places
    kinds: dict[tuple[str, str, str], str] = {}
    seen_movers: set[tuple[str, str]] = set()
    for campaign, mover, kind, place_kind, detail in rows:
        if not whereabouts._moves(kind) or whereabouts._moves(place_kind) or place_kind == "item":
            continue
        word, past = place_words.slot(detail, kind)
        if past or word in whereabouts._NEVER_ENDS:
            continue
        if kind == "faction" and word != place_words.LAST_SEEN:
            continue
        out.campaigns.add(campaign)
        seen_movers.add((campaign, mover))
        key = (campaign, mover, word)
        held[key] += 1
        kinds[key] = kind
    out.movers_with_a_place = len(seen_movers)
    for key, n in held.items():
        if n > 1:
            out.over[(kinds[key], min(n, 4))] += 1  # ("4" means four or more)
    return out


async def read_rows(db: Database, guild_id: int, campaign_id: str | None) -> list[Row]:
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT r.campaign_id, r.subject_id, s.type AS skind, o.type AS okind, r.detail"
            " FROM memory_relations r"
            " JOIN memory_entities s ON (s.guild_id, s.campaign_id, s.id)"
            "   = (r.guild_id, r.campaign_id, r.subject_id)"
            " JOIN memory_entities o ON (o.guild_id, o.campaign_id, o.id)"
            "   = (r.guild_id, r.campaign_id, r.object_id)"
            " WHERE r.guild_id = %s AND r.predicate = 'located_in' AND r.status = 'confirmed'"
            " AND r.to_session_at IS NULL AND s.status = 'confirmed' AND o.status = 'confirmed'"
            " AND (%s::text IS NULL OR r.campaign_id = %s)",
            (guild_id, campaign_id, campaign_id),
        )
        return [
            (
                str(r["campaign_id"]), str(r["subject_id"]), str(r["skind"]), str(r["okind"]),
                str(r["detail"] or ""),
            )
            for r in await cur.fetchall()
        ]  # fmt: skip


def report(c: Counts) -> str:
    split = ", ".join(
        f"{kind}: {n} hold {'4 or more' if places == 4 else places}"
        for (kind, places), n in sorted(c.over.items())
    )
    return (
        f"Movers check (read only, nothing written): {len(c.campaigns)} campaigns, "
        f"{c.movers_with_a_place} movers with a current place, {c.movers_over} hold more than "
        f"one place of one kind" + (f" ({split})." if split else ".")
    )


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="movers_check", description=__doc__)
    parser.add_argument("--guild", type=int, required=True, help="the server to look at")
    parser.add_argument("--campaign", help="only this campaign of the server")
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("movers_check: DATABASE_URL isn't set", file=sys.stderr)
        return 2
    db = await Database.open(url, migrate=False, max_size=2)
    try:
        counts = count_movers(await read_rows(db, args.guild, args.campaign))
    finally:
        await db.close()
    print(report(counts))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
