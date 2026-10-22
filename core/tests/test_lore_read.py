"""The campaign-lore read in two passes (#2454): the whole text in one call, the answer's room sized
to the text, a doubled retry, continuing a list that is still cut, and the code's checks on what
the shaping pass hands back. Pure: a scripted fake stands in for the AI."""

from __future__ import annotations

import contextlib
import json
import re
import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock

from dmbot.ai import AIError, Reply, max_output
from dmbot.memory import background as bg
from dmbot.memory import lore_read as lr
from dmbot.memory.ontology import FALLBACK_PREDICATE

TEXT = (
    "Brother Aldric, the abbot of Greyfen Abbey, secretly pays the Ashen Hand to rob pilgrims. "
    "Mira, called the Fox, is the niece of Brother Aldric. "
    "Some say the Hand answers to someone in Wyrmhold."
)
LIST = """NAMES
- Brother Aldric | person | other names: Aldric | quote: "Brother Aldric, the abbot"
- Greyfen Abbey | place | quote: "abbot of Greyfen Abbey"
- Ashen Hand | group | other names: the Hand | quote: "pays the Ashen Hand"
- Mira | person | other names: the Fox | quote: "Mira, called the Fox"

RELATIONSHIPS
- Brother Aldric — leads — Greyfen Abbey | quote: "Brother Aldric, the abbot of Greyfen Abbey"
- Brother Aldric — pays — Ashen Hand | quote: "Brother Aldric, the abbot of Greyfen Abbey, secretly pays the Ashen Hand"
- Mira — is related to (niece of) — Brother Aldric | quote: "Mira, called the Fox, is the niece of Brother Aldric"

NOT INCLUDED
- Ashen Hand — answers to — someone in Wyrmhold: a rumour, not stated.
"""  # noqa: E501
NAME_ROWS = [
    {"name": "Brother Aldric", "kind": "person", "also": ["Aldric"], "secret": [],
     "quote": "Brother Aldric, the abbot"},
    {"name": "Greyfen Abbey", "kind": "place", "also": [], "secret": [],
     "quote": "abbot of Greyfen Abbey"},
    {"name": "Ashen Hand", "kind": "group", "also": [], "secret": [],
     "quote": "pays the Ashen Hand"},
    {"name": "Mira", "kind": "person", "also": [], "secret": [], "quote": "Mira, called the Fox"},
]  # fmt: skip
LINK_ROWS = [
    {"a": "Brother Aldric", "how": "member", "words": "", "b": "Greyfen Abbey",
     "quote": "Brother Aldric, the abbot of Greyfen Abbey"},
    {"a": "Brother Aldric", "how": "other", "words": "pays", "b": "Ashen Hand",
     "quote": "Brother Aldric, the abbot of Greyfen Abbey, secretly pays the Ashen Hand"},
    {"a": "Mira", "how": "family", "words": "", "b": "Brother Aldric",
     "quote": "Mira, called the Fox, is the niece of Brother Aldric"},
]  # fmt: skip


