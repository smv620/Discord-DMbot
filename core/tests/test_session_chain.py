"""Where the story is, through a session's words (#4173 slice 4; Supervisor's ruling, 2026-11-13
04:14): only the DM moves a chain, players' places are read against it, leaving and heading words,
lists, split groups and Rejoin. Pure. Invented and published place names only."""

from __future__ import annotations

import unittest

from dmbot.memory.session_chain import MAIN, Line, rejoined, walk
from dmbot.memory.story_chain import Place

DALE = Place("dale", "region", ("Icewind Dale",))
TARGOS = Place("targos", "settlement", ("Targos",))
BRYN = Place("bryn", "settlement", ("Bryn Shander",))
INN = Place("inn", "building", ("Three Flags Sailing", "the Three Flags"))
ROOM = Place("room", "room", ("common room",))
HALL = Place("hall", "building", ("Speaker's Hall",))
PLACES = (DALE, TARGOS, BRYN, INN, ROOM, HALL)


def dm(text: str, chain: str | None = MAIN) -> Line:
    return Line(text, True, chain)


def player(text: str, chain: str | None = MAIN) -> Line:
    return Line(text, False, chain)


def run(*lines: Line, chains: dict[str, list[str]] | None = None, stated: tuple[str, ...] = ()):  # type: ignore[no-untyped-def]
    return walk(lines, PLACES, chains or {}, set(stated))


class TheDmMovesIt(unittest.TestCase):
    def test_each_place_the_dm_names_sits_in_the_nearest_bigger_open_one(self) -> None:
        got = run(
            dm("You reach Icewind Dale."),
            dm("By dusk you are in Targos."),
            dm("You take rooms at the Three Flags Sailing."),
            dm("The common room is loud."),
        )
        self.assertEqual(got.pairs, [("targos", "dale"), ("inn", "targos"), ("room", "inn")])
        self.assertEqual(got.chains[MAIN], ["dale", "targos", "inn", "room"])

    def test_it_carries_over_from_last_session(self) -> None:
        got = run(dm("You sit down in the common room."), chains={MAIN: ["dale", "targos", "inn"]})
        self.assertEqual(got.pairs, [("room", "inn")])

    def test_a_new_place_closes_its_own_size_and_smaller(self) -> None:
        got = run(dm("You ride into Bryn Shander."), chains={MAIN: ["dale", "targos", "inn"]})
        self.assertEqual(got.chains[MAIN], ["dale", "bryn"])
        self.assertEqual(got.pairs, [("bryn", "dale")])


class PlayersAreRead(unittest.TestCase):
    def test_a_players_place_is_read_against_the_chain_and_never_moves_it(self) -> None:
        got = run(
            player("My cousin lives in Bryn Shander."), chains={MAIN: ["dale", "targos", "inn"]}
        )
        self.assertEqual(got.chains[MAIN], ["dale", "targos", "inn"])  # (not moved)
        self.assertEqual(got.pairs, [("bryn", "dale")])  # (its parent, from the chain as it is)

    def test_a_player_leaving_word_closes_nothing(self) -> None:
        got = run(player("I say we left Targos already."), chains={MAIN: ["dale", "targos"]})
        self.assertEqual(got.chains[MAIN], ["dale", "targos"])


class LeavingWords(unittest.TestCase):
    def test_leaving_closes_the_place_and_what_is_inside(self) -> None:
        got = run(dm("You left Targos at dawn."), chains={MAIN: ["dale", "targos", "inn"]})
        self.assertEqual(got.chains[MAIN], ["dale"])

    def test_left_for_is_a_heading_for(self) -> None:
        # Supervisor's condition: "they left for Targos" doesn't close Targos
        got = run(dm("They left for Targos."), chains={MAIN: ["dale", "targos", "inn"]})
        self.assertEqual(got.chains[MAIN], ["dale"])  # (between places: Targos isn't open yet)
        got = run(dm("They left for Bryn Shander."), chains={MAIN: ["dale", "targos", "inn"]})
        self.assertEqual(got.chains[MAIN], ["dale"])  # (on the way: the town and inn closed)

    def test_a_leaving_word_closes_only_a_place_open_on_that_chain(self) -> None:
        # Supervisor's condition: "She left the letter in Bryn Shander" leaves no open place
        got = run(
            dm("She left the letter in Bryn Shander."), chains={MAIN: ["dale", "targos", "inn"]}
        )
        self.assertEqual(got.chains[MAIN], ["dale", "targos", "inn"])  # (closes nothing)
        self.assertEqual(got.pairs, [("bryn", "dale")])  # (still read, against the chain)
        got = run(dm("They left Bryn Shander behind."), chains={MAIN: ["dale", "targos"]})
        self.assertEqual(got.chains[MAIN], ["dale", "targos"])  # (Bryn wasn't open: nothing)

    def test_on_the_road_closes_towns_and_smaller(self) -> None:
        got = run(
            dm("After three days on the road you are cold."),
            chains={MAIN: ["dale", "targos", "inn"]},
        )
        self.assertEqual(got.chains[MAIN], ["dale"])


class Lists(unittest.TestCase):
    def test_a_list_opens_nothing_and_gives_no_parent(self) -> None:
        got = run(
            dm("The towns are Targos, Bryn Shander and the Three Flags Sailing."),
            chains={MAIN: ["dale"]},
        )
        self.assertEqual((got.pairs, got.chains[MAIN]), ([], ["dale"]))


class OneGuess(unittest.TestCase):
    def test_a_place_with_a_parent_gets_none_and_one_guess_each(self) -> None:
        got = run(
            dm("You reach Targos."),
            dm("The Three Flags Sailing."),
            dm("Back at the Three Flags Sailing."),
            chains={MAIN: ["dale"]},
            stated=("targos",),
        )
        self.assertEqual(got.pairs, [("inn", "targos")])


class SplitGroups(unittest.TestCase):
    def test_each_group_starts_from_the_party_and_moves_on_its_own(self) -> None:
        got = run(
            dm("Group one walks into Speaker's Hall.", chain="g1"),
            dm("Group two takes a table in the common room.", chain="g2"),
            chains={MAIN: ["dale", "targos", "inn"]},
        )
        self.assertEqual(got.chains["g1"], ["dale", "targos", "hall"])
        self.assertEqual(got.chains["g2"], ["dale", "targos", "inn", "room"])
        self.assertEqual(got.chains[MAIN], ["dale", "targos", "inn"])

    def test_a_line_nobody_can_place_is_skipped(self) -> None:
        got = run(dm("You reach Bryn Shander.", chain=None), chains={MAIN: ["dale"]})
        self.assertEqual((got.pairs, got.chains[MAIN]), ([], ["dale"]))

    def test_rejoin_keeps_only_what_every_group_still_shares(self) -> None:
        self.assertEqual(
            rejoined([["dale", "targos", "hall"], ["dale", "targos", "inn", "room"]]),
            ["dale", "targos"],
        )
        self.assertEqual(rejoined([["dale", "bryn"], ["dale", "targos"]]), ["dale"])
        self.assertEqual(rejoined([]), [])


class Spellings(unittest.TestCase):
    def test_a_short_spelling_inside_a_longer_one_is_one_place(self) -> None:
        got = run(dm("You enter the Three Flags Sailing."), chains={MAIN: ["dale", "targos"]})
        self.assertEqual(got.pairs, [("inn", "targos")])
