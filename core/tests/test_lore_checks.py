"""The checks on a lore read's relationships that threw away real ones (#2841): the owner's upload
lost about half to our own code. Invented names and text throughout; no network, no database.

1. A name's other spellings (this read's, secret ones too) count in the quote check, across the
   slices pass 2 reads in (a name line and the link lines that use its spelling).
2. A surname written once for two people ("Brannoc (father) & Mira (mother) Ashgrove").
3. A list sentence needs a window longer than 20 words, and a quote that opens its paragraph
   may borrow the one before.
4. he/him/his are one person, so "His father cared for him and his sister" is one open pronoun.
"""

from __future__ import annotations

import json
import unittest
from collections import Counter
from unittest import mock

from dmbot.ai import Reply
from dmbot.memory import background as bg
from dmbot.memory import lore_read as lr
from dmbot.memory.end_match import Ends
from tests import test_lore_read as base


def link(a: str, b: str, quote: str, how: str = "friend") -> dict[str, str]:
    return {"a": a, "how": how, "words": "", "b": b, "quote": quote}


def read_links(
    rows: list[dict[str, str]],
    text: str,
    aliases: dict[str, list[str]] | None = None,
    unsettled: list[bg.Unsettled] | None = None,
) -> tuple[list[bg.Connection], Counter[str]]:
    lost: Counter[str] = Counter()
    got = bg.read_connections(
        json.dumps({"connections": rows}), text, set(), lost, aliases, unsettled
    )
    return got, lost


class TheSpellingsOfOneName(unittest.TestCase):
    TEXT = "Ken is now called Brannoc. Ken taught Mira the knife."

    def test_a_quote_with_one_spelling_backs_an_end_written_with_another(self) -> None:
        rows = [link("Brannoc", "Mira", "Ken taught Mira the knife.")]
        got, lost = read_links(rows, self.TEXT, {"Ken": ["Brannoc"]})
        self.assertEqual((len(got), dict(lost)), (1, {}))

    def test_a_secret_spelling_counts_the_same(self) -> None:
        rows = [link("Brannoc", "Mira", "Ken taught Mira the knife.")]
        got, _ = read_links(rows, self.TEXT, {"Ken": ["Brannoc"]})  # (secret ones are passed alike)
        self.assertEqual(len(got), 1)

    def test_without_the_spelling_the_quote_is_still_refused(self) -> None:
        rows = [link("Brannoc", "Mira", "Ken taught Mira the knife.")]
        got, lost = read_links(rows, self.TEXT, {})
        self.assertEqual((got, dict(lost)), ([], {"not_in_text": 1}))

    def test_a_quote_that_really_lacks_an_end_is_refused_even_with_spellings(self) -> None:
        text = "Ken is now called Brannoc. Ken lived in Greyfen. Tavin trained alone."
        rows = [link("Brannoc", "Mira", "Tavin trained alone.")]
        got, lost = read_links(rows, text, {"Ken": ["Brannoc"]})
        self.assertEqual((got, dict(lost)), ([], {"not_in_text": 1}))

    def test_a_spelling_two_names_claim_belongs_to_neither(self) -> None:
        text = "Ken and Tavin were both called the Fox. The Fox taught Mira."
        rows = [link("Ken", "Mira", "The Fox taught Mira.")]
        got, lost = read_links(rows, text, {"Ken": ["the Fox"], "Tavin": ["the Fox"]})
        self.assertEqual((got, dict(lost)), ([], {"not_in_text": 1}))


