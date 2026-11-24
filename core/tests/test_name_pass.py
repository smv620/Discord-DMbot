"""PlotBot's name pass on the cleaned transcript (#2628), the pure part: which words are candidates,
what is asked, and which answers are believed. No database, no AI."""

from __future__ import annotations

import dataclasses
import json
import unittest
from typing import Any

from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.transcript import name_pass as np
from tests.test_transcript_cleaner import CERRIC, alias, entity

KETH, VANE, DREN = "k" * 32, "v" * 32, "d" * 32


def make(*, secret: bool = True) -> CampaignLookup:
    entities = [
        entity(KETH, "Kethrandil", "player_character", played_by=7),
        entity(DREN, "Drenwick", "npc"),
        entity(CERRIC, "Cerric", "npc"),
    ]
    aliases = [alias(KETH, "Kethrandil"), alias(DREN, "Drenwick"), alias(CERRIC, "Cerric")]
    if secret:
        entities.append(entity(VANE, "Silas Vane", "npc"))
        aliases.append(alias(VANE, "Silas Vane", secret=True))
    return CampaignLookup.build(LookupData(1, tuple(entities), tuple(aliases), (), (), (), ()))


def held(lookup: Any) -> frozenset[str]:
    """A table that holds every entry the DM hasn't kept back (#4802: the pass is given the gate's
    table view; here, the entries not marked "Players don't know yet")."""
    return frozenset(e.id for e in lookup.entities.values() if not e.hidden_from_players)


def cands(lines: Any, lookup: Any, **kw: Any) -> Any:
    return np.candidates(lines, lookup, table=held(lookup), **kw)


def surv(lines: Any, lookup: Any, *more: Any, **kw: Any) -> Any:
    return np.survey(lines, lookup, *more, table=held(lookup), **kw)


LINES = ["Keirandal drew his bow.", "Then Keirandal said no.", "We followed Keirandal home."]


