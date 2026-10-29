"""The lore read finds every relationship the text states, not about half (#2854).

Pass 1 asked for names and relationships in one list and stopped long before the text ran out of
relationships (23 lines for about 48 stated). Two changes, both shown here with invented names and
a stub AI:

- the pass-1 prompt asks for every relationship, sentence by sentence, several per sentence;
- the second look asks about each paragraph that has fewer relationship lines than its names need,
  one call a paragraph, instead of one call for the names with no line.

The stub stands in for the model: when asked for a whole document it stops early (as the real one
did), and when asked about one paragraph it answers from the paragraph it is given, by the
prompt's own kinds. So the test holds the plumbing: the right paragraphs are asked, each with its
own text and names, and what comes back reaches the end as a relationship.
"""

from __future__ import annotations

import asyncio
import json
import re
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.ai import Reply
from dmbot.memory import lore_read as lr

# (who, the words the text uses, whom, the kind the shaping pass maps it to, the plain-list words)
FACTS = [
    ("Ysolde Marrow", "is a scout associated with", "Lantern League", "member", "is a member of"),
    ("Pell Wick", "is a bodyguard for", "Orrin Dace", "works for", "works for"),
    ("Sorrel Tane", "served in", "Tin Alliance", "member", "is a member of"),
    ("Orrin Dace", "mentored", "Pell Wick", "mentor", "mentors"),
    ("Orrin Dace", "also mentored", "Sorrel Tane", "mentor", "mentors"),
    ("Orrin Dace", "founded", "Lantern League", "founded", "founded"),
    ("Ysolde Marrow", "resides in", "Ravenmoor", "in", "lives in"),
    ("Hale Brisk", "leads", "Tin Alliance", "leads", "leads"),
    ("Hale Brisk", "is a rival of", "Orrin Dace", "rival", "is a rival of"),
    ("Mavis Crowe", "owns", "Gull Inn", "owns", "owns"),
    ("Gull Inn", "stands in", "Ravenmoor", "in", "is in"),
    ("Tobin Reeve", "serves", "Hale Brisk", "serves", "serves"),
    ("Tobin Reeve", "is a friend of", "Mavis Crowe", "friend", "is a friend of"),
    ("Edda Flint", "is an ally of", "Tin Alliance", "ally", "is an ally of"),
    ("Edda Flint", "is an enemy of", "Lantern League", "enemy", "is an enemy of"),
    ("Corin Vale", "worships", "Saint Wren", "worships", "worships"),
    ("Corin Vale", "is the father of", "Edda Flint", "family", "is family of (father of)"),
    ("Wynn Ashby", "knows", "Corin Vale", "knows", "knows"),
    ("Wynn Ashby", "works for", "Mavis Crowe", "works for", "works for"),
    ("Saint Wren", "is revered in", "Ravenmoor", "in", "is in"),
]
NAMES = list(dict.fromkeys(x for f in FACTS for x in (f[0], f[2])))
# two sentences to a paragraph (a line of the document), each paragraph padded with plain words
PARAGRAPHS = [
    " ".join(f"{a} {words} {b}." for a, words, b, _, _ in FACTS[i : i + 2])
    + " The days there were long and quiet, and little else is told of them in the old books."
    for i in range(0, len(FACTS), 2)
]
DOCUMENT = "\n".join(PARAGRAPHS)
STOPS_AFTER = 8  # the pass-1 stub writes this many relationship lines and no more


def sentence_of(a: str, words: str, b: str) -> str:
    return f"{a} {words} {b}"


def names_list() -> str:
    return "NAMES\n" + "\n".join(f'- {n} | person | quote: "{n}"' for n in NAMES) + "\n"


def links_lines(facts: list[tuple[str, str, str, str, str]]) -> str:
    return "".join(
        f'- {a} — {plain} — {b} | quote: "{sentence_of(a, words, b)}"\n'
        for a, words, b, _, plain in facts
    )


def shaped(request: str) -> str:
    """Pass 2: the JSON for the lines it was given (it may only reformat)."""
    rows = []
    for a, words, b, how, plain in FACTS:
        if f"- {a} — {plain} — {b} |" in request:
            rows.append(
                {"a": a, "how": how, "words": "", "b": b, "quote": sentence_of(a, words, b)}
            )
    names = [
        {"name": n, "kind": "person", "also": [], "secret": [], "quote": ""}
        for n in NAMES
        if f"- {n} |" in request
    ]
    return json.dumps({"names": names, "connections": rows})


def model() -> Any:
    calls: list[tuple[str, str]] = []

    async def complete(system: str, text: str, *, max_tokens: int, schema: Any = None) -> Reply:
        if schema is not None:
            return Reply(shaped(text), False)
        calls.append((system, text))
        if "SECOND LOOK" in system:
            paragraph = text.split("<document>", 1)[1].split("</document>", 1)[0]
            here = [f for f in FACTS if sentence_of(f[0], f[1], f[2]) in paragraph]
            return Reply("RELATIONSHIPS\n" + links_lines(here), False)
        # the whole document: every name, but it stops after a few relationships
        return Reply(names_list() + "\nRELATIONSHIPS\n" + links_lines(FACTS[:STOPS_AFTER]), False)

    fake = SimpleNamespace(complete=AsyncMock(side_effect=complete), model="claude-sonnet-5-5")
    fake.calls = calls
    return fake


