"""Who speaks for a character (#2562): a PC's own feeling or choice comes from its player, an NPC's
from the DM; what the DM says of a PC happened to it; what a player says of someone else's
character is what they said, never the truth. Pure: no database."""

from __future__ import annotations

import unittest

from dmbot.memory.claims import Extracted, Names, effect_note, judge
from dmbot.memory.models import NPC, PLAYER_CHARACTER
from dmbot.memory.ontology import Ontology
from dmbot.memory.voice import EFFECT, INNER_STATE, OWN, PARTY, PARTY_REASON, voice

ONTO = Ontology.build()
MIA, DMID, BOB = 7, 8, 9  # Mia plays Cerric; 8 is the DM; Bob is another player
CERRIC, GRISH, KEEP = "c" * 32, "g" * 32, "k" * 32
NAMES = Names(
    {
        "cerric": ((CERRIC, "character"),),
        "grish": ((GRISH, "character"),),
        "the keep": ((KEEP, "faction"),),
    },
    roles={CERRIC: (PLAYER_CHARACTER, MIA), GRISH: (NPC, None)},
)
EVERYONE = {MIA, DMID, BOB}


def says(subject: str, relationship: str, obj: str) -> Extracted:
    return Extracted(subject, relationship, obj, "", ("1",), "player_said")


class TheRule(unittest.TestCase):
    def test_a_pcs_own_feeling_said_by_its_player_is_its_own(self) -> None:
        self.assertEqual(
            voice(PLAYER_CHARACTER, "enemy_of", "player_said", [MIA], MIA, [DMID]), OWN
        )

    def test_the_same_said_by_the_dm_is_something_that_happened_to_it(self) -> None:
        self.assertEqual(
            voice(PLAYER_CHARACTER, "enemy_of", "dm_said", [DMID], MIA, [DMID]), EFFECT
        )

    def test_the_same_said_by_another_player_is_the_partys(self) -> None:
        self.assertEqual(
            voice(PLAYER_CHARACTER, "enemy_of", "player_said", [BOB], MIA, [DMID]), PARTY
        )
        self.assertEqual(  # (the player and another together are not the player alone)
            voice(PLAYER_CHARACTER, "enemy_of", "player_said", [MIA, BOB], MIA, [DMID]), PARTY
        )

    def test_a_pc_with_no_player_yet_is_never_its_own(self) -> None:
        self.assertEqual(
            voice(PLAYER_CHARACTER, "serves", "player_said", [MIA], None, [DMID]), PARTY
        )

    def test_an_npcs_feeling_said_by_a_player_is_the_partys_and_by_the_dm_is_the_truths(
        self,
    ) -> None:
        self.assertEqual(voice(NPC, "ally_of", "player_said", [MIA], None, [DMID]), PARTY)
        self.assertEqual(voice(NPC, "ally_of", "dm_said", [DMID], None, [DMID]), OWN)
        self.assertEqual(
            voice(None, "ally_of", "dm_said", [DMID], None, [DMID]), OWN
        )  # no role yet

    def test_what_is_not_an_inner_state_is_left_alone(self) -> None:
        for predicate in ("knows", "carries", "member_of", "kin_of", "did", "affected", "plays"):
            self.assertNotIn(predicate, INNER_STATE)
            self.assertEqual(voice(NPC, predicate, "player_said", [MIA], None, [DMID]), OWN)

    def test_no_known_speaker_is_not_the_characters_own(self) -> None:
        self.assertEqual(voice(NPC, "enemy_of", "dm_said", [None], None, [DMID]), PARTY)

    def test_every_inner_state_relationship_exists_in_the_ontology(self) -> None:
        self.assertTrue(set(ONTO.predicates) >= INNER_STATE)

    def test_the_list_is_the_flag_on_the_definitions(self) -> None:
        self.assertEqual(INNER_STATE, {"sworn_to", "ally_of", "enemy_of", "serves", "rival_of"})
        flagged = {key for key, term in ONTO.predicates.items() if term.inner_state}
        self.assertEqual(flagged, INNER_STATE)

    def test_the_flag_is_not_part_of_the_stored_meaning(self) -> None:
        # (setting it must not move a fingerprint: it changes who speaks, not what is said)
        from dataclasses import replace

        from dmbot.memory.definitions import definition, fingerprint

        term = ONTO.predicates["ally_of"]
        plain = replace(term, inner_state=False)
        self.assertEqual(fingerprint(term), fingerprint(plain))
        self.assertEqual(definition(term), definition(plain))


class TheClaims(unittest.TestCase):
    def judge(self, claim: Extracted, speakers: list[int | None]) -> str:
        return judge(claim, ONTO, NAMES, speakers, EVERYONE, None, {DMID})

    def test_a_pcs_player_saying_what_their_character_feels_goes_to_the_dm(self) -> None:
        self.assertEqual(self.judge(says("Cerric", "is an enemy of", "Grish"), [MIA]), "")

    def test_the_dm_saying_it_goes_to_the_dm_with_a_note_that_it_happened_to_them(self) -> None:
        claim = says("Cerric", "is an enemy of", "Grish")
        self.assertEqual(self.judge(claim, [DMID]), "")
        note = effect_note(claim, ONTO, NAMES, [DMID], {DMID})
        self.assertIn("Cerric", note)
        self.assertIn("not as Cerric's own", note)
        self.assertEqual(effect_note(claim, ONTO, NAMES, [MIA], {DMID}), "")  # their own: no note

    def test_another_player_saying_it_is_not_offered_as_true(self) -> None:
        self.assertEqual(self.judge(says("Cerric", "is an enemy of", "Grish"), [BOB]), PARTY_REASON)

    def test_a_player_saying_an_npcs_feeling_is_not_offered_as_true(self) -> None:
        self.assertEqual(self.judge(says("Grish", "is an ally of", "Cerric"), [MIA]), PARTY_REASON)
        self.assertEqual(self.judge(says("Grish", "is an ally of", "Cerric"), [DMID]), "")

    def test_what_is_not_an_inner_state_is_judged_as_before(self) -> None:
        self.assertEqual(self.judge(says("Grish", "knows", "Cerric"), [MIA]), "")

    def test_a_groups_attitude_stays_the_dms(self) -> None:
        self.assertEqual(self.judge(says("The Keep", "is an enemy of", "Grish"), [MIA]), "")


if __name__ == "__main__":
    unittest.main()