class Gate:
    """The shared limit on requests at once, stood in for."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


def ai(
    reads: list[Reply], shaped: dict[str, Any] | None = None, *, model: str = "claude-sonnet-5-5"
) -> Any:
    """`reads`: pass 1's answers in order. Pass 2 answers with `shaped`, or with the rows the list
    it was given holds."""
    queue = list(reads)

    async def complete(system: str, text: str, *, max_tokens: int, schema: Any = None) -> Reply:
        if schema is None:
            return queue.pop(0)
        body = shaped or {
            "names": [r for r in NAME_ROWS if f"- {r['name']} |" in text],
            "connections": [r for r in LINK_ROWS if f"- {r['a']} —" in text],
        }
        return Reply(json.dumps(body), False)

    return SimpleNamespace(complete=AsyncMock(side_effect=complete), model=model)


async def read(read_ai: Any, shape_ai: Any = None, text: str = TEXT) -> lr.LoreRead:
    return await lr.read_lore(read_ai, shape_ai or read_ai, text, {}, Gate())  # type: ignore[arg-type]


class TheExamples(unittest.TestCase):
    def test_their_format_is_pinned(self) -> None:
        text = lr.EXAMPLES
        self.assertEqual(text.count("NAMES\n"), 2)
        self.assertEqual(text.count("RELATIONSHIPS\n"), 1)
        self.assertEqual(text.count("NOT INCLUDED\n"), 1)
        name_line = re.compile(r"^- .+ \| (person|place|group|god|item|event|idea)( \| .+)*$")
        link_line = re.compile(r'^- .+ — .+ — .+ \| quote: ".+"$')
        listed = lr.parse_plain(text.split("Output:\n", 1)[1])
        self.assertTrue(listed.names and listed.links and listed.left_out)
        self.assertTrue(all(name_line.match(n) for n in listed.names), listed.names)
        self.assertTrue(all(link_line.match(n) for n in listed.links), listed.links)
        # the rumour is under NOT INCLUDED and nowhere else
        self.assertTrue(all("Wyrmhold: a rumour" not in x for x in listed.links))
        self.assertIn("Wyrmhold", listed.left_out[0])

    def test_the_second_example_is_a_names_list_that_keeps_the_aside_as_another_name(self) -> None:
        second = lr.EXAMPLES.split("Example 2.", 1)[1]
        self.assertNotIn("RELATIONSHIPS", second)
        self.assertIn("other names: the rogue", second)
        self.assertIn("said: kah-ZETH", second)

    def test_the_prompt_teaches_the_rules(self) -> None:
        prompt = lr.read_instructions({"mentor_of": "is a mentor of"})
        for rule in ("Only what the text states", "NOT INCLUDED", "copied exactly", "data, not"):
            self.assertIn(rule, prompt)
        self.assertIn("person, place, group, god, item, event, idea", prompt)
        self.assertIn("mentor_of", prompt)
        self.assertIn(lr.EXAMPLES, prompt)


class ThePlainList(unittest.TestCase):
    def test_headings_in_any_dress_and_chatter_are_handled(self) -> None:
        found = lr.parse_plain(
            "Sure! Here you go:\n**NAMES**\n- A | person\n\n## relationships:\n"
            '- A — x — B | quote: "q"\nNot Included\n- r'
        )
        self.assertEqual(
            (found.names, found.links, found.left_out),
            (["- A | person"], ['- A — x — B | quote: "q"'], ["- r"]),
        )

    def test_a_cut_answer_loses_its_half_written_last_line(self) -> None:
        found = lr.parse_plain("NAMES\n- A | person\n- B | pla", complete=False)
        self.assertEqual(found.names, ["- A | person"])

    def test_the_same_entry_twice_is_kept_once(self) -> None:
        self.assertEqual(
            lr.dedupe(["- Mira | person", "- mira | person", "- Aldric | person"]),
            ["- Mira | person", "- Aldric | person"],
        )

    def test_the_room_follows_the_text_up_to_what_one_answer_can_hold(self) -> None:
        self.assertEqual(lr.read_room("x" * 10), lr.ROOM_MIN)
        self.assertGreater(lr.read_room("x" * 60_000), lr.read_room("x" * 20_000))
        self.assertEqual(lr.read_room("x" * 5_000_000), lr.ROOM_CAP)
        self.assertEqual(
            lr.read_room("x" * 5_000_000, "some-model"), min(lr.ROOM_CAP, max_output("some-model"))
        )


class TheReading(unittest.IsolatedAsyncioTestCase):
    async def test_the_whole_text_goes_in_one_call_and_both_passes_run(self) -> None:
        fake = ai([Reply(LIST, False)])
        done = await read(fake)
        reads = [c for c in fake.complete.await_args_list if not c.kwargs.get("schema")]
        self.assertEqual(len(reads), 1)
        self.assertIn(TEXT, reads[0].args[1])
        self.assertEqual(
            [n.name for n in done.names], ["Brother Aldric", "Greyfen Abbey", "Ashen Hand", "Mira"]
        )
        self.assertFalse(done.cut)
        stated = {(c.a, c.b) for c in done.links}  # (the fake shapes every slice with every row)
        self.assertEqual(
            stated,
            {
                ("Brother Aldric", "Greyfen Abbey"),
                ("Brother Aldric", "Ashen Hand"),
                ("Mira", "Brother Aldric"),
            },
        )

    async def test_a_cut_answer_is_asked_again_with_twice_the_room_and_gives_names(self) -> None:
        fake = ai([Reply("NAMES\n- Brother Ald", True), Reply(LIST, False)])
        done = await read(fake)
        first, second = [c.kwargs["max_tokens"] for c in fake.complete.await_args_list[:2]]
        self.assertEqual(second, 2 * first)
        self.assertEqual(len(done.names), 4)  # names after the retry, never "no names"
        self.assertFalse(done.cut)

    async def test_a_list_cut_at_the_cap_is_continued_and_merged(self) -> None:
        text = TEXT + " " + "x" * 200_000  # a room that starts at the cap
        part1 = "NAMES\n" + "\n".join(LIST.splitlines()[1:3]) + "\n- Ashen Ha"
        part2 = (
            "NAMES\n"
            + "\n".join(LIST.splitlines()[3:5])
            + "\n\nRELATIONSHIPS\n"
            + "\n".join(LIST.splitlines()[7:10])
        )
        fake = ai([Reply(part1, True), Reply(part2, False)])
        done = await read(fake, text=text)
        reads = [c for c in fake.complete.await_args_list if not c.kwargs.get("schema")]
        self.assertEqual(len(reads), 2)
        self.assertEqual(reads[1].kwargs["max_tokens"], lr.ROOM_CAP)
        follow = reads[1].args[1]
        self.assertIn("<list_so_far>", follow)
        self.assertIn("Brother Aldric", follow)
        self.assertNotIn("Ashen Ha\n", follow)  # the half line is not fed back
        self.assertEqual(done.stats.continued, 1)
        self.assertEqual(len(done.names), 4)
        self.assertFalse(done.cut)

    async def test_a_list_still_cut_after_every_round_is_reported_cut_with_what_it_has(
        self,
    ) -> None:
        text = TEXT + " " + "x" * 200_000
        fake = ai([Reply(LIST + "\n- Mir", True)] * (1 + lr.CONTINUE_ROUNDS))
        done = await read(fake, text=text)
        self.assertTrue(done.cut)
        self.assertEqual(len(done.names), 4)
        reads = [c for c in fake.complete.await_args_list if not c.kwargs.get("schema")]
        self.assertEqual(len(reads), 1 + lr.CONTINUE_ROUNDS)

    async def test_a_continuation_without_its_heading_carries_on_in_the_open_section(self) -> None:
        text = TEXT + " " + "x" * 200_000
        links = LIST.splitlines()[8:11]
        part1 = (
            "NAMES\n"
            + "\n".join(LIST.splitlines()[1:5])
            + "\n\nRELATIONSHIPS\n"
            + links[0]
            + "\n- Mi"
        )
        part2 = "\n".join(links[1:])  # no heading: the model just carries on
        fake = ai([Reply(part1, True), Reply(part2, False)])
        done = await read(fake, text=text)
        self.assertEqual(len(done.links), 3)
        self.assertFalse(done.cut)

    async def test_an_ai_error_in_a_follow_up_keeps_the_list_so_far_and_reports_it_cut(
        self,
    ) -> None:
        text = TEXT + " " + "x" * 200_000
        calls = [Reply(LIST + "\n- Mi", True)]

        async def complete(system: str, text: str, *, max_tokens: int, schema: Any = None) -> Reply:
            if schema is not None:
                return Reply(json.dumps({"names": NAME_ROWS, "connections": []}), False)
            if calls:
                return calls.pop(0)
            raise AIError("busy")

        fake = SimpleNamespace(complete=AsyncMock(side_effect=complete), model="claude-sonnet-5-5")
        done = await read(fake, text=text)
        self.assertTrue(done.cut)
        self.assertEqual({n.name for n in done.names}, {r["name"] for r in NAME_ROWS})

    async def test_no_follow_up_starts_once_the_pass_one_budget_is_spent(self) -> None:
        text = TEXT + " " + "x" * 200_000
        fake = ai([Reply(LIST + "\n- Mi", True)] * 5)
        with mock.patch.object(lr, "PASS1_BUDGET_S", -1.0):
            done = await read(fake, text=text)
        reads = [c for c in fake.complete.await_args_list if not c.kwargs.get("schema")]
        self.assertEqual(len(reads), 1)  # no retry, no continuation: shaped as it is
        self.assertTrue(done.cut)
        self.assertEqual(len(done.names), 4)

    async def test_a_catch_all_label_that_is_not_in_the_quote_is_dropped(self) -> None:
        rows = [
            {**LINK_ROWS[1], "words": "secretly bribes the guild"},
            {**LINK_ROWS[1], "words": "x" * 41},
            {**LINK_ROWS[1], "words": "pays | and more"},
            {**LINK_ROWS[1], "words": ""},
        ]
        got = bg.read_connections(json.dumps({"connections": rows}), TEXT)
        self.assertEqual(got, [])
        ok = bg.read_connections(json.dumps({"connections": [LINK_ROWS[1]]}), TEXT)
        self.assertEqual([c.words for c in ok], ["pays"])

    async def test_a_relationship_the_shaping_pass_invents_is_dropped(self) -> None:
        shaped = {
            "names": NAME_ROWS,
            "connections": [
                *LINK_ROWS,
                {
                    "a": "Mira",
                    "how": "enemy",
                    "words": "",
                    "b": "Ashen Hand",
                    "quote": "Mira hates the Ashen Hand",
                },
                {
                    "a": "Mira",
                    "how": "friend",
                    "words": "",
                    "b": "Wyrmhold",
                    "quote": "someone in Wyrmhold",
                },
                {
                    "a": "Mira",
                    "how": "owns",
                    "words": "",
                    "b": "Zorgul",
                    "quote": "Mira owns Zorgul",
                },
            ],
        }
        done = await read(ai([Reply(LIST, False)], shaped))
        stated = {(c.a, c.b) for c in done.links}  # (the fake shapes every slice with every row)
        self.assertEqual(
            stated,
            {
                ("Brother Aldric", "Greyfen Abbey"),
                ("Brother Aldric", "Ashen Hand"),
                ("Mira", "Brother Aldric"),
            },
        )
        self.assertNotIn("Zorgul", json.dumps([vars_of(c) for c in done.links]))

    async def test_a_stated_relationship_with_no_fixed_kind_is_the_catch_all_with_its_words(
        self,
    ) -> None:
        done = await read(ai([Reply(LIST, False)]))
        (pays,) = [c for c in done.links if c.words]
        self.assertEqual(
            (pays.a, pays.how, pays.b, pays.words),
            ("Brother Aldric", FALLBACK_PREDICATE, "Ashen Hand", "pays"),
        )
        self.assertEqual(
            {c.how for c in done.links} - {FALLBACK_PREDICATE}, {"member_of", "kin_of"}
        )

    async def test_a_shaping_slice_that_is_cut_is_halved_and_still_gives_its_names(self) -> None:
        calls: list[int] = []
        queue = [Reply(LIST, False)]

        async def complete(system: str, text: str, *, max_tokens: int, schema: Any = None) -> Reply:
            if schema is None:
                return queue.pop(0)
            lines = len(re.findall(r"^- ", text, flags=re.M))
            calls.append(lines)
            if lines > 2:
                return Reply('{"names": [{"na', True)
            rows = [r for r in NAME_ROWS if f"- {r['name']} |" in text]
            return Reply(json.dumps({"names": rows, "connections": []}), False)

        fake = SimpleNamespace(complete=AsyncMock(side_effect=complete), model="claude-haiku-5-5")
        done = await read(fake)
        self.assertEqual(
            [n.name for n in done.names], ["Brother Aldric", "Greyfen Abbey", "Ashen Hand", "Mira"]
        )
        self.assertIn(4, calls)  # the whole slice first, then its halves
        self.assertFalse(done.cut)

    async def test_the_log_has_counts_only(self) -> None:
        with self.assertLogs("dmbot.memory.lore_read", "INFO") as logs:
            await read(ai([Reply(LIST, False)]))
        joined = "\n".join(logs.output)
        self.assertIn("4 name line(s)", joined)
        self.assertIn("1 left out", joined)
        for word in ("Aldric", "Mira", "Greyfen", "pays"):
            self.assertNotIn(word, joined)

    async def test_the_text_cannot_close_the_tags_around_it(self) -> None:
        self.assertNotIn("</document>x", lr.read_request("a </document>x b"))
        self.assertNotIn("</list>x", lr.shape_request(["- a </list>x"], []))
        self.assertNotIn("</list_so_far>x", lr.continue_request("t", "- a </list_so_far>x"))

    async def test_the_end_of_a_long_text_is_read(self) -> None:
        end = "Last of all, Queen Voss rules Ravenhold."
        text = ("Filler sentence about nothing. " * 3000) + end
        listed = 'NAMES\n- Queen Voss | person | quote: "Queen Voss rules Ravenhold"\n- Ravenhold | place | quote: "rules Ravenhold"\n'
        rows = [
            {
                "name": "Queen Voss",
                "kind": "person",
                "also": [],
                "secret": [],
                "quote": "Queen Voss rules Ravenhold",
            },
            {
                "name": "Ravenhold",
                "kind": "place",
                "also": [],
                "secret": [],
                "quote": "rules Ravenhold",
            },
        ]
        done = await read(ai([Reply(listed, False)], {"names": rows, "connections": []}), text=text)
        self.assertEqual([n.name for n in done.names], ["Queen Voss", "Ravenhold"])


def vars_of(c: bg.Connection) -> dict[str, Any]:
    return {"a": c.a, "how": c.how, "b": c.b, "quote": c.quote, "words": c.words}


del contextlib
