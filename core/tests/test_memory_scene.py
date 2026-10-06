"""Speech-to-text hints that follow the scene (#126, #127), without Discord or a database."""

import unittest

from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.memory.models import (
    CONFIRMED,
    FIX,
    KEEP,
    PROPOSED,
    Alias,
    Correction,
    Entity,
    Relation,
    name_key,
)
from dmbot.memory.scene import MAX_HINTS, SCENE_WINDOW_S, SceneTracker, mentions, scene_hints

TRIBE, CHIEF, CAMP, TOWN, CERRIC, GUESS, STRANGER = (c * 32 for c in "abcdefg")
MIA, DEE = 8, 9


def entity(eid: str, name: str, kind: str = "npc", status: str = CONFIRMED) -> Entity:
    return Entity(eid, kind, name, "", status, None, "dm", 0)


def alias(eid: str, text: str, *, secret: bool = False, status: str = CONFIRMED) -> Alias:
    aid = eid[:16] + name_key(text).replace(" ", "").ljust(16, "0")[:16]
    return Alias(aid, eid, text, name_key(text), "full", None, secret, status, (), "dm", 0)


def link(
    a: str, b: str, status: str = CONFIRMED, n: str = "1", *, secret: bool = False
) -> Relation:
    return Relation(n * 32, a, "member_of", b, "", 1.0, status, "dm", (),
                    None, None, None, None, secret, 0)  # fmt: skip


def lookup(
    *relations: Relation, more: tuple[Entity, ...] = (), corrections: tuple[Correction, ...] = ()
) -> CampaignLookup:
    entities = (
        entity(TRIBE, "Frostwolf tribe", "faction"),
        entity(CHIEF, "Ulfgar"),
        entity(CAMP, "Wolf Hollow", "place"),
        entity(TOWN, "Bryn Shander", "place"),
        entity(CERRIC, "Cerric", "player_character"),
        entity(GUESS, "Hrothgar", "concept", PROPOSED),
        entity(STRANGER, "Belleros"),
        *more,
    )
    aliases = (
        alias(TRIBE, "Frostwolf tribe"),
        alias(TRIBE, "the Frostwolves"),
        alias(CHIEF, "Ulfgar"),
        alias(CHIEF, "the chief"),
        alias(CAMP, "Wolf Hollow"),
        alias(TOWN, "Bryn Shander"),
        alias(CERRIC, "Cerric"),
        alias(GUESS, "Hrothgar", status=PROPOSED),
        alias(STRANGER, "Belleros"),
        alias(STRANGER, "the hooded stranger", secret=True),
        *(alias(e.id, e.name) for e in more),
    )
    return CampaignLookup.build(LookupData(1, entities, aliases, corrections, relations))


class Mentions(unittest.TestCase):
    def test_names_in_a_line_are_found_whatever_the_case(self) -> None:
        names = lookup()
        found = mentions(names, "We follow THE FROSTWOLVES back to bryn shander, okay?")
        self.assertEqual(found, {TRIBE, TOWN})

    def test_secret_and_suggested_names_never_count(self) -> None:
        names = lookup()
        self.assertEqual(mentions(names, "The hooded stranger talks to Hrothgar."), set())

    def test_the_dms_fixed_spellings_count_and_keep_as_heard_doesnt(self) -> None:
        names = lookup(
            corrections=(
                Correction("1" * 32, "Ulf Gar", "ulf gar", CHIEF, FIX, "dm", 0),
                Correction("2" * 32, "Wolf Hollow", "wolf hollow", None, KEEP, "dm", 0),
            )
        )
        self.assertEqual(mentions(names, "ask ulf gar"), {CHIEF})
        self.assertEqual(mentions(names, "a wolf hollow tree"), set())