class Finding(unittest.TestCase):
    def test_a_misheard_list_name_is_a_candidate_with_its_list_name_near(self) -> None:
        got = cands(LINES, make())
        self.assertEqual([c.heard for c in got], ["Keirandal"])
        self.assertEqual((got[0].times, got[0].near[0].name), (3, "Kethrandil"))
        self.assertEqual(got[0].near[0].kind, "player character")
        self.assertTrue(all("Keirandal" in line for line in got[0].lines))

    def test_a_name_the_table_doesnt_hold_is_never_asked_about_one_it_holds_is(self) -> None:
        # (#4802, ruling Q2: what the table may read is the gate's table view, not the flags)
        lookup = make()
        everyone = frozenset(lookup.entities)
        asked = np.candidates(LINES, lookup, table=everyone)
        self.assertEqual([c.heard for c in asked], ["Keirandal"])
        self.assertEqual(np.candidates(LINES, lookup, table=everyone - {KETH}), [])
        self.assertTrue(np.secret_test(lookup, everyone - {KETH})("Keirandal"))
        self.assertFalse(np.secret_test(lookup, everyone)("Keirandal"))

    def test_a_suggested_name_is_not_kept_back_so_a_spelling_near_it_is_still_asked(self) -> None:
        # (#4802 review: the gate holds only confirmed entries, so a suggested one is never "kept
        # from the table"; it is on nobody's list yet, as before the table view)
        lookup = make()
        waiting = dataclasses.replace(lookup.entities[KETH], status="proposed")
        lookup = dataclasses.replace(lookup, entities={**lookup.entities, KETH: waiting})
        self.assertNotIn(KETH, np.kept_from_table(lookup, frozenset()))
        self.assertFalse(np.secret_test(lookup, frozenset())("Keirandal"))

    def test_the_dm_only_kinds_stay_out_even_when_the_table_holds_them(self) -> None:
        # (as before #4802: a story thread or a deed is the DM's by kind)
        lookup = make()
        thread = dataclasses.replace(lookup.entities[KETH], type="quest")
        quest = dataclasses.replace(lookup, entities={**lookup.entities, KETH: thread})
        self.assertIn(KETH, np.kept_from_table(quest, frozenset(quest.entities)))
        self.assertEqual(np.candidates(LINES, quest, table=frozenset(quest.entities)), [])

    def test_the_list_name_itself_an_ordinary_word_and_a_far_name_are_not(self) -> None:
        lines = ["Kethrandil rode on.", "Kindly step aside.", "Then Brannoch laughed."]
        self.assertEqual(cands(lines, make()), [])

    def test_a_word_close_to_a_secret_name_is_never_a_candidate(self) -> None:
        lines = ["Silas Vain waved.", "Silas Vain left.", "Then Silas Vain sat."]
        self.assertEqual(cands(lines, make()), [])
        # the same word is one when there is no secret to be near
        near_a_public = ["Drenwik nodded.", "Then Drenwik left.", "Drenwik sat."]
        self.assertEqual([c.heard for c in cands(near_a_public, make())], ["Drenwik"])

    def test_the_secret_test_agrees_with_the_pass_s_own(self) -> None:
        # (the after-session name step drops a name close to a secret whole, #5133: same test)
        from dmbot.transcript.name_pass import _near, secret_test

        for lookup in (make(), make(secret=False)):
            # (a table shaped like the flags, and one built by hand that doesn't hold Kethrandil)
            for table in (held(lookup), frozenset(lookup.entities) - {KETH}):
                close = secret_test(lookup, table)
                for word in ("Silas Vain", "Silas Vane", "Drenwik", "Kethrandil", "Keirandal"):
                    self.assertEqual(
                        close(word),
                        _near(lookup, word, np.kept_from_table(lookup, table))[1],
                        (word, lookup.secret_lengths),
                    )
        self.assertTrue(secret_test(make(), held(make()))("Silas Vain"))
        self.assertFalse(secret_test(make(secret=False), held(make(secret=False)))("Silas Vain"))

    def test_a_word_for_a_person_at_the_table_is_left_alone(self) -> None:
        lines = ["Keirandal rolled.", "Then Keirandal rolled again.", "Keirandal won."]
        self.assertEqual(cands(lines, make(), people=["keirandal"]), [])

    def test_a_kept_as_heard_word_is_left_alone(self) -> None:
        base = make()
        kept = CampaignLookup.__dataclass_fields__  # (a lookup with the word kept: below)
        self.assertTrue(kept)
        import dataclasses

        lookup = dataclasses.replace(base, keep_keys=frozenset({"keirandal"}))
        self.assertEqual(cands(LINES, lookup), [])


class Asking(unittest.TestCase):
    def test_the_question_has_the_word_the_list_names_and_a_short_line_only(self) -> None:
        got = cands(LINES, make())
        text = np.question(got)
        self.assertIn('"Keirandal"', text)
        self.assertIn("Kethrandil (player character)", text)
        self.assertNotIn("Silas Vane", text)  # a secret name is never in a prompt
        self.assertNotIn("Drenwick", text)  # nor a list name that is not near


