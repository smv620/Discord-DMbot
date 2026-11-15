"""The parts of a story thread (#4471, quests slice 2; design `docs/design/quests.md` §4 to §5b):
objectives, complications, rewards promised and given, help given or lent, and time-limit words,
one row each in `memory_thread_parts` beside the thread entry (migration 0504).

The rules kept here, for every caller:
- **Only the DM writes them in this slice**, as confirmed rows; the readers that suggest parts as
  "read" come in slice 3 (§13 rule 1).
- **A correction is made in place; a change in the story is history** (§4, the rule ruled for route
  condition on #4258). Changing a part's words, who or what it names, its terms or when a reward is
  due edits the row (one logged change, one Undo). A new state ("done", "overcome", "settled",
  "returned") closes the old row at the story's time and opens a new one, so what it was stays.
  Removing a part rejects it; it never becomes history.
- **Nothing worked out is stored** (§13 rule 4): how much is still owed, how many objectives are
  done, whether a time limit passed. `owed` and `done_count` work them out when shown.
- **One reader** (`parts_for`) applies what players may see (§10.2): a part of a thread the player
  may see, kept by the DM and shown to the table, naming only entries the player may see.
  `test_thread_part_readers` holds every other file to it.

Only ids, counts and kinds are logged, never a part's words."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory import gate
from dmbot.memory._changes import ENTITIES, THREAD_PARTS, Changes, Scope
from dmbot.memory.models import CONFIRMED, REJECTED, MemoryRuleError, new_id
from dmbot.memory.ontology import Ontology

OBJECTIVE, COMPLICATION, REWARD, REWARD_GIVEN, HELP, TIME_LIMIT = (
    "objective", "complication", "reward", "reward_given", "help", "time_limit",
)  # fmt: skip
KINDS = (OBJECTIVE, COMPLICATION, REWARD, REWARD_GIVEN, HELP, TIME_LIMIT)
# Each kind's states (design §5), the first one a new part starts in. "Passed" for a time limit and
# "partly given" for a reward are worked out, never stored (§13 rule 4).
STATES: Mapping[str, tuple[str, ...]] = {
    OBJECTIVE: ("to_do", "done", "failed", "not_needed"),
    COMPLICATION: ("in_the_way", "overcome", "no_longer_matters"),
    REWARD: ("owed", "settled", "wont_be_given", "turned_down"),
    REWARD_GIVEN: ("given",),
    HELP: ("to_keep", "lent", "returned", "not_returned"),
    TIME_LIMIT: ("running", "met", "lifted"),
}
# Help starts as "to keep" unless the words say it must come back (§5b: DMbot never assumes a
# debt); the DM picks "lent" when it was lent.
START_STATES = {HELP: ("to_keep", "lent")}
UP_FRONT, AFTERWARDS = "up_front", "afterwards"
SOFT, HARD = "soft", "hard"
READ, REJECTED_PART = "read", "rejected"
DM_SOURCE, LORE, SESSION = "dm", "lore", "session"
WORDS_MAX, TERMS_MAX, UNIT_MAX = 200, 200, 30
AMOUNT_MAX = 1_000_000_000.0  # (the table's own limit)

NOT_A_THREAD = "Only a story thread the DM has kept has parts."
SAME_PART = "This story thread already has that."
GONE = "That part is gone already: it was changed or taken off meanwhile."
WRONG_UNDER = "That can only go under an objective of the same thread, one level deep."
WRONG_PROMISE = "A reward given can only count against a reward promised in the same thread."
NOT_WHO = "Pick a character or a group."
NO_WORDS = "Say what it is, in a few words."
ONLY_DM = "Only the DM can do that."
NOT_A_STATE = "That isn't one of its choices."

# "100 gold", "50 gp", "1,000 gold pieces", "2.5 silver": a number and the word after it
_AMOUNT = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s+([a-z][a-z'-]*)", re.I)


@dataclass(frozen=True, slots=True)
class Part:
    """One part as the caller may show it. `who_id` / `about_id` are blank when the viewer may not
    see that entry; `closed` is the story time it became history (None while it stands)."""

    id: str
    thread_id: str
    kind: str
    words: str
    state: str
    under_id: str | None
    who_id: str | None
    about_id: str | None
    amount: float | None
    unit: str
    position: int | None
    due: str
    terms: str
    limit_kind: str
    status: str
    source: str
    table_knows: bool
    since: int | None
    closed: int | None


def read_amount(words: str) -> tuple[float | None, str]:
    """A reward's number and unit when its words give exactly one ("100 gold" → 100, "gold"), so a
    promise can be counted against what was given (§5a). Words with no number, or with two, give
    none: "50 now and 50 later" is two promises, which the DM writes as two."""
    found = _AMOUNT.findall(words)
    if len(found) != 1:
        return None, ""
    number, unit = found[0]
    value = float(number.replace(",", ""))
    if not 0 < value <= AMOUNT_MAX:
        return None, ""
    return value, unit.lower()[:UNIT_MAX]


def owed(promise: Part, given: Iterable[Part]) -> float | None:
    """How much of a promise is still owed, when it and what was given against it count in the
    same unit (§5a: "50 gold still owed"); None when it can't be counted. Worked out, never
    stored."""
    if promise.amount is None:
        return None
    against = [g for g in given if g.under_id == promise.id and g.closed is None]
    if any(g.amount is None or g.unit != promise.unit for g in against):
        return None
    left = promise.amount - sum(g.amount or 0.0 for g in against)
    return max(left, 0.0)


def done_count(parts: Iterable[Part]) -> tuple[int, int]:
    """(done, all) of a thread's standing objectives, sub-steps included: worked out when shown."""
    standing = [p for p in parts if p.kind == OBJECTIVE and p.closed is None]
    return sum(1 for p in standing if p.state == "done"), len(standing)


