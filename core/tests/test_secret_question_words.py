"""The website's words for the 🤫 Known or secret? card equal Discord's (#4598): the website may not
import the Discord layer, so its copy (`web/secret_question_words.py`) is held equal here, with
Discord's bold marks taken off. Pure words: no database."""

from __future__ import annotations

import unittest

from dmbot.memory import secret_questions as sq
from dmbot.ui import secret_questions as card
from dmbot.web import secret_question_words as web


def plain(text: str) -> str:
    """Discord's bold and italic marks off, and its ↩️ before Undo: the website has the word only."""
    return text.replace("**", "").replace("_", "").replace("↩️ ", "")


def bare(line: str) -> str:
    """A button line without its leading emoji."""
    return line.split(" ", 1)[1] if line and not line[0].isascii() else line


def q(kind: str, **change: object) -> sq.Question:
    return sq.Question("q", kind, "memory_relations", "i", dict(change) or {"secret": True}, "", 1)


class TheWordsAreEqual(unittest.TestCase):
    def lines(self, shown: card.Shown) -> list[str]:
        return [plain(x) for x in card.card_text(shown, 1).splitlines()]

    def test_narrow(self) -> None:
        got = web.card("narrow", "A serves B", "connection", "", "", [], False)
        lines = self.lines(card.Shown(q("narrow"), "**A serves B**", "connection"))
        self.assertIn(got["heading"], lines)
        self.assertIn(got["keep"]["says"], [bare(x) for x in lines])
        self.assertIn(got["make"]["says"], [bare(x) for x in lines])

    def test_narrow_knowers_and_clue_audience(self) -> None:
        said = web.make_secret_says("name", ["Ilsa", "Wren"], False)
        self.assertIn(
            said,
            plain(
                card._make_secret_says(
                    card.Shown(
                        q("narrow", knowers_off=["x"]), "x", "name", knowers=("Ilsa", "Wren")
                    )
                )
            ),
        )
        said = web.make_secret_says("clue", [], True)
        self.assertIn(
            said,
            plain(
                card._make_secret_says(card.Shown(q("narrow", audience_limited=True), "x", "clue"))
            ),
        )
        self.assertEqual(
            web.make_secret_says("name", [], False),
            plain(card._make_secret_says(card.Shown(q("narrow"), "x", "name"))),
        )

    def test_the_fixed_words(self) -> None:
        self.assertEqual(web.WHY, card.WHY)
        self.assertEqual(web.GONE_LINE, card.GONE_LINE)
        self.assertEqual(web.HEADING, plain(card.HEADING).replace("🤫 ", "").strip())
        for ours, theirs in (
            (web.KEEP_KNOWN, card.KEEP_KNOWN), (web.MAKE_SECRET, card.MAKE_SECRET),
            (web.KEEP_BOTH, card.KEEP_BOTH), (web.END_OLD, card.END_OLD),
            (web.KEEP_SECRET, card.KEEP_SECRET), (web.LET_KNOW, card.LET_KNOW),
        ):  # fmt: skip
            self.assertTrue(theirs.endswith(ours), (ours, theirs))

    def test_replace_and_reveal(self) -> None:
        got = web.card("replace", "A serves B", "connection", "", "A serves C", [], False)
        lines = self.lines(
            card.Shown(
                q("replace", end=True, by="r"), "**A serves B**", "connection", new="**A serves C**"
            )
        )
        self.assertIn(got["heading"].split(" Now: ")[0], lines[1])
        self.assertIn("Now: A serves C.", lines[2])
        got = web.card("reveal", "Tamsin", "other name", "Ash", "", [], False)
        lines = self.lines(
            card.Shown(q("reveal", secret=False), "**Tamsin**", "other name", owner="**Ash**")
        )
        self.assertIn(got["heading"], lines)

    def test_what_each_answer_says_afterwards(self) -> None:
        for kind, change, make_done, keep_done in (
            ("narrow", {"secret": True}, True, False),
            ("replace", {"end": True, "by": "r"}, True, False),
            ("reveal", {"secret": False}, False, True),
        ):
            got = web.card(kind, "Item", "name", "Owner", "", [], False)
            shown = card.Shown(q(kind, **change), "**Item**", "name", owner="**Owner**")
            self.assertEqual(got["make"]["done"], plain(card.done_text(shown, make_done)))
            self.assertEqual(got["keep"]["done"], plain(card.done_text(shown, keep_done)))