class Believing(unittest.TestCase):
    def setUp(self) -> None:
        self.cands = cands(LINES, make())

    def answer(self, *items: dict[str, object]) -> str:
        return json.dumps({"answers": list(items)})

    def test_a_sure_answer_naming_an_offered_name_is_a_fix(self) -> None:
        got = np.decide(self.answer({"n": 1, "answer": "is", "name": "Kethrandil"}), self.cands)
        self.assertEqual(
            [(d.heard, d.name, d.entity_id) for d in got.sure],
            [("Keirandal", "Kethrandil", KETH)],
        )
        self.assertEqual((got.unsure, got.new), ([], []))

    def test_two_offered_names_too_alike_to_choose_between_are_never_a_fix(self) -> None:
        """#2628 review: whatever PlotBot says, code does not choose between look-alikes."""
        a = np.Near(KETH, "Kethrandil", "player character", 0.70)
        b = np.Near(DREN, "Kethrandel", "npc", 0.66)  # within the margin of the first
        cand = np.Candidate("Keirandal", (), 3, (a, b), ())
        self.assertTrue(np.too_alike_to_choose(cand))
        got = np.decide(self.answer({"n": 1, "answer": "is", "name": "Kethrandil"}), [cand])
        self.assertEqual((got.sure, len(got.unsure)), ([], 1))  # goes to the DM
        far = np.Near(DREN, "Drenwick", "npc", 0.50)
        apart = np.Candidate("Keirandal", (), 3, (a, far), ())
        self.assertFalse(np.too_alike_to_choose(apart))
        sure = np.decide(self.answer({"n": 1, "answer": "is", "name": "Kethrandil"}), [apart])
        self.assertEqual(len(sure.sure), 1)
        self.assertFalse(np.too_alike_to_choose(np.Candidate("Keirandal", (), 3, (a,), ())))

    def test_new_is_new_and_every_other_answer_is_unsure_and_changes_nothing(self) -> None:
        got = np.decide(self.answer({"n": 1, "answer": "new", "name": ""}), self.cands)
        self.assertEqual((got.sure, len(got.new), got.unsure), ([], 1, []))
        for item in (
            {"n": 1, "answer": "unsure", "name": "Kethrandil"},
            {"n": 1, "answer": "is", "name": "Drenwick"},  # not offered for this word
            {"n": 1, "answer": "is", "name": "Silas Vane"},  # never offered
            {"n": 2, "answer": "is", "name": "Kethrandil"},  # no such word: this one has no answer
            {"n": "1", "answer": "is", "name": "Kethrandil"},
            "nonsense",
        ):
            got = np.decide(self.answer(item), self.cands)  # type: ignore[arg-type]
            self.assertEqual((got.sure, got.new, len(got.unsure)), ([], [], 1), item)
        for raw in ("", "not json", "[]", '{"answers": 5}', '{"x": 1}'):
            got = np.decide(raw, self.cands)
            self.assertEqual((got.sure, got.new, len(got.unsure)), ([], [], 1), raw)

    def test_a_word_is_fixed_once(self) -> None:
        twice = self.answer(
            {"n": 1, "answer": "is", "name": "Kethrandil"},
            {"n": 1, "answer": "is", "name": "Kethrandil"},
        )
        self.assertEqual(len(np.decide(twice, self.cands).sure), 1)


class Reporting(unittest.TestCase):
    def test_forms_are_counted_without_case_and_shown_most_heard_first(self) -> None:
        got = np.group_forms([("Keirandal", 3), ("keirandal", 2), ("Kerendal", 4)])
        self.assertEqual(got, (("Keirandal", 5), ("Kerendal", 4)))

    def test_the_report_names_each_fix_its_spellings_and_what_was_asked(self) -> None:
        near = np.Near(KETH, "Kethrandil", "player character", 0.7)
        report = np.Report(
            fixed=(np.Fixed("Kethrandil", (("Keith and Dill", 4), ("Keth randil", 3)), 5),),
            unsure=(
                np.Candidate("Kerran", (), 2, (near, np.Near(DREN, "Keirran", "npc", 0.6)), ()),
            ),
            new=(np.Candidate("Zorgul", (), 1, (), ()),),
            guarded=0,
        )
        self.assertEqual(
            np.report_text(report),
            "**Names fixed this session**\n"
            "Kethrandil: corrected 7 times\n"
            "  (4) Keith and Dill\n"
            "  (3) Keth randil\n"
            "Not sure (asked the DM): 1\n"
            "  (2) Kerran → Kethrandil or Keirran?\n"
            "New names (asked the DM): 1\n"
            "Left as heard (secret-name guard): 0",
        )

    def test_the_counts_line_has_no_spelling_in_it(self) -> None:
        report = np.Report(
            fixed=(np.Fixed("Kethrandil", (("Keith and Dill", 4),), 5),),
            new=(np.Candidate("A", (), 1, (), ()), np.Candidate("B", (), 1, (), ())),
        )
        line = np.counts_line(report)
        self.assertEqual(line, "1 names, 4 fixes, 0 unsure, 2 new, 0 guarded")
        self.assertNotIn("Keith", line)
        self.assertNotIn("Kethrandil", line)

    def test_a_quiet_report_is_quiet(self) -> None:
        self.assertTrue(np.Report().quiet)
        self.assertFalse(np.Report(guarded=1).quiet)

    def test_batches_differ_by_name_and_session_and_are_stable(self) -> None:
        self.assertEqual(np.batch_of("s1", "a"), np.batch_of("s1", "a"))
        self.assertNotEqual(np.batch_of("s1", "a"), np.batch_of("s1", "b"))
        self.assertNotEqual(np.batch_of("s1", "a"), np.batch_of("s2", "a"))
        self.assertGreaterEqual(np.batch_of("s1", "a"), np.BATCH_BASE)


