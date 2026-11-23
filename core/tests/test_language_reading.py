"""The languages heard in a session (#4229 slice 1d, DMBOT_SESSION_LANGUAGES; Supervisor's ruling
A): the reader's rules on the invented `language_cases` text (heard used, named only, can't speak, a
misheard word, a question, none), the extractor's use of the claims call (no extra call, counts
only in the log), and the bot's step that asks the DM about each. Invented names, a canned AI."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.ai import Reply
from dmbot.config import ConfigError, load_settings
from dmbot.memory import extractor, language_reading
from dmbot.memory.language_reading import Heard, check, once_each, parse_languages
from tests.test_config import BASE
from tests.test_extractor import DM, Kept, dm_session, go, line

CASES = Path(__file__).parent / "fixtures" / "story_graph"


def said() -> dict[str, str]:
    rows = (CASES / "language_cases_session.txt").read_text(encoding="utf-8").splitlines()
    return {str(i): row.split("] ", 1)[1].split(": ", 1)[1] for i, row in enumerate(rows, 1)}


def answer(*items: tuple[str, str, int]) -> str:
    return json.dumps(
        {"claims": [], "languages": [{"who": w, "language": g, "lines": [n]} for w, g, n in items]}
    )


class TheCases(unittest.TestCase):
    def test_the_key_is_what_the_reader_keeps(self) -> None:
        key = json.loads((CASES / "language_cases_key.json").read_text(encoding="utf-8"))
        got = parse_languages(
            answer(
                ("Okthandom", "Dwarvish", 2), ("Duna Kell", "Dwarvish", 3),
                ("Pell Arrow", "Halfling", 4), ("Belleros", "Elvish", 5),
                ("Belleros", "Elvish", 6), ("Cerric", "Giant", 7), ("Claude", "Orc", 8),
                ("Claude", "Common", 9),
            ),
            said(),
        )  # fmt: skip
        self.assertEqual(
            [(h.language, h.lines) for h in got.heard],
            [(k["language"], (str(k["line"]),)) for k in key["languages"]],
        )
        self.assertEqual(got.refused, 4)  # (runes, can't speak, a question, none in the line)

    def test_the_key_is_well_formed(self) -> None:
        key = json.loads((CASES / "language_cases_key.json").read_text(encoding="utf-8"))
        nodes = {n["name"] for n in key["nodes"]}
        lines = said()
        for row in key["languages"]:
            self.assertIn(row["character"], nodes)
            self.assertEqual(row["doc"], "session")
            self.assertIn(str(row["line"]), lines)
        for row in key["must_not_languages"]:
            self.assertIn(str(row["line"]), lines)

    def test_each_rule(self) -> None:
        self.assertEqual(check("Okthandom", "Dwarvish", "Okthandom answers her in Dwarvish."), "")
        self.assertEqual(check("Belleros", "Elvish", "Belleros replies to him in Elfish."), "")
        self.assertEqual(check("Cerric", "Giant", "Cerric doesn't speak Giant."),
                         "language_not_spoken")  # fmt: skip
        self.assertEqual(check("Claude", "Orc", "Does anyone here speak Orc?"),
                         "language_not_spoken")  # fmt: skip
        self.assertEqual(check("Belleros", "Elvish", "The runes on the door are Elvish."),
                         "language_not_spoken")  # fmt: skip
        self.assertEqual(check("Okthandom", "Sylvan", "Okthandom answers in Dwarvish."),
                         "language_not_in_line")  # fmt: skip
        self.assertEqual(
            check("Okthandom", "Draconic", "Okthandom answers in Dwarvish."), "language_not_in_line"
        )  # (a mishearing is a letter or two, not a swap)
        for who in ("he", "the party", "DM", ""):
            self.assertEqual(check(who, "Dwarvish", "He answers in Dwarvish."), "language_who")
        self.assertEqual(check("Okthandom", "speaks softly", "Okthandom speaks softly."),
                         "language_words")  # fmt: skip

    def test_the_reviewers_false_readings(self) -> None:
        no = "language_not_spoken"
        for who, language, said in (
            ("Duna Kell", "Dwarvish", "The dwarves answer in Common."),  # (a people, not a word)
            ("Belleros", "Elvish", "The elves sing as they pass."),
            ("Cerric", "Giant", "Cerric talks to the giants."),
        ):
            self.assertNotEqual(check(who, language, said), "", said)
        for said in (
            "Okthandom says the runes are Dwarvish.",
            "The map in the chest is in Dwarvish.",
            "Okthandom can't understand Dwarvish.",
        ):
            self.assertEqual(check("Okthandom", "Dwarvish", said), no, said)
        for said in (  # (a negation or a question elsewhere in the line is not about speaking)
            "Okthandom, who doesn't trust her, answers in Dwarvish.",
            "No, Okthandom answers in Dwarvish.",
            "Okthandom answers in Dwarvish: what do you want?",
        ):
            self.assertEqual(check("Okthandom", "Dwarvish", said), "", said)

    def test_malformed_and_repeats(self) -> None:
        self.assertEqual(parse_languages("not json", said()).heard, [])
        got = parse_languages(
            answer(("Okthandom", "Dwarvish", 2), ("okthandom", "dwarvish", 2), ("X", "Elvish", 99)),
            said(),
        )
        self.assertEqual(len(got.heard), 1)
        self.assertEqual(got.dropped, {"language_lines": 1})
        once = once_each([got.heard[0], got.heard[0]])
        self.assertEqual(len(once), 1)
        # (#4745: heard in two batches, the one question carries both batches' lines and speakers)
        a = Heard("Okthandom", "Dwarvish", ("2",), (7,))
        b = Heard("okthandom", "dwarvish", ("12", "2"), (5,))
        self.assertEqual(once_each([a, b]), [Heard("Okthandom", "Dwarvish", ("2", "12"), (5, 7))])


class LanguageAnswer:
    """A fake AI: the claims call answers with no claims and the languages it is given."""

    def __init__(self, text: str, *, cut: bool = False) -> None:
        self.systems: list[str] = []
        self.text, self.cut = text, cut

    async def complete(self, system: str, text: str, *, max_tokens: int = 0) -> Reply:
        self.systems.append(system)
        return Reply(self.text, self.cut, 10, 5, "m-fast")


class InTheClaimsCall(unittest.IsolatedAsyncioTestCase):
    async def test_off_by_default_the_prompt_is_the_claims_one(self) -> None:
        ai = LanguageAnswer(answer(("Okthandom", "Dwarvish", 1)))
        report = await go(ai, Kept(), dm_session(10))
        self.assertEqual(ai.systems, [extractor.SYSTEM])
        self.assertEqual((report.languages, report.heard_languages), (0, []))

    async def test_on_it_costs_no_call_and_gathers_what_holds(self) -> None:
        ai = LanguageAnswer(answer(("Okthandom", "Dwarvish", 1), ("Cerric", "Giant", 2)))
        lines = [
            line(1, DM, "Okthandom answers her in Dwarvish."),
            line(2, DM, "Cerric doesn't speak Giant."),
            *dm_session(10)[2:],
        ]
        report = await go(ai, Kept(), lines, languages=True)
        self.assertEqual(report.calls, 1)
        self.assertIn(language_reading.LANGUAGE_ADDENDUM, ai.systems[0])
        self.assertEqual([(h.who, h.language) for h in report.heard_languages],
                         [("Okthandom", "Dwarvish")])  # fmt: skip
        self.assertIn("languages: 1 heard", report.line())
        self.assertNotIn("Dwarvish", report.line())  # (counts only in the log)

    async def test_a_speaker_who_stops_meanwhile_leaves_nothing_and_twice_is_once(self) -> None:
        ai = LanguageAnswer(answer(("Okthandom", "Dwarvish", 1)))
        lines = [line(1, DM, "Okthandom answers her in Dwarvish."), *dm_session(20)[1:]]
        asked = []

        async def consenting() -> set[int]:
            asked.append(1)
            return {DM} if len(asked) < 3 else set()  # (the DM stops before the end)

        report = await go(ai, Kept(), lines, consenting, languages=True)
        self.assertEqual(report.heard_languages, [])
        self.assertIn("language_speaker_gone", report.line())
        kept = await go(LanguageAnswer(answer(("Okthandom", "Dwarvish", 1))), Kept(),
                        [line(1, DM, "Okthandom answers in Dwarvish."), *dm_session(10)[1:]],
                        languages=True)  # fmt: skip
        self.assertEqual(kept.heard_languages[0].speakers, (DM,))  # (#4745: who said it, ids)
        again = await go(LanguageAnswer(answer(("Okthandom", "Dwarvish", 1))), Kept(),
                         [line(1, DM, "Okthandom answers in Dwarvish."), *dm_session(20)[1:]],
                         languages=True)  # fmt: skip
        self.assertEqual(len(again.heard_languages), 1)  # (heard in two batches: one)

    async def test_a_cut_answer_is_counted(self) -> None:
        report = await go(LanguageAnswer('{"claims": [{"sub', cut=True), Kept(), dm_session(10),
                          languages=True)  # fmt: skip
        self.assertEqual(report.languages_cut, 1)


class TheSwitch(unittest.TestCase):
    def test_off_by_default_and_checked(self) -> None:
        base = BASE
        self.assertFalse(load_settings(base).session_languages_on)
        self.assertTrue(
            load_settings({**base, "DMBOT_SESSION_LANGUAGES": "1"}).session_languages_on
        )
        with self.assertRaises(ConfigError):
            load_settings({**base, "DMBOT_SESSION_LANGUAGES": "maybe"})


class TheQuestions(unittest.IsolatedAsyncioTestCase):
    async def test_each_heard_language_is_a_question_and_a_sheet_line_goes_to_the_dms(self) -> None:
        from dmbot.bot import DMBot
        from dmbot.memory.language_reading import Heard

        camp = SimpleNamespace(dm_accounts=frozenset({7}), name="Grey Reach")
        bot: Any = SimpleNamespace(
            campaigns=SimpleNamespace(get=AsyncMock(return_value=camp)), memory=object(),
            _dm_message=AsyncMock(return_value=object()),
        )  # fmt: skip
        report = SimpleNamespace(heard_languages=[Heard("Okthandom", "Dwarvish", ("2",))])
        asked = AsyncMock(return_value=(1, [("Belleros", "Elvish")]))
        with patch("dmbot.ui.name_background.ask_languages", asked):
            await DMBot._ask_heard_languages(bot, 1, "c" * 32, report, 1_700_000_000)  # type: ignore[arg-type]
        assert asked.await_args is not None and bot._dm_message.await_args is not None
        spoken = asked.await_args.args[2]
        self.assertEqual(
            [(s.name, s.language, s.quote) for s in spoken], [("Okthandom", "Dwarvish", "")]
        )  # (no words of the transcript kept)
        self.assertTrue(asked.await_args.args[3].startswith("the session of "))
        bot._dm_message.assert_awaited_once()
        self.assertEqual(
            bot._dm_message.await_args.args[1],
            "🗣️ **Grey Reach**, the session of " + asked.await_args.args[3][15:] + ": DMbot heard\n"
            "• **Belleros** spoke **Elvish**, which isn't on their sheet.\n"
            "If that's right, the player can add it to their sheet. Nothing was changed.",
        )

    async def test_with_no_discord_the_questions_are_kept_and_only_the_sheet_note_waits(
        self,
    ) -> None:
        # (#4904 3b-4c: a worker has no Discord and a row may carry no names, so the courtesy note
        # is not sent from there; the questions are written as always)
        from dmbot.bot import DMBot
        from dmbot.memory.language_reading import Heard

        camp = SimpleNamespace(dm_accounts=frozenset({7}), name="Grey Reach")
        bot: Any = SimpleNamespace(
            campaigns=SimpleNamespace(get=AsyncMock(return_value=camp)), memory=object(),
            _dm_message=AsyncMock(return_value=object()), headless=True,
        )  # fmt: skip
        report = SimpleNamespace(heard_languages=[Heard("Okthandom", "Dwarvish", ("2",))])
        asked = AsyncMock(return_value=(1, [("Belleros", "Elvish")]))
        with patch("dmbot.ui.name_background.ask_languages", asked):
            await DMBot._ask_heard_languages(bot, 1, "c" * 32, report, 1_700_000_000)  # type: ignore[arg-type]
        asked.assert_awaited_once()
        bot._dm_message.assert_not_awaited()

    def test_the_sheet_message_is_capped_and_escaped(self) -> None:
        from dmbot.ui.name_background import SHEET_LANGUAGES_SHOWN, heard_sheet_languages

        many = [(f"P_{i}", "Elvish") for i in range(SHEET_LANGUAGES_SHOWN + 2)]
        text = heard_sheet_languages("Camp", "the session of 1 Jan 2027", many)
        self.assertIn("…and 2 more.", text)
        self.assertIn("P\\_0", text)
        self.assertEqual(heard_sheet_languages("Camp", "x", []), "")
