"""The Transcript Cleaner's name fixes (#127), without Discord or a database: the
mishearings it must fix and the traps it must leave alone (docs/PLAN.md)."""

import time
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
    name_key,
)
from dmbot.transcript.cleaner import (
    DM_FIX,
    SOUND,
    SPELLING,
    Vocabulary,
    characters,
    clean,
    likeness,
    own_name,
)

BELLEROS, CERRIC, KAZETH, TOWN, GUESS, TRIBE, THORIN, MAREN, MARRON = (c * 32 for c in "abcdefghi")
MIA, DEE = 8, 9
# Every entry in the scene: the traps must hold even then.
EVERYONE = frozenset((BELLEROS, CERRIC, KAZETH, TOWN, GUESS, TRIBE, THORIN, MAREN, MARRON))


def entity(
    eid: str,
    name: str,
    kind: str = "npc",
    status: str = CONFIRMED,
    *,
    played_by: int | None = None,
    created_at: int = 0,
) -> Entity:
    return Entity(eid, kind, name, "", status, None, "dm", created_at, played_by)


def alias(eid: str, text: str, *, secret: bool = False, status: str = CONFIRMED) -> Alias:
    aid = eid[:16] + name_key(text).replace(" ", "").ljust(16, "0")[:16]
    return Alias(aid, eid, text, name_key(text), "full", None, secret, status, (), "dm", 0)


def correction(heard: str, entity_id: str | None, action: str) -> Correction:
    return Correction(name_key(heard).ljust(32, "0")[:32], heard, name_key(heard),
                      entity_id, action, "dm", 0)  # fmt: skip


def lookup(
    *,
    more: tuple[Entity, ...] = (),
    more_aliases: tuple[Alias, ...] = (),
    corrections: tuple[Correction, ...] = (),
) -> CampaignLookup:
    entities = (
        entity(BELLEROS, "Belleros"),
        entity(CERRIC, "Cerric", "player_character", played_by=MIA),
        entity(KAZETH, "Ka'zeth"),
        entity(TOWN, "Bryn Shander", "place"),
        entity(GUESS, "Hrothgar", "concept", PROPOSED),
        entity(TRIBE, "Frostwolf tribe", "faction"),
        entity(THORIN, "Thorin"),
        *more,
    )
    aliases = (
        alias(BELLEROS, "Belleros"),
        alias(BELLEROS, "the hooded stranger", secret=True),
        alias(CERRIC, "Cerric"),
        alias(KAZETH, "Ka'zeth"),
        alias(TOWN, "Bryn Shander"),
        alias(GUESS, "Hrothgar", status=PROPOSED),
        alias(TRIBE, "Frostwolf tribe"),
        alias(TRIBE, "Frostwolves"),
        alias(THORIN, "Thorin"),
        *more_aliases,
    )
    return CampaignLookup.build(LookupData(1, entities, aliases, corrections, ()))


def text(heard: str, names: CampaignLookup | None = None, **kwargs: object) -> str:
    kwargs.setdefault("scene", EVERYONE)
    return clean(names or lookup(), heard, **kwargs).text  # type: ignore[arg-type]


