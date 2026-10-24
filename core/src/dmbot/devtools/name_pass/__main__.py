"""PlotBot's name pass on a stored session, from the command line (#2628), for dev1's check on
the owner's imported Test M session and any other.

    python -m dmbot.devtools.name_pass --guild GUILD --session SESSION_ID [--dry-run] [--yes]

It runs the real pass (`transcript.name_pass_run`: the same candidates, the same one question to
PlotBot, the same narrow writes into the cleaned text) and prints what it did for each name:

    Kethrandil: corrected 8 times
      (4) Keth and Dill
    Not sure (asked the DM): 2
    New names (asked the DM): 1
    Left as heard (secret-name guard): 0

then one line of counts only. `--dry-run` writes nothing: the same fixes are counted on a copy of
the lines. Without `--yes` it does not call the AI: it only says how many words code found to ask
about (a number, no words). A run without `--dry-run` writes the fixes into the cleaned text, with
the same Undo numbers as a live session (the DM sees them on the DM screen only for a live
session; here, the numbers are not shown).

**Privacy:** the spellings in the report are words from a transcript. They go to this terminal
only; the tool never logs them. Paste nothing from the report into GitHub: only the last line, the
counts (`names, fixes, unsure, new, guarded`), belongs in the testing logs. The AI key comes from
the environment and is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Sequence

from dmbot.transcript import name_pass as np


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="name_pass", description=__doc__)
    parser.add_argument("--guild", type=int, required=True, help="the Discord server's number")
    parser.add_argument("--session", required=True, help="the stored session's ID")
    parser.add_argument("--dry-run", action="store_true", help="write nothing")
    parser.add_argument("--yes", action="store_true", help="call the AI (it costs a little)")
    return parser.parse_args(argv)


def output(report: np.Report, *, dry_run: bool) -> list[str]:
    """What the tool prints: the report, then the counts line (the only part for the logs)."""
    head = "DRY RUN: nothing was written.\n" if dry_run else ""
    return [head + np.report_text(report), "", "Counts: " + np.counts_line(report)]


async def main_async(args: argparse.Namespace) -> int:
    from dmbot.ai import FEATURE_TIERS, AnthropicClient, Feature
    from dmbot.config import ConfigError, parse_ai_models
    from dmbot.db import Database
    from dmbot.memory.lookup import LookupCache
    from dmbot.memory.store import MemoryStore
    from dmbot.transcript import name_pass_run
    from dmbot.transcript.store import TranscriptStore

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("name_pass: DATABASE_URL isn't set", file=sys.stderr)
        return 2
    db = await Database.open(url, migrate=False, max_size=2)
    try:
        transcripts = TranscriptStore(db)
        session = await transcripts.session(args.guild, args.session)
        if session is None:
            print("name_pass: no such session in that server", file=sys.stderr)
            return 2
        memory = MemoryStore(db)
        lookup = await LookupCache(memory).get(args.guild, session.campaign_id)
        if not args.yes:
            texts = [x.text for x in await transcripts.lines(args.guild, args.session)]
            found = np.survey(texts, lookup)
            print(
                f"{len(found.items)} words to ask the AI about (one call), {found.new} new names, "
                f"{found.guarded} kept as heard. Nothing was run. Add --yes to call the AI."
            )
            return 0
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            print("name_pass: ANTHROPIC_API_KEY isn't set", file=sys.stderr)
            return 2
        try:
            models = parse_ai_models(lambda name: os.environ.get(name, ""))[0]
        except ConfigError as exc:
            print(f"name_pass: {exc}", file=sys.stderr)
            return 2
        ai = AnthropicClient(key, models)
        client = ai.tier(FEATURE_TIERS[Feature.NAME_PASS], Feature.NAME_PASS)

        async def ask(system: str, question: str) -> str:
            reply = await client.complete(system, question, max_tokens=2000, schema=np.SCHEMA)
            return str(reply.text)

        try:
            now = int(time.time())
            report = await name_pass_run.run(
                transcripts, lookup, ask, args.guild, session.campaign_id, args.session,
                now=now, forget_before=now - int(memory.keep_days * 86400),
                dry_run=args.dry_run,
            )  # fmt: skip
        finally:
            await ai.close()
        print("\n".join(output(report, dry_run=args.dry_run)))
        return 0
    finally:
        await db.close()


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
