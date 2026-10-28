"""Recording an after-session item as "fixed" when the DM changes its connection (#2661).

A report item (a difference the party has wrong, or a contradiction) names connections. When the
DM changes one of them so that it says something else, removes it, or ends it, the item is what
the DM fixed: the DM's answer for it becomes `fixed` (the page already reads that answer), in the
same transaction as the change, from the change log of that write. Only the DM's own writes count
(the extractor's, an Undo's and a merge's repointing do not), and a secret, pin or reveal changes
nothing the story says. An item the DM already answered "intended" or "not a problem" keeps that
answer; "remind" gives way to "fixed". For a difference where The truth has no such connection
(`denied`), the DM making that connection The truth's (confirming it) fixes it too.

`answered_by` is 0: the store knows the DM's write did it, not which DM. A check that finds an item
again loses its `fixed` answer (the change was undone, or did not fix it); one that no longer
finds it takes its `fixed` answer with it. Ids and words DMbot wrote itself only: no transcript.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from dmbot.memory.report_keys import (
    CONTRADICTION,
    DIFFERENCE,
    contradiction_key,
    difference_key,
)

FIXED = "fixed"
REMIND = "remind"
SAYS = ("predicate", "detail", "directional")  # what a connection says; its ends are not changed


def changed_meaning(
    op: str, before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> bool:
    """Did this write to a connection change what it says, take it away or end it? An update is
    judged by its before and after; a delete always counts. An insert changes nothing."""
    if op == "delete":
        return before is not None
    if op != "update" or before is None or after is None:
        return False
    if before.get("status") != "rejected" and after.get("status") == "rejected":
        return True
    if before.get("to_session_at") is None and after.get("to_session_at") is not None:
        return True
    return any(before.get(k) != after.get(k) for k in SAYS)


async def names(
    conn: Any, guild_id: int, campaign_id: str, relation_ids: Collection[str]
) -> set[tuple[str, str]]:
    """The after-session items (kind, answer key) that name any of these connections. Asked
    before a delete: the items cascade away with the connection."""
    keys: set[tuple[str, str]] = set()
    ids = list(relation_ids)
    if not ids:
        return keys
    cur = await conn.execute(
        "SELECT perspective_id, relation_id FROM memory_differences"
        " WHERE guild_id = %s AND campaign_id = %s"
        " AND (relation_id = ANY(%s) OR truth_relation_id = ANY(%s))",
        (guild_id, campaign_id, ids, ids),
    )
    for r in await cur.fetchall():
        keys.add((DIFFERENCE, difference_key(r["perspective_id"], r["relation_id"])))
    cur = await conn.execute(
        "SELECT perspective_id, kind, first_relation_id, second_relation_id"
        " FROM memory_contradictions WHERE guild_id = %s AND campaign_id = %s"
        " AND (first_relation_id = ANY(%s) OR second_relation_id = ANY(%s))",
        (guild_id, campaign_id, ids, ids),
    )
    for r in await cur.fetchall():
        keys.add(
            (
                CONTRADICTION,
                contradiction_key(
                    r["perspective_id"], r["kind"], r["first_relation_id"],
                    r["second_relation_id"],
                ),
            )
        )  # fmt: skip
    return keys


async def denied_by(
    conn: Any, guild_id: int, campaign_id: str, believed: Collection[tuple[str, str]]
) -> set[tuple[str, str]]:
    """The `denied` differences The truth just took up: the party's connection now has a belief of
    The truth's, which is the DM's fix (confirming it, usually). Pairs stay pairs: a belief of
    another view in the same write fixes nothing."""
    if not believed:
        return set()
    cur = await conn.execute(
        "SELECT d.perspective_id, d.relation_id FROM memory_differences d"
        " JOIN unnest(%s::text[], %s::text[]) AS b(perspective_id, relation_id)"
        "   ON b.relation_id = d.relation_id"
        " JOIN memory_perspectives p ON p.guild_id = d.guild_id AND p.campaign_id = d.campaign_id"
        "   AND p.id = b.perspective_id AND p.kind = 'truth'"
        " WHERE d.guild_id = %s AND d.campaign_id = %s AND d.kind = 'denied'",
        ([p for p, _ in believed], [r for _, r in believed], guild_id, campaign_id),
    )
    return {
        (DIFFERENCE, difference_key(r["perspective_id"], r["relation_id"]))
        for r in await cur.fetchall()
    }


async def record(
    conn: Any, guild_id: int, campaign_id: str, keys: Collection[tuple[str, str]], now: int
) -> int:
    """Mark these items (kind, answer key) as fixed, in one statement. Another answer stands:
    only a `remind` gives way. How many items were marked."""
    if not keys:
        return 0
    ordered = sorted(set(keys))
    cur = await conn.execute(
        "INSERT INTO report_answers AS a (guild_id, campaign_id, item_key, item_kind, answer,"
        " answered_by, answered_at, remind_from)"
        " SELECT %s, %s, u.item_key, u.item_kind, %s, 0, %s, NULL"
        " FROM unnest(%s::text[], %s::text[]) AS u(item_kind, item_key)"
        " ON CONFLICT (guild_id, campaign_id, item_key) DO UPDATE SET answer = EXCLUDED.answer,"
        " answered_by = EXCLUDED.answered_by, answered_at = EXCLUDED.answered_at,"
        # the date of the "remind me" goes aside, for an Undo to put back
        " remind_was = a.remind_from, remind_from = NULL WHERE a.answer = %s",
        (
            guild_id,
            campaign_id,
            FIXED,
            now,
            [k for k, _ in ordered],
            [i for _, i in ordered],
            REMIND,
        ),
    )
    return int(cur.rowcount)


async def give_back(conn: Any, guild_id: int, campaign_id: str, keys: Collection[str]) -> int:
    """A `fixed` answer on these items was wrong (the change was undone, or the check found the
    item again): it goes, and a "remind me" it replaced comes back with its date. Any other answer
    stays."""
    if not keys:
        return 0
    cur = await conn.execute(
        "UPDATE report_answers SET answer = %s, remind_from = remind_was, remind_was = NULL"
        " WHERE guild_id = %s AND campaign_id = %s AND answer = %s AND remind_was IS NOT NULL"
        " AND item_key = ANY(%s)",
        (REMIND, guild_id, campaign_id, FIXED, list(keys)),
    )
    back = int(cur.rowcount)
    cur = await conn.execute(
        "DELETE FROM report_answers WHERE guild_id = %s AND campaign_id = %s AND answer = %s"
        " AND item_key = ANY(%s)",
        (guild_id, campaign_id, FIXED, list(keys)),
    )
    return back + int(cur.rowcount)


async def denied_names(
    conn: Any, guild_id: int, campaign_id: str, relation_ids: Collection[str]
) -> set[tuple[str, str]]:
    """The `denied` differences about these connections (what The truth taking up a connection
    fixed): the only items a belief taken away can bring back."""
    if not relation_ids:
        return set()
    cur = await conn.execute(
        "SELECT perspective_id, relation_id FROM memory_differences"
        " WHERE guild_id = %s AND campaign_id = %s AND kind = 'denied' AND relation_id = ANY(%s)",
        (guild_id, campaign_id, list(relation_ids)),
    )
    return {
        (DIFFERENCE, difference_key(r["perspective_id"], r["relation_id"]))
        for r in await cur.fetchall()
    }


async def forget_unfound(
    conn: Any, guild_id: int, campaign_id: str, item_kind: str, keep_keys: Collection[str]
) -> int:
    """A check no longer finds some items of this kind: their `fixed` answers go with them."""
    cur = await conn.execute(
        "DELETE FROM report_answers WHERE guild_id = %s AND campaign_id = %s AND answer = %s"
        " AND item_kind = %s AND item_key <> ALL(%s)",
        (guild_id, campaign_id, FIXED, item_kind, list(keep_keys)),
    )
    return int(cur.rowcount)
