"""`relationships._knowers`: who knows a statement, from the member lists the door sends (#3154).

Pure (no database). The rule itself is `gate.knowers_of` (#3228), checked against `gate.reach` in
`test_gate_knowers_of.py`; this file checks that the page's lists reach it intact and that what it
gives equals what `gate.reach` gives for every character."""

from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any

from dmbot.memory import gate, party_icons
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
    pairs = [[m, f"g{rng.randint(0, 3)}"] for m in people for _ in range(rng.randint(0, 2))]
    # (a group can itself belong to a group, and then it is both)
    data, who = relationships._knowers(_known(rows, pairs, entries))
    for e in entries:
        found = data.knowers.get((gate.ENTRY, e), {})
        listed = found if isinstance(found, Mapping) else dict.fromkeys(found, 1.0)
        expect = {c for c in people if gate.reach(c, listed, data.groups) is not None}
        assert who(gate.ENTRY, e) == expect


def test_sharing_the_work_changes_no_answer() -> None:
    known = _known(
        [[gate.ENTRY, "e1", "party", 1.0], [gate.CONNECTION, "x", "ann", 1.0]],
        [["ann", "party"], ["bob", "party"]],
        ["e1"],
    )
    known["connections"] = {"x": ["e1", "e1"]}
    known["viewers"] = [{"id": "ann", "name": "Ann"}, {"id": "bob", "name": "Bob"}]
    shared = relationships._knowers(known)
    assert relationships.know_words(known, shared) == relationships.know_words(known)
    assert relationships.link_holders(known, shared) == relationships.link_holders(known)


def _guess_known(rows: list[list[Any]], groups: list[list[str]], pcs: list[str]) -> dict[str, Any]:
    known = _known(rows, groups, ["e1"])
    known["connections"] = {"x": ["e1", "e1"]}
    known["playerCharacters"] = pcs
    known["viewers"] = [
        {"id": "ann", "name": "Ann"},
        {"id": "bob", "name": "Bob"},
        {"id": "cy", "name": "Cy"},
        {"id": "dee", "name": "Dee"},
    ]
    return known


def test_an_npc_below_one_is_marked_as_dmbots_guess_sure_or_likely() -> None:
    known = _guess_known(
        [
            [gate.CONNECTION, "x", "ann", 0.9],
            [gate.CONNECTION, "x", "bob", 0.6],
            [gate.CONNECTION, "x", "cy", 1.0],
        ],
        [],
        [],
    )
    got = relationships.link_holders(known)["x"]
    assert got == [
        {"id": "ann", "name": "Ann", "status": "npc", "guess": "sure"},
        {"id": "bob", "name": "Bob", "status": "npc", "guess": "likely"},
        {"id": "cy", "name": "Cy", "status": "npc"},
    ]


def test_a_player_character_is_never_marked_and_neither_is_a_group_member() -> None:
    known = _guess_known(
        [[gate.CONNECTION, "x", "ann", 0.6], [gate.CONNECTION, "x", "party", 1.0]],
        [["dee", "party"]],
        ["ann"],
    )
    known["viewers"].append({"id": "party", "name": "The party"})
    got = {h["id"]: h for h in relationships.link_holders(known)["x"]}
    assert "guess" not in got["ann"]  # (a player character's certainty is another matter)
    assert "guess" not in got["dee"]  # (knows only through the group's row)


def test_the_knowers_come_party_first_then_those_who_left_then_the_npcs() -> None:
    """The "Who knows this?" list in Discord's order (#5332): the party now in the party list's
    order, then characters who left, then NPCs, each group by name; `status` says which."""
    known = _guess_known(
        [
            [gate.CONNECTION, "x", "dee", 1.0],
            [gate.CONNECTION, "x", "cy", 1.0],
            [gate.CONNECTION, "x", "bob", 1.0],
            [gate.CONNECTION, "x", "ann", 1.0],
        ],
        [],
        ["ann", "bob", "cy", "dee"],
    )
    known["viewers"].append({"id": "eve", "name": "Eve"})
    known["partyOrder"] = ["dee", "bob"]  # (the party list's own order, not by name)
    known["leftParty"] = ["cy"]  # (cy left, so is no longer in the party)
    known["playerCharacters"] = ["bob", "dee", "ann"]  # (the party now: cy is not in it)
    known["knowers"].append([gate.CONNECTION, "x", "eve", 1.0])
    got = relationships.link_holders(known)["x"]
    assert [(h["name"], h["status"]) for h in got] == [
        ("Dee", "party"),
        ("Bob", "party"),
        ("Ann", "party"),  # (a member the list has no place for follows the ordered ones)
        ("Cy", "left"),
        ("Eve", "npc"),
    ]


def _party_known() -> dict[str, Any]:
    known = _guess_known([], [], ["ann", "bob"])
    known["partyOrder"] = ["ann", "bob"]
    known["leftParty"] = ["cy"]
    known["leftWhy"] = {"cy": "retired"}
    return known


def test_the_preview_line_is_the_same_words_as_the_line_on_every_other_surface() -> None:
    known = _party_known()
    assert relationships.preview_line(known, ["ann", "bob"]) == party_icons.WHOLE
    assert relationships.preview_line(known, ["ann"]) == "👤 Known to: Ann"
    assert relationships.preview_line(known, [], common=True) == party_icons.EVERYONE
    nobody = relationships.preview_line(known, ["cy", "dee"])
    assert nobody.startswith("🤫 No one in the party knows. Known to: Cy (retired)")


def test_the_preview_line_ignores_an_id_the_door_does_not_list() -> None:
    known = _party_known()
    assert relationships.preview_line(known, ["ann", "0" * 32]) == "👤 Known to: Ann"
    assert relationships.preview_line(known, ["0" * 32]) == party_icons.NO_ONE
