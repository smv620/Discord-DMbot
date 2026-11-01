"""`relationships._knowers`: who knows a statement, worked out from the member lists once (#3154).

Pure (no database): the faster form must give exactly what `gate.reach` gives for every character,
since it is the same rule written so it does not ask every group member about every statement."""

from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any

from dmbot.memory import gate
from dmbot.web import relationships


def _known(rows: list[list[Any]], groups: list[list[str]], entries: list[str]) -> dict[str, Any]:
    return {
        "liveEntries": entries,
        "states": {},
        "connections": {},
        "aliases": {},
        "knowers": rows,
        "groups": groups,
        "playerCharacters": [],
    }


def test_a_group_member_knows_what_the_group_knows() -> None:
    known = _known(
        [[gate.ENTRY, "e1", "party", 1.0], [gate.ENTRY, "e1", "npc", 0.5]],
        [["ann", "party"], ["bob", "party"], ["cy", "other"]],
        ["e1"],
    )
    _data, who = relationships._knowers(known)
    assert who(gate.ENTRY, "e1") == {"party", "npc", "ann", "bob"}
    assert who(gate.ENTRY, "nothing") == set()


def test_the_same_answer_as_reach_for_every_character() -> None:
    rng = random.Random(3154)
    people = [f"c{i}" for i in range(12)] + [f"g{i}" for i in range(4)]
    entries = [f"e{i}" for i in range(30)]
    rows = [
        [gate.ENTRY, e, c, rng.choice([0.4, 1.0])]
        for e in entries
        for c in rng.sample(people, rng.randint(0, 5))
    ]
    pairs = [[m, f"g{rng.randint(0, 3)}"] for m in people[:12] for _ in range(rng.randint(0, 2))]
    data, who = relationships._knowers(_known(rows, pairs, entries))
    for e in entries:
        found = data.knowers.get((gate.ENTRY, e), {})
        listed = found if isinstance(found, Mapping) else dict.fromkeys(found, 1.0)
        expect = {c for c in people if gate.reach(c, listed, data.groups) is not None}
        assert who(gate.ENTRY, e) == expect
