"""📥 Add many against known names (#369), without Discord or a database."""

import unittest

from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.memory.models import CONFIRMED, PROPOSED, Alias, Entity, MoreNames, name_key
from dmbot.memory.name_list import parse
from dmbot.memory.sounds import sound_codes
from dmbot.ui.list_matches import KindDiffers, Plan, plan

BELL, AURIL, TOWN, ULF = (c * 32 for c in "abcd")


def entity(eid: str, name: str, kind: str) -> Entity:
    return Entity(eid, kind, name, "", CONFIRMED, None, "dm", 0)


def alias(eid: str, text: str, *, secret: bool = False) -> Alias:
    aid = eid[:16] + name_key(text).replace(" ", "").ljust(16, "0")[:16]
    return Alias(
        aid, eid, text, name_key(text), "full", None, secret, CONFIRMED, sound_codes(text), "dm", 0
    )


NAMES = CampaignLookup.build(
    LookupData(
        1,
        (
            entity(BELL, "Belleros", "npc"),
            entity(AURIL, "Auril", "deity"),
            entity(TOWN, "Bryn Shander", "place"),
            entity(ULF, "Ulfgar", "npc"),
        ),
        (
            alias(BELL, "Belleros"),
            alias(BELL, "Bell"),
            alias(BELL, "the hooded stranger", secret=True),
            alias(AURIL, "Auril"),
            alias(AURIL, "Frostmaiden"),
            alias(TOWN, "Bryn Shander"),
            alias(ULF, "Ulfgar"),
        ),
        (),
        (),
    )
)


def run(text: str, *, secrets: bool = True) -> Plan:
    return plan(parse(text, secrets=secrets).lines, NAMES, secrets=secrets)


class SameName(unittest.TestCase):
    def test_new_other_names_fold_into_the_known_name(self) -> None:
        p = run("Belleros | NPC | Bel, the old knight, Bell")
        self.assertEqual(p.new, [])
        self.assertEqual(p.more, [MoreNames(BELL, ("Bel", "the old knight"))])
        self.assertEqual((p.known, p.enriched, p.dropped), (1, 1, 0))

    def test_swapped_name_and_other_name_changes_nothing(self) -> None:
        p = run("Frostmaiden | god | Auril")
        self.assertEqual((p.new, p.more, p.known), ([], [], 1))
        self.assertEqual(p.swapped, [("Frostmaiden", "Auril")])

    def test_known_by_another_of_its_names_adds_the_name_too(self) -> None:
        p = run("the Frost Queen | god | Frostmaiden")
        self.assertEqual(p.more, [MoreNames(AURIL, ("the Frost Queen",))])
        self.assertEqual(p.swapped, [])

    def test_an_other_name_of_a_different_name_is_left_out(self) -> None:
        p = run("Ulfgar | NPC | Ulf, Bell")
        self.assertEqual(p.more, [MoreNames(ULF, ("Ulf",))])
        self.assertEqual(p.dropped, 1)

    def test_a_kind_that_differs_is_asked_never_changed(self) -> None:
        p = run("Auril | place\nAuril | town")
        self.assertEqual(p.kinds, [KindDiffers(AURIL, "Auril", "deity", "place")])
        self.assertEqual((p.new, p.more), ([], []))

    def test_other_or_no_kind_never_counts_as_different(self) -> None:
        self.assertEqual(run("Auril | other\nUlfgar").kinds, [])

    def test_a_secret_name_is_no_match_for_a_player(self) -> None:
        player = run("the hooded stranger | NPC", secrets=False)
        self.assertEqual([n.name for n in player.new], ["the hooded stranger"])
        self.assertEqual((player.known, player.near), (0, []))
        dm = run("the hooded stranger | NPC")
        self.assertEqual((dm.new, dm.known), ([], 1))


class NoGuessing(unittest.TestCase):
    def test_other_names_of_two_known_names_are_not_a_reason_to_join(self) -> None:
        p = run("Newname | NPC | Bell, Auril")
        self.assertEqual([(n.name, n.status, n.others) for n in p.new], [("Newname", PROPOSED, ())])
        self.assertEqual((p.more, p.known, p.dropped, p.look), ([], 0, 2, 1))

    def test_a_name_this_list_just_gave_is_listed_twice_not_known(self) -> None:
        p = run("Auril | god | Frosty\nFrosty | god")
        self.assertEqual(p.more, [MoreNames(AURIL, ("Frosty",))])
        self.assertEqual((p.known, p.repeated, p.swapped, p.kinds), (1, 1, [], []))

    def test_a_secret_name_is_never_a_near_match(self) -> None:
        p = run("the hooded strangr | NPC")  # joining would make the spelling public
        self.assertEqual(p.near, [])


