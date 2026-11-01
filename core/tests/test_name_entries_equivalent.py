"""`gate.name_entries` after it was made cheaper (#3228, #3154) gives exactly the answers it gave
before, for the DM, the party and a player, over rows that mix every case the function has: other
names, secret own names, unnamed people hanging off entries, joined unnamed people, names learned
in different orders. Pure: no database. Invented names throughout."""

from __future__ import annotations

import random
import time
import unittest
from typing import Any

from dmbot.memory import gate
from dmbot.memory.gate import ALIAS, ENTRY, NameRows, Viewer, Visible
from dmbot.memory.models import lookup_key
from tests.name_entries_reference import reference_name_entries


def row(i: int, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"e{i}", "name": f"Name {i}", "status": "confirmed", "unnamed": False,
        "unnamed_anchor": None, "unnamed_what": None, "merged_into": None,
        "hidden_from_players": False, "audience_limited": False,
    }  # fmt: skip
    return {**base, **kw}


def alias(n: int, entity: str, text: str, *, secret: bool = False) -> dict[str, Any]:
    return {
        "id": f"a{n}",
        "entity_id": entity,
        "text": text,
        "key": lookup_key(text),
        "secret": secret,
    }


def world(size: int, seed: int) -> tuple[NameRows, list[str], Visible]:
    rng = random.Random(seed)
    entities: dict[str, dict[str, Any]] = {}
    aliases: list[dict[str, Any]] = []
    counter = 0
    for i in range(size):
        kind = rng.random()
        if kind < 0.12 and i > 3:  # an unnamed person hanging off an earlier entry
            anchor = f"e{rng.randrange(i)}"
            entities[f"e{i}"] = row(
                i, unnamed=True, unnamed_anchor=anchor, unnamed_what="the guard",
                name=f"the guard at {anchor} (name unknown)",
            )  # fmt: skip
        elif kind < 0.18 and i > 3:  # an unnamed person joined into an earlier entry
            into = f"e{rng.randrange(i)}"
            entities[f"e{i}"] = row(
                i, unnamed=True, merged_into=into, status="merged", unnamed_what="a hooded one",
                unnamed_anchor=None, name="someone (name unknown)",
            )  # fmt: skip
        else:
            entities[f"e{i}"] = row(i, hidden_from_players=rng.random() < 0.15)
    for i, e in entities.items():
        counter += 1
        aliases.append(alias(counter, i, str(e["name"]), secret=rng.random() < 0.1))
        for _ in range(rng.randrange(0, 4)):
            counter += 1
            text = (
                f"{e['name']} {rng.choice(['the Bold', 'Rook', 'Shade', 'of Greywater'])} {counter}"
            )
            if e["merged_into"] is not None and rng.random() < 0.5:
                text = str(e["name"])
            aliases.append(alias(counter, i, text, secret=rng.random() < 0.3))
    learned = [
        (a["id"], f"e{rng.randrange(min(size, 6))}", rng.randrange(1, 50))
        for a in aliases
        if rng.random() < 0.5
    ]
    sureness: dict[tuple[str, str], float] = {}
    seen_aliases = set()
    for i in entities:
        if rng.random() < 0.5:
            sureness[(ENTRY, i)] = 1.0
    for a in aliases:
        if rng.random() < 0.4:
            seen_aliases.add(a["id"])
            sureness[(ALIAS, a["id"])] = 1.0
    seen = Visible(
        entries=frozenset(i for (k, i) in sureness if k == ENTRY),
        aliases=frozenset(seen_aliases),
        sureness=sureness,
    )
    return NameRows(entities, aliases, learned), list(entities), seen


class Equivalent(unittest.TestCase):
    def test_the_same_names_for_every_viewer_and_option(self) -> None:
        for seed in range(12):
            rows, ids, seen = world(120, seed)
            for viewer in (Viewer.the_dm(), Viewer.the_party(), Viewer.player({"e0", "e2"})):
                everything = Visible(everything=True)
                for public in (False, True):
                    for on_their_own in ((), tuple(a["id"] for a in rows.aliases[::7])):
                        for shown in (seen, everything):
                            with self.subTest(seed=seed, public=public, own=bool(on_their_own)):
                                got_texts: dict[str, str] = {}
                                want_texts: dict[str, str] = {}
                                got = gate.name_entries(
                                    shown, viewer, ids, rows, public=public,
                                    texts_out=got_texts, on_their_own=on_their_own,
                                )  # fmt: skip
                                want = reference_name_entries(
                                    shown, viewer, ids, rows, public=public,
                                    texts_out=want_texts, on_their_own=on_their_own,
                                )  # fmt: skip
                                self.assertEqual(got, want)
                                self.assertEqual(got_texts, want_texts)

    def test_a_subset_and_repeats_and_unknown_ids(self) -> None:
        rows, ids, seen = world(80, 99)
        asked = [*ids[::3], "missing", ids[0], ids[0]]
        viewer = Viewer.player({"e1"})
        self.assertEqual(
            gate.name_entries(seen, viewer, asked, rows),
            reference_name_entries(seen, viewer, asked, rows),
        )


class NotQuadratic(unittest.TestCase):
    def test_four_times_the_entries_costs_far_less_than_sixteen_times(self) -> None:
        def took(size: int) -> float:
            rows, ids, seen = world(size, 5)
            start = time.perf_counter()
            gate.name_entries(seen, Viewer.player({"e1"}), ids, rows)
            return time.perf_counter() - start

        small, big = min(took(500) for _ in range(3)), min(took(2000) for _ in range(3))
        # (a generous bound: linear is 4x, quadratic is 16x)
        self.assertLess(big, small * 9 + 0.05)