class AcrossTheSlices(unittest.IsolatedAsyncioTestCase):
    """The name line and the link line that uses its spelling are shaped in different calls."""

    TEXT = "Ken is now called Brannoc. Ken taught Mira the knife."
    LIST = (
        "NAMES\n"
        '- Ken | person | other names: Brannoc | quote: "Ken is now called Brannoc"\n'
        '- Mira | person | quote: "Ken taught Mira the knife"\n\n'
        "RELATIONSHIPS\n"
        '- Brannoc — is a friend of — Mira | quote: "Ken taught Mira the knife."\n'
    )

    async def test_the_link_is_checked_once_every_name_is_known(self) -> None:
        name_rows = [
            {"name": "Ken", "kind": "person", "also": ["Brannoc"], "secret": [], "quote": ""},
            {"name": "Mira", "kind": "person", "also": [], "secret": [], "quote": ""},
        ]
        link_rows = [link("Brannoc", "Mira", "Ken taught Mira the knife.")]
        with (
            mock.patch.object(base, "NAME_ROWS", name_rows),
            mock.patch.object(base, "LINK_ROWS", link_rows),
            mock.patch.object(lr, "SHAPE_LINES", 1),
        ):
            fake = base.ai([Reply(self.LIST, False)])
            done = await lr.read_lore(fake, fake, self.TEXT, {}, base.Gate())
        self.assertEqual(len(done.links), 1)
        self.assertEqual(done.stats.dropped["not_in_text"], 0)

    async def test_a_name_the_campaign_has_keeps_its_new_spellings_for_the_links(self) -> None:
        name_rows = [
            {"name": "Ken", "kind": "person", "also": ["Brannoc"], "secret": [], "quote": ""},
            {"name": "Mira", "kind": "person", "also": [], "secret": [], "quote": ""},
        ]
        link_rows = [link("Brannoc", "Mira", "Ken taught Mira the knife.")]
        with (
            mock.patch.object(base, "NAME_ROWS", name_rows),
            mock.patch.object(base, "LINK_ROWS", link_rows),
            mock.patch.object(lr, "SHAPE_LINES", 1),
        ):
            fake = base.ai([Reply(self.LIST, False)])
            done = await lr.read_lore(
                fake, fake, self.TEXT, {}, base.Gate(), skip_name=lambda n: n == "Ken"
            )
        self.assertEqual([f.name for f in done.names], ["Mira"])  # (Ken is not offered again)
        self.assertEqual(done.known_spellings, {"Ken": ("Brannoc",)})
        self.assertEqual(len(done.links), 1)

    async def test_a_known_name_with_nothing_new_is_dropped_as_before(self) -> None:
        listed = 'NAMES\n- Ken | person | quote: "Ken"\n\nRELATIONSHIPS\n'
        with mock.patch.object(base, "NAME_ROWS", []), mock.patch.object(base, "LINK_ROWS", []):
            fake = base.ai([Reply(listed, False)])
            await lr.read_lore(
                fake, fake, self.TEXT, {}, base.Gate(), skip_name=lambda n: n == "Ken"
            )
        shaped = [c.args[1] for c in fake.complete.await_args_list if c.kwargs.get("schema")]
        self.assertEqual(shaped, [])  # (no line left to shape)


class AnEndWritesAnOtherSpellingByItsWords(unittest.TestCase):
    def test_a_public_spelling_fits_an_end_by_its_words_for_its_own_name(self) -> None:
        reader = Ends({"ken": "Ken"}, ["Ken"], {"Brannoc Vale": "Ken"})
        self.assertEqual(reader.match("Vale").name, "Ken")
        self.assertEqual(reader.match("Brannoc").name, "Ken")

    def test_two_spellings_of_one_name_are_still_one_name(self) -> None:
        reader = Ends({"ken": "Ken"}, ["Ken"], {"Brannoc Vale": "Ken", "Old Vale": "Ken"})
        self.assertEqual(reader.match("Vale").name, "Ken")

    def test_a_word_two_names_share_is_still_a_question(self) -> None:
        reader = Ends({}, ["Ken", "Tavin"], {"Brannoc Vale": "Ken", "Tavin Vale": "Tavin"})
        got = reader.match("Vale")
        self.assertEqual((got.name, got.ambiguous), (None, ("Ken", "Tavin")))


class ASurnameWrittenOnce(unittest.TestCase):
    TEXT = "Brannoc (father) & Mira (mother) Ashgrove raised Tavin in Greyfen."

    def test_the_full_name_is_there_when_its_words_are_close_and_in_order(self) -> None:
        reply = json.dumps(
            {"names": [{"name": "Brannoc Ashgrove", "kind": "person", "also": [], "secret": []}]}
        )
        self.assertEqual([f.name for f in bg.read_answer(reply, self.TEXT)], ["Brannoc Ashgrove"])

    def test_a_link_to_it_is_kept(self) -> None:
        rows = [
            link(
                "Brannoc Ashgrove",
                "Tavin",
                "Brannoc (father) & Mira (mother) Ashgrove raised Tavin",
            )
        ]
        got, lost = read_links(rows, self.TEXT)
        self.assertEqual((len(got), dict(lost)), (1, {}))

    def test_words_far_apart_or_out_of_order_are_not_a_name(self) -> None:
        text = "Ashgrove is a town. " + "Far away " * 9 + "Brannoc lived alone."
        reply = json.dumps(
            {"names": [{"name": "Brannoc Ashgrove", "kind": "person", "also": [], "secret": []}]}
        )
        self.assertEqual(bg.read_answer(reply, text), [])
        self.assertEqual(bg.read_answer(reply, "Ashgrove (the family) Brannoc rode."), [])

    def test_a_short_word_is_no_surname(self) -> None:
        reply = json.dumps(
            {"names": [{"name": "Brannoc Of", "kind": "person", "also": [], "secret": []}]}
        )
        self.assertEqual(bg.read_answer(reply, "Brannoc (the elder) & Mira of Greyfen."), [])


