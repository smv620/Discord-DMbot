"""The wizard's house rules (#4211): the house-rule documents a DM added in step 7 (a file, or the
typed box, which the page stores as a document too) are read into the new campaign through the
same file reader and the same adder as the House rules page's upload, so the numbering, limits and
words are the page's. No Discord here.

A document with numbered lines ("12. Potions are a bonus action.") is read as the house-rules
file format. A plain list, one rule a line, is numbered in order (the typed box's shape), because a
DM with no file is not sent away. Wizard imports only ever ADD: the campaign is new, and a retaken
run meets rules already added, which `apply` skips by their words. Counts only in logs and notes,
never a rule's words.
"""

from __future__ import annotations

import logging
from typing import Any

from dmbot.rules import house_file, house_file_sync

log = logging.getLogger(__name__)

HOUSE_UNREADABLE = "house_unreadable"  # a document had no rule that could be read


def numbered(text: str) -> str:
    """The text as the house-rules file format: unchanged when it has a numbered line, else every
    line that is not blank or a `#` note becomes the next numbered rule."""
    lines = text.lstrip("\ufeff").splitlines()
    if house_file.parse(text).rules:
        return text
    rules = [_plain(ln) for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    rules = [rule for rule in rules if rule]
    return "\n".join(f"{n}. {rule}" for n, rule in enumerate(rules, 1))


def _plain(line: str) -> str:
    """One line of a plain list without its bullet or "1)" marker."""
    line = line.strip().lstrip("-*\u2022 ").strip()
    head, mark, rest = line.partition(")")
    return rest.strip() if mark and head.isdigit() else line


async def read_documents(
    store: Any, guild_id: int, campaign_id: str, user_id: int, docs: list[dict[str, Any]]
) -> tuple[int, int, int]:
    """Read each document into the campaign. Returns (rules added, rules skipped, documents that
    held no readable rule). One document's trouble never stops the next."""
    added = skipped = unreadable = 0
    for doc in docs:
        parsed = house_file.parse(numbered(str(doc["text"])))
        if not parsed.rules:
            unreadable += 1
            continue
        # Every rule not already there (by its words) is an ADD: a second document's "1." must not
        # be taken for a change to the first document's rule 1. A number already used makes the
        # store give the next free one (house_file_sync, "numbers are for good").
        present = {(r.rule, r.supersedes) for r in await store.list(guild_id, campaign_id)}
        items = [
            house_file_sync.Item(house_file_sync.ADD, r.number, r.rule, r.supersedes)
            for r in parsed.rules
            if (r.rule, r.supersedes) not in present
        ]
        done = await house_file_sync.apply(
            store, guild_id, campaign_id, user_id, items,
            source="upload", source_name=str(doc.get("name") or "")[:200] or None,
        )  # fmt: skip
        skipped += len(done.skipped)
        added += len(items) - len(done.skipped)
    log.info(
        "A wizard run read %d house rule document(s): %d added, %d skipped, %d unreadable",
        len(docs),
        added,
        skipped,
        unreadable,
    )  # counts only
    return added, skipped, unreadable
