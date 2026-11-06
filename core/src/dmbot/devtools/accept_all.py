"""Accepts every waiting suggestion in the public sample campaign, as the DM pressing Yes (#3544).

    python -m dmbot.devtools.accept_all --guild GUILD_ID --campaign CAMPAIGN_ID [--dry-run | --yes]

For dev1, after each deploy, so the sample lore and session do not need a person to press anything
in Discord. It goes through the store's own DM calls (the same ones the Yes buttons use), so every
rule is checked again and each change can be undone like the DM's own: the run prints the numbers
of the changes it made (lowest to highest, and how many), which is how a run is undone.
In this order:
every waiting name, every waiting other name or spelling, every waiting connection that was found
in a text (family roles and feelings included), every "Which one?" question (answered with
DMbot's own first candidate) and every same-person question (answered with the best offer). A
name that needs a person to say who plays it, a question with no suggestion, and anything a rule
refuses stay waiting and are counted.

Guard: it refuses any campaign not named "test of the frostmaiden (sample)", the same as
`graph_key_check`, and there is no flag to override that. Without `--dry-run` it needs `--yes`.
Output: counts by kind only (accepted, left waiting, refused by a rule), never a name or a word of
a text. It reads no transcript. Connect as the normal `dmbot` user, as `movers_check` does.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import Counter
from dataclasses import dataclass, field

from dmbot.db import Database
from dmbot.memory import backstory_keep
from dmbot.memory.backstory_links import DONE, BackstoryLinks
from dmbot.memory.models import CONFIRMED, DM, PLAYER_CHARACTER, MemoryRuleError
from dmbot.memory.store import MemoryStore
from dmbot.ui.names import background_kind

log = logging.getLogger(__name__)

SAMPLE_CAMPAIGN = "test of the frostmaiden (sample)"
NAMES, OTHER_NAMES, CONNECTIONS, QUESTIONS, SAME_PERSON = (
    "names", "other names", "connections", "which-one questions", "same-person questions",
)  # fmt: skip
KINDS = (NAMES, OTHER_NAMES, CONNECTIONS, QUESTIONS, SAME_PERSON)


@dataclass
class Counts:
    accepted: Counter[str] = field(default_factory=Counter)
    waiting: Counter[str] = field(default_factory=Counter)  # left for a person to answer
    refused: Counter[str] = field(default_factory=Counter)  # a rule said no (it stays waiting)
    batches: list[int] = field(default_factory=list)  # the changes made, to undo a run by hand

    def add_batch(self, batch: int | None) -> None:
        if batch is not None:
            self.batches.append(batch)

    def line(self, dry_run: bool) -> str:
        parts = [
            f"{kind}: {self.accepted[kind]} {'would be accepted' if dry_run else 'accepted'},"
            f" {self.waiting[kind]} left waiting, {self.refused[kind]} refused by a rule"
            for kind in KINDS
        ]
        made = sorted(self.batches)
        undo = f"; changes {made[0]} to {made[-1]} ({len(made)})" if made else ""
        return "accept_all: " + "; ".join(parts) + undo


class WrongCampaign(Exception):
    """The campaign is not the public sample: nothing is read or written."""


async def check_campaign(db: Database, guild_id: int, campaign_id: str) -> None:
    """Refuses any campaign that is not the public sample, before anything else is done."""
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT name FROM campaigns WHERE guild_id = %s AND id = %s", (guild_id, campaign_id)
        )
        row = await cur.fetchone()
    if row is None or str(row["name"]) != SAMPLE_CAMPAIGN:
        raise WrongCampaign(
            f"accept_all only runs on the campaign named {SAMPLE_CAMPAIGN!r}; nothing was done"
        )


async def accept_names(
    memory: MemoryStore, guild: int, campaign: str, counts: Counts, dry_run: bool
) -> None:
    """The DM's one-press Yes: only a name that was read with its kind (`background_kind`) is
    confirmed as that kind. One that needs the kind chosen, or who plays it, is for a person."""
    for entity in await memory.entities(guild, campaign, statuses=("proposed",)):
        kind = background_kind(entity)
        if not kind or entity.role == PLAYER_CHARACTER:
            counts.waiting[NAMES] += 1
            continue
        if dry_run:
            counts.accepted[NAMES] += 1
            continue
        try:
            written = await memory.confirm_entity(guild, campaign, entity.id, kind, source=DM)
            counts.accepted[NAMES] += 1
            counts.add_batch(written.batch)
        except Exception as exc:  # a rule, or anything else: counted, the run goes on
            _refused(counts, NAMES, exc)


def _refused(counts: Counts, kind: str, exc: Exception) -> None:
    counts.refused[kind] += 1
    if not isinstance(exc, MemoryRuleError):
        log.warning("accept_all: a %s could not be accepted (%s)", kind, type(exc).__name__)


async def accept_other_names(
    memory: MemoryStore, guild: int, campaign: str, counts: Counts, dry_run: bool
) -> None:
    for alias, _entry in await memory.suggested_names(guild, campaign):
        if dry_run:
            counts.accepted[OTHER_NAMES] += 1
            continue
        try:
            written = await memory.update_alias(
                guild, campaign, alias.id, source=DM, status=CONFIRMED
            )
            counts.accepted[OTHER_NAMES] += 1
            counts.add_batch(written.batch)
        except Exception as exc:
            _refused(counts, OTHER_NAMES, exc)


async def accept_connections(
    memory: MemoryStore,
    links: BackstoryLinks,
    guild: int,
    campaign: str,
    counts: Counts,
    dry_run: bool,
) -> None:
    """Keeps the connections that are ready (both names answered), as the DM's keep-all does; the
    rest wait for a name or an answer."""
    onto = await memory.ontology(guild, campaign)
    symmetric = {p.key for p in onto.predicates.values() if p.symmetric}
    every = await links.waiting(guild, campaign, symmetric=symmetric)
    ready = [w for w in every if w.ready]
    counts.waiting[CONNECTIONS] += len(every) - len(ready)
    if dry_run:
        counts.accepted[CONNECTIONS] += len(ready)
        return
    try:
        done = await backstory_keep.keep_all(memory, links, guild, campaign, ready)
    except Exception as exc:  # the connections kept before it stay kept
        counts.refused[CONNECTIONS] += len(ready)
        log.warning("accept_all: keeping connections stopped (%s)", type(exc).__name__)
        return
    counts.accepted[CONNECTIONS] += len(done.kept)
    counts.refused[CONNECTIONS] += done.refused
    for _link, batch in done.kept:
        counts.add_batch(batch)


async def accept_questions(
    memory: MemoryStore,
    links: BackstoryLinks,
    guild: int,
    campaign: str,
    counts: Counts,
    dry_run: bool,
) -> None:
    for question in await links.which_waiting(guild, campaign):
        if not question.candidates:  # no suggestion: a person decides
            counts.waiting[QUESTIONS] += 1
            continue
        if dry_run:
            counts.accepted[QUESTIONS] += 1
            continue
        try:
            answer = await links.pick_which(guild, campaign, question.id, question.candidates[0][0])
        except Exception as exc:
            _refused(counts, QUESTIONS, exc)
            continue
        if answer == DONE:
            counts.accepted[QUESTIONS] += 1
        else:
            counts.refused[QUESTIONS] += 1


async def accept_same_person(
    memory: MemoryStore, guild: int, campaign: str, counts: Counts, dry_run: bool
) -> None:
    """The best offer for each unnamed entry. The offers are read again after every join (a join
    changes the others), as the DM's list is, until a pass accepts none."""
    answered: set[str] = set()  # unnamed entries already answered (or refused: not tried twice)
    while True:
        offers = [o for o in await memory.same_person_offers(guild, campaign)
                  if o.unnamed_id not in answered]  # fmt: skip
        if not offers:
            return
        offer = offers[0]
        answered.add(offer.unnamed_id)
        if dry_run:
            counts.accepted[SAME_PERSON] += 1
            continue
        try:
            written = await memory.join_same_person(
                guild, campaign, offer.unnamed_id, offer.named_id
            )
            counts.accepted[SAME_PERSON] += 1
            counts.add_batch(written.batch)
        except Exception as exc:
            _refused(counts, SAME_PERSON, exc)


