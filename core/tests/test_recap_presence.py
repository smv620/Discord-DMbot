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

    def test_a_soft_c_and_an_s_meet_so_selwin_is_celwyn(self) -> None:
        got = canonical(
            "Selwin drew his sword.",
            {},
            names=["Celwyn", "Tavin"],
            secret_names=[],
            sound_names=["Celwyn", "Tavin"],
        )
        self.assertEqual(got, "Celwyn drew his sword.")

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
        prose = rd.Prose("《Calm》 Sarek lit the fire.", ("soft strings",))
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
    def test_present_is_every_pc_whose_player_spoke_in_the_session_and_who_the_beat_names(
        self,
    ) -> None:
        far = {1: 1, 400: 2}  # the second player spoke far from the beat: still at the table
        self.assertEqual(bt.present_in((1, 1), far, PARTY, (), PCS), ("Belleros", "Greyfen"))
        self.assertEqual(
            bt.present_in((1, 1), far, PARTY, ("Ysolde",), PCS), ("Belleros", "Greyfen", "Ysolde")
        )

    def test_a_pc_whose_player_never_spoke_is_not_present_unless_named(self) -> None:
        quiet = {1: 1, 2: 2}
        self.assertEqual(bt.present_in((1, 2), quiet, PARTY, (), PCS), ("Belleros", "Greyfen"))

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
        self.assertEqual(kept[0].present, ("Belleros", "Greyfen", "Tavin", "Ysolde"))


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
        self.assertIn("Ysolde was there too", got[0])
        self.assertNotIn("travelled", got[0])  # the sentence is never quoted (it may open secret)

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
            rd.Prose(text, ("soft",)), beats=[CAMP], transcript="", allowed_names=PCS,
            secret_names=[], pcs=PCS,
        )  # fmt: skip
        self.assertTrue(any("was there too" in p for p in got))


class TheWritersNames(unittest.TestCase):
    def test_the_writer_is_given_the_story_memory_names_not_the_misheard_spelling(self) -> None:
        beat = bt.Beat(1, "Tavin lit the fire.", ("Tavin",), (1,), "event", present=("Tavin",))
        names = [n for n in NAMES if n not in ALIASES]
        message = rd.prose_message([beat], "", names, None)
        listed = message.split("<names>")[1].split("</names>")[0]
        self.assertIn("Tavin", listed)
        self.assertNotIn("Sarek", listed)
        self.assertIn("Present: Tavin.", message)


class TheDoubts(unittest.TestCase):
    """Places the check must not refuse a healthy recap."""

    def test_a_pair_sentence_in_a_bigger_scene_passes(self) -> None:
        got = rd.presence_problems(
            "Belleros handed Greyfen the key at the river camp.", [CAMP], PCS
        )
        self.assertEqual(got, [])

    def test_a_beat_that_names_all_four_is_enough_without_a_group_word(self) -> None:
        """The owner's case: the beat names every character, no "party", no "together"."""
        beat = bt.Beat(
            1, "Belleros, Greyfen, Tavin and Ysolde ride to the river and make camp.",
            ("Belleros", "Greyfen", "Tavin", "Ysolde"), (1, 8), "event", present=tuple(PCS),
        )  # fmt: skip
        text = "Belleros, Greyfen and Tavin rode to the river and made camp."
        got = rd.presence_problems(text, [beat], PCS)
        self.assertEqual(len(got), 1)
        self.assertIn("Ysolde was there too", got[0])
        ways = (
            "The party rode to the river and made camp.",
            "Everyone rode to the river and made camp.",
            "Belleros, Greyfen and Tavin, with the group, rode to the river and made camp.",
        )
        for way_out in ways:
            self.assertEqual(rd.presence_problems(way_out, [beat], PCS), [], way_out)

    def test_together_is_not_a_way_out(self) -> None:
        beat = bt.Beat(
            1, "Belleros, Greyfen, Tavin and Ysolde ride to the river and make camp.",
            ("Belleros", "Greyfen", "Tavin", "Ysolde"), (1, 8), "event", present=tuple(PCS),
        )  # fmt: skip
        text = "Belleros, Greyfen and Tavin together rode to the river and made camp."
        self.assertEqual(len(rd.presence_problems(text, [beat], PCS)), 1)

    def test_a_vague_match_is_not_judged(self) -> None:
        got = rd.presence_problems("Belleros, Greyfen and Tavin sat down.", [CAMP], PCS)
        self.assertEqual(got, [])

    def test_the_last_try_is_not_ended_by_the_scene_check(self) -> None:
        text = "《Calm》 Belleros, Greyfen and Tavin travelled to the river and made camp."
        prose = rd.Prose(text, ("soft",))
        kw = dict(beats=[CAMP], transcript="", allowed_names=PCS, secret_names=[], pcs=PCS)
        self.assertTrue(any("was there too" in p for p in rd.check_prose(prose, **kw)))  # type: ignore[arg-type]
        self.assertFalse(
            any("was there too" in p for p in rd.check_prose(prose, last=True, **kw))  # type: ignore[arg-type]
        )

    def test_a_secret_name_opening_the_sentence_is_never_in_the_message(self) -> None:
        text = "Vaelor, Belleros, Greyfen and Tavin travelled to the river and made camp."
        got = rd.presence_problems(text, [CAMP], PCS)
        self.assertEqual(len(got), 1)
        self.assertNotIn("Vaelor", got[0])

    def test_a_dm_narrated_scene_counts_every_player_who_spoke_in_the_session(self) -> None:
        speakers = {10: 0, 500: 1, 900: 2, 901: 3}  # the DM (0) narrates; players speak elsewhere
        party = {1: ("Belleros",), 2: ("Greyfen",), 3: ("Tavin",)}
        got = bt.present_in((10,), speakers, party, (), PCS)
        self.assertEqual(got, ("Belleros", "Greyfen", "Tavin"))

    def test_a_stored_present_that_is_not_a_list_is_nothing(self) -> None:
        beat = {"n": 1, "what": "x", "who": [], "lines": [1], "kind": "event", "present": "Tavin"}
        self.assertEqual(bt.from_json([beat])[0].present, ())


