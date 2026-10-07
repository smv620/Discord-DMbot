"""The Transcript Cleaner's name fixes (#127), without Discord or a database: the
mishearings it must fix and the traps it must leave alone (docs/PLAN.md)."""

import unittest
from unittest.mock import patch

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
from dmbot.memory.sounds import sound_codes
from dmbot.transcript import cleaner
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
    hooded: bool = True,
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
        *([alias(BELLEROS, "the hooded stranger", secret=True)] if hooded else []),
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

    def test_a_one_word_secret_split_in_three_still_blocks_a_fix(self) -> None:
        # #295 review: only secret names of very different lengths, so a 3-word run
        # must still be checked ("Silasvane" heard as "Si Las Vain")
        for long_secret in (
            (),
            (alias(BELLEROS, "the Red Lady of the Kazeth Hills", secret=True),),
        ):
            names = lookup(
                hooded=False,
                more_aliases=(alias(BELLEROS, "Silasvane", secret=True), *long_secret),
                corrections=(correction("Vain", BELLEROS, FIX),),
            )
            with self.subTest(long_secret=bool(long_secret)):
                self.assertEqual(text("I met Si Las Vain today", names), "I met Si Las Vain today")

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


class WrittenSecretTest(unittest.TestCase):
    """The line as written is checked too (#321 review): a DM's fixed spelling needs no
    likeness, so it could write a secret name the heard words never showed."""

    def test_a_dm_fix_never_completes_a_secret_name(self) -> None:
        names = lookup(
            more=(entity(MAREN, "Vane"),),
            more_aliases=(alias(MAREN, "Vane"), alias(BELLEROS, "Silas Vane", secret=True)),
            corrections=(correction("Bane", MAREN, FIX),),
        )
        result = clean(names, "I met Silas Bane today", scene=EVERYONE)
        self.assertEqual((result.text, result.fixes), ("I met Silas Bane today", ()))
        # the same rule elsewhere in the line still works
        self.assertEqual(
            text("Bane waits. I met Silas Bane", names), "Vane waits. I met Silas Bane"
        )

    def test_a_dm_rule_never_renames_someone_at_the_table(self) -> None:
        names = lookup(corrections=(correction("Sara", CERRIC, FIX),))
        self.assertEqual(text("thanks Sara", names, people=["Sara"]), "thanks Sara")

    def test_a_real_first_name_is_not_pulled_into_an_npc(self) -> None:
        names = lookup(more=(entity(MAREN, "Mara"),), more_aliases=(alias(MAREN, "Mara"),))
        self.assertEqual(text("so Mary, your turn", names), "so Mary, your turn")

    def test_a_secret_name_not_in_latin_letters(self) -> None:
        names = lookup(
            more=(entity(MAREN, "Вейна"),),
            more_aliases=(alias(MAREN, "Вейна"), alias(BELLEROS, "Сайлас Вейн", secret=True)),
            corrections=(correction("Бейн", MAREN, FIX),),
        )
        self.assertEqual(text("Я видел Сайлас Бейн", names), "Я видел Сайлас Бейн")
        self.assertEqual(text("Бейн ушёл", names), "Вейна ушёл")  # elsewhere it's fixed

    def test_two_fixes_together_never_form_a_secret(self) -> None:
        names = lookup(
            more=(entity(MAREN, "Vane"), entity(MARRON, "Thorne")),
            more_aliases=(
                alias(MAREN, "Vane"),
                alias(MARRON, "Thorne"),
                alias(BELLEROS, "Vane Thorne", secret=True),
            ),
            corrections=(correction("Bane", MAREN, FIX), correction("Horn", MARRON, FIX)),
        )
        self.assertEqual(text("I met Bane Horn", names), "I met Bane Horn")
        self.assertEqual(text("Bane left. Horn stayed.", names), "Vane left. Thorne stayed.")

    def test_rebuilt_until_no_secret_is_left(self) -> None:
        # Dropping one fix can expose another: Горн Торн, then Торн Дорн
        ids = [c * 32 for c in "jklm"]
        written = ["Торн", "Дорн", "Морн", "Корн"]
        heard = ["Горн", "Ворн", "Борн", "Порн"]
        names = lookup(
            more=tuple(entity(i, w) for i, w in zip(ids, written, strict=True)),
            more_aliases=(
                *(alias(i, w) for i, w in zip(ids, written, strict=True)),
                alias(BELLEROS, "Торн Дорн", secret=True),
                alias(BELLEROS, "Дорн Морн", secret=True),
                alias(BELLEROS, "Морн Корн", secret=True),
            ),
            corrections=tuple(correction(h, i, FIX) for h, i in zip(heard, ids, strict=True)),
        )
        result = clean(names, "Горн Ворн Борн Порн", scene=EVERYONE)
        for secret in ("Торн Дорн", "Дорн Морн", "Морн Корн"):
            self.assertNotIn(secret, result.text)

    def test_too_many_rebuilds_keep_the_line_as_heard(self) -> None:
        names = lookup(
            more=(entity(MAREN, "Vane"),),
            more_aliases=(alias(MAREN, "Vane"), alias(BELLEROS, "Silas Vane", secret=True)),
            corrections=(correction("Bane", MAREN, FIX),),
        )
        with patch.object(cleaner, "MAX_REBUILDS", 0), self.assertLogs(cleaner.log, "DEBUG"):
            result = clean(names, "Bane left", scene=EVERYONE)
        self.assertEqual((result.text, result.fixes), ("Bane left", ()))

    def test_a_dm_rule_keeps_the_possessive(self) -> None:
        names = lookup(
            more=(entity(MAREN, "Vane"),),
            more_aliases=(alias(MAREN, "Vane"),),
            corrections=(correction("Banes", MAREN, FIX),),
        )
        self.assertEqual(text("that is Bane's dog", names), "that is Vane's dog")

    def test_a_dm_rule_never_renames_someone_in_any_form(self) -> None:
        for rule, heard in (("Sara's", "it is Sara's turn"), ("Sara Bell", "thanks Sara Bell")):
            with self.subTest(rule=rule):
                names = lookup(corrections=(correction(rule, CERRIC, FIX),))
                self.assertEqual(text(heard, names, people=["Sara"]), heard)

    def test_no_checks_left_means_no_fixes(self) -> None:
        with patch.object(cleaner, "SECRET_CHECKS_PER_LINE", 0):
            result = clean(lookup(), "I think Beleros has the key.", scene=EVERYONE)
        self.assertEqual((result.text, result.fixes), ("I think Beleros has the key.", ()))