class MishearingsTest(unittest.TestCase):
    def test_a_name_heard_misspelled_mid_sentence_is_fixed(self) -> None:
        result = clean(lookup(), "I think Beleros has the key.", scene=EVERYONE)
        self.assertEqual(result.text, "I think Belleros has the key.")
        (fix,) = result.fixes
        self.assertEqual((fix.heard, fix.written, fix.entity_id, fix.how),
                         ("Beleros", "Belleros", BELLEROS, SOUND))  # fmt: skip
        self.assertEqual(fix.start, len("I think "))

    def test_the_serrated_blade_sentence_only_has_the_name_fixed(self) -> None:
        heard = "So Beleros has a serrated blade so he will saw through the rope"
        self.assertEqual(
            text(heard), "So Belleros has a serrated blade so he will saw through the rope"
        )

    def test_a_name_split_in_two_is_joined(self) -> None:
        self.assertEqual(text("we meet Ka Zeth at dawn"), "we meet Ka'zeth at dawn")

    def test_the_same_letters_spelled_another_way(self) -> None:
        result = clean(lookup(), "we meet Kazeth at dawn")
        self.assertEqual(result.text, "we meet Ka'zeth at dawn")
        self.assertEqual(result.fixes[0].how, SPELLING)

    def test_a_name_with_a_missing_capital(self) -> None:
        self.assertEqual(text("we ride to Bryn shander"), "we ride to Bryn Shander")

    def test_a_possessive_keeps_its_ending(self) -> None:
        self.assertEqual(text("that is Beleros's sword"), "that is Belleros's sword")

    def test_a_spelling_the_dm_fixed(self) -> None:
        names = lookup(corrections=(correction("Sara", CERRIC, FIX),))
        result = clean(names, "Sara draws her bow")
        self.assertEqual(result.text, "Cerric draws her bow")
        self.assertEqual(result.fixes[0].how, DM_FIX)

    def test_a_name_is_written_the_way_it_was_said(self) -> None:
        # "Frostwolves" is the tribe's other name: not changed to "Frostwolf tribe"
        self.assertEqual(text("we track the Frostwolfs north"), "we track the Frostwolves north")

    def test_a_name_starting_a_sentence_once_seen_mid_sentence(self) -> None:
        vocabulary = Vocabulary()
        vocabulary.note(DEE, "I saw Beleros earlier")
        self.assertEqual(
            text("Beleros has a serrated blade", vocabulary=vocabulary),
            "Belleros has a serrated blade",
        )
        # within one line too
        self.assertEqual(text("Beleros runs. I follow Beleros"), "Belleros runs. I follow Belleros")

    def test_several_fixes_in_one_line(self) -> None:
        result = clean(lookup(), "then Beleros and Ka Zeth ride to Bryn shander", scene=EVERYONE)
        self.assertEqual(result.text, "then Belleros and Ka'zeth ride to Bryn Shander")
        self.assertEqual(len(result.fixes), 3)


class TrapsTest(unittest.TestCase):
    def test_a_sentence_that_makes_sense_is_never_changed(self) -> None:
        for heard in (
            "he has a serrated blade so he will saw through the rope",
            "ring the bell and wait",
            "Sorry, I rolled a three",
            "Then we go north",
        ):
            with self.subTest(heard=heard):
                self.assertEqual(text(heard), heard)

    def test_a_capital_starting_a_sentence_is_no_evidence(self) -> None:
        # Thorn sounds like Thorin, but it's a real word starting a sentence
        self.assertEqual(text("Thorn bushes everywhere"), "Thorn bushes everywhere")

    def test_a_word_said_in_lower_case_is_a_word(self) -> None:
        vocabulary = Vocabulary()
        vocabulary.note(DEE, "careful, a thorn")
        self.assertEqual(text("I step on a Thorn", vocabulary=vocabulary), "I step on a Thorn")
        self.assertEqual(text("a thorn, a Thorn"), "a thorn, a Thorn")

    def test_lower_case_words_are_never_joined_into_a_name(self) -> None:
        self.assertEqual(text("ring the Bell or us all die"), "ring the Bell or us all die")

    def test_a_lower_case_real_word_keeps_its_capitals(self) -> None:
        names = lookup(more=(entity(MAREN, "Bell"),), more_aliases=(alias(MAREN, "Bell"),))
        self.assertEqual(text("ring the bell", names), "ring the bell")

    def test_the_secret_identity_is_never_revealed(self) -> None:
        heard = "the hooded stranger walks in"
        self.assertEqual(text(heard), heard)

    def test_a_word_sounding_like_a_secret_name_is_left_alone(self) -> None:
        names = lookup(more_aliases=(alias(BELLEROS, "Valen", secret=True),))
        self.assertEqual(text("then Valin left", names), "then Valin left")

    def test_a_dm_fix_never_rewrites_a_secret_name(self) -> None:
        names = lookup(corrections=(correction("the hooded stranger", BELLEROS, FIX),))
        self.assertEqual(text("the hooded stranger waits", names), "the hooded stranger waits")

    def test_a_suggested_name_never_makes_a_fix(self) -> None:
        self.assertEqual(text("then Hrothgarr roars"), "then Hrothgarr roars")

    def test_two_names_sounding_alike_are_left_alone(self) -> None:
        names = lookup(
            more=(entity(MAREN, "Maren"), entity(MARRON, "Marron")),
            more_aliases=(alias(MAREN, "Maren"), alias(MARRON, "Marron")),
        )
        self.assertEqual(text("then Marin speaks", names), "then Marin speaks")

    def test_a_proposed_entry_sounding_alike_makes_it_unsure(self) -> None:
        names = lookup(
            more=(entity(MAREN, "Belleras", status=PROPOSED),),
            more_aliases=(alias(MAREN, "Belleras", status=PROPOSED),),
        )
        self.assertEqual(text("then Beleros speaks", names), "then Beleros speaks")

    def test_keep_as_heard(self) -> None:
        names = lookup(corrections=(correction("Beleros", None, KEEP),))
        self.assertEqual(text("then Beleros speaks", names), "then Beleros speaks")

    def test_people_at_the_table_are_never_changed(self) -> None:
        self.assertEqual(
            text("thanks Belaros", people=["Belaros"]),
            "thanks Belaros",
        )

    def test_spelled_too_differently(self) -> None:
        self.assertLess(likeness("Plrs", "Belleros"), 0.7)
        self.assertEqual(text("then Pillars fall"), "then Pillars fall")

    def test_common_words_and_game_terms_are_never_names(self) -> None:
        names = lookup(more=(entity(MAREN, "Perseption"),),
                       more_aliases=(alias(MAREN, "Perseption"),))  # fmt: skip
        self.assertEqual(text("roll a Perception check", names), "roll a Perception check")

    def test_short_words_are_left_alone(self) -> None:
        names = lookup(more=(entity(MAREN, "Ilo"),), more_aliases=(alias(MAREN, "Ilo"),))
        self.assertEqual(text("then Ila said", names), "then Ila said")

    def test_a_known_name_is_not_changed_by_sound(self) -> None:
        # Cerric is right as heard; nothing else sounding alike may replace it
        self.assertEqual(text("then Cerric said"), "then Cerric said")

    def test_an_empty_line(self) -> None:
        self.assertEqual(clean(lookup(), "").text, "")