async def run(
    db: Database, guild: int, campaign: str, *, dry_run: bool, memory: MemoryStore | None = None
) -> Counts:
    """Everything waiting, accepted in that order. Names first (a connection waits on its names),
    then the questions that turn into connections, then the connections they made."""
    await check_campaign(db, guild, campaign)
    memory = memory or MemoryStore(db)
    links = BackstoryLinks(db)
    counts = Counts()
    steps = (
        (NAMES, accept_names(memory, guild, campaign, counts, dry_run)),
        (OTHER_NAMES, accept_other_names(memory, guild, campaign, counts, dry_run)),
        (QUESTIONS, accept_questions(memory, links, guild, campaign, counts, dry_run)),
        (CONNECTIONS, accept_connections(memory, links, guild, campaign, counts, dry_run)),
        (SAME_PERSON, accept_same_person(memory, guild, campaign, counts, dry_run)),
    )
    for kind, step in steps:  # one kind failing as a whole never loses the counts so far
        try:
            await step
        except Exception as exc:
            _refused(counts, kind, exc)
    return counts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument("--guild", type=int, required=True)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--dry-run", action="store_true", help="count only, change nothing")
    parser.add_argument(
        "--yes", action="store_true", help="really accept (not needed for a dry run)"
    )
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    if not args.dry_run and not args.yes:
        print("accept_all: add --yes to accept, or --dry-run to count", file=sys.stderr)
        return 2
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("accept_all: DATABASE_URL isn't set", file=sys.stderr)
        return 2
    db = await Database.open(url, migrate=False, max_size=4)
    try:
        counts = await run(db, args.guild, args.campaign, dry_run=args.dry_run)
    except WrongCampaign as exc:
        print(f"accept_all: {exc}", file=sys.stderr)
        return 2
    finally:
        await db.close()
    print(counts.line(args.dry_run))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