def from_row(row: Mapping[str, Any]) -> Part:
    amount = row["amount"]
    return Part(
        str(row["id"]), str(row["thread_id"]), str(row["kind"]), str(row["words"]),
        str(row["state"]), row["under_id"], row["who_id"], row["about_id"],
        None if amount is None else float(amount), str(row["unit"]), row["position"],
        str(row["due"]), str(row["terms"]), str(row["limit_kind"]), str(row["status"]),
        str(row["source"]), bool(row["table_knows"]), row["from_session_at"],
        row["to_session_at"],
    )  # fmt: skip


_OPEN = " AND status <> 'rejected' AND to_session_at IS NULL"


async def story_now(w: Changes) -> int:
    """The story's time for a change said now: the campaign's latest session's start, or, before
    any session, the campaign's own start (the rule ruled for route condition on #4258)."""
    cur = await w.conn.execute(
        "SELECT COALESCE((SELECT max(started_at) FROM transcript_sessions"
        " WHERE guild_id = %(g)s AND campaign_id = %(c)s),"
        " (SELECT created_at FROM campaigns WHERE guild_id = %(g)s AND id = %(c)s)) AS at",
        {"g": w.guild_id, "c": w.campaign_id},
    )
    row = await cur.fetchone()
    return int(row["at"]) if row is not None and row["at"] is not None else int(w.now)


async def _thread(w: Scope, onto: Ontology, thread_id: str) -> Mapping[str, Any]:
    row = await w.get(ENTITIES, thread_id, lock=False)
    if row is None or row["status"] != CONFIRMED or not onto.is_a(str(row["type"]), "thread"):
        raise MemoryRuleError(NOT_A_THREAD)
    return row


async def _open_part(w: Changes, part_id: str) -> dict[str, Any]:
    row = await w.get(THREAD_PARTS, part_id)
    if row is None or row["status"] == REJECTED_PART or row["to_session_at"] is not None:
        raise MemoryRuleError(GONE)
    return row


async def _check_names(
    w: Scope, onto: Ontology, kind: str, who_id: str | None, about_id: str | None
) -> None:
    """Who promised, gave or lent it is a character or a group; what it names is a live entry."""
    if who_id is not None:
        if kind not in (REWARD, REWARD_GIVEN, HELP):
            raise MemoryRuleError(NOT_WHO)
        who = await w.get(ENTITIES, who_id, lock=False)
        if (
            who is None
            or who["status"] == REJECTED
            or not any(onto.is_a(str(who["type"]), k) for k in ("character", "faction"))
        ):
            raise MemoryRuleError(NOT_WHO)
    if about_id is not None:
        about = await w.get(ENTITIES, about_id, lock=False)
        if about is None or about["status"] == REJECTED:
            raise MemoryRuleError(GONE)


async def _check_under(w: Changes, thread_id: str, kind: str, under_id: str | None) -> None:
    """A sub-step goes under a standing top-level objective of the same thread (one level deep,
    §4); a reward given counts against a standing promise of the same thread (§5a)."""
    if under_id is None:
        return
    parent = await w.get(THREAD_PARTS, under_id, lock=False)
    standing = (
        parent is not None
        and parent["thread_id"] == thread_id
        and parent["status"] != REJECTED_PART
        and parent["to_session_at"] is None
    )
    if kind == OBJECTIVE:
        if not standing or parent is None or parent["kind"] != OBJECTIVE or parent["under_id"]:
            raise MemoryRuleError(WRONG_UNDER)
    elif kind == REWARD_GIVEN:
        if not standing or parent is None or parent["kind"] != REWARD:
            raise MemoryRuleError(WRONG_PROMISE)
    else:
        raise MemoryRuleError(WRONG_UNDER)


