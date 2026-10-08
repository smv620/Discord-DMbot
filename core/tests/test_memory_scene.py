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
    HeardCount,
    Relation,
    name_key,
)
from dmbot.memory.scene import (
    MAX_HINTS,
    SCENE_WINDOW_S,
    Found,
    SceneTracker,
    find_mentions,
    mentions,
    prepare,
    scene_hints,
)

TRIBE, CHIEF, CAMP, TOWN, CERRIC, GUESS, STRANGER = (c * 32 for c in "abcdefg")
MIA, DEE = 8, 9
NOW = 1_800_000_000.0  # Unix seconds
DAY = 86400


def entity(eid: str, name: str, kind: str = "npc", status: str = CONFIRMED) -> Entity:
    return Entity(eid, kind, name, "", status, None, "dm", 0)


def alias(eid: str, text: str, *, secret: bool = False, status: str = CONFIRMED) -> Alias:
    aid = eid[:16] + name_key(text).replace(" ", "").ljust(16, "0")[:16]
    return Alias(aid, eid, text, name_key(text), "full", None, secret, status, (), "dm", 0)


def link(
    a: str, b: str, status: str = CONFIRMED, n: str = "1", *, secret: bool = False
) -> Relation:
    return Relation(
        n * 32, a, "member_of", b, "", 1.0, status, "dm", (), None, None, None, None, secret, 0
    )


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
        hints = scene_hints(names, prepare(names, NOW), scene, 101.0, people=["Mia"])
        self.assertEqual(hints[:2], ["Cerric", "Mia"])  # characters and players always
        self.assertEqual(hints[2:4], ["Frostwolf tribe", "the Frostwolves"])  # the scene
        self.assertEqual(hints[4:6], ["Ulfgar", "the chief"])  # the tip of the tongue
        self.assertLess(hints.index("Wolf Hollow"), hints.index("Bryn Shander"))
        self.assertLess(hints.index("Mia"), hints.index("Bryn Shander"))  # the rest after
        self.assertLess(hints.index("Bryn Shander"), hints.index("Hrothgar"))  # guesses last
        self.assertNotIn("the hooded stranger", hints)

    def test_sheet_names_come_right_after_the_characters_never_a_secret_one(self) -> None:
        names = lookup()
        sheet = ["Test Spell", "The Hooded Stranger", "Test Feat"]  # #723
        hints = scene_hints(
            names, prepare(names, NOW), SceneTracker(), 1.0, people=["Mia"], sheet=sheet
        )
        self.assertEqual(hints[:4], ["Cerric", "Test Spell", "Test Feat", "Mia"])
        self.assertNotIn("The Hooded Stranger", hints)  # also a secret name here
        crowd = [f"Spell {i}" for i in range(100)]
        hints = scene_hints(names, prepare(names, NOW), SceneTracker(), 1.0, sheet=crowd)
        self.assertEqual(len(hints), 50)  # within the cap

    def test_people_not_at_the_table_come_last(self) -> None:
        names = lookup(link(CHIEF, TRIBE))
        scene = SceneTracker()
        hints = scene_hints(
            names, prepare(names, NOW), scene, 1.0, people=["Mia"], absent=["Oskar", "Tamsin"]
        )
        self.assertEqual(hints[:2], ["Cerric", "Mia"])  # at the table: right after characters
        self.assertEqual(hints[-2:], ["Oskar", "Tamsin"])  # agreed, but not here: last
        # A big server's absent members never crowd out the campaign's names.
        crowd = [f"Member {i}" for i in range(200)]
        hints = scene_hints(names, prepare(names, NOW), scene, 1.0, absent=crowd, limit=6)
        self.assertNotIn("Member 0", hints[:5])

    def test_only_links_the_dm_confirmed_pull_names_in(self) -> None:
        names = lookup(link(CHIEF, TRIBE, status=PROPOSED))
        scene = SceneTracker()
        scene.note_line(names, "Frostwolf tribe", MIA, 0.0)
        hints = scene_hints(names, prepare(names, NOW), scene, 1.0, people=["Mia"])
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
        hints = scene_hints(names, prepare(names, NOW), scene, 1.0)
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
        hints = scene_hints(names, prepare(names, NOW), scene, 301.0)
        self.assertLess(hints.index("Ulfgar"), hints.index("Bryn Shander"))
        for t in (302.0, 303.0, 304.0):
            scene.note_line(names, "Bryn Shander", MIA, t)
        hints = scene_hints(names, prepare(names, NOW), scene, 305.0)
        self.assertLess(hints.index("Bryn Shander"), hints.index("Ulfgar"))

    def test_names_fade_out_of_the_scene_but_stay_ahead_of_the_rest(self) -> None:
        names = lookup()
        scene = SceneTracker()
        scene.note_line(names, "Wolf Hollow", MIA, 0.0)
        later = SCENE_WINDOW_S + 1
        self.assertEqual(scene.scene(later), {})
        self.assertEqual(scene.earlier(later), [CAMP])
        hints = scene_hints(names, prepare(names, NOW), scene, later, people=["Mia"])
        self.assertLess(hints.index("Mia"), hints.index("Wolf Hollow"))
        self.assertLess(hints.index("Wolf Hollow"), hints.index("Bryn Shander"))

    def test_one_hint_per_name_and_never_more_than_the_limit(self) -> None:
        many = tuple(entity(f"{i:032x}", f"Name{i}") for i in range(200))
        names = lookup(more=many)
        hints = scene_hints(
            names, prepare(names, NOW), SceneTracker(), 0.0, people=["cerric", "Mia"]
        )
        self.assertEqual(len(hints), MAX_HINTS)
        self.assertEqual(len({name_key(h) for h in hints}), len(hints))
        self.assertNotIn("cerric", hints)  # same name as the character, already there

    def test_a_long_session_keeps_a_bounded_history(self) -> None:
        names = lookup()
        scene = SceneTracker()
        for t in range(1000):
            scene.note_line(names, "Ulfgar", MIA, float(t))
        self.assertLessEqual(len(scene.said[CHIEF]), 20)