class TheAliasEdges(unittest.TestCase):
    def test_an_alias_that_starts_a_longer_known_name_leaves_it_alone(self) -> None:
        got = canonical(
            "Sarek Dawnbringer opened the gate.",
            ALIASES,
            names=[*NAMES, "Sarek Dawnbringer"],
            secret_names=[],
        )
        self.assertEqual(got, "Sarek Dawnbringer opened the gate.")

    def familiar(self, text: str, alias: str = "Doran") -> str:
        return canonical(
            text,
            {alias: "Doran Ashvale"},
            names=["Doran Ashvale", "Tavin"],
            secret_names=[],
            sound_names=["Doran Ashvale"],
        )

    def test_the_familiar_first_name_stays_and_the_full_name_is_never_doubled(self) -> None:
        for text in (
            "Doran Ashvale stood at the gate.",
            "Doran stood at the gate.",
            "Then Doran Ashvale and Doran walked on. Doran spoke.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.familiar(text), text)

    def test_the_familiar_surname_stays_too(self) -> None:
        for text in ("Ashvale stood at the gate.", "Doran Ashvale and Ashvale walked on."):
            with self.subTest(text=text):
                self.assertEqual(self.familiar(text, alias="Ashvale"), text)

    def test_a_misheard_spelling_of_the_same_character_is_still_put_right(self) -> None:
        got = canonical(
            "Dorin stood. Doran Ashvale spoke. Doran nodded.",
            {"Doran": "Doran Ashvale", "Dorin": "Doran Ashvale"},
            names=["Doran Ashvale", "Tavin"],
            secret_names=[],
        )
        self.assertEqual(got, "Doran Ashvale stood. Doran Ashvale spoke. Doran nodded.")

    def test_a_rewrite_never_leaves_a_name_followed_by_its_own_last_word(self) -> None:
        got = canonical(
            "Sarek Ashvale arrived. Then Sarek spoke.",
            {"Sarek": "Tavin Ashvale"},
            names=["Tavin Ashvale", "Tavin"],
            secret_names=[],
        )
        self.assertNotIn("Ashvale Ashvale", got)

    def test_a_doubling_the_writer_made_itself_is_left_alone(self) -> None:
        self.assertEqual(
            put("Bora Bora lay ahead and Sarek nodded."), "Bora Bora lay ahead and Tavin nodded."
        )

    def test_the_whole_path_keeps_a_familiar_name(self) -> None:
        beat = bt.Beat(1, "Doran stood.", ("Doran Ashvale",), (1,), "event")
        prose = rd.Prose("《Calm》 Doran Ashvale stood. Doran spoke. Doran nodded.", ("strings",))
        fixed, _ = rd.respelled(
            prose,
            beats=[beat],
            allowed_names=["Doran Ashvale"],
            secret_names=[],
            transcript="doran stood",
            aliases={"Doran": "Doran Ashvale"},
        )
        self.assertEqual(fixed.summary, "《Calm》 Doran Ashvale stood. Doran spoke. Doran nodded.")

    def test_a_new_name_is_never_turned_into_an_npc(self) -> None:
        got = canonical(
            "Brandy lit the lamp.",
            ALIASES,
            names=[*NAMES, "Brand"],
            secret_names=[],
            sound_names=["Tavin"],
        )
        self.assertEqual(got, "Brandy lit the lamp.")

    def test_a_spelling_that_is_another_entrys_real_name_is_not_an_alias(self) -> None:
        tavin = entity("Tavin", 7)
        sarek = entity("Sarek", None, "npc")
        names = SimpleNamespace(
            entities={"e1": tavin, "e2": sarek},
            names=[entry("Sarek")],  # an alias row of e1 spelt like the NPC "Sarek"
            fixes={},
        )
        aliases, _, _ = recap_people(names, ["Tavin", "Sarek"])
        self.assertEqual(aliases, {})

    def test_a_bad_played_by_costs_the_presence_list_not_the_recap(self) -> None:
        bad = entity("Tavin", "x")  # type: ignore[arg-type]  # a bad stored value, on purpose
        names = SimpleNamespace(entities={"e1": bad}, names=[], fixes={})
        self.assertEqual(recap_people(names, ["Tavin"]), ({}, [], {}))