def _clean(words: str, most: int) -> str:
    return " ".join(words.split())[:most]


async def _same_open(
    w: Scope, thread_id: str, kind: str, words: str, *, skip: str | None = None
) -> bool:
    rows = await w.select(
        THREAD_PARTS,
        " AND thread_id = %s AND kind = %s AND lower(words) = lower(%s)" + _OPEN,
        [thread_id, kind, words],
    )
    return any(str(r["id"]) != skip for r in rows)


async def add(
    w: Changes,
    onto: Ontology,
    thread_id: str,
    kind: str,
    words: str,
    *,
    state: str | None = None,
    under_id: str | None = None,
    who_id: str | None = None,
    about_id: str | None = None,
    due: str = "",
    terms: str = "",
    limit_kind: str = "",
) -> dict[str, Any]:
    """The DM adds a part to a kept thread: confirmed, hidden from players until the DM shows it
    (§10.2: false for the DM's own typing). An objective goes to the end of its list."""
    if kind not in KINDS:
        raise ValueError(f"Not a kind of part: {kind}")
    text = _clean(words, WORDS_MAX)
    if not text:
        raise MemoryRuleError(NO_WORDS)
    start = state or STATES[kind][0]
    if start not in START_STATES.get(kind, STATES[kind][:1]):
        raise MemoryRuleError(NOT_A_STATE)
    if due not in ("", UP_FRONT, AFTERWARDS) or (due and kind != REWARD):
        raise ValueError("A due time is a promised reward's only.")
    if limit_kind not in ("", SOFT, HARD) or (limit_kind and kind != TIME_LIMIT):
        raise ValueError("Soft or hard is a time limit's only.")
    if terms and kind != HELP:
        raise ValueError("Terms are help's only.")
    await _thread(w, onto, thread_id)
    await _check_under(w, thread_id, kind, under_id)
    await _check_names(w, onto, kind, who_id, about_id)
    if await _same_open(w, thread_id, kind, text):
        raise MemoryRuleError(SAME_PART)
    amount, unit = read_amount(text) if kind in (REWARD, REWARD_GIVEN) else (None, "")
    position = None
    if kind == OBJECTIVE:
        siblings = await w.select(
            THREAD_PARTS,
            " AND thread_id = %s AND kind = 'objective' AND under_id IS NOT DISTINCT FROM %s"
            + _OPEN,
            [thread_id, under_id],
        )
        position = 1 + max((int(r["position"] or 0) for r in siblings), default=-1)
    return await w.insert(
        THREAD_PARTS,
        {
            "id": new_id(), "thread_id": thread_id, "kind": kind, "words": text, "state": start,
            "under_id": under_id, "who_id": who_id, "about_id": about_id, "amount": amount,
            "unit": unit, "position": position, "due": due,
            "terms": _clean(terms, TERMS_MAX), "limit_kind": limit_kind, "due_minute": None,
            "due_from": "", "from_session_at": await story_now(w), "to_session_at": None,
            "from_game_time": None, "to_game_time": None, "status": CONFIRMED,
            "source": DM_SOURCE, "table_knows": False, "created_at": w.now,
        },
    )  # fmt: skip


_EDITABLE = ("words", "who_id", "about_id", "due", "terms", "limit_kind", "under_id")


