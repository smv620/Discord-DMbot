"""The keys the DM's answers to the after-session report's items are kept under (#2661). Pure, and
apart from `report_store` so the pieces that must forget or record an answer (a connection the DM
changed, a check that finds an item again) can use them without a cycle."""

from __future__ import annotations

import hashlib

CONTRADICTION, DIFFERENCE = "contradiction", "difference"


def contradiction_key(perspective_id: str, kind: str, first_id: str, second_id: str) -> str:
    """The answer key of a contradiction item: a hash of its own ids (all hex, never a word). The
    two connections are in the order the grade table keeps them (the smaller id first, a CHECK
    there), so the same clash always has the same key."""
    return _key(CONTRADICTION, perspective_id, kind, first_id, second_id)


def difference_key(perspective_id: str, relation_id: str) -> str:
    return _key(DIFFERENCE, perspective_id, relation_id)


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]