class SpeedTest(unittest.TestCase):
    """Work per line, counted rather than timed, so a busy test machine can't fail it
    (#311): the secret-name check codes runs of words, and that's what costs."""

    LINE = " ".join(["then Beleros and the Wolf ran toward Bryn shander"] * 6)

    def codes_used(self, names: CampaignLookup) -> int:
        with patch("dmbot.transcript.cleaner.sound_codes", wraps=sound_codes) as codes:
            clean(names, self.LINE, scene=EVERYONE)
        return codes.call_count

    def test_a_very_long_secret_name_stays_quick(self) -> None:
        # 20 words, within the 100-character limit; every length was tried before
        secret = " ".join(f"w{i:02d}" for i in range(20))
        names = lookup(more_aliases=(alias(BELLEROS, secret, secret=True),))
        self.assertEqual(clean(names, self.LINE, scene=EVERYONE).text.count("Belleros"), 6)
        self.assertLess(self.codes_used(names), 600)

    def test_many_secret_names_have_a_limit_per_line(self) -> None:
        secrets = tuple(
            alias(BELLEROS, " ".join(f"s{n}x{i}" for i in range(n)), secret=True)
            for n in range(1, 21)
        )
        names = lookup(more_aliases=secrets)
        # the heard words and the line as written each have one budget
        self.assertLessEqual(self.codes_used(names), 2 * cleaner.SECRET_CHECKS_PER_LINE + 60)


class QuestionTest(unittest.TestCase):
    """Two or three confirmed names sounding alike: the DM is asked (#296)."""

    def alike(self, *more: Entity, **kwargs: object) -> CampaignLookup:
        entities = (entity(MAREN, "Maren"), entity(MARRON, "Marron"), *more)
        return lookup(
            more=entities,
            more_aliases=tuple(alias(e.id, e.name) for e in entities),
            **kwargs,  # type: ignore[arg-type]
        )

    def test_two_names_sounding_alike_ask_the_dm(self) -> None:
        result = clean(self.alike(), "then Marin speaks", scene=EVERYONE)
        self.assertEqual(result.text, "then Marin speaks")  # left as heard
        (question,) = result.questions
        self.assertEqual(question.heard, "Marin")
        self.assertEqual({name for _, name in question.options}, {"Maren", "Marron"})
        self.assertEqual(question.start, len("then "))

    def test_no_scene_needed_to_ask(self) -> None:
        self.assertEqual(len(clean(self.alike(), "then Marin speaks", scene=()).questions), 1)

    def test_never_about_a_secret_name(self) -> None:
        names = lookup(
            more=(entity(MAREN, "Maren"), entity(MARRON, "Marron")),
            more_aliases=(alias(MAREN, "Maren"), alias(MARRON, "Marron", secret=True)),
        )
        self.assertEqual(clean(names, "then Marin speaks", scene=EVERYONE).questions, ())

    def test_never_with_a_suggested_name(self) -> None:
        names = lookup(
            more=(entity(MAREN, "Maren"), entity(MARRON, "Marron", status=PROPOSED)),
            more_aliases=(alias(MAREN, "Maren"), alias(MARRON, "Marron", status=PROPOSED)),
        )
        self.assertEqual(clean(names, "then Marin speaks", scene=EVERYONE).questions, ())

    def test_not_when_too_many_sound_alike(self) -> None:
        extra = tuple(
            entity(c * 32, n) for c, n in zip("jkl", ("Marun", "Moran", "Myren"), strict=True)
        )
        names = self.alike(*extra)
        self.assertEqual(clean(names, "then Marin speaks", scene=EVERYONE).questions, ())

    def test_not_next_to_a_secret_name(self) -> None:
        names = lookup(
            more=(entity(MAREN, "Maren"), entity(MARRON, "Marron")),
            more_aliases=(
                alias(MAREN, "Maren"),
                alias(MARRON, "Marron"),
                alias(BELLEROS, "Silas Marin", secret=True),
            ),
        )
        self.assertEqual(clean(names, "I met Silas Marin", scene=EVERYONE).questions, ())


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