class Hints(unittest.TestCase):
    def test_the_chief_is_on_the_tip_of_the_tongue(self) -> None:
        # The owner's example: talk of the Frostwolf tribe, the chief never named yet.
        names = lookup(link(CHIEF, TRIBE), link(CAMP, TRIBE, n="2"))
        scene = SceneTracker()
        scene.note_line(names, "The Frostwolf tribe is camped by the river.", MIA, 100.0)
        hints = scene_hints(names, scene, 101.0, people=["Mia"])
        self.assertEqual(hints[:2], ["Cerric", "Mia"])  # characters and players always
        self.assertEqual(hints[2:4], ["Frostwolf tribe", "the Frostwolves"])  # the scene
        self.assertEqual(hints[4:6], ["Ulfgar", "the chief"])  # the tip of the tongue
        self.assertLess(hints.index("Wolf Hollow"), hints.index("Bryn Shander"))
        self.assertLess(hints.index("Mia"), hints.index("Bryn Shander"))  # the rest after
        self.assertLess(hints.index("Bryn Shander"), hints.index("Hrothgar"))  # guesses last
        self.assertNotIn("the hooded stranger", hints)

    def test_only_links_the_dm_confirmed_pull_names_in(self) -> None:
        names = lookup(link(CHIEF, TRIBE, status=PROPOSED))
        scene = SceneTracker()
        scene.note_line(names, "Frostwolf tribe", MIA, 0.0)
        hints = scene_hints(names, scene, 1.0, people=["Mia"])
        self.assertLess(hints.index("Bryn Shander"), hints.index("Ulfgar"))  # not pulled in

    def test_saying_a_secret_name_never_brings_in_the_real_one(self) -> None:
        names = lookup(link(STRANGER, TRIBE))
        scene = SceneTracker()
        scene.note_line(names, "The hooded stranger watches us.", MIA, 0.0)
        self.assertEqual(scene.scene(1.0), {})

    def test_a_secret_connection_never_pulls_a_name_in(self) -> None:
        names = lookup(link(CHIEF, TRIBE, secret=True))
        scene = SceneTracker()
        scene.note_line(names, "Frostwolf tribe", MIA, 0.0)
        hints = scene_hints(names, scene, 1.0)
        self.assertLess(hints.index("Bryn Shander"), hints.index("Ulfgar"))

    def test_someone_who_stops_being_recorded_no_longer_counts(self) -> None:
        names = lookup()
        scene = SceneTracker()
        scene.note_line(names, "Ulfgar", MIA, 0.0)
        scene.note_line(names, "Bryn Shander", DEE, 0.0)
        scene.forget_speaker(MIA)
        self.assertEqual(set(scene.scene(1.0)), {TOWN})

    def test_more_recent_and_more_often_comes_first(self) -> None:
        names = lookup()
        scene = SceneTracker()
        scene.note_line(names, "Bryn Shander", MIA, 0.0)
        scene.note_line(names, "Ulfgar", MIA, 300.0)
        hints = scene_hints(names, scene, 301.0)
        self.assertLess(hints.index("Ulfgar"), hints.index("Bryn Shander"))
        for t in (302.0, 303.0, 304.0):
            scene.note_line(names, "Bryn Shander", MIA, t)
        hints = scene_hints(names, scene, 305.0)
        self.assertLess(hints.index("Bryn Shander"), hints.index("Ulfgar"))

    def test_names_fade_out_of_the_scene_but_stay_ahead_of_the_rest(self) -> None:
        names = lookup()
        scene = SceneTracker()
        scene.note_line(names, "Wolf Hollow", MIA, 0.0)
        later = SCENE_WINDOW_S + 1
        self.assertEqual(scene.scene(later), {})
        self.assertEqual(scene.earlier(later), [CAMP])
        hints = scene_hints(names, scene, later, people=["Mia"])
        self.assertLess(hints.index("Mia"), hints.index("Wolf Hollow"))
        self.assertLess(hints.index("Wolf Hollow"), hints.index("Bryn Shander"))

    def test_one_hint_per_name_and_never_more_than_the_limit(self) -> None:
        many = tuple(entity(f"{i:032x}", f"Name{i}") for i in range(200))
        names = lookup(more=many)
        hints = scene_hints(names, SceneTracker(), 0.0, people=["cerric", "Mia"])
        self.assertEqual(len(hints), MAX_HINTS)
        self.assertEqual(len({name_key(h) for h in hints}), len(hints))
        self.assertNotIn("cerric", hints)  # same name as the character, already there

    def test_a_long_session_keeps_a_bounded_history(self) -> None:
        names = lookup()
        scene = SceneTracker()
        for t in range(1000):
            scene.note_line(names, "Ulfgar", MIA, float(t))
        self.assertLessEqual(len(scene.said[CHIEF].times), 50)
