"""What waits in a campaign's backstory-connection queue (#2650), for dev1 on the server.

    python -m dmbot.devtools.backstory_links --guild GUILD_ID --campaign CAMPAIGN_ID

No AI and nothing changed: it counts the connections that wait for the DM (ready to check, and
waiting for names that are not answered yet), by kind of relationship and by upload. The owner's
live check of a backstory upload is one fresh upload after a deploy and this printout, recorded in
the testing logs as counts. Counts only: no name, no quote and no upload name is printed (an
upload is shown by its number).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from collections.abc import Sequence

from dmbot.db import Database
from dmbot.memory.backstory_links import BackstoryLinks


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="backstory_links", description=__doc__)
    parser.add_argument(
        "--guild", type=int, required=True, help="the server's id (rows are per server)"
    )
    parser.add_argument("--campaign", required=True, help="the campaign's id")
    return parser.parse_args(argv)


def report(rows: Sequence[tuple[str, str, bool, int]]) -> list[str]:
    """The printout, line by line, from (relationship, upload, ready, count) rows."""
    ready = sum(n for _, _, r, n in rows if r)
    waits = sum(n for _, _, r, n in rows if not r)
    lines = [f"waiting: {ready + waits}", f"ready to check: {ready}", f"waiting for names: {waits}"]
    kinds: Counter[str] = Counter()
    for kind, _, _, n in rows:
        kinds[kind] += n
    lines += [f"kind {kind}: {n}" for kind, n in sorted(kinds.items())]
    uploads = sorted({upload for _, upload, _, _ in rows})
    for number, upload in enumerate(uploads, 1):
        mine = [(r, n) for _, u, r, n in rows if u == upload]
        label = f"upload {number}" if upload else "no upload label (queued before #2650)"
        now = sum(n for r, n in mine if r)
        later = sum(n for r, n in mine if not r)
        lines.append(f"{label}: {now} ready, {later} waiting")
    return lines


async def main_async(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("backstory_links: DATABASE_URL isn't set", file=sys.stderr)
        return 2
    db = await Database.open(url, migrate=False, max_size=2)
    try:
        store = BackstoryLinks(db)
        rows = await store.counts(args.guild, args.campaign)
        picks = await store.count_which(args.guild, args.campaign)
    finally:
        await db.close()
    print("\n".join([*report(rows), f"waiting for a pick (which one?): {picks}"]))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