async def correct(w: Changes, onto: Ontology, part_id: str, **changes: Any) -> dict[str, Any]:
    """The DM changes a standing part from its card. Supervisor's boundary on #4471: rewording that
    doesn't change what is true, who or what it names, and where it sits are corrected in place
    (never history); a change to what is true (a reward's amount through its new words, when it is
    due, a loan's terms, a soft limit made hard) is a change in the story, so the old row closes and
    a new one opens, as a tick does (`_story_change`). When in doubt, a change in the story."""
    unknown = set(changes) - set(_EDITABLE)
    if unknown:
        raise ValueError(f"Not a correction: {sorted(unknown)}")
    row = await _open_part(w, part_id)
    kind, thread_id = str(row["kind"]), str(row["thread_id"])
    new: dict[str, Any] = {}
    if "words" in changes:
        text = _clean(str(changes["words"]), WORDS_MAX)
        if not text:
            raise MemoryRuleError(NO_WORDS)
        if await _same_open(w, thread_id, kind, text, skip=part_id):
            raise MemoryRuleError(SAME_PART)
        new["words"] = text
        if kind in (REWARD, REWARD_GIVEN):
            new["amount"], new["unit"] = read_amount(text)
    if "due" in changes:
        if kind != REWARD or changes["due"] not in ("", UP_FRONT, AFTERWARDS):
            raise ValueError("A due time is a promised reward's only.")
        new["due"] = changes["due"]
    if "terms" in changes:
        if kind != HELP:
            raise ValueError("Terms are help's only.")
        new["terms"] = _clean(str(changes["terms"]), TERMS_MAX)
    if "limit_kind" in changes:
        if kind != TIME_LIMIT or changes["limit_kind"] not in ("", SOFT, HARD):
            raise ValueError("Soft or hard is a time limit's only.")
        new["limit_kind"] = changes["limit_kind"]
    if "under_id" in changes:
        under = changes["under_id"]
        if under == part_id:
            raise MemoryRuleError(WRONG_UNDER if kind == OBJECTIVE else WRONG_PROMISE)
        if kind == OBJECTIVE and under is not None:
            kids = await w.select(THREAD_PARTS, " AND under_id = %s" + _OPEN, [part_id])
            if kids:  # (one level deep: an objective with sub-steps stays at the top)
                raise MemoryRuleError(WRONG_UNDER)
        await _check_under(w, thread_id, kind, under)
        new["under_id"] = under
    who = changes.get("who_id", row["who_id"])
    about = changes.get("about_id", row["about_id"])
    if "who_id" in changes or "about_id" in changes:
        await _check_names(w, onto, kind, who, about)
        new["who_id"], new["about_id"] = who, about
    new = {k: v for k, v in new.items() if row[k] != v}
    if new.keys() & _STORY_FIELDS:
        return await _story_change(w, row, new)
    return await w.update(THREAD_PARTS, part_id, new) if new else row


# What changing says something new happened in the story, not that the words were wrong (#4471).
_STORY_FIELDS = frozenset({"state", "amount", "unit", "due", "terms", "limit_kind"})


async def _story_change(
    w: Changes, row: Mapping[str, Any], new: Mapping[str, Any]
) -> dict[str, Any]:
    """The old row closes at the story's time (never before it began) and a new one opens with the
    changes; what stood under the old row (sub-steps, rewards given against a promise) moves to the
    new one. §4's history rule, ruled for route condition on #4258."""
    part_id = str(row["id"])
    start = row["from_session_at"]
    at = await story_now(w)
    at = at if start is None else max(at, int(start))
    await w.update(THREAD_PARTS, part_id, {"to_session_at": at})
    made = await w.insert(
        THREAD_PARTS,
        {
            **{c: row[c] for c in THREAD_PARTS.columns},
            **new,
            "id": new_id(), "from_session_at": at, "to_session_at": None, "created_at": w.now,
        },
    )  # fmt: skip
    await _move_under(w, part_id, str(made["id"]))
    return made


async def set_state(w: Changes, part_id: str, state: str) -> dict[str, Any]:
    """A change in the story (§4): the old row closes at the story's time and a new one opens with
    the new state; what stood under the old row (sub-steps, rewards given against a promise) moves
    to the new one. The same state again changes nothing."""
    row = await _open_part(w, part_id)
    kind = str(row["kind"])
    if state not in STATES[kind]:
        raise MemoryRuleError(NOT_A_STATE)
    if state == row["state"]:
        return row
    return await _story_change(w, row, {"state": state})


async def _move_under(w: Changes, old_id: str, new_id_: str | None) -> None:
    """What stood under one row (sub-steps, rewards given) moves to another, or to the top."""
    kids = await w.select(THREAD_PARTS, " AND under_id = %s" + _OPEN, [old_id])
    await w.update_rows(THREAD_PARTS, {str(k["id"]): {"under_id": new_id_} for k in kids})


async def remove(w: Changes, part_id: str) -> dict[str, Any]:
    """The DM says it was wrong: the part is rejected, never history (§4). A sub-step under it moves
    to the top, and a reward given against it stays as given, not matched (§5a)."""
    await _open_part(w, part_id)
    await _move_under(w, part_id, None)
    return await w.update(THREAD_PARTS, part_id, {"status": REJECTED_PART})


async def show(w: Changes, part_id: str, shown: bool) -> bool:
    """The DM's 👁️ / 🙈 for one part (§10.2). The rows it closed go with it, so what players are
    told it was matches what they are told it is. True when anything changed."""
    row = await _open_part(w, part_id)
    rows = await w.select(
        THREAD_PARTS,
        " AND thread_id = %s AND kind = %s AND lower(words) = lower(%s) AND status <> 'rejected'",
        [row["thread_id"], row["kind"], row["words"]],
    )
    changed = {str(r["id"]): {"table_knows": shown} for r in rows if r["table_knows"] != shown}
    await w.update_rows(THREAD_PARTS, changed)
    return bool(changed)