class NearName(unittest.TestCase):
    def test_a_near_spelling_is_saved_as_a_suggestion_and_asked(self) -> None:
        p = run("Beleros | NPC")
        self.assertEqual([(n.name, n.status) for n in p.new], [("Beleros", PROPOSED)])
        (near,) = p.near
        self.assertEqual((near.position, near.like, near.entity_id), (0, "Belleros", BELL))
        self.assertEqual(p.look, 0)  # asked below, not counted for 📝 Check new names
        self.assertEqual(p.more, [])  # never joined on its own

    def test_one_word_needs_a_closer_spelling(self) -> None:
        p = run("Belros | NPC")  # 0.86 alike: close, but one word needs 0.9
        self.assertEqual(p.near, [])
        self.assertEqual(p.look, 1)  # still sounds like Belleros: waits for a check

    def test_a_different_kind_is_not_near(self) -> None:
        self.assertEqual(run("Beleros | place").near, [])

    def test_a_near_name_earlier_in_the_list(self) -> None:
        p = run("Thornewick | place\nThornwick | place")
        self.assertEqual([n.status for n in p.new], [CONFIRMED, PROPOSED])
        (near,) = p.near
        self.assertEqual((near.like, near.entity_id, near.like_position), ("Thornewick", None, 0))

    def test_a_new_name_and_its_other_name_listed_again_fold_together(self) -> None:
        p = run("Thornewick | place | Thorne\nThorne | place | the Wick")
        (only,) = p.new
        self.assertEqual(only.others, ("Thorne", "the Wick"))
        self.assertEqual(p.repeated, 1)


class Repeated(unittest.TestCase):
    def test_a_kind_given_later_counts(self) -> None:
        (line,) = parse("Ulfgar\nUlfgar | other\nUlfgar | NPC", secrets=True).lines
        self.assertEqual((line.kind, line.kind_word), ("npc", "NPC"))

    def test_repeated_lines_keep_every_other_name(self) -> None:
        parsed = parse("Ulfgar | NPC | Ulf\nulfgar | | the chief, Ulf", secrets=True)
        (line,) = parsed.lines
        self.assertEqual(line.others, ("Ulf", "the chief"))
        self.assertEqual(parsed.repeated, 1)


if __name__ == "__main__":
    unittest.main()


class Bounded(unittest.TestCase):
    """#369 perf-qa: one uploaded list can't keep the bot busy for minutes."""

    def count(self, text: str, names: CampaignLookup = NAMES) -> tuple[Plan, int]:
        from unittest.mock import patch

        from dmbot.transcript.cleaner import likeness as real
        from dmbot.ui import list_matches

        calls = 0

        def counted(a: str, b: str) -> float:
            nonlocal calls
            calls += 1
            return real(a, b)

        with patch.object(list_matches, "likeness", counted):
            p = plan(parse(text, secrets=True).lines, names, secrets=True)
        return p, calls

    def test_two_thousand_alike_lines_stay_within_the_budget(self) -> None:
        text = "\n".join(f"Bel{'l' * (n % 3)}er{'o' * (n % 2)}s{n} | NPC" for n in range(2000))
        p, calls = self.count(text)
        self.assertLessEqual(calls, list_matches_budget())
        self.assertEqual(len(p.new), 2000)
        # Anything left unchecked waits for the DM instead of being confirmed.
        self.assertTrue(all(n.status == PROPOSED for n in p.new[-100:]))

    def test_a_crowded_sound_is_not_compared_name_by_name(self) -> None:
        import itertools

        spellings = sorted(
            f"B{a}l{ll}{b}r{c}s" for a, b, c in itertools.product("aeiou", repeat=3) for ll in "l "
        )  # 250 ways to write one sound
        many = tuple(
            entity(f"{n:032x}", s.replace(" ", ""), "npc") for n, s in enumerate(spellings)
        )
        crowd = CampaignLookup.build(
            LookupData(1, many, tuple(alias(e.id, e.name) for e in many), (), ())
        )
        p, calls = self.count("Bellerrus | NPC", crowd)
        self.assertEqual(calls, 0)
        self.assertEqual([n.status for n in p.new], [PROPOSED])


def list_matches_budget() -> int:
    from dmbot.ui.list_matches import BUDGET

    return BUDGET
