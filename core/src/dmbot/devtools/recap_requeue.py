"""Start one session's recap over (#2341), for dev1 on the server.

    python -m dmbot.devtools.recap_requeue GUILD_ID CAMPAIGN_ID SESSION_START_MS

Puts the recap job back in the queue with its tries and half-done work cleared, so the worker
writes it again on its next round. For a recap that failed on our side and has been fixed since;
the DM's Try agains are not used. Prints one line and never the recap's text.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence

from dmbot.db import Database
from dmbot.recaps.store import RecapStore


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="recap_requeue", description=__doc__)
    parser.add_argument("guild_id", type=int)
    parser.add_argument("campaign_id")
    parser.add_argument("session_at", type=int, help="the session's start, as the recap job has it")
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("recap_requeue: DATABASE_URL isn't set", file=sys.stderr)
        return 2
    db = await Database.open(url, migrate=False, max_size=2)
    try:
        done = await RecapStore(db).requeue(args.guild_id, args.campaign_id, args.session_at)
    finally:
        await db.close()
    print("queued again" if done else "no recap to start over (not found, posted or dropped)")
    return 0 if done else 1


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
