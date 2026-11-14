"""People a lore text describes but never names (#2959): the checks, with invented text only."""

from __future__ import annotations

import unittest
from collections import Counter

from dmbot.memory import background as bg
from dmbot.memory import lore_read as lr
from dmbot.memory import unnamed_read as ur
from dmbot.memory.models import name_key
from dmbot.memory.who_resolve import UnnamedLink

PLAIN = (
    "Garrick Pell was born in Ashmere, the son of a stern ropemaker mother and an absent father."
)
QUOTE = "Garrick Pell was born in Ashmere, the son of a stern ropemaker mother and an absent father"


def line(anchor: str, role: str, what: str, quote: str = QUOTE) -> str:
    return f'- {anchor} | {role} | {what} | quote: "{quote}"'


def run(
    lines: list[str],
    text: str = PLAIN,
    links: list[bg.Connection] | None = None,
    names: tuple[str, ...] = ("Garrick Pell", "Ashmere"),
    *,
    people: tuple[str, ...] = (),
    places: tuple[str, ...] = (),
) -> tuple[list[UnnamedLink], Counter[str]]:
    lost: Counter[str] = Counter()
    keys = {name_key(n) for n in names}
    got = ur.read_unnamed(
        lines, text, {n: [] for n in names}, links or [], names, bg.reach_names(keys), lost,
        bg.reach_names({name_key(n) for n in people}), {name_key(n) for n in places},
    )  # fmt: skip
    return got, lost