class ReviewTrapsTest(unittest.TestCase):
    """Wrong fixes found in review (#127): real names, secret names misheard."""

    def test_a_single_word_needs_the_name_in_the_scene(self) -> None:
        self.assertEqual(text("I think Beleros has it", scene=()), "I think Beleros has it")
        self.assertEqual(
            text("I think Beleros has it", scene={BELLEROS}), "I think Belleros has it"
        )

    def test_a_split_name_needs_no_scene(self) -> None:
        self.assertEqual(text("we meet Ka Zeth", scene=()), "we meet Ka'zeth")

    def test_a_player_character_needs_the_scene_too(self) -> None:
        self.assertEqual(text("then Ceric shoots", scene=()), "then Ceric shoots")
        self.assertEqual(text("then Ceric shoots", scene={CERRIC}), "then Cerric shoots")

    def test_a_real_first_name_is_not_pulled_into_a_players_character(self) -> None:
        # Mara is a player's character and in the scene; "Mary" is spelled 0.75 alike
        names = lookup(
            more=(entity(MAREN, "Mara", "player_character", played_by=DEE),),
            more_aliases=(alias(MAREN, "Mara"),),
        )
        self.assertEqual(text("so Mary, your turn", names), "so Mary, your turn")

    def test_real_names_and_brands_outside_the_scene_stay(self) -> None:
        names = lookup(
            more=(entity(MAREN, "Mara"), entity(MARRON, "Amazonia", "place")),
            more_aliases=(alias(MAREN, "Mara"), alias(MARRON, "Amazonia")),
        )
        for heard in ("I saw Mary at the shop", "I ordered from Amazon yesterday"):
            with self.subTest(heard=heard):
                self.assertEqual(text(heard, names, scene={BELLEROS}), heard)

    def test_a_dm_fix_never_lands_inside_a_misheard_secret_name(self) -> None:
        names = lookup(
            more_aliases=(alias(BELLEROS, "Silas Vane", secret=True),),
            corrections=(correction("Vain", BELLEROS, FIX), correction("Strangr", BELLEROS, FIX)),
        )
        self.assertEqual(text("I met Silas Vain today", names), "I met Silas Vain today")
        self.assertEqual(text("I met the hooded strangr", names), "I met the hooded strangr")

    def test_nothing_changes_inside_a_long_secret_name(self) -> None:
        names = lookup(
            more_aliases=(alias(BELLEROS, "the Red Lady of the Kazeth Hills", secret=True),)
        )
        heard = "I met the Red Lady of the Kazeth Hills"
        self.assertEqual(text(heard, names), heard)

    def test_a_right_name_with_an_ending_is_no_fix(self) -> None:
        self.assertEqual(clean(lookup(), "I said Belleros's", scene=EVERYONE).fixes, ())

    def test_all_capitals_are_left_alone(self) -> None:
        self.assertEqual(text("I saw BELEROS today"), "I saw BELEROS today")

    def test_the_first_word_of_a_display_name_is_protected(self) -> None:
        self.assertEqual(text("thanks Belaros", people=["Belaros J"]), "thanks Belaros")


