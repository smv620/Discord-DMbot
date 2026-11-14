"""Where the story is, through a session's words (#4173 slice 4; Supervisor's ruling, 2026-11-13
04:14): only the DM moves a chain, players' places are read against it, leaving and heading words,
lists, split groups and Rejoin. Pure. Invented and published place names only."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory.scene_moves import ARRIVE, HEADING, LEAVE, NONE, TRAVEL, Move
from dmbot.memory.session_chain import MAIN, Leg, Line, rejoined, walk
from dmbot.memory.story_chain import Place

DALE = Place("dale", "region", ("Icewind Dale",))
TARGOS = Place("targos", "settlement", ("Targos",))
BRYN = Place("bryn", "settlement", ("Bryn Shander",))
INN = Place("inn", "building", ("Three Flags Sailing", "the Three Flags"))
ROOM = Place("room", "room", ("common room",))
HALL = Place("hall", "building", ("Speaker's Hall",))
PLACES = (DALE, TARGOS, BRYN, INN, ROOM, HALL)


def dm(text: str, chain: str | None = MAIN, moves: list[Move] | None = None) -> Line:
    return Line(text, True, chain, tuple(moves or ()))


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


class Moves(unittest.TestCase):
    """The AI's scene moves (the owner's change, 2026-11-13): the walk applies them, only to places
    it knows, and "left" only to a place open on that chain."""

    def test_leaving_closes_the_place_and_what_is_inside(self) -> None:
        got = run(
            dm("You leave Targos behind.", moves=[Move(1, LEAVE, "Targos")]),
            chains={MAIN: ["dale", "targos", "inn"]},
        )
        self.assertEqual(got.chains[MAIN], ["dale"])

    def test_heading_for_closes_its_size_and_smaller_and_opens_nothing(self) -> None:
        got = run(
            dm("They left for Bryn Shander.", moves=[Move(1, HEADING, "Bryn Shander")]),
            chains={MAIN: ["dale", "targos", "inn"]},
        )
        self.assertEqual(got.chains[MAIN], ["dale"])

    def test_left_the_letter_is_none_and_moves_nothing(self) -> None:
        got = run(
            dm("She left the letter in Bryn Shander.", moves=[Move(1, NONE, "Bryn Shander")]),
            chains={MAIN: ["dale", "targos", "inn"]},
        )
        self.assertEqual(got.chains[MAIN], ["dale", "targos", "inn"])  # (closes nothing)
        self.assertEqual(got.pairs, [("bryn", "dale")])  # (still read, against the chain)

    def test_a_leaving_closes_only_a_place_open_on_that_chain(self) -> None:
        got = run(
            dm("They left Bryn Shander behind.", moves=[Move(1, LEAVE, "Bryn Shander")]),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(got.chains[MAIN], ["dale", "targos"])

    def test_travel_closes_towns_and_smaller(self) -> None:
        got = run(
            dm("After three days on the road you are cold.", moves=[Move(1, TRAVEL, "")]),
            chains={MAIN: ["dale", "targos", "inn"]},
        )
        self.assertEqual(got.chains[MAIN], ["dale"])

    def test_a_move_on_a_place_nobody_knows_is_ignored(self) -> None:
        got = run(
            dm("You leave Neverwinter.", moves=[Move(1, LEAVE, "Neverwinter")]),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(got.chains[MAIN], ["dale", "targos"])

    def test_a_players_moves_are_never_applied(self) -> None:
        got = run(
            Line("We leave Targos.", False, MAIN, (Move(1, LEAVE, "Targos"),)),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(got.chains[MAIN], ["dale", "targos"])

    def test_no_keywords_without_the_ai(self) -> None:
        # (the old fixed words are gone: with no move found, a named place is simply reached)
        got = run(dm("They left for Bryn Shander."), chains={MAIN: ["dale", "targos"]})
        self.assertEqual(got.chains[MAIN], ["dale", "bryn"])


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


class AfterTheReview(unittest.TestCase):
    """The reviewer's cases on slice 4."""

    def test_x_in_y_puts_x_inside_y(self) -> None:
        got = run(
            dm("You take rooms at the Three Flags Sailing in Targos."), chains={MAIN: ["dale"]}
        )
        self.assertEqual(got.pairs, [("targos", "dale"), ("inn", "targos")])
        self.assertEqual(got.chains[MAIN], ["dale", "targos", "inn"])

    def test_leaving_then_arriving_moves_to_the_new_place(self) -> None:
        got = run(
            dm(
                "They left Targos and reached Bryn Shander.",
                moves=[Move(1, LEAVE, "Targos"), Move(1, ARRIVE, "Bryn Shander")],
            ),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(got.chains[MAIN], ["dale", "bryn"])


class Journeys(unittest.TestCase):
    """#4173 slice 6d part 3 (Supervisor's ruling, 2026-11-13, and the reviewer's cases): the
    party's own chain brought by the DM from one town to another (the AI's explicit "arrive") is a
    journey, with the one way the DM's words on the journey say, or none; never a split group's,
    never a town named in passing."""

    def arrive(self, text: str, town: str, chain: str | None = MAIN) -> Line:
        return dm(text, chain, [Move(1, ARRIVE, town)])

    def test_a_move_between_towns_is_a_journey_by_the_way_said(self) -> None:
        got = run(
            self.arrive("You take the road and reach Bryn Shander.", "Bryn Shander"),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(got.legs, [Leg("targos", "bryn", "road")])

    def test_no_way_word_is_a_journey_with_no_way(self) -> None:
        got = run(
            self.arrive("You reach Bryn Shander.", "Bryn Shander"),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(got.legs, [Leg("targos", "bryn", "")])  # (never a guessed "wild")

    def test_the_town_is_remembered_across_travel(self) -> None:
        got = run(
            dm("Three days on the river.", moves=[Move(1, TRAVEL, "")]),
            self.arrive("You reach Bryn Shander.", "Bryn Shander"),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(got.legs, [Leg("targos", "bryn", "river")])  # (no end named: no length)

    def test_the_dms_words_on_the_way_give_its_length(self) -> None:
        # (#4255 slice 1b: by `route_length`'s set phrases, the arrival's own name included)
        got = run(
            dm("Three days on the river to Bryn Shander.", moves=[Move(1, TRAVEL, "")]),
            self.arrive("You reach Bryn Shander.", "Bryn Shander"),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(got.legs[0].length, "Three days on the river")
        named = run(
            self.arrive("It's only 1 hour to Bryn Shander, and you arrive.", "Bryn Shander"),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(named.legs[0].length, "1 hour")

    def test_a_length_said_of_another_place_is_not_the_journeys(self) -> None:
        # (the reviewer's case: narration on the way speaks of other places)
        other = run(
            dm("Kelvin's Cairn is twenty miles from here.", moves=[Move(1, TRAVEL, "")]),
            self.arrive("You set off and reach Bryn Shander.", "Bryn Shander"),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(other.legs[0].length, "")
        both = run(
            dm("Kelvin's Cairn is twenty miles from here.", moves=[Move(1, TRAVEL, "")]),
            self.arrive("It's only 1 hour to Bryn Shander, and you arrive.", "Bryn Shander"),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(both.legs[0].length, "1 hour")  # (the other place's never cancels it)

    def test_a_town_named_in_passing_is_no_journey(self) -> None:
        got = run(
            dm("A letter from Bryn Shander arrives."),
            dm("You reach Termalaine, a day north of Bryn Shander."),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(got.legs, [])

    def test_talk_in_town_says_nothing_of_the_way(self) -> None:
        got = run(
            dm("A ship is in the harbour."),
            dm("The druid uses wild shape."),
            dm("You leave Targos behind.", moves=[Move(1, LEAVE, "Targos")]),
            self.arrive("You reach Bryn Shander.", "Bryn Shander"),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(got.legs, [Leg("targos", "bryn", "")])

    def test_two_ways_in_one_journey_is_no_one_way(self) -> None:
        got = run(
            self.arrive("You leave the road and board a ship to Bryn Shander.", "Bryn Shander"),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(got.legs, [Leg("targos", "bryn", "")])

    def test_there_and_back_is_two_journeys(self) -> None:
        got = run(
            self.arrive("You reach Bryn Shander by road.", "Bryn Shander"),
            self.arrive("You ride back to Targos by road.", "Targos"),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(got.legs, [Leg("targos", "bryn", "road"), Leg("bryn", "targos", "road")])

    def test_a_split_groups_move_writes_nothing(self) -> None:
        got = run(
            self.arrive("Group one reaches Bryn Shander.", "Bryn Shander", chain="g1"),
            chains={MAIN: ["dale", "targos"]},
        )
        self.assertEqual(got.legs, [])

    def test_the_last_town_carries_over_a_rejoin(self) -> None:
        got = walk(
            [self.arrive("You reach Bryn Shander.", "Bryn Shander")], PLACES, {MAIN: ["dale"]},
            set(), last_town="targos",
        )  # fmt: skip
        self.assertEqual((got.legs, got.town), ([Leg("targos", "bryn", "")], "bryn"))

    def test_a_kept_town_no_longer_a_town_starts_no_journey(self) -> None:
        # (#4338: the town from the session before is checked again; "dale" is a region now)
        got = walk(
            [self.arrive("You reach Bryn Shander.", "Bryn Shander")], PLACES, {MAIN: []},
            set(), last_town="dale",
        )  # fmt: skip
        self.assertEqual((got.legs, got.town), ([], "bryn"))

    def test_the_partys_own_town_wins_over_a_kept_one(self) -> None:
        # (#4338: the kept town is used only when the party's chain holds none)
        got = walk(
            [self.arrive("You reach Bryn Shander.", "Bryn Shander")], PLACES,
            {MAIN: ["dale", "targos"]}, set(), last_town="bryn",
        )  # fmt: skip
        self.assertEqual(got.legs, [Leg("targos", "bryn", "")])

    def test_a_players_words_and_a_building_make_no_journey(self) -> None:
        got = run(
            player("Let's go to Bryn Shander by road."),
            dm("You sit in the common room."),
            chains={MAIN: ["dale", "targos", "inn"]},
        )
        self.assertEqual(got.legs, [])


class NamedRoads(unittest.TestCase):
    ROAD = Place("road", "", ("the Bryn Shander Road",))

    def go(self, *lines: Line) -> Any:
        return walk(
            list(lines), PLACES, {MAIN: ["dale", "targos", "inn"]}, set(), routes=[self.ROAD]
        )

    def test_being_put_on_a_named_road_closes_the_town(self) -> None:
        got = self.go(
            dm(
                "You set out on the Bryn Shander Road.",
                moves=[Move(1, ARRIVE, "the Bryn Shander Road")],
            )
        )
        self.assertEqual(got.chains[MAIN], ["dale"])
        self.assertEqual(got.legs, [])  # (a road named for a town is no arrival there)

    def test_a_road_named_left_or_without_a_move_closes_nothing(self) -> None:
        for line in (
            dm("The Bryn Shander Road runs north of here."),
            dm("You leave the Bryn Shander Road.", moves=[Move(1, LEAVE, "the Bryn Shander Road")]),
        ):
            with self.subTest(text=line.text):
                self.assertEqual(self.go(line).chains[MAIN], ["dale", "targos", "inn"])