class TheReportAsText(unittest.TestCase):
    """A report is kept as text until the import's one report is made (#4904)."""

    def test_a_report_comes_back_equal(self) -> None:
        from dmbot.transcript.name_pass import (
            Candidate,
            Fixed,
            Near,
            Report,
            report_from_json,
            report_to_json,
        )

        near = Near("e1", "Ærik: the Red", "character", 0.83)
        cand = Candidate("Vane", ("Oscar Vane",), 3, (near,), ("said: by the gate",))
        report = Report(
            fixed=(Fixed("Oskar Vane", (("Oscar", 2), ("Oskar", 1)), 7_000_000_000_001),),
            unsure=(cand,),
            new=(cand,),
            guarded=2,
            cut=True,
        )
        self.assertEqual(report_from_json(report_to_json(report)), report)
        self.assertEqual(report_from_json(report_to_json(Report())), Report())


class TheReadersCandidates(unittest.TestCase):
    """Slice 4 (docs/design/session_names.md, section 7; #5133): the name reader's spellings join
    the capital-letter scan's, so a known name heard in lower case is asked about."""

    LOWER = ("we met keirandal at dawn", "keirandal waved from the cart")

    def test_a_known_name_heard_in_lower_case_is_asked_only_with_the_reader(self) -> None:
        from dmbot.memory.scan import Suggestion

        self.assertEqual(cands(self.LOWER, make()), [])
        got = surv(self.LOWER, make(), extra=[Suggestion("keirandal", 2)]).items
        self.assertEqual([c.heard for c in got], ["keirandal"])
        self.assertEqual(got[0].near[0].name, "Kethrandil")
        self.assertTrue(got[0].lines)  # (its lines are found in any case)

    def test_the_union_never_asks_one_spelling_twice(self) -> None:
        from dmbot.memory.scan import Suggestion

        got = surv(LINES, make(), extra=[Suggestion("Keirandal", 3)]).items
        self.assertEqual(sum(1 for c in got if c.heard.casefold() == "keirandal"), 1)

    def test_past_the_limit_the_closest_go_first_and_the_rest_are_counted(self) -> None:
        from unittest.mock import patch

        from dmbot.memory.scan import Suggestion

        lines = [*self.LOWER, "drenwik was there", "drenwik again"]
        extra = [Suggestion("keirandal", 2), Suggestion("drenwik", 9)]
        with patch.object(np, "MAX_CANDIDATES", 1):
            got = surv(lines, make(), extra=extra)
        self.assertEqual(len(got.items), 1)
        best = max(n.likeness for n in got.items[0].near)
        other = "drenwik" if got.items[0].heard == "keirandal" else "keirandal"
        self.assertEqual(got.left_out, (other,))
        self.assertGreaterEqual(best, 0)  # (the closest first, whatever the hearings)

    def test_without_the_reader_the_order_is_today_s(self) -> None:
        got = surv(LINES, make())
        self.assertEqual(
            [c.times for c in got.items], sorted((c.times for c in got.items), reverse=True)
        )
        self.assertEqual(got.left_out, ())

    def test_a_known_name_or_a_person_at_the_table_from_the_reader_is_never_asked(self) -> None:
        from dmbot.memory.scan import Suggestion

        got = surv(
            self.LOWER, make(), ["Mia"], extra=[Suggestion("kethrandil", 2), Suggestion("mia", 2)]
        ).items
        self.assertEqual(got, [])