class Guesses(unittest.TestCase):
    def guessed(self) -> CampaignLookup:
        names = lookup()
        data = LookupData(
            2,
            tuple(names.entities.values()),
            (
                *(
                    alias(e.entity_id, e.text, secret=e.secret, status=CONFIRMED)
                    for e in names.names
                    if e.confirmed or e.entity_id != GUESS
                ),
                alias(CHIEF, "Ulfy", status=PROPOSED),
                alias(GUESS, "Hrothgar", status=PROPOSED),
            ),
            (),
            (),
        )
        return CampaignLookup.build(data)

    def test_a_suggested_other_name_never_counts_or_jumps_ahead(self) -> None:
        names = self.guessed()
        self.assertEqual(mentions(names, "ask Ulfy"), set())
        scene = SceneTracker()
        scene.note_line(names, "Ulfgar", MIA, 0.0)
        hints = scene_hints(names, prepare(names, NOW), scene, 1.0)
        self.assertLess(hints.index("Wolf Hollow"), hints.index("Ulfy"))  # guesses last
        self.assertLess(hints.index("Bryn Shander"), hints.index("Hrothgar"))

    def test_a_shorter_name_inside_a_secret_one_adds_nothing(self) -> None:
        names = CampaignLookup.build(
            LookupData(
                1,
                (entity(STRANGER, "Belleros"),),
                (
                    alias(STRANGER, "Belleros"),
                    alias(STRANGER, "Vane"),
                    alias(STRANGER, "Lord Vane", secret=True),
                ),
                (),
                (),
            )
        )
        self.assertEqual(mentions(names, "Lord Vane arrives"), set())
        self.assertEqual(mentions(names, "Vane arrives"), {STRANGER})

    def test_at_most_three_names_per_entry(self) -> None:
        many = tuple(alias(CHIEF, f"Ulfgar the {w}") for w in ("Red", "Bold", "Tall", "Old"))
        base = lookup()
        names = CampaignLookup.build(
            LookupData(
                1,
                tuple(base.entities.values()),
                (
                    *(
                        alias(
                            e.entity_id,
                            e.text,
                            secret=e.secret,
                            status=CONFIRMED if e.confirmed else PROPOSED,
                        )
                        for e in base.names
                    ),
                    *many,
                ),
                (),
                (),
            )
        )
        scene = SceneTracker()
        scene.note_line(names, "Ulfgar", MIA, 0.0)
        hints = scene_hints(names, prepare(names, NOW), scene, 1.0)
        chief = [h for h in hints if h.startswith("Ulfgar") or h == "the chief"]
        self.assertEqual(hints[0:3], ["Cerric", "Ulfgar", chief[1]])
        self.assertEqual(hints.index("Ulfgar"), 1)
        self.assertEqual(len([h for h in hints[1:4] if h in chief]), 3)

    def test_no_hints_when_the_limit_is_zero(self) -> None:
        names = lookup()
        self.assertEqual(scene_hints(names, prepare(names, NOW), SceneTracker(), 0.0, limit=0), [])

    def test_forgetting_one_speaker_keeps_the_others(self) -> None:
        names = lookup()
        scene = SceneTracker()
        scene.note_line(names, "Ulfgar", MIA, 0.0)
        scene.note_line(names, "Ulfgar", DEE, 1.0)
        scene.forget_speaker(MIA)
        self.assertEqual(scene.said[CHIEF], [(1.0, DEE)])