class AListSentence(unittest.TestCase):
    SENTENCE = (
        "Brannoc mentored two apprentices: Tavin, a quiet scout from the north road who learned "
        "every trail by heart, and also Mira, a healer from the river towns who never stopped "
        "asking questions."
    )
    TEXT = SENTENCE + " She became his closest friend."

    def test_the_window_runs_back_to_the_name_when_twenty_words_cannot_hold_both(self) -> None:
        rows = [link("Brannoc", "Mira", "She became his closest friend.")]
        got, lost = read_links(rows, self.TEXT)
        self.assertEqual(len(got), 1)
        words = got[0].quote.split()
        self.assertGreater(len(words), bg.QUOTE_WORDS)
        self.assertLessEqual(len(words), bg.LONG_QUOTE_WORDS)
        self.assertTrue(got[0].quote.startswith("Brannoc mentored"))
        self.assertEqual(dict(lost), {"quote_widened": 1, "quote_long": 1})

    def test_more_than_forty_words_is_still_too_far(self) -> None:
        text = self.SENTENCE + " " + "Ken waited. " * 3 + "She became his closest friend."
        text = text.replace(self.SENTENCE, "Brannoc " + "walked slowly " * 30 + "with Mira.")
        rows = [link("Brannoc", "Mira", "She became his closest friend.")]
        got, _ = read_links(rows, text)
        self.assertEqual(got, [])

    def test_a_quote_that_opens_its_paragraph_borrows_the_last_sentence_before_it(self) -> None:
        text = "Brannoc lived in Greyfen.\n\nHis sister, Mira, ran the farm."
        rows = [link("Brannoc", "Mira", "His sister, Mira, ran the farm.", "family")]
        got, lost = read_links(rows, text)
        self.assertEqual(len(got), 1)
        self.assertEqual(dict(lost), {"quote_widened": 1, "quote_prev_paragraph": 1})

    def test_a_quote_in_the_middle_of_a_paragraph_does_not_reach_the_one_before(self) -> None:
        text = "Brannoc lived in Greyfen.\n\nThe farm was old. His sister, Mira, ran it."
        rows = [link("Brannoc", "Mira", "His sister, Mira, ran it.", "family")]
        got, lost = read_links(rows, text)
        self.assertEqual((got, dict(lost)), ([], {"not_in_text": 1}))


class TwoFormsOfOnePronoun(unittest.TestCase):
    TEXT = "Greyfen was cold that year. His father cared for him and his sister, Mira."

    def ask(self, text: str = TEXT) -> tuple[list[bg.Connection], list[bg.Unsettled], Counter[str]]:
        rows = [link("Brannoc", "Mira", "His father cared for him and his sister, Mira.", "family")]
        open_: list[bg.Unsettled] = []
        # Brannoc has to be in the text for the check to get this far
        full = text + " Brannoc"
        got, lost = read_links(rows, full, unsettled=open_)
        return got, open_, lost

    def test_his_and_him_are_one_person_so_the_line_is_handed_over(self) -> None:
        got, open_, _ = self.ask()
        self.assertEqual(got, [])
        self.assertEqual(len(open_), 1)
        self.assertEqual(open_[0].phrase.casefold(), "his")

    def test_two_different_people_are_still_not_guessed(self) -> None:
        rows = [link("Brannoc", "Mira", "She cared for him and his sister, Mira.", "family")]
        open_: list[bg.Unsettled] = []
        full = "Greyfen was cold. She cared for him and his sister, Mira. Brannoc"
        got, lost = read_links(rows, full, unsettled=open_)
        self.assertEqual((got, open_, dict(lost)), ([], [], {"not_in_text": 1}))

    def test_with_the_check_off_the_line_is_left_out_and_counted_as_before(self) -> None:
        rows = [link("Brannoc", "Mira", "His father cared for him and his sister, Mira.", "family")]
        got, lost = read_links(rows, self.TEXT + " Brannoc", unsettled=None)
        self.assertEqual((got, dict(lost)), ([], {"not_in_text": 1}))
