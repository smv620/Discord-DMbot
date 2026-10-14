"""The official relationships, kept in the database by version (#1189; docs/STORY_MEMORY.md,
"The official list of relationships, traceable in the database").

The definitions live in code (`ontology.CORE_PREDICATES`). At every start they are written to
`ontology_predicate_versions`: a key and version that are new are inserted, one that exists is
never rewritten. A fact records the version it was written under, so what a relationship meant
then can always be looked up. Changing a definition means raising its `version` (and
`meaning_version` when the meaning, not just the wording, changed); a test fails when the code
differs from what was released under the same version.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from dmbot.db import Database
from dmbot.memory.ontology import CORE_PREDICATES, PredicateTerm, encode_not_this


def definition(term: PredicateTerm) -> dict[str, Any]:
    """The fields stored for one definition (everything that says what it means)."""
    return {
        "key": term.key,
        "version": term.version,
        "label": term.label,
        "inverse_label": term.inverse_label,
        "description": term.description,
        "covers": list(term.covers),
        "not_this": encode_not_this(term.not_this),
        "examples": list(term.examples),
        "synonyms": list(term.synonyms),
        "subject_types": list(term.subject_types),
        "object_types": list(term.object_types),
        "is_symmetric": term.symmetric,
    }


# What a relationship MEANS: changing any of these is a new version. Its wording (label,
# description, examples) can be fixed without one.
MEANING = (
    "subject_types", "object_types", "is_symmetric", "covers", "not_this", "synonyms",
)  # fmt: skip


def meaning(term: PredicateTerm) -> dict[str, Any]:
    full = definition(term)
    return {k: full[k] for k in MEANING} | {
        "max_per_subject": term.max_per_subject,
        "conflicts_with": list(term.conflicts_with),
    }


def fingerprint(term: PredicateTerm) -> str:
    """A short hash of what the relationship means: the same meaning always gives the same
    one, so a test can tell the meaning changed without its version going up."""
    text = json.dumps(meaning(term), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def missing(term: PredicateTerm) -> list[str]:
    """What a core relationship still lacks of its full definition."""
    gaps = []
    if not term.description.strip():
        gaps.append("description")
    for name in ("covers", "not_this", "examples"):
        if not getattr(term, name):
            gaps.append(name)
    if not term.inverse_label.strip():
        gaps.append("inverse_label")
    return gaps


_INSERT = (
    "INSERT INTO ontology_predicate_versions (key, version, label,"
    " inverse_label, description, covers, not_this, examples, synonyms, subject_types,"
    " object_types, is_symmetric, created_at)"
    " VALUES (%(key)s, %(version)s, %(label)s, %(inverse_label)s,"
    " %(description)s, %(covers)s, %(not_this)s, %(examples)s, %(synonyms)s,"
    " %(subject_types)s, %(object_types)s, %(is_symmetric)s, %(created_at)s)"
    " ON CONFLICT (key, version) DO NOTHING"
)


async def seed(db: Database, clock: Callable[[], float] = time.time) -> int:
    """Write the definitions this release ships. Returns how many rows were new (0 on a
    second start). One round trip for the whole list."""
    now = int(clock())
    rows = [{**definition(p), "created_at": now} for p in CORE_PREDICATES]
    async with db.unscoped() as conn:
        before = await _count(conn)
        async with conn.cursor() as cur:
            await cur.executemany(_INSERT, rows)
        return await _count(conn) - before


async def _count(conn: Any) -> int:
    cur = await conn.execute("SELECT count(*) AS n FROM ontology_predicate_versions")
    row = await cur.fetchone()
    return int(row["n"]) if row is not None else 0


async def stored(db: Database) -> dict[tuple[str, int], dict[str, Any]]:
    """Every stored definition, by (key, version)."""
    async with db.unscoped() as conn:
        cur = await conn.execute("SELECT * FROM ontology_predicate_versions")
        return {(str(r["key"]), int(r["version"])): dict(r) for r in await cur.fetchall()}
