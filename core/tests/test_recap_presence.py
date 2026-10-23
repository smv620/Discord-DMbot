"""A character called by its story-memory name, and who was there (#2573). Made-up names: "Sarek"
is how the speech to text heard the player character "Tavin"."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from dmbot.recaps import beats as bt
from dmbot.recaps import draft as rd
from dmbot.recaps.inputs import recap_people
from dmbot.recaps.unknown_names import canonical

ALIASES = {"Sarek": "Tavin", "Belleros Stormborn": "Belleros"}
NAMES = ["Tavin", "Sarek", "Belleros", "Greyfen", "Ysolde", "Belleros Stormborn", "Karrowmere"]
PCS = ["Belleros", "Greyfen", "Tavin", "Ysolde"]
ORDINARY = {"camp", "party", "the", "with"}


def put(text: str, secret: tuple[str, ...] = ()) -> str:
    return canonical(text, ALIASES, names=NAMES, secret_names=secret, ordinary_words=ORDINARY)


class TheirNames(unittest.TestCase):
    def test_a_misheard_pc_comes_out_as_its_story_memory_name(self) -> None:
        self.assertEqual(
            put("Sarek lit the fire. Then Sarek's blade rang."),
            "Tavin lit the fire. Then Tavin's blade rang.",
        )

    def test_a_longer_other_spelling_is_put_right_too(self) -> None:
        self.assertEqual(put("Belleros Stormborn stood up."), "Belleros stood up.")

    def test_a_name_that_sounds_like_exactly_one_known_name_is_that_name(self) -> None:
        self.assertEqual(put("Sarec drew his sword."), "Tavin drew his sword.")

    def test_a_name_that_matches_nothing_is_left_alone(self) -> None:
        self.assertEqual(put("Morwick drew his sword."), "Morwick drew his sword.")

    def test_an_ordinary_word_is_never_put_right(self) -> None:
        self.assertEqual(put("Camp was made."), "Camp was made.")

    def test_a_name_near_a_secret_name_is_left_alone(self) -> None:
        self.assertEqual(put("Sarec drew his sword.", secret=("Sarrek",)), "Sarec drew his sword.")

    def test_a_known_name_is_not_touched(self) -> None:
        self.assertEqual(put("Greyfen nodded."), "Greyfen nodded.")

    def test_the_prose_is_put_right_before_the_name_check(self) -> None:
        beat = bt.Beat(1, "Sarek lit the fire.", ("Sarek",), (1,), "event")
        prose = rd.Prose("《Calm》 Sarek lit the fire.", ["soft strings"])
        fixed, _ = rd.respelled(
            prose,
            beats=[beat],
            allowed_names=NAMES,
            secret_names=[],
            transcript="sarek lit the fire",
            aliases=ALIASES,
        )
        self.assertIn("Tavin lit the fire", fixed.summary)
        self.assertNotIn("Sarek", fixed.summary)


def entry(text: str, **more: bool) -> SimpleNamespace:
    flags = {"confirmed": True, "secret": False, "person": False, **more}
    return SimpleNamespace(entity_id="e1", text=text, **flags)


def entity(name: str, user: int | None, role: str = "player_character") -> SimpleNamespace:
    return SimpleNamespace(name=name, played_by=user, role=role)


class TheStoryMemory(unittest.TestCase):
    def test_the_aliases_pcs_and_party_come_from_the_confirmed_visible_names(self) -> None:
        tavin = entity("Tavin", 7)
        names = SimpleNamespace(
            entities={"e1": tavin, "e2": entity("Ysolde", 8), "e3": entity("Mox", None, "npc")},
            names=[
                SimpleNamespace(
                    entity_id="e1", text="Tavin", confirmed=True, secret=False, person=False
                ),
                SimpleNamespace(
                    entity_id="e1", text="Sarek", confirmed=True, secret=False, person=False
                ),
                SimpleNamespace(
                    entity_id="e1", text="Hidden", confirmed=True, secret=True, person=False
                ),
                SimpleNamespace(
                    entity_id="e1", text="Maybe", confirmed=False, secret=False, person=False
                ),
            ],
            fixes={"tavn": ("e1",), "many": ("e1", "e2")},
        )
        aliases, pcs, party = recap_people(names, ["Tavin", "Ysolde", "Mox"])
        self.assertEqual(aliases, {"Sarek": "Tavin", "tavn": "Tavin"})
        self.assertEqual(pcs, ["Tavin", "Ysolde"])
        self.assertEqual(party, {7: ("Tavin",), 8: ("Ysolde",)})


# Lines 1-8: three players talk around the camp; Ysolde's player (user 4) is silent until line 9.
SPEAKERS = {1: 1, 2: 2, 3: 3, 4: 1, 5: 2, 6: 3, 7: 1, 8: 3, 9: 4}
PARTY = {1: ("Belleros",), 2: ("Greyfen",), 3: ("Tavin",), 4: ("Ysolde",)}


class ThePresence(unittest.TestCase):
    def test_present_is_who_spoke_in_the_stretch_and_who_the_beat_names(self) -> None:
        got = bt.present_in((2, 5), SPEAKERS, PARTY, ("Ysolde",), PCS)
        self.assertEqual(got, ("Belleros", "Greyfen", "Tavin", "Ysolde"))
        self.assertEqual(bt.present_in((1, 1), SPEAKERS, PARTY, (), PCS), ("Belleros",))

    def test_a_beat_keeps_its_present_list_in_storage_and_the_writer_sees_it(self) -> None:
        beat = bt.Beat(1, "The party camps.", (), (1, 8), "event", present=("Belleros", "Tavin"))
        self.assertEqual(bt.from_json(bt.as_json([beat]))[0].present, ("Belleros", "Tavin"))
        self.assertIn("Present: Belleros, Tavin.", bt.sheet([beat]))

    def test_check_gives_each_beat_its_present_list_and_canonical_names(self) -> None:
        raw = [
            {"what": "Sarek and Belleros make camp by the river.", "who": ["Sarek"],
             "lines": [1, 8], "kind": "event"},
        ] * 3  # fmt: skip
        lines = {n: f"Sarek and Belleros make camp {n}" for n in SPEAKERS}
        kept, _ = bt.check(
            raw,
            lines=lines,
            speakers=SPEAKERS,
            allowed_names=["Tavin", "Sarek", "Belleros"],
            secret_names=[],
            aliases={"Sarek": "Tavin"},
            party=PARTY,
            pcs=PCS,
        )
        self.assertEqual(kept[0].who, ("Tavin",))
        self.assertIn("Tavin and Belleros", kept[0].what)
        self.assertEqual(kept[0].present, ("Belleros", "Greyfen", "Tavin"))


CAMP = bt.Beat(
    1, "The whole party travels to the river and makes camp together.", (), (1, 8), "event",
    present=("Belleros", "Greyfen", "Tavin", "Ysolde"),
)  # fmt: skip


class TheCheck(unittest.TestCase):
    def problems(self, text: str) -> list[str]:
        return rd.presence_problems(text, [CAMP], PCS)

    def test_a_camp_scene_naming_three_of_four_present_fails(self) -> None:
        got = self.problems("Belleros, Greyfen and Tavin travelled to the river and made camp.")
        self.assertEqual(len(got), 1)
        self.assertIn("Ysolde", got[0])

    def test_naming_everyone_or_the_party_passes(self) -> None:
        self.assertEqual(
            self.problems(
                "Belleros, Greyfen, Tavin and Ysolde travelled to the river and made camp."
            ),
            [],
        )
        self.assertEqual(
            self.problems("The party travelled to the river and made camp together."), []
        )
        self.assertEqual(
            self.problems(
                "Belleros and Greyfen, with the party behind them, travelled to the river."
            ),
            [],
        )

    def test_a_pc_who_was_not_present_fails(self) -> None:
        away = bt.Beat(
            1, "Greyfen and Tavin scout the old road alone.", (), (2, 3), "event",
            present=("Greyfen", "Tavin"),
        )  # fmt: skip
        got = rd.presence_problems("Greyfen and Ysolde scouted the old road alone.", [away], PCS)
        self.assertEqual(len(got), 1)
        self.assertIn("Ysolde was not there", got[0])

    def test_a_sentence_about_nothing_in_the_beats_or_one_name_is_not_checked(self) -> None:
        self.assertEqual(self.problems("Belleros felt a chill."), [])
        self.assertEqual(self.problems("Rain fell on Belleros and Greyfen that night."), [])

    def test_beats_with_no_present_list_are_not_checked(self) -> None:
        old = bt.Beat(1, CAMP.what, (), (1, 8), "event")
        self.assertEqual(
            rd.presence_problems("Belleros and Greyfen travelled to the river.", [old], PCS), []
        )

    def test_the_prose_check_reports_it_in_plain_words(self) -> None:
        text = "《Calm》 Belleros, Greyfen and Tavin travelled to the river and made camp."
        got = rd.check_prose(
            rd.Prose(text, ["soft"]), beats=[CAMP], transcript="", allowed_names=PCS,
            secret_names=[], pcs=PCS,
        )  # fmt: skip
        self.assertTrue(any("names only some of the characters" in p for p in got))


class TheWritersNames(unittest.TestCase):
    def test_the_writer_is_given_the_story_memory_names_not_the_misheard_spelling(self) -> None:
        beat = bt.Beat(1, "Tavin lit the fire.", ("Tavin",), (1,), "event", present=("Tavin",))
        names = [n for n in NAMES if n not in ALIASES]
        message = rd.prose_message([beat], "", names, None)
        listed = message.split("<names>")[1].split("</names>")[0]
        self.assertIn("Tavin", listed)
        self.assertNotIn("Sarek", listed)
        self.assertIn("Present: Tavin.", message)