async def parts_for(
    scope: Scope,
    thread_ids: Sequence[str],
    viewer: gate.Viewer,
    *,
    history: bool = False,
    shown: Collection[str] | None = None,
) -> list[Part]:
    """The parts of these threads as `viewer` may see them, the one reader of the table (§10.2,
    §13 rule 5; `test_thread_part_readers`). The DM sees every part not taken off (waiting reads
    too, once slice 3 writes them). A player sees a part only when its thread is one they may see,
    the DM kept it and showed it to the table; a person or thing it names they may not see is left
    blank. `shown`: the entries the caller's own list shows that player, when it has worked them
    out. `history`: closed rows too, oldest first after the standing ones."""
    if not thread_ids:
        return []
    rows = await scope.select(
        THREAD_PARTS,
        " AND thread_id = ANY(%s) AND status <> 'rejected'"
        + ("" if history else " AND to_session_at IS NULL")
        + " ORDER BY thread_id, kind, COALESCE(position, 0), created_at, id",
        [list(thread_ids)],
    )
    parts = [from_row(r) for r in rows]
    if viewer.dm:
        return parts
    if shown is None:
        seen = await gate.visible_to(scope, viewer)
        names = {p.thread_id for p in parts} | {
            i for p in parts for i in (p.who_id, p.about_id) if i is not None
        }
        known = {i for i in names if seen.knows(gate.ENTRY, i)}
    else:
        known = set(shown)
    threads = {  # (a thread the player may see, kept by the DM)
        str(r["id"])
        for r in await scope.select(
            ENTITIES,
            " AND id = ANY(%s) AND status = 'confirmed'",
            [sorted(known & {p.thread_id for p in parts})],
        )
    }
    out: list[Part] = []
    for p in parts:
        if p.thread_id not in threads or p.status != CONFIRMED or not p.table_knows:
            continue
        out.append(_blank(p, who=p.who_id in known, about=p.about_id in known))
    return out


def _blank(p: Part, *, who: bool, about: bool) -> Part:
    if who and about:
        return p
    fields = {s: getattr(p, s) for s in Part.__slots__}
    fields["who_id"] = p.who_id if who else None
    fields["about_id"] = p.about_id if about else None
    return Part(**fields)


async def move_on_merge(w: Changes, keep_id: str, gone_id: str) -> None:
    """Two entries said to be one (§4): the parts of a thread merged away move to the kept thread,
    and what named the gone entry names the kept one. A standing part that would say the same as
    one the kept thread has is taken off (rejected, logged) and what stood under it moves across,
    so one open row per thread, kind and words holds. One read when nothing names the gone entry
    (a merge's statement budget, #164)."""
    rows = await w.select(
        THREAD_PARTS,
        " AND (thread_id = %s OR who_id = %s OR about_id = %s)",
        [gone_id, gone_id, gone_id],
    )
    if not rows:
        return
    moving = [r for r in rows if r["thread_id"] == gone_id]
    theirs = {}
    if moving:
        theirs = {
            (str(r["kind"]), str(r["words"]).lower()): str(r["id"])
            for r in await w.select(THREAD_PARTS, " AND thread_id = %s" + _OPEN, [keep_id])
        }
    for r in moving:
        key = (str(r["kind"]), str(r["words"]).lower())
        if r["status"] != REJECTED_PART and r["to_session_at"] is None and key in theirs:
            await _move_under(w, str(r["id"]), theirs[key])
            await w.update(THREAD_PARTS, str(r["id"]), {"status": REJECTED_PART})
    await w.update_rows(
        THREAD_PARTS,
        {
            str(r["id"]): {
                c: keep_id for c in ("thread_id", "who_id", "about_id") if r[c] == gone_id
            }
            for r in rows
        },
    )


async def drop_on_kind_change(w: Changes, ids: Sequence[str], is_thread: bool) -> None:
    """An entry that is no longer a thread has no parts: they are deleted (logged, so Undo of the
    kind change brings them back), sub-steps first."""
    if is_thread or not ids:
        return
    rows = await w.select(THREAD_PARTS, " AND thread_id = ANY(%s)", [list(ids)])
    for r in sorted(rows, key=lambda r: r["under_id"] is None):  # (what stands under first)
        await w.delete(THREAD_PARTS, str(r["id"]))