class WhatThePassTellsTheStep(unittest.TestCase):
    """Slice 4 (#5133, the reviewer): `run` tells the name step what it settled only once it has
    finished, and a "sure" answer that wrote nothing is not settled."""

    def run_pass(self, ask: object, written: bool = True) -> tuple[set[str], set[str], set[str]]:
        import asyncio
        from types import SimpleNamespace

        from dmbot.memory.scan import Suggestion
        from dmbot.transcript import name_pass_run

        lines = [SimpleNamespace(text=t, sidebar="", typed=False, user_id=1, topic="game")
                 for t in TheReadersCandidates.LOWER]  # fmt: skip

        async def read(*_a: object) -> list[SimpleNamespace]:
            return lines

        async def respell(*_a: object, **_k: object) -> object:
            return SimpleNamespace(forms=[("keirandal", 2)] if written else [])

        store = SimpleNamespace(lines=read, respell_names=respell)
        asked: set[str] = set()
        left: set[str] = set()
        fixed: set[str] = set()
        asyncio.run(
            name_pass_run.run(
                store, make(), ask, 1, "c1", "s1", now=0, forget_before=0,  # type: ignore[arg-type]
                extra=[Suggestion("keirandal", 2)], asked=asked, left_out=left, fixed_keys=fixed,
                table=held(make()),
            )
        )  # fmt: skip
        return asked, left, fixed

    def test_a_fix_is_settled_and_reported_as_written(self) -> None:
        async def ask(*_a: object) -> str:
            return json.dumps({"answers": [{"n": 1, "answer": "is", "name": "Kethrandil"}]})

        asked, left, fixed = self.run_pass(ask)
        self.assertEqual((asked, left, fixed), ({"keirandal"}, set(), {"keirandal"}))

    def test_a_failed_call_settles_nothing(self) -> None:
        import asyncio
        from types import SimpleNamespace

        from dmbot.memory.scan import Suggestion
        from dmbot.transcript import name_pass_run

        async def ask(*_a: object) -> str:
            raise RuntimeError("down")

        async def read(*_a: object) -> list[object]:
            return [SimpleNamespace(text=t, sidebar="", typed=False, user_id=1, topic="game")
                    for t in TheReadersCandidates.LOWER]  # fmt: skip

        asked: set[str] = set()
        with self.assertRaises(RuntimeError):
            asyncio.run(
                name_pass_run.run(
                    SimpleNamespace(lines=read), make(), ask, 1, "c1", "s1", now=0,  # type: ignore[arg-type]
                    forget_before=0, extra=[Suggestion("keirandal", 2)], asked=asked,
                    table=held(make()),
                )
            )  # fmt: skip
        self.assertEqual(asked, set())  # (so the step's "Sounds like" questions stay)

    def test_a_sure_answer_that_wrote_nothing_is_not_settled(self) -> None:
        async def ask(*_a: object) -> str:
            return json.dumps({"answers": [{"n": 1, "answer": "is", "name": "Kethrandil"}]})

        asked, _left, fixed = self.run_pass(ask, written=False)
        self.assertEqual((asked, fixed), (set(), set()))

    def test_unsure_is_settled_for_the_dm_s_own_question(self) -> None:
        async def ask(*_a: object) -> str:
            return json.dumps({"answers": [{"n": 1, "answer": "unsure"}]})

        asked, _left, fixed = self.run_pass(ask)
        self.assertEqual((asked, fixed), ({"keirandal"}, set()))


class TheNameStepsCloseness(unittest.IsolatedAsyncioTestCase):
    """(#4802 review) the after-session name step's "close to a name the table may not read" and
    "heard wrong for" read the same table view as the pass: never the flags."""

    async def test_a_name_the_table_doesnt_hold_is_close_to_kept_and_never_heard_wrong(
        self,
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from dmbot.bot import DMBot

        lookup = make()
        everyone = frozenset(lookup.entities)
        for table, kept in ((everyone, False), (everyone - {KETH}, True)):
            bot = SimpleNamespace(
                lookup=SimpleNamespace(get=AsyncMock(return_value=lookup)),
                memory=SimpleNamespace(entries_held=AsyncMock(return_value=table)),
            )
            got = await DMBot._name_closeness(bot, 1, "c1")  # type: ignore[arg-type]
            assert got is not None
            heard_wrong, near_secret = got
            with self.subTest(kept=kept):
                self.assertEqual(near_secret("Keirandal"), kept)
                self.assertEqual(heard_wrong("Kethrandel") is None, kept)