class TheUnnamedRead(unittest.TestCase):
    def test_a_mother_and_a_father_the_text_never_names_are_kept(self) -> None:
        got, lost = run(
            [line("Garrick Pell", "mother", "stern ropemaker mother"),
             line("Garrick Pell", "father", "absent father")]
        )  # fmt: skip
        self.assertEqual(sum(lost.values()), 0)
        self.assertEqual([(u.fixed, u.what, u.role, u.how) for u in got], [
            ("Garrick Pell", "stern ropemaker mother", "parent", "kin_of"),
            ("Garrick Pell", "absent father", "parent", "kin_of"),
        ])  # fmt: skip
        self.assertTrue(all(not u.fixed_is_a and u.anchored for u in got))  # (they are the parents)
        self.assertEqual(got[0].quote, QUOTE)  # word for word

    def test_a_child_is_kept_the_other_way_round_and_a_sibling_has_no_order(self) -> None:
        text = "Garrick Pell, father of a lonely son, rode out."
        quote = "Garrick Pell, father of a lonely son, rode out"
        (son,), _ = run([line("Garrick Pell", "son", "lonely son", quote)], text)
        self.assertEqual((son.role, son.fixed_is_a), ("parent", True))  # (he is the parent)
        text = "Garrick Pell had an elder sister who kept the forge."
        (sis,), _ = run([line("Garrick Pell", "sister", "elder sister", text[:-1])], text)
        self.assertEqual(sis.role, "sibling")

    def test_a_sentence_with_no_named_subject_reaches_back_to_the_name(self) -> None:
        text = "Garrick Pell was born in Ashmere. Raised in wealth as the son of a weaver mother."
        quote = "Raised in wealth as the son of a weaver mother"
        (got,), lost = run([line("Garrick Pell", "mother", "weaver mother", quote)], text)
        self.assertEqual(lost["quote_reach"], 0)  # (one sentence back is the plain widening)
        self.assertTrue(got.quote.startswith("Garrick Pell was born"))
        self.assertIn(got.quote, text)

    def test_another_name_in_between_stops_the_reach(self) -> None:
        text = (
            "Garrick Pell was born in Ashmere. Old Tamsin Rook kept the inn. "
            "Everyone knew the weaver mother. Raised in wealth as the son of a weaver mother."
        )
        quote = "Raised in wealth as the son of a weaver mother"
        got, lost = run(
            [line("Garrick Pell", "mother", "weaver mother", quote)], text,
            names=("Garrick Pell", "Ashmere", "Tamsin Rook"),
        )  # fmt: skip
        self.assertEqual((got, lost["not_in_text"]), ([], 1))

    def test_a_person_the_text_names_anywhere_is_never_a_second_entry(self) -> None:
        # The name comes AFTER the description: this read already ties the end to a named father.
        named = bg.Connection(
            "Garrick Pell", "kin_of", "Hobb", "his father, Hobb, taught him", words="parent"
        )
        got, lost = run(
            [line("Garrick Pell", "mother", "stern ropemaker mother"),
             line("Garrick Pell", "father", "absent father")],
            links=[named],
        )  # fmt: skip
        self.assertEqual([u.what for u in got], ["stern ropemaker mother"])
        self.assertEqual(lost["already_named"], 1)
        # and a person whose words are a name of the read is no unnamed person
        got, lost = run(
            [line("Garrick Pell", "father", "absent father")],
            names=("Garrick Pell", "Absent Father"),
        )
        self.assertEqual((got, lost["already_named"]), ([], 1))

    def test_every_other_way_a_line_fails_is_counted_and_nothing_is_made(self) -> None:
        got, lost = run(
            [line("Garrick Pell", "mother", " ".join(["word"] * 13)),  # past 12 words: no phrase
             line("Garrick Pell", "mother", "kindly cook"),  # the words are not in the quote
             line("Garrick Pell", "mother", "stern ropemaker mother", "he was never born at all"),
             "- not a well formed line"]
        )  # fmt: skip
        self.assertEqual(got, [])
        self.assertEqual(
            (lost["word_not_allowed"], lost["not_in_text"], lost["unreadable"]), (1, 2, 1)
        )

    def test_the_same_person_twice_and_the_cap(self) -> None:
        _, lost = run([line("Garrick Pell", "mother", "stern ropemaker mother")] * 2)
        self.assertEqual(lost["same_pair"], 1)
        kinds = [f"k{i:02d}" for i in range(ur.MAX_UNNAMED + 5)]
        text = " ".join(f"Garrick Pell had an uncle of kind{c}." for c in kinds)
        many = [
            line(
                "Garrick Pell",
                "uncle",
                f"uncle of kind{c}",
                f"Garrick Pell had an uncle of kind{c}",
            )
            for c in kinds
        ]
        got, lost = run(many, text)
        self.assertEqual((len(got), lost["over_cap"]), (ur.MAX_UNNAMED, 5))

    def test_a_second_person_in_the_quote_leaves_it_unclear_whose_parent_it_is(self) -> None:
        text = "Garrick Pell met Tamsin Rook, the son of a stern ropemaker mother."
        quote = "Garrick Pell met Tamsin Rook, the son of a stern ropemaker mother"
        lost: Counter[str] = Counter()
        names = ("Garrick Pell", "Tamsin Rook")
        got = ur.read_unnamed(
            [line("Garrick Pell", "mother", "stern ropemaker mother", quote)], text, {}, [],
            names, set(), lost, people=bg.reach_names({name_key(n) for n in names}),
        )  # fmt: skip
        self.assertEqual((got, lost["not_in_text"]), ([], 1))
        # a place in the quote is no second person (Example 7 has one)
        got, _ = run([line("Garrick Pell", "mother", "stern ropemaker mother")])
        self.assertEqual(len(got), 1)

    def test_only_the_roles_the_prompt_offers_are_family_here(self) -> None:
        for role in ("wife", "grandfather", "child"):
            self.assertEqual(ur._kin(role) is not None, True, role)  # (the neutral words too)
        for role in ("apprentice", "rival", "mentor"):
            self.assertIsNone(ur._kin(role), role)

    def test_any_other_tie_is_kept_with_a_person_on_their_own(self) -> None:
        """Owner's rule (#3736): anyone the story mentions beside a named entry is kept. The line
        reads from the person to the named one; the person is not hung off it."""
        text = "At the Three Flags Sailing, a barmaid with red hair warned Garrick Pell."
        quote = "a barmaid with red hair warned Garrick Pell"
        cases = {
            "knows (neutral)": ("knows", ""),
            "is a rival of": ("rival_of", ""),
            "warned": ("associated_with", "warned"),
        }
        for role, (how, words) in cases.items():
            with self.subTest(role):
                got, _ = run([line("Garrick Pell", role, "barmaid with red hair", quote)], text)
                (u,) = got
                self.assertEqual(
                    (u.how, u.words, u.anchored, u.fixed_is_a, u.what),
                    (how, words, False, False, "barmaid with red hair"),
                )
        place = "At the Three Flags Sailing, a barmaid with red hair warned Garrick Pell"
        at_the_inn = line("Three Flags Sailing", "works in", "barmaid with red hair", place)
        names = ("Garrick Pell", "Three Flags Sailing")
        got, _ = run([at_the_inn], text, names=names, places=("Three Flags Sailing",))
        self.assertEqual((got[0].how, got[0].words), ("located_in", "works in"))
        # (a place word only on a place: Garrick Pell is no place)
        on_a_person = line("Garrick Pell", "works in", "barmaid with red hair", quote)
        got, lost = run([on_a_person], text, names=names, places=("Three Flags Sailing",))
        self.assertEqual((got, lost["word_not_allowed"]), ([], 1))

    def test_the_relationship_s_words_are_held_to_the_quote(self) -> None:
        """Nothing rides in that the text does not say, as for a relationship line (#3606)."""
        text = "At the Three Flags Sailing, a barmaid with red hair warned Garrick Pell."
        quote = "a barmaid with red hair warned Garrick Pell"
        for role in ("plotted to murder the king alongside", "w" * 60):
            with self.subTest(role[:10]):
                got, lost = run([line("Garrick Pell", role, "barmaid with red hair", quote)], text)
                self.assertEqual((got, lost["word_not_allowed"]), ([], 1))
        got, _ = run(
            [line("Garrick Pell", "knows (sworn enemy)", "barmaid with red hair", quote)], text
        )
        self.assertNotEqual(got[0].words, "sworn enemy")  # (no cue in "warned": stepped down)

    def test_someone_tied_to_two_named_people_in_one_quote_is_kept_for_each(self) -> None:
        text = "A barmaid with red hair warned Garrick Pell and Mira Vane."
        quote = "A barmaid with red hair warned Garrick Pell and Mira Vane"
        names = ("Garrick Pell", "Mira Vane")
        got, lost = run(
            [line("Garrick Pell", "warned", "barmaid with red hair", quote),
             line("Mira Vane", "warned", "barmaid with red hair", quote)],
            text, names=names, people=names,
        )  # fmt: skip
        self.assertEqual((sorted(u.fixed for u in got), sum(lost.values())), (list(names), 0))
        self.assertTrue(all(u.role == "" for u in got))  # (on their own: no role to an anchor)

    def test_the_head_word_is_no_little_word(self) -> None:
        text = "A man with a cane warned Garrick Pell."
        got, lost = run(
            [
                line(
                    "Garrick Pell",
                    "warned",
                    "man with cane",
                    "A man with a cane warned Garrick Pell",
                )
            ],
            text,
        )
        self.assertEqual((len(got), sum(lost.values())), (1, 0))

    def test_the_description_s_own_words_must_be_in_the_quote(self) -> None:
        text = "At the Three Flags Sailing, a barmaid with red hair warned Garrick Pell."
        got, lost = run(
            [line("Garrick Pell", "knows", "cook with a limp", "warned Garrick Pell")], text
        )
        self.assertEqual((got, lost["not_in_text"]), ([], 1))

    def test_a_description_that_shares_a_word_with_a_name_is_still_a_person(self) -> None:
        got, lost = run(
            [line("Garrick Pell", "mother", "stern ropemaker mother")],
            names=("Garrick Pell", "Ashmere", "Stern", "Ropemaker's Row"),
        )
        self.assertEqual((len(got), lost["already_named"]), (1, 0))

    def test_the_role_word_inside_a_longer_one_is_not_a_named_parent(self) -> None:
        in_law = bg.Connection(
            "Garrick Pell", "kin_of", "Hilda", "his mother-in-law, Hilda, came", words="parent"
        )
        got, lost = run([line("Garrick Pell", "mother", "stern ropemaker mother")], links=[in_law])
        self.assertEqual((len(got), lost["already_named"]), (1, 0))


