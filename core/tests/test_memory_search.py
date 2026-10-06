"""Finding a name (#126, step 2), without Discord or a database."""

import unittest

from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.memory.models import CONFIRMED, PROPOSED, Alias, Entity, HeardCount, name_key
from dmbot.memory.search import INSIDE, SOUND, START, WHOLE, WORD, find, said_lately

BELL, ULF, TOWN, GUESS = (c * 32 for c in "abcd")


def entity(eid: str, name: str, status: str = CONFIRMED) -> Entity:
    return Entity(eid, "npc", name, "", status, None, "dm", 0)


def alias(eid: str, text: str, *, secret: bool = False, status: str = CONFIRMED) -> Alias:
    from dmbot.memory.sounds import sound_codes

    aid = eid[:16] + name_key(text).replace(" ", "").ljust(16, "0")[:16]
    return Alias(
        aid, eid, text, name_key(text), "full", None, secret, status, sound_codes(text), "dm", 0
    )


def names(heard: tuple[HeardCount, ...] = ()) -> CampaignLookup:
    return CampaignLookup.build(
        LookupData(
            1,
            (
                entity(BELL, "Belleros"),
                entity(ULF, "Ulfgar"),
                entity(TOWN, "Bryn Shander"),
                entity(GUESS, "Hrothgar", PROPOSED),
            ),
            (
                alias(BELL, "Belleros"),
                alias(BELL, "Bell"),
                alias(BELL, "the hooded stranger", secret=True),
                alias(ULF, "Ulfgar"),
                alias(ULF, "the Frostwolf chief"),
                alias(TOWN, "Bryn Shander"),
                alias(GUESS, "Hrothgar", status=PROPOSED),
            ),
            (),
            (),
            heard,
        )
    )


class Find(unittest.TestCase):
    def test_best_matches_first(self) -> None:
        self.assertEqual(
            [(m.entity_id, m.how) for m in find(names(), "bell", secrets=False)], [(BELL, WHOLE)]
        )
        self.assertEqual(find(names(), "Bel", secrets=False)[0].how, START)
        self.assertEqual(find(names(), "shander", secrets=False)[0].how, WORD)
        self.assertEqual(find(names(), "rost", secrets=False)[0].how, INSIDE)
        self.assertEqual([m.entity_id for m in find(names(), "bell or us", secrets=False)], [BELL])
        self.assertEqual(find(names(), "bell or us", secrets=False)[0].how, SOUND)

    def test_secret_names_only_for_the_dm(self) -> None:
        self.assertEqual(find(names(), "hooded", secrets=False), [])
        (match,) = find(names(), "hooded", secrets=True)
        self.assertEqual((match.entity_id, match.secret), (BELL, True))

    def test_one_result_per_entry_with_its_own_name_preferred(self) -> None:
        results = find(names(), "b", secrets=True)
        self.assertEqual(len({m.entity_id for m in results}), len(results))
        self.assertEqual(next(m for m in results if m.entity_id == BELL).matched, "Belleros")

    def test_guesses_and_empty_searches_find_nothing(self) -> None:
        self.assertEqual(find(names(), "hroth", secrets=True), [])
        self.assertEqual(find(names(), "  ?! ", secrets=True), [])

    def test_said_lately_for_an_empty_type_ahead(self) -> None:
        heard = (HeardCount(BELL, 3, 100), HeardCount(ULF, 9, 200), HeardCount(GUESS, 50, 300))
        self.assertEqual(said_lately(names(heard)), [ULF, BELL])
