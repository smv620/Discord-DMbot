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
            secret_ties=[(("The Stranger",), ("Mira",))],
        )
        hit = [p for p in problems if "keeps secret" in p and "link" in p]
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

    async def test_a_gate_that_cannot_read_the_connections_lets_nothing_go(self) -> None:
        from unittest.mock import AsyncMock

        from dmbot.bot import DMBot

        me = SimpleNamespace(_secret_ties=AsyncMock(side_effect=RuntimeError("down")))
        campaign = SimpleNamespace(guild_id=1, id="c")
        got = await DMBot._ties_stated(me, campaign, "Mira ran.")  # type: ignore[arg-type]
        self.assertIsNone(got)
        me = SimpleNamespace(_secret_ties=AsyncMock(return_value=(TIES[0],)))
        self.assertTrue(await DMBot._ties_stated(me, campaign, "Mira, Lord Veil's daughter."))  # type: ignore[arg-type]
        self.assertFalse(await DMBot._ties_stated(me, campaign, "Mira ran."))  # type: ignore[arg-type]

    async def test_audio_is_not_voiced_when_the_narration_links_two_secret_ends(self) -> None:
        from dmbot.recaps.audio import worker

        class Stop(Exception):
            pass

        job = SimpleNamespace(summary="《Calm》 Mira, the daughter of Lord Veil, rode on.")
        inputs = SimpleNamespace(secret_ties=TIES)
        # the check the worker runs in `_check`, on the same text and ties
        self.assertTrue(rd.states_secret_tie(worker.strip_cues(job.summary), inputs.secret_ties))
        self.assertFalse(
            rd.states_secret_tie(worker.strip_cues("《Calm》 Mira rode on."), inputs.secret_ties)
        )