def with_heard(
    names: CampaignLookup, heard: tuple[HeardCount, ...], recent: tuple[int, ...]
) -> CampaignLookup:
    data = LookupData(
        names.version + 1,
        tuple(names.entities.values()),
        tuple(
            alias(
                e.entity_id, e.text, secret=e.secret, status=CONFIRMED if e.confirmed else PROPOSED
            )
            for e in names.names
        ),
        (),
        (),
        heard,
        recent,
    )
    return CampaignLookup.build(data)


class StoredMentions(unittest.TestCase):
    def test_the_matcher_says_where_and_how(self) -> None:
        names = lookup(
            corrections=(Correction("1" * 32, "Ulf Gar", "ulf gar", CHIEF, FIX, "dm", 0),)
        )
        found = find_mentions(names, "Ask ulf gar about Bryn Shander.")
        self.assertEqual(
            sorted(found, key=lambda f: f.span),
            [Found(CHIEF, (4, 11), "spelling"), Found(TOWN, (18, 30), "exact")],
        )

    def test_last_sessions_then_most_said_then_old_names_never_said(self) -> None:
        last, before, old = int(NOW - 7 * DAY), int(NOW - 14 * DAY), int(NOW - 60 * DAY)
        names = with_heard(
            lookup(),
            (
                HeardCount(TOWN, 3, last),
                HeardCount(TRIBE, 9, before),
                HeardCount(CAMP, 40, old),  # said a lot, but not lately
                HeardCount(STRANGER, 1, int(NOW - 400 * DAY)),  # unsaid for over a year
            ),
            (last, before),
        )
        hints = scene_hints(names, prepare(names, NOW), SceneTracker(), 0.0)
        order = [
            hints.index(n) for n in ("Bryn Shander", "Frostwolf tribe", "Wolf Hollow", "Ulfgar")
        ]
        self.assertEqual(
            order, sorted(order)
        )  # last session, the one before, most said, then old never-said
        self.assertNotIn("Belleros", hints)  # dropped until said again

    def test_never_said_names_come_newest_first(self) -> None:
        newer = entity("9" * 32, "Zed")
        newer = Entity(newer.id, "npc", "Zed", "", CONFIRMED, None, "dm", 99)
        names = lookup(more=(newer,))
        hints = scene_hints(names, prepare(names, NOW), SceneTracker(), 0.0)
        self.assertLess(hints.index("Zed"), hints.index("Belleros"))
