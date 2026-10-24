"""A secret name said at the table is no longer secret for that session's recap (#2591), but only
the name: its secret connections stay refused. Made-up names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory.models import spelling_key
from dmbot.recaps import draft as rd
from dmbot.recaps.beats import Beat
from dmbot.recaps.inputs import EVERY_NAME, recap_name_sets, said_in
from dmbot.recaps.text import narration_of


def entry(text: str, *, secret: bool = False, person: bool = False, hidden: bool = False) -> Any:
    return SimpleNamespace(
        text=text, confirmed=True, secret=secret, person=person, entry_hidden=hidden
    )


def line(text: str, sidebar: str = "") -> Any:
    return SimpleNamespace(heard=text, text=text, sidebar=sidebar)


NAMES = SimpleNamespace(
    names=[entry("Lord Veil"), entry("The Stranger", secret=True), entry("Mira")]
)


class TheNameSets(unittest.TestCase):
    def sets(self, lines: list[Any] | None, recanted: Any = ()) -> tuple[list[str], list[str]]:
        return recap_name_sets(NAMES, None if lines is None else said_in(lines), recanted)

    def test_a_secret_name_said_by_the_table_is_allowed_for_that_recap(self) -> None:
        allowed, secret = self.sets([line("We follow the stranger into the keep")])
        self.assertIn("The Stranger", allowed)
        self.assertNotIn("The Stranger", secret)

    def test_a_secret_name_not_said_stays_secret(self) -> None:
        allowed, secret = self.sets([line("We follow Mira into the keep")])
        self.assertNotIn("The Stranger", allowed)
        self.assertIn("The Stranger", secret)

    def test_said_only_in_the_dm_sidebar_or_a_table_question_it_is_still_secret(self) -> None:
        allowed, secret = self.sets([line("Who is the stranger?", sidebar="question")])
        self.assertIn("The Stranger", secret)
        self.assertNotIn("The Stranger", allowed)

    def test_only_whole_words_count(self) -> None:
        _, secret = self.sets([line("The strangers left")])
        self.assertIn("The Stranger", secret)

    def test_a_recanted_name_is_secret_again(self) -> None:
        said = [line("We follow the stranger into the keep")]
        allowed, secret = self.sets(said, {spelling_key("The Stranger")})
        self.assertIn("The Stranger", secret)
        self.assertNotIn("The Stranger", allowed)

    def test_when_recanted_cannot_be_read_nothing_said_counts(self) -> None:
        _, secret = self.sets([line("the stranger")], EVERY_NAME)
        self.assertIn("The Stranger", secret)

    def test_with_no_lines_nothing_is_revealed(self) -> None:
        _, secret = self.sets(None)
        self.assertIn("The Stranger", secret)

    def test_a_spelling_counts_for_itself_only(self) -> None:
        names = SimpleNamespace(
            names=[entry("The Stranger", secret=True), entry("The Hooded One", secret=True)]
        )
        allowed, secret = recap_name_sets(names, said_in([line("the stranger spoke")]))
        self.assertEqual((allowed, secret), (["The Stranger"], ["The Hooded One"]))

    def test_a_secret_word_too_short_to_tell_is_never_taken_as_said(self) -> None:
        names = SimpleNamespace(names=[entry("Ox", secret=True)])
        allowed, secret = recap_name_sets(names, said_in([line("an ox pulled the cart")]))
        self.assertEqual((allowed, secret), ([], ["Ox"]))


TIES = [(("Lord Veil", "The Stranger"), ("Mira",))]  # a secret connection between two names
PCS = ["Mira"]
BEAT = Beat(1, "Mira follows the stranger.", ("Mira", "The Stranger"), (1,), "event")


class TheSecretConnection(unittest.TestCase):
    def test_a_sentence_naming_both_ends_is_a_stated_connection(self) -> None:
        self.assertTrue(rd.states_secret_tie("Mira is the daughter of Lord Veil.", TIES))
        self.assertFalse(rd.states_secret_tie("The Stranger watched. Mira ran to him.", []))

    def test_each_end_on_its_own_is_fine_even_in_one_recap(self) -> None:
        text = "Mira ran for the gate. Later the stranger crossed the ford."
        self.assertFalse(rd.states_secret_tie(text, TIES))

    def test_a_name_both_ends_share_says_nothing(self) -> None:
        ties = [(("Lord Veil", "Mira"), ("Mira", "Sarek"))]
        self.assertFalse(rd.states_secret_tie("Mira ran.", ties))

    def test_the_draft_check_refuses_it_without_naming_anyone(self) -> None:
        text = (
            "《Tense》 The Stranger led the way. Mira, who is secretly the stranger's daughter, "
            "followed."
        )
        problems = rd.check_prose(
            rd.Prose(text, ("soft",)),
            beats=[BEAT],
            transcript="the stranger mira",
            allowed_names=["Mira", "The Stranger", "Lord Veil"],
            secret_names=[],
            pcs=PCS,
            identity_ties=[(("The Stranger",), ("Mira",))],
        )
        hit = [p for p in problems if "same person or place" in p]
        self.assertTrue(hit, problems)
        self.assertFalse(any("Mira" in p or "Stranger" in p for p in hit))


class TheOtherWays(unittest.IsolatedAsyncioTestCase):
    """The review's cases (#2591): identity links, possessive ends, clues, fail-closed gates."""

    def test_a_secret_spelling_is_tied_to_the_entrys_public_names(self) -> None:
        from dmbot.recaps.inputs import identity_ties

        names = SimpleNamespace(
            names=[
                SimpleNamespace(text="Belleros", entity_id="e1", confirmed=True, secret=False,
                                person=False, clue=False),
                SimpleNamespace(text="The Stranger", entity_id="e1", confirmed=True, secret=True,
                                person=False, clue=False),
                SimpleNamespace(text="The Map", entity_id="e2", confirmed=True, secret=True,
                                person=False, clue=True),
            ]
        )  # fmt: skip
        ties = identity_ties(names)
        self.assertEqual(ties, [(("The Stranger",), ("Belleros",))])  # a clue is not a spelling
        self.assertTrue(rd.states_secret_tie("The Stranger was really Belleros.", ties))
        self.assertFalse(rd.states_secret_tie("The Stranger walked in. Belleros slept.", ties))

    def test_an_end_with_a_possessive_is_still_found(self) -> None:
        ties = [(("Dragon's Rest",), ("Mira",))]
        self.assertTrue(rd.states_secret_tie("Mira slept at Dragon's Rest.", ties))
        self.assertTrue(rd.states_secret_tie("Mira's cloak lay at Dragon's Rest.", ties))

    def test_an_end_with_no_letters_never_matches_everything(self) -> None:
        self.assertFalse(rd.states_secret_tie("Mira ran.", [(("!!!",), ("Mira",))]))

    def test_a_clue_name_said_aloud_stays_secret(self) -> None:
        names = SimpleNamespace(names=[entry("The Map", secret=True)])
        names.names[0].clue = True
        allowed, secret = recap_name_sets(names, said_in([line("we find the map")]))
        self.assertEqual((allowed, secret), ([], ["The Map"]))

    async def test_with_no_store_to_ask_every_secret_name_stays_secret(self) -> None:
        from dmbot.recaps.inputs import recap_recanted

        got = await recap_recanted(None, 1, "c", 100)
        self.assertIn("anything", got)
        self.assertTrue(got)  # (a test like `if recanted:` never reads "none")

    async def test_relations_that_cannot_be_read_never_stop_a_recap(self) -> None:
        from unittest.mock import AsyncMock, patch

        from dmbot import bot as bot_module
        from dmbot.bot import DMBot

        me = SimpleNamespace(memory=object())
        with patch.object(bot_module, "secret_connections", AsyncMock(side_effect=OSError("x"))):
            self.assertEqual(await DMBot._relation_ties(me, 1, "c"), ())  # type: ignore[arg-type]
        self.assertEqual(await DMBot._relation_ties(SimpleNamespace(memory=None), 1, "c"), ())  # type: ignore[arg-type]

    def test_a_relation_is_a_note_for_the_dm_never_a_refusal(self) -> None:
        text = "《Tense》 Mira and Lord Veil reached the inn. Mira slept."
        problems = rd.check_prose(
            rd.Prose(text, ("soft",)),
            beats=[BEAT],
            transcript="mira lord veil",
            allowed_names=["Mira", "Lord Veil"],
            secret_names=[],
            pcs=PCS,
            identity_ties=[],  # (relations are no longer passed to the check)
        )
        self.assertFalse([p for p in problems if "secret" in p], problems)
        (note,) = rd.tie_notes(narration_of(text), TIES)
        self.assertIn("Mira", note)
        self.assertIn("Lord Veil", note)
        self.assertIn("secret connection", note)
        self.assertEqual(rd.tie_notes("Mira ran. Lord Veil slept.", TIES), [])  # apart: no note

    def test_the_writer_is_told_the_pairs_with_no_relation_words_and_no_secret_names(self) -> None:
        pairs = rd.apart_pairs(TIES, ["Mira", "Lord Veil"])  # (The Stranger is a secret spelling)
        self.assertEqual(pairs, [("Lord Veil", "Mira")])
        message = rd.prose_message([BEAT], "", ["Mira"], None, apart=pairs)
        self.assertIn("<apart>", message)
        self.assertIn("Lord Veil", message)
        block = message.split("<apart>")[1].split("</apart>")[0]
        self.assertNotIn("Stranger", block)  # (a secret spelling never goes to the writer)
        self.assertNotIn("daughter", message)
        self.assertEqual(rd.apart_pairs(TIES, ["Mira"]), [])  # an end with no usable name
        self.assertNotIn("<apart>", rd.prose_message([BEAT], "", ["Mira"], None))

    def test_an_identity_pair_in_one_sentence_is_still_refused(self) -> None:
        text = "《Tense》 The Stranger, who is really Belleros, led the way."
        problems = rd.check_prose(
            rd.Prose(text, ("soft",)),
            beats=[BEAT],
            transcript="the stranger belleros",
            allowed_names=["The Stranger", "Belleros"],
            secret_names=[],
            pcs=PCS,
            identity_ties=[(("The Stranger",), ("Belleros",))],
        )
        self.assertTrue([p for p in problems if "same person or place" in p], problems)
        self.assertFalse(any("Stranger" in p or "Belleros" in p for p in problems))

    def test_the_note_goes_in_the_critics_notes_the_dm_page_shows(self) -> None:
        from dmbot.recaps.pipeline import RecapWorker

        job = SimpleNamespace(summary="《Tense》 Mira and Lord Veil reached the inn.", critics=None)
        finish = rd.Finish("The Inn", ("Mira slept.",))
        inputs = SimpleNamespace(relation_ties=TIES)
        got = RecapWorker._tie_notes(job, finish, inputs)  # type: ignore[arg-type]
        from dmbot.recaps import critics

        (note,) = critics.loop_from_json(got["critics"]).notes
        self.assertIn("secret connection", note)
        none = RecapWorker._tie_notes(job, finish, SimpleNamespace(relation_ties=[]))  # type: ignore[arg-type]
        self.assertEqual(none, {})