class TheListParser(unittest.TestCase):
    def test_the_unnamed_heading_is_its_own_section(self) -> None:
        reply = (
            'NAMES\n- Garrick Pell | person | quote: "Garrick"\n\nRELATIONSHIPS\n\nUNNAMED\n'
            + line("Garrick Pell", "mother", "weaver mother")
        )
        listed = lr.parse_plain(reply)
        self.assertEqual((len(listed.names), len(listed.unnamed)), (1, 1))
        self.assertIn("UNNAMED", lr.plain_text(listed))

    def test_a_mother_and_a_father_of_one_character_are_two_lines_not_one(self) -> None:
        both = [line("Garrick Pell", "mother", "weaver mother"),
                line("Garrick Pell", "father", "absent father"),
                line("Garrick Pell", "father", "Absent Father")]  # fmt: skip
        self.assertEqual(len(lr.dedupe_unnamed(both)), 2)

    def test_the_long_heading_is_read_as_the_same_section(self) -> None:
        for heading in ("UNNAMED", "UNNAMED PEOPLE", "**Unnamed people:**"):
            listed = lr.parse_plain(
                f"{heading}\n{line('Garrick Pell', 'mother', 'weaver mother')}\n"
            )
            self.assertEqual(len(listed.unnamed), 1, heading)

    def test_the_seventh_example_reads_back_to_two_unnamed_parents(self) -> None:
        example = lr.EXAMPLES.split("Example 7.", 1)[1].split("Example 8.", 1)[0]
        document = example.split("<document>\n", 1)[1].split("\n</document>", 1)[0]
        listed = lr.parse_plain(example.split("Output:\n", 1)[1])
        lost: Counter[str] = Counter()
        got = ur.read_unnamed(
            listed.unnamed, document, {}, [], ["Garrick Pell", "Ashmere"], set(), lost
        )
        self.assertEqual(sum(lost.values()), 0)
        self.assertEqual({u.what for u in got}, {"stern ropemaker mother", "absent father"})

    def test_his_sister_his_child_gives_one_unnamed_child_and_the_sister_is_already_named(
        self,
    ) -> None:
        # The owner's shape (#3158): the sister is named elsewhere, the child never is, and the
        # text does not say a son or a daughter, so the neutral word is the role.
        text = (
            "Corvin Hale hunts across the dale before the shadow claims his sister, his child, "
            "and himself."
        )
        quote = "before the shadow claims his sister, his child, and himself"
        sister = bg.Connection(
            "Corvin Hale", "kin_of", "Pell", "his younger sister, Pell, hears it", words="sibling"
        )
        got, lost = run(
            [line("Corvin Hale", "sister", "his sister", quote),
             line("Corvin Hale", "child", "his child", quote)],
            text,
            links=[sister],
            names=("Corvin Hale", "Pell"),
        )  # fmt: skip
        self.assertEqual(
            [(u.what, u.role, u.fixed_is_a) for u in got], [("his child", "parent", True)]
        )
        self.assertEqual(lost["already_named"], 1)

    def test_the_prompt_offers_the_neutral_words_and_every_one_is_a_role_here(self) -> None:
        neutral = ("parent", "child", "sibling", "spouse", "grandparent", "grandchild")
        prompt = " ".join(lr.read_instructions().split())
        self.assertIn("use the neutral word when the text does not say", prompt)
        for word in neutral:
            self.assertIn(word, prompt)
            self.assertIn(word, ur.ROLES)
            self.assertIsNotNone(ur._kin(word))


class TheEighthExample(unittest.TestCase):
    def test_it_reads_back_to_one_barmaid_with_two_ties(self) -> None:
        example = lr.EXAMPLES.split("Example 8.", 1)[1]
        document = example.split("<document>\n", 1)[1].split("\n</document>", 1)[0]
        listed = lr.parse_plain(example.split("Output:\n", 1)[1])
        lost: Counter[str] = Counter()
        got = ur.read_unnamed(
            listed.unnamed,
            document,
            {},
            [],
            ["Tavin", "Three Flags Sailing"],
            set(),
            lost,
            people=bg.reach_names({"tavin"}),
            places={name_key("Three Flags Sailing")},
        )  # fmt: skip  (as `lore_read` passes them)
        self.assertEqual(sum(lost.values()), 0)
        self.assertEqual(
            sorted((u.fixed, u.how, u.anchored) for u in got),
            [("Tavin", "knows", False), ("Three Flags Sailing", "located_in", False)],
        )
        self.assertEqual({u.what for u in got}, {"barmaid with red hair"})


if __name__ == "__main__":
    unittest.main()
