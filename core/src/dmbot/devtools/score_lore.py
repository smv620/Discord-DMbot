"""Scores one lore read against an answer key, pair by pair, with counts only (#2757).

    python -m dmbot.devtools.score_lore --text DOC.txt --reply REPLY.json --key KEY.json

`DOC.txt` is the document that was read, `REPLY.json` is what the AI answered for it (one answer,
or a JSON list of answers, each in the lore read's own shape: `names` and `connections`), and
`KEY.json` lists the pairs a person expects (`{"aliases": {"Full Name": ["Other"]}, "pairs":
[{"id": "p01", "a": "...", "b": "...", "kind": "sibling"}]}`; `kind` is optional).

For each key pair it says where the pair was lost, by running the same checks the read runs
(`background.read_answer`, `read_connections`, `keep_connections`), so a change to the prompt or
the checks gets a number: `kept`, `held_for_who_is_it`, `dropped:<reason>`, `not_listed` (the AI
never wrote the pair) or `no_entry` (an end is not among the names the read kept). No AI is
called and nothing is stored. Only the pair's id and a status are printed, never a name, a quote
or a word of the document, so a run on a real document can be pasted into an issue. Pure apart
from reading the three files.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dmbot.memory import background as bg
from dmbot.memory.end_match import SHORTEST
from dmbot.memory.models import name_key

KEPT = "kept"
HELD = "held_for_who_is_it"
NOT_LISTED = "not_listed"
NO_ENTRY = "no_entry"
# How far a pair got, worst first: a pair with several lines is judged by its furthest one.
_RANK = {NO_ENTRY: 0, NOT_LISTED: 1, "dropped": 2, HELD: 3, KEPT: 4}


@dataclass(frozen=True, slots=True)
class Pair:
    id: str
    a: str
    b: str
    kind: str = ""  # the role or kind the person expects ("sibling", "mentor"), or ""


@dataclass(frozen=True, slots=True)
class Score:
    pair: str
    status: str  # one of the constants above, or "dropped:<reason>"
    kind_matches: bool | None = None  # for a kept pair with an expected kind


def _same(end: str, other: str) -> bool:
    """Whether two ends are one name: the same words, or one holds all the other's (a first name
    for the full name). A one-word match needs three letters, so "Al" matches nothing."""
    a, b = name_key(end).split(), name_key(other).split()
    if not a or not b:
        return False
    if a == b:
        return True
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return all(len(w) >= SHORTEST and w in big for w in small)


def _group(found: Sequence[bg.Found], aliases: Mapping[str, Sequence[str]]) -> list[set[str]]:
    """Each entry's names as a set: its name, its other and secret spellings, and the key's own."""
    out: list[set[str]] = []
    for f in found:
        names = {f.name, *f.also, *f.secret}
        for full, others in aliases.items():
            if any(_same(full, n) for n in names):
                names |= {full, *others}
        out.append(names)
    return out


def _is(end: str, names: set[str]) -> bool:
    return any(_same(end, n) for n in names)


def _replies(raw: object) -> list[str]:
    """The AI's answers as strings: one object, one string, or a list of either."""
    items = raw if isinstance(raw, list) else [raw]
    return [x if isinstance(x, str) else json.dumps(x) for x in items]


def score(
    text: str, replies: Sequence[str], pairs: Sequence[Pair], aliases: Mapping[str, Sequence[str]]
) -> list[Score]:
    """Where each key pair was lost, or that it was kept."""
    found: list[bg.Found] = []
    for reply in replies:
        found.extend(bg.read_answer(reply, text))
    groups = _group(found, aliases)
    alias_keys = {name_key(f.name): [name_key(x) for x in f.also] for f in found}
    for full, others in aliases.items():
        alias_keys.setdefault(name_key(full), []).extend(name_key(x) for x in others)
    lines: list[dict[str, Any]] = []
    for reply in replies:
        data = bg._json(reply)  # (the read's own tolerant parser)
        items = data.get("connections") if isinstance(data, dict) else None
        lines.extend(x for x in items or [] if isinstance(x, dict))
    # Each line is read alone, so the reason it was lost is its own.
    status: list[tuple[dict[str, Any], str, bg.Connection | None]] = []
    passed: list[bg.Connection] = []
    for line in lines:
        lost: Counter[str] = Counter()
        held: list[bg.Unsettled] = []
        got = bg.read_connections(
            json.dumps({"connections": [line]}), text, dropped=lost, aliases=alias_keys,
            unsettled=held,
        )  # fmt: skip
        if got:
            passed.append(got[0])
            status.append((line, "", got[0]))
        elif held:
            status.append((line, HELD, None))
        else:
            reason = next(iter(lost), "unreadable")
            status.append((line, f"dropped:{reason}", None))
    joint = bg.keep_connections([passed], found, {})
    kept_keys = {frozenset((name_key(c.a), name_key(c.b))) for c in joint}
    alone: dict[int, str] = {}
    for c in passed:
        lost = Counter()
        one = bg.keep_connections([[c]], found, {}, dropped=lost)
        alone[id(c)] = "" if one else f"dropped:{next(iter(lost), 'name_not_found')}"
    results: list[Score] = []
    for p in pairs:
        ends_a = next((g for g in groups if _is(p.a, g)), None)
        ends_b = next((g for g in groups if _is(p.b, g)), None)
        if ends_a is None or ends_b is None:
            results.append(Score(p.id, NO_ENTRY))
            continue
        best, best_conn = NOT_LISTED, None
        for line, state, conn in status:
            la, lb = str(line.get("a", "")), str(line.get("b", ""))
            if not ((_is(la, ends_a) and _is(lb, ends_b)) or (_is(la, ends_b) and _is(lb, ends_a))):
                continue
            if conn is not None:
                reached = alone[id(conn)]
                key = frozenset((name_key(conn.a), name_key(conn.b)))
                if reached:
                    state = reached
                elif key not in kept_keys:
                    state = "dropped:same_pair"  # (kept alone, not with the rest: a pair twice)
                else:
                    state = KEPT
            rank = _RANK["dropped" if state.startswith("dropped") else state]
            if rank >= _RANK["dropped" if best.startswith("dropped") else best]:
                best, best_conn = state, conn
        ok = None
        if best == KEPT and p.kind and best_conn is not None:
            ok = p.kind in (best_conn.words, best_conn.how, best_conn.how.removesuffix("_of"))
        results.append(Score(p.id, best, ok))
    return results


def tally(scores: Sequence[Score]) -> Counter[str]:
    """How many pairs ended each way (a `dropped:<reason>` is counted under its reason)."""
    return Counter(s.status for s in scores)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("--text", required=True, type=Path)
    ap.add_argument("--reply", required=True, type=Path)
    ap.add_argument("--key", required=True, type=Path)
    args = ap.parse_args(argv)
    text = args.text.read_text(encoding="utf-8-sig")
    replies = _replies(json.loads(args.reply.read_text(encoding="utf-8")))
    key = json.loads(args.key.read_text(encoding="utf-8"))
    pairs = [Pair(p["id"], p["a"], p["b"], p.get("kind", "")) for p in key["pairs"]]
    scores = score(text, replies, pairs, key.get("aliases", {}))
    for s in scores:
        extra = "" if s.kind_matches is None else f" kind={'ok' if s.kind_matches else 'other'}"
        print(f"{s.pair}: {s.status}{extra}")
    counts = tally(scores)
    print(f"pairs={len(scores)} kept={counts[KEPT]}")
    for status, n in sorted(counts.items()):
        print(f"  {status}={n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