class Gate:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class ThePassOnePrompt(unittest.TestCase):
    def test_it_asks_for_every_kind_the_text_can_state(self) -> None:
        prompt = lr.read_instructions()
        for kind in (
            "is a member of",
            "works for",
            "serves",
            "mentors",
            "is an apprentice of",
            "is a rival of",
            "is an ally of",
            "is an enemy of",
            "owns",
            "lives in",
            "founded",
            "leads",
            "knows",
            "is family of",
        ):
            self.assertIn(kind, prompt)

    def test_it_asks_sentence_by_sentence_several_to_a_sentence_and_never_to_infer(
        self,
    ) -> None:
        prompt = lr.read_instructions()
        self.assertIn("sentence by sentence", prompt)
        self.assertIn("One sentence may give several", prompt)
        self.assertIn("never infer", prompt)


class TheDocument(unittest.TestCase):
    def test_the_test_document_states_twenty_relationships_in_ten_paragraphs(self) -> None:
        self.assertEqual((len(FACTS), len(PARAGRAPHS)), (20, 10))
        self.assertGreater(len(DOCUMENT), lr.SECOND_LOOK_MIN_CHARS)
        for a, words, b, _, _ in FACTS:
            self.assertIn(sentence_of(a, words, b), DOCUMENT)


class TheSecondLookByParagraph(unittest.IsolatedAsyncioTestCase):
    async def read(self) -> tuple[Any, Any]:
        fake = model()
        done = await lr.read_lore(fake, fake, DOCUMENT, {}, Gate())
        return fake, done

    async def test_all_twenty_relationships_come_through(self) -> None:
        _, done = await self.read()
        pairs = {(c.a, c.b) for c in done.links}
        self.assertEqual(pairs, {(a, b) for a, _, b, _, _ in FACTS})
        self.assertEqual(len(done.links), 20)

    async def test_only_the_paragraphs_that_were_skipped_are_asked_about(self) -> None:
        fake, done = await self.read()
        asked = [t for s, t in fake.calls if "SECOND LOOK" in s]
        # the first four paragraphs hold the eight lines pass 1 wrote; the other six were skipped
        self.assertEqual(len(asked), 6)
        for request in asked:
            paragraph = request.split("<document>", 1)[1].split("</document>", 1)[0]
            self.assertEqual(len(paragraph.strip().splitlines()), 1)  # one paragraph at a time
        self.assertEqual(done.stats.paragraphs_asked, 6)
        self.assertEqual(done.stats.pass1_links, STOPS_AFTER)

    async def test_each_paragraph_is_given_its_own_names(self) -> None:
        fake, _ = await self.read()
        asked = [t for s, t in fake.calls if "SECOND LOOK" in s]
        for request in asked:
            paragraph = request.split("<document>", 1)[1].split("</document>", 1)[0]
            listed = re.findall(r"\n- (.+)", request.split("<names_to_check>", 1)[1])
            self.assertGreaterEqual(len(listed), 2)
            for name in listed:
                self.assertIn(name, paragraph)  # (a name the paragraph does not hold is not asked)

    async def test_the_log_counts_lines_per_paragraph_before_and_after_never_words(self) -> None:
        _, done = await self.read()
        before, after = done.stats.paragraph_lines_before, done.stats.paragraph_lines_after
        self.assertRegex(before, r"^\d+/\d+/\d+/\d+$")
        self.assertRegex(after, r"^\d+/\d+/\d+/\d+$")
        self.assertNotEqual(before, after)
        # six paragraphs had no line before, none have none after
        self.assertEqual(int(before.split("/")[0]), 6)
        self.assertEqual(int(after.split("/")[0]), 0)

    async def test_a_document_pass_one_read_in_full_is_asked_nothing_more(self) -> None:
        async def complete(system: str, text: str, *, max_tokens: int, schema: Any = None) -> Reply:
            if schema is not None:
                return Reply(shaped(text), False)
            return Reply(names_list() + "\nRELATIONSHIPS\n" + links_lines(FACTS), False)

        fake = SimpleNamespace(complete=AsyncMock(side_effect=complete), model="claude-sonnet-5-5")
        done = await lr.read_lore(fake, fake, DOCUMENT, {}, Gate())
        self.assertEqual((done.stats.paragraphs_asked, done.stats.second_calls), (0, 0))
        self.assertEqual(len(done.links), 20)

    async def test_no_more_than_the_most_paragraphs_are_asked(self) -> None:
        lines = [f"Aldo{n} and Beda{n} met." for n in range(lr.PARAGRAPH_LOOKS + 15)]
        text = "\n".join(lines) + " " + "Filler about nothing. " * 80
        listed = lr.PlainList(
            names=[f"- {w} | person" for n in range(len(lines)) for w in (f"Aldo{n}", f"Beda{n}")],
            links=[],
        )
        gaps = lr.paragraph_gaps(text, listed)
        self.assertEqual(len(gaps), len(lines))  # (each asks for a line)
        self.assertEqual(len(gaps[: lr.PARAGRAPH_LOOKS]), lr.PARAGRAPH_LOOKS)