class MoreTrapsTest(unittest.TestCase):
    def test_one_speakers_lower_case_word_protects_anothers_capital(self) -> None:
        vocabulary = Vocabulary()
        vocabulary.note(MIA, "careful, a thorn")
        self.assertEqual(text("I see a Thorn", vocabulary=vocabulary), "I see a Thorn")
        vocabulary.forget_speaker(MIA)
        self.assertEqual(text("I see a Thorn", vocabulary=vocabulary), "I see a Thorin")

    def test_words_split_by_punctuation_are_not_joined(self) -> None:
        self.assertEqual(text("we meet Ka, Zeth"), "we meet Ka, Zeth")

    def test_a_curly_possessive(self) -> None:
        self.assertEqual(text("that is Beleros’s sword"), "that is Belleros’s sword")


class SpeedTest(unittest.TestCase):
    def test_a_very_long_secret_name_stays_quick(self) -> None:
        # #295: every run length was tried around each fix (over 0.5 s a line here)
        secret = " ".join(f"word{i}" for i in range(32))
        names = lookup(more_aliases=(alias(BELLEROS, secret, secret=True),))
        line = " ".join(["then Beleros and the Wolf ran toward Bryn shander"] * 6)
        started = time.perf_counter()
        for _ in range(5):
            clean(names, line, scene=EVERYONE)
        self.assertLess((time.perf_counter() - started) / 5, 0.25)  # ~0.03 s; CI is slower


class VocabularyTest(unittest.TestCase):
    def test_forget_speaker(self) -> None:
        vocabulary = Vocabulary()
        vocabulary.note(DEE, "a thorn and Beleros")
        self.assertTrue(vocabulary.is_word("Thorn"))
        self.assertTrue(vocabulary.is_name("beleros"))
        vocabulary.forget_speaker(DEE)
        self.assertFalse(vocabulary.is_word("thorn"))
        self.assertFalse(vocabulary.is_name("beleros"))

    def test_bounded_keeping_the_latest(self) -> None:
        vocabulary = Vocabulary(limit=2)
        vocabulary.note(DEE, "one two three")
        vocabulary.note(DEE, "four")
        self.assertEqual(len(vocabulary.lower[DEE]), 2)
        self.assertTrue(vocabulary.is_word("four"))


class CharactersTest(unittest.TestCase):
    def test_each_player_gets_their_character(self) -> None:
        self.assertEqual(characters(lookup()), {MIA: "Cerric"})

    def test_the_newest_of_two(self) -> None:
        names = lookup(
            more=(entity(MAREN, "Maren", "player_character", played_by=MIA, created_at=5),),
            more_aliases=(alias(MAREN, "Maren"),),
        )
        self.assertEqual(characters(names), {MIA: "Maren"})

    def test_only_confirmed_and_never_secret(self) -> None:
        names = lookup(
            more=(
                entity(MAREN, "Maren", "player_character", PROPOSED, played_by=DEE),
                entity(MARRON, "Marron", "player_character", played_by=DEE),
            ),
            more_aliases=(alias(MAREN, "Maren"), alias(MARRON, "Marron", secret=True)),
        )
        self.assertEqual(characters(names), {MIA: "Cerric"})
        self.assertIsNone(own_name(names, MARRON))


if __name__ == "__main__":
    unittest.main()
