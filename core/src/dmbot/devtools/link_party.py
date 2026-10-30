"""Backfill "The party" for campaigns that were played before the rule existed (#3036).

    python -m dmbot.devtools.link_party --guild GUILD_ID [--campaign CAMPAIGN_ID] [--dry-run]

For each campaign of the server (or the one given), each stored session in date order: the people
who had a line in it, the ones who still agree, linked by the same rule the bot runs after a live
session (`MemoryStore.link_party`). Attendance is stored for past sessions too (transcript lines),
so the backfill covers them. Only user ids are read, never a line's words.

A server is named, not all of them: every read here is scoped to one server, as the isolation
rule requires. `--dry-run` writes nothing and prints what a real run would do. Its numbers come
from reads that copy the rule's checks, so they can drift from a real run; a real run prints the
exact numbers. Counts only, one line per campaign and one total; no names, no words. A session
that fails stops the run with its error; each session is saved on its own, so a rerun is safe.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.campaigns import CampaignStore
from dmbot.consent import ConsentStore
from dmbot.db import Database
from dmbot.memory.models import CONFIRMED
from dmbot.memory.store import MemoryStore
from dmbot.transcript.attendance import session_speakers
from dmbot.transcript.store import TranscriptStore

log = logging.getLogger(__name__)


@dataclass
class Totals:
    campaigns: int = 0
    sessions: int = 0
    characters: int = 0
    pairs: int = 0
    members: int = 0


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="link_party", description=__doc__)
    parser.add_argument("--guild", type=int, required=True, help="the server to backfill")
    parser.add_argument("--campaign", help="only this campaign of the server")
    parser.add_argument("--dry-run", action="store_true", help="count, and write nothing")
    return parser.parse_args(argv)


async def campaign_sessions(db: Database, guild_id: int, campaign_id: str) -> list[str]:
    """This campaign's stored session ids in date order (read only)."""
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT id FROM transcript_sessions WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY started_at, id",
            (guild_id, campaign_id),
        )
        return [str(r["id"]) for r in await cur.fetchall()]


async def dry_count(
    db: Database, guild_id: int, campaign_id: str, speakers: set[int]
) -> tuple[int, int, int]:
    """(characters, pairs, members) a real run would add for these speakers. Reads only: the
    player characters these people play, the `knows` pairs already there, and the characters
    not yet members of The party."""
    if not speakers:
        return 0, 0, 0
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT id, played_by, created_at FROM memory_entities"
            " WHERE guild_id = %s AND campaign_id = %s AND role = 'player_character'"
            " AND status = %s AND merged_into IS NULL AND played_by = ANY(%s)",
            (guild_id, campaign_id, CONFIRMED, sorted(speakers)),
        )
        # The rule takes each player's newest character, so one per player counts.
        newest: dict[int, tuple[int, str]] = {}
        for r in await cur.fetchall():
            key = (r["created_at"], str(r["id"]))
            player = int(r["played_by"])
            if player not in newest or key > newest[player]:
                newest[player] = key
        chars = sorted(entity_id for _, entity_id in newest.values())
        if len(chars) < 2:
            return len(chars), 0, len(chars)
        cur = await conn.execute(
            "SELECT subject_id, object_id FROM memory_relations"
            " WHERE guild_id = %s AND campaign_id = %s AND predicate = 'knows'"
            " AND subject_id = ANY(%s) AND object_id = ANY(%s)",
            (guild_id, campaign_id, chars, chars),
        )
        known = {
            frozenset((str(r["subject_id"]), str(r["object_id"]))) for r in await cur.fetchall()
        }
        cur = await conn.execute(
            "SELECT subject_id FROM memory_relations WHERE guild_id = %s AND campaign_id = %s"
            " AND predicate = 'member_of' AND subject_id = ANY(%s)",
            (guild_id, campaign_id, chars),
        )
        in_party = {str(r["subject_id"]) for r in await cur.fetchall()}
    pairs = sum(
        1 for i, a in enumerate(chars) for b in chars[i + 1 :] if frozenset((a, b)) not in known
    )
    return len(chars), pairs, len(chars) - len(in_party)


async def backfill(db: Database, args: argparse.Namespace) -> Totals:
    totals = Totals()
    guild_id = args.guild
    campaigns = await CampaignStore(db).list_campaigns(guild_id)
    if args.campaign:
        campaigns = [c for c in campaigns if c.id == args.campaign]
    transcripts = TranscriptStore(db)
    memory = MemoryStore(db)
    agreed = await ConsentStore(db).consenting(guild_id)
    for campaign in campaigns:
        totals.campaigns += 1
        for session_id in await campaign_sessions(db, guild_id, campaign.id):
            totals.sessions += 1
            speakers = {
                u
                for u in await session_speakers(transcripts.db, guild_id, session_id)
                if u in agreed
            }
            if args.dry_run:
                chars, pairs, members = await dry_count(db, guild_id, campaign.id, speakers)
            else:
                linked = await memory.link_party(guild_id, campaign.id, speakers)
                chars, pairs, members = linked.characters, linked.pairs, linked.members
            totals.characters += chars
            totals.pairs += pairs
            totals.members += members
    return totals


async def main_async(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("link_party: DATABASE_URL isn't set", file=sys.stderr)
        return 2
    db = await Database.open(url, migrate=False, max_size=2)
    try:
        totals = await backfill(db, args)
    finally:
        await db.close()
    verb = "Would link" if args.dry_run else "Linked"
    print(
        f"{verb} The party in {totals.campaigns} campaigns, {totals.sessions} sessions: "
        f"{totals.characters} characters, {totals.pairs} pairs, {totals.members} members."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
