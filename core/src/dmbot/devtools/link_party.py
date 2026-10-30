"""Backfill "The party" for campaigns that were played before the rule existed (#3036).

    python -m dmbot.devtools.link_party --guild GUILD_ID [--campaign CAMPAIGN_ID] [--dry-run]

For each campaign of the server (or the one given), each stored session in date order: the people
who spoke at the table in it, the ones who still agree, linked by the same rule the bot runs after a
live session (`MemoryStore.link_party`). Attendance is stored for past sessions too (transcript
lines), so the backfill covers them. Only user ids are read, never a line's words.

A server is named, not all of them: every read here is scoped to one server, as the isolation
rule requires. `--dry-run` writes nothing and prints only how many sessions and speakers it would
look at: the links themselves depend on what is already linked, so a real run is the only exact
count. A session that fails stops the run with its error; each session is saved on its own, so a
rerun is safe. Counts only, one line; no names, no words.
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
from dmbot.memory.store import MemoryStore
from dmbot.transcript.attendance import session_speakers
from dmbot.transcript.store import TranscriptStore

log = logging.getLogger(__name__)


@dataclass
class Totals:
    campaigns: int = 0
    sessions: int = 0
    speakers: int = 0  # a session's speakers, summed (a person in two sessions counts twice)
    characters: int = 0
    pairs: int = 0
    members: int = 0


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="link_party", description=__doc__)
    parser.add_argument("--guild", type=int, required=True, help="the server to backfill")
    parser.add_argument("--campaign", help="only this campaign of the server")
    parser.add_argument("--dry-run", action="store_true", help="count sessions, and write nothing")
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


async def backfill(db: Database, args: argparse.Namespace) -> Totals:
    totals = Totals()
    guild_id = args.guild
    campaigns = await CampaignStore(db).list_campaigns(guild_id)
    if args.campaign:
        campaigns = [c for c in campaigns if c.id == args.campaign]
    transcripts = TranscriptStore(db)
    memory = MemoryStore(db)
    # Agreed, as the bot's live step is (a pause is not a refusal, #3036).
    agreed = await ConsentStore(db).agreed(guild_id)
    for campaign in campaigns:
        totals.campaigns += 1
        for session_id in await campaign_sessions(db, guild_id, campaign.id):
            totals.sessions += 1
            speakers_here = await session_speakers(
                transcripts.db, guild_id, campaign.id, session_id
            )
            speakers = {u for u in speakers_here if u in agreed}
            totals.speakers += len(speakers)
            if args.dry_run:
                continue
            linked = await memory.link_party(guild_id, campaign.id, speakers)
            totals.characters += linked.characters
            totals.pairs += linked.pairs
            totals.members += linked.members
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
    if args.dry_run:
        print(
            f"Dry run, nothing written: {totals.campaigns} campaigns, {totals.sessions} sessions, "
            f"{totals.speakers} speakers to look at. Run without --dry-run for the exact links."
        )
    else:
        print(
            f"Linked The party in {totals.campaigns} campaigns, {totals.sessions} sessions: "
            f"{totals.characters} characters, {totals.pairs} pairs, {totals.members} members."
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
