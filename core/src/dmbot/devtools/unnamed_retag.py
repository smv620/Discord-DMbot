"""Give a "(first seen …)" to unnamed people whose names read the same by Python's key (#3743).

    python -m dmbot.devtools.unnamed_retag --guild GUILD_ID [--campaign CAMPAIGN_ID] [--dry-run]

Migration 0379 made every unnamed person's name unique, grouping look-alikes with a SQL key that
keeps accents and doubled spaces apart; DMbot's own `name_key` does not. This finds any pair that
key takes as one, in each campaign of the server (or the one given), and gives the later ones the
same "(first seen <session date>)" a new person gets (`MemoryStore.retag_unnamed`: the oldest keeps
its name, one change per campaign, so the DM can undo it). `--dry-run` writes nothing. One server
at a time, as the isolation rule requires. Counts only, one line: no names.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence

from dmbot.db import Database
from dmbot.memory.store import MemoryStore


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="unnamed_retag", description=__doc__)
    parser.add_argument("--guild", type=int, required=True, help="the server to look at")
    parser.add_argument("--campaign", help="only this campaign of the server")
    parser.add_argument("--dry-run", action="store_true", help="count, and write nothing")
    return parser.parse_args(argv)


async def campaigns_of(db: Database, guild_id: int, only: str | None) -> list[str]:
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT id FROM campaigns WHERE guild_id = %s AND (%s::text IS NULL OR id = %s)"
            " ORDER BY id",
            (guild_id, only, only),
        )
        return [str(r["id"]) for r in await cur.fetchall()]


async def run(db: Database, guild_id: int, only: str | None, dry_run: bool) -> tuple[int, int]:
    """(campaigns looked at, people given a new name)."""
    memory = MemoryStore(db)
    ids = await campaigns_of(db, guild_id, only)
    total = 0
    for campaign_id in ids:
        total += await memory.retag_unnamed(guild_id, campaign_id, dry_run=dry_run)
    return len(ids), total


async def main_async(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("unnamed_retag: DATABASE_URL isn't set", file=sys.stderr)
        return 2
    db = await Database.open(url, migrate=False, max_size=2)
    try:
        campaigns, people = await run(db, args.guild, args.campaign, args.dry_run)
    finally:
        await db.close()
    done = "would get" if args.dry_run else "got"
    print(f"Unnamed retag: {campaigns} campaign(s), {people} person(s) {done} a 'first seen'.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
