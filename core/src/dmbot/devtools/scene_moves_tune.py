"""Tune the scene-move filter on a real session (#4173 slice 4; Supervisor's ruling, 2026-11-13:
at least 95% of the sample session key's moves must pass the numbers filter). dev1 runs it on the
server, where the session and its stored numbers are; it prints counts only, never a line's words.

    DATABASE_URL=... VOYAGE_API_KEY=... python -m dmbot.devtools.scene_moves_tune \\
        --guild 123 --session <session id> --dm 456 --moves 12,40,77

`--moves`: the line numbers (as the story extractor numbers them, `extractor.readable`) where the
session key says the party moved. For each threshold and margin on a small grid it prints how many
of those pass (and the share), and how many DM lines in all would be sent to the AI, so the lowest
cost that keeps 95% can be read off; then the AI calls and lines with and without the filter.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence

from dmbot.db import Database
from dmbot.memory import scene_move_step, scene_moves
from dmbot.memory.story_chain_store import readable_lines, stored_vectors
from dmbot.memory.voyage import HttpTransport, Voyage
from dmbot.transcript.store import TranscriptStore

THRESHOLDS = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60)
MARGINS = (-0.05, 0.0, 0.02, 0.05)
MODEL = "voyage-3.5"


async def main_async(args: argparse.Namespace) -> int:
    url, key = os.environ.get("DATABASE_URL", ""), os.environ.get("VOYAGE_API_KEY", "")
    if not url or not key:
        print("scene_moves_tune: DATABASE_URL and VOYAGE_API_KEY must be set", file=sys.stderr)
        return 2
    db = await Database.open(url, migrate=False, max_size=2)
    try:
        lines = await TranscriptStore(db).lines(args.guild, args.session)
        everyone = {line.user_id for line in lines}
        said = readable_lines(lines, set(), everyone)  # (counts only leave this tool)
        dm = set(args.dm)
        vectors = await stored_vectors(db, args.guild, args.session, args.model, dm)
        voyage = Voyage(HttpTransport(key), embed_model=args.model, rerank_model="rerank-2.5")
        moves_v, misses_v = await scene_move_step.example_vectors(voyage, args.model)
    finally:
        await db.close()
    dm_lines = [s for s in said if s.user_id in dm]
    keyed = set(args.moves)
    with_numbers = [s for s in dm_lines if (s.started_ms, s.user_id) in vectors]
    print(f"DM lines: {len(dm_lines)}; with numbers: {len(with_numbers)}; key moves: {len(keyed)}")
    print("threshold margin  moves passed  share  DM lines sent (with context)")
    numbers = [s.number for s in said]
    for t in THRESHOLDS:
        for m in MARGINS:
            passed = {
                s.number
                for s in dm_lines
                if (v := vectors.get((s.started_ms, s.user_id))) is None
                or scene_moves.likely(v, moves_v, misses_v, t, m)
            }
            hit = len(keyed & passed)
            share = hit / len(keyed) if keyed else 1.0
            sent = len(scene_moves.with_context(passed, numbers))
            print(f"{t:9.2f} {m:6.2f}  {hit:5d}/{len(keyed):<5d}  {share:5.0%}  {sent}")
    calls = -(-len(dm_lines) // scene_moves.LINES_PER_CALL)
    print(f"without the filter: {len(dm_lines)} DM lines asked, {calls} AI calls")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--guild", type=int, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--dm", type=int, action="append", required=True)
    parser.add_argument("--moves", type=lambda s: [int(x) for x in s.split(",") if x], default=[])
    parser.add_argument("--model", default=MODEL)
    return asyncio.run(main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
