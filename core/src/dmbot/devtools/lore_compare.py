"""One lore read, run straight through the reader, with counts only (#4382).

    ANTHROPIC_API_KEY=... python -m dmbot.devtools.lore_compare --text LORE.txt --yes

Calls `lore_read.read_lore` the way a lore upload does (`ui/name_background.py`): pass 1 on the
careful tier (`Feature.NAMES_BACKGROUND_READ`), the shaping on the names tier (`Feature.NAMES`),
both with their system text cached, three AI requests at once. It reads the text as a fresh
campaign would: no own kinds, no names already known, no other spellings, no player characters,
so two runs on the same text compare the reader's code and its instructions alone (dev1 runs it
twice, once with a changed `read_instructions` mounted over the file, and compares).

What it never does: start the bot or log in to Discord, open a database, store anything, or print
a name, a quote or a word of the text. It prints counts: names and connections found and kept, the
reader's own `Stats` (why lines were dropped, by reason), AI calls, tokens and dollars at the list
prices on file. Without `--yes` nothing is called. The key comes from ANTHROPIC_API_KEY and is
never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from dmbot.ai import FEATURE_TIERS, AnthropicClient, Feature
from dmbot.config import ConfigError, parse_ai_models
from dmbot.devtools.claims import cost
from dmbot.devtools.common import Metered
from dmbot.devtools.costs.prices import UnknownPrice, price_of
from dmbot.memory import lore_read

AT_ONCE = 3  # as `ui.name_lists.AI_AT_ONCE`: the upload's own limit


def _dollars(m: Metered) -> str:
    try:
        cost_in, cost_out = price_of(m.model)
    except UnknownPrice:
        return "n/a (no price on file)"
    usd = cost.dollars((cost_in.dollars, cost_out.dollars), m.input_tokens, m.output_tokens)
    return f"${usd:.4f}"


def render(read: lore_read.LoreRead, careful: Metered, shaping: Metered) -> list[str]:
    """Counts only: never a name, a quote or a word of the text."""
    s = read.stats
    skipped = f", skipped ({s.second_skipped})" if s.second_skipped else ""
    out = [
        f"names: {len(read.names)} kept (pass 1 listed {s.names_listed}, left out {s.left_out}, "
        f"with no relationship line {s.lonely})",
        f"connections: {len(read.links)} kept (pass 1 listed {s.links_listed}, of them "
        f"{s.pass1_links} before the second look, which added {s.extra_links}; shaping kept "
        f"{s.links_kept})",
        f"unnamed people: {len(read.unnamed)} (listed {s.unnamed_listed}); trait lines: "
        f"{len(read.trait_lines)}",
        f"cut: {read.cut}; pass 1 calls {s.read_calls} (cut {s.read_cut}, continued {s.continued},"
        f" asked again with more room {s.room_retries}); second look: {s.paragraphs_asked} "
        f"paragraphs, {s.second_calls} calls{skipped}; "
        f"shaping calls {s.shape_calls} (cut {s.shape_cut})",
        f"paragraph lines (0/1/2/3+): before {s.paragraph_lines_before or '-'}, after "
        f"{s.paragraph_lines_after or '-'}",
        "dropped: " + (", ".join(f"{k} {v}" for k, v in sorted(s.dropped.items())) or "none"),
    ]
    for label, m in (("careful (pass 1)", careful), ("names (shaping)", shaping)):
        out.append(
            f"{label}: {m.model}: {m.calls} calls, {m.input_tokens} in / {m.output_tokens} out, "
            f"{_dollars(m)}"
        )
    return out


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="lore_compare", description=__doc__)
    parser.add_argument("--text", required=True, type=Path, help="the lore, as plain text")
    parser.add_argument(
        "--players-line",
        action="store_true",
        help="ask for each name's players' line, as an upload the players know all of does",
    )
    parser.add_argument("--yes", action="store_true", help="make the paid calls")
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        models = parse_ai_models(lambda name: os.environ.get(name, ""))[0]
    except ConfigError as exc:
        print(f"lore_compare: {exc}", file=sys.stderr)
        return 2
    try:
        text = args.text.read_text(encoding="utf-8")
    except OSError as exc:  # (the type only: the message holds the path)
        print(f"lore_compare: can't read the text ({type(exc).__name__})", file=sys.stderr)
        return 2
    print(f"text: {len(text.split())} words; models: careful={models.careful} fast={models.fast}")
    if not args.yes:
        print("No calls made: --yes reads the text with the AI (it costs money).")
        return 0
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("lore_compare: ANTHROPIC_API_KEY isn't set", file=sys.stderr)
        return 2
    client = AnthropicClient(key, models)
    careful = Metered(
        client.tier(
            FEATURE_TIERS[Feature.NAMES_BACKGROUND_READ],
            Feature.NAMES_BACKGROUND_READ,
            cache_system=True,
        )
    )
    shaping = Metered(client.tier(FEATURE_TIERS[Feature.NAMES], Feature.NAMES, cache_system=True))
    try:
        read = await lore_read.read_lore(
            careful, shaping, text, {}, asyncio.Semaphore(AT_ONCE),
            players_line=args.players_line,
        )  # fmt: skip
    finally:
        await client.close()
    print("\n".join(render(read, careful, shaping)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