class TheParagraphs(unittest.TestCase):
    def test_a_long_paragraph_is_cut_at_a_sentence_end(self) -> None:
        text = "A sentence here. " * 400
        pieces = lr.paragraphs(text)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(p) <= lr.PARAGRAPH_CHARS for p in pieces))
        self.assertTrue(all(p.endswith(".") for p in pieces))

    def test_a_sentence_with_a_line_between_its_names_is_not_a_gap(self) -> None:
        listed = lr.PlainList(
            names=["- Mira | person", "- Aldric | person", "- Greyfen | place"],
            links=[
                '- Mira — knows — Aldric | quote: "x"',
                '- Aldric — leads — Greyfen | quote: "x"',
            ],
        )
        self.assertEqual(lr.paragraph_gaps("Mira knew Aldric, who led Greyfen.", listed), [])

    def test_an_other_name_counts_a_name_as_there(self) -> None:
        listed = lr.PlainList(
            names=["- Mira | person | other names: the Fox", "- Aldric | person"], links=[]
        )
        gaps = lr.paragraph_gaps("The Fox once met Aldric on the road.", listed)
        self.assertEqual([(g[1], g[2]) for g in gaps], [(["Mira", "Aldric"], 1)])


class TheGapRules(unittest.TestCase):
    def test_three_names_in_a_sentence_with_one_line_is_a_gap(self) -> None:
        listed = lr.PlainList(
            names=["- Orrin | person", "- Pell | person", "- Sorrel | person"],
            links=['- Orrin — mentors — Pell | quote: "x"'],
        )
        gaps = lr.paragraph_gaps("Orrin mentored two apprentices, Pell and Sorrel.", listed)
        self.assertEqual([g[2] for g in gaps], [1])
        listed.links.append('- Orrin — mentors — Sorrel | quote: "x"')
        self.assertEqual(
            lr.paragraph_gaps("Orrin mentored two apprentices, Pell and Sorrel.", listed), []
        )

    def test_a_line_written_with_an_other_name_counts_for_the_main_name(self) -> None:
        listed = lr.PlainList(
            names=["- Mira | person | other names: the Fox", "- Aldric | person"],
            links=['- the Fox — knows — Aldric | quote: "x"'],
        )
        self.assertEqual(lr.paragraph_gaps("Mira, the Fox, met Aldric on the road.", listed), [])


class TheSecondLookStops(unittest.IsolatedAsyncioTestCase):
    NAMES = "NAMES\n- Mira | person\n- Brother Aldric | person\n- Greyfen Abbey | place\n"

    async def test_out_of_funds_cancels_the_other_paragraphs_calls(self) -> None:
        from dmbot.ai import AIOutOfFunds

        text = "\n".join(
            f"Mira met Brother Aldric at Greyfen Abbey, {n}. " + "Filler. " * 200 for n in range(5)
        )
        started = 0
        finished = 0

        async def complete(
            system: str, text_: str, *, max_tokens: int, schema: Any = None
        ) -> Reply:
            nonlocal started, finished
            if schema is not None:
                return Reply(json.dumps({"names": [], "connections": []}), False)
            if "SECOND LOOK" not in system:
                return Reply(self.NAMES, False)
            started += 1
            if started == 1:
                raise AIOutOfFunds("no credit")
            await asyncio.sleep(5)
            finished += 1
            return Reply("RELATIONSHIPS\n", False)

        fake = SimpleNamespace(complete=AsyncMock(side_effect=complete), model="claude-sonnet-5-5")
        with self.assertRaises(AIOutOfFunds):
            await lr.read_lore(fake, fake, text, {}, Gate())
        self.assertEqual(finished, 0)  # (none of the others ran to the end)

    async def test_a_bug_in_a_call_is_not_swallowed_as_an_ai_failure(self) -> None:
        text = "Mira met Brother Aldric at Greyfen Abbey. " + "Filler about nothing. " * 80

        async def complete(
            system: str, text_: str, *, max_tokens: int, schema: Any = None
        ) -> Reply:
            if schema is not None:
                return Reply(json.dumps({"names": [], "connections": []}), False)
            if "SECOND LOOK" not in system:
                return Reply(self.NAMES, False)
            raise KeyError("a bug")

        fake = SimpleNamespace(complete=AsyncMock(side_effect=complete), model="claude-sonnet-5-5")
        with self.assertRaises(KeyError):
            await lr.read_lore(fake, fake, text, {}, Gate())

    async def test_progress_is_told_as_each_paragraph_comes_back(self) -> None:
        told: list[tuple[str, int, int]] = []
        fake = model()
        await lr.read_lore(
            fake, fake, DOCUMENT, {}, Gate(), progress=lambda s, d, t: told.append((s, d, t))
        )
        looking = [d for s, d, _ in told if s == "looking"]
        self.assertGreater(
            len(looking), 2
        )  # (the start of the second look, then one beat per paragraph back)
